"""Vetted 3D model catalog and its local cache.

Lessons name models by catalog id only. Each file is downloaded once from its pinned URL,
checked against its sha256, and served same-origin, so the browser never contacts a
third party and a changed upstream file is refused rather than shown.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("lucid.models")

CATALOG_PATH = Path(__file__).resolve().parents[1] / "config" / "models.json"
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "model_cache"
MAX_MODEL_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class CatalogModel:
    id: str
    name: str
    description: str
    url: str
    sha256: str
    bytes: int
    license: str
    credit: str
    clips: tuple[str, ...]

    def public(self, ready: bool) -> dict:
        return {"id": self.id, "name": self.name, "license": self.license, "credit": self.credit,
                "clips": list(self.clips), "bytes": self.bytes, "ready": ready}


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, CatalogModel]:
    entries = json.loads(path.read_text(encoding="utf-8"))["models"]
    catalog = {e["id"]: CatalogModel(**{**e, "clips": tuple(e["clips"])}) for e in entries}
    if len(catalog) != len(entries):
        raise ValueError("Duplicate model id in catalog")
    return catalog


CATALOG = load_catalog()


def describe_for_author() -> str:
    """One line per model for the author model's prompt."""
    return "\n".join(f"  {m.id}: {m.name}. {m.description}" + (f" Clips: {', '.join(m.clips)}." if m.clips else "")
                     for m in CATALOG.values())


class ModelError(RuntimeError):
    pass


class ModelLibrary:
    """Download-once cache. `fetch` is injectable so tests never touch the network."""

    def __init__(self, cache_dir: Path | str = DEFAULT_CACHE, catalog=None, fetch=None):
        self.cache_dir = Path(cache_dir)
        self.catalog = CATALOG if catalog is None else catalog
        self._fetch = fetch or self._download
        self._locks = {model_id: threading.Lock() for model_id in self.catalog}

    @classmethod
    def from_env(cls):
        return cls(os.getenv("LUCID_MODEL_CACHE") or DEFAULT_CACHE)

    def _file(self, model: CatalogModel) -> Path:
        # Content-addressed: a catalog hash change never serves a stale file.
        return self.cache_dir / f"{model.id}-{model.sha256[:16]}.glb"

    def ready(self, model_id: str) -> bool:
        model = self.catalog.get(model_id)
        return model is not None and self._file(model).is_file()

    @staticmethod
    def _download(url: str) -> bytes:
        with httpx.stream("GET", url, follow_redirects=True, timeout=30.0) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_MODEL_BYTES:
                    raise ModelError("Model exceeds the size limit")
                chunks.append(chunk)
        return b"".join(chunks)

    def path(self, model_id: str) -> Path:
        """The verified local file, downloading it first if needed."""
        model = self.catalog.get(model_id)
        if model is None:
            raise KeyError(model_id)
        target = self._file(model)
        with self._locks[model_id]:
            if target.is_file():
                return target
            data = self._fetch(model.url)
            if len(data) > MAX_MODEL_BYTES or hashlib.sha256(data).hexdigest() != model.sha256:
                raise ModelError(f"Model {model_id} failed its integrity check")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(".part")
            partial.write_bytes(data)
            partial.replace(target)
            return target

    def prefetch(self):
        """Download every catalog model in the background so lessons show them without waiting."""
        def run():
            for model_id in self.catalog:
                try:
                    self.path(model_id)
                except (httpx.HTTPError, ModelError, OSError) as exc:
                    log.warning("Model %s not cached: %s", model_id, type(exc).__name__)
        thread = threading.Thread(target=run, name="lucid-model-prefetch", daemon=True)
        thread.start()
        return thread
