"""Provider-independent per-learner notes for the Realtime teacher.

The teacher keeps short notes about how this learner learns (misconceptions, representations that
worked, preferences) under /memories. Notes are scoped to one learner, stored next to the
lesson library in the graph database, and capped in size. The app preloads them into each
request, so reads never cost an extra model round trip.
"""
from __future__ import annotations

import posixpath
import re
from datetime import datetime, timezone

from types import SimpleNamespace
from sqlalchemy import Column, MetaData, String, Table, Text, delete, insert, select, update

ROOT = "/memories"
MAX_FILES = 8
MAX_FILE_CHARS = 4000
PATH = re.compile(r"^/memories(/[A-Za-z0-9_.-]{1,64}){1,3}$")

notes = Table("lucid_learner_memory_v1", MetaData(),
              Column("human_id", String, primary_key=True),
              Column("path", String, primary_key=True),
              Column("content", Text, nullable=False),
              Column("updated_at", String, nullable=False))


class MemoryError(ValueError):
    """A command the store refuses; the message goes back to the model as a tool error."""


def _path(raw, *, root_ok=False):
    path = posixpath.normpath(str(raw or ""))
    if root_ok and path == ROOT:
        return path
    if ".." in str(raw) or not PATH.match(path):
        raise MemoryError(f"Paths must look like /memories/notes.md (got {raw!r})")
    return path


class LearnerMemory:
    """The store; `for_learner` returns a notes tool bound to one learner."""

    def __init__(self, graph):
        self.graph = graph
        with graph.store.transaction(write=True) as connection:
            notes.create(connection, checkfirst=True)

    def files(self, user) -> dict[str, str]:
        with self.graph.store.transaction() as connection:
            rows = connection.execute(select(notes.c.path, notes.c.content)
                                      .where(notes.c.human_id == user).order_by(notes.c.path))
            return {row.path: row.content for row in rows}

    def render(self, user) -> str:
        """All of a learner's notes as prompt text."""
        files = self.files(user)
        if not files:
            return "(no notes yet: this may be a new learner)"
        return "\n\n".join(f"{path}:\n{content}" for path, content in files.items())

    def write(self, user, path, content):
        if len(content) > MAX_FILE_CHARS:
            raise MemoryError(f"Keep each notes file under {MAX_FILE_CHARS} characters; summarize instead")
        now = datetime.now(timezone.utc).isoformat()
        with self.graph.store.transaction(write=True) as connection:
            key = (notes.c.human_id == user) & (notes.c.path == path)
            if connection.execute(select(notes.c.path).where(key)).first():
                connection.execute(update(notes).where(key).values(content=content, updated_at=now))
            else:
                count = len(connection.execute(select(notes.c.path).where(notes.c.human_id == user)).all())
                if count >= MAX_FILES:
                    raise MemoryError(f"At most {MAX_FILES} notes files; edit or delete an existing one")
                connection.execute(insert(notes).values(human_id=user, path=path, content=content, updated_at=now))

    def remove(self, user, path):
        with self.graph.store.transaction(write=True) as connection:
            removed = connection.execute(delete(notes).where(
                (notes.c.human_id == user) & ((notes.c.path == path) | notes.c.path.startswith(path + "/")))).rowcount
        if not removed:
            raise MemoryError(f"No such notes file: {path}")

    def for_learner(self, user) -> "LearnerMemoryTool":
        return LearnerMemoryTool(self, user)


class LearnerMemoryTool:
    """Provider-independent notes commands bound to one learner."""

    def __init__(self, store: LearnerMemory, user):
        self.store, self.user = store, user

    def to_dict(self):
        # Compatibility for archived provider adapters; the live agent uses function tools.
        return {"type": "memory_20250818", "name": "memory"}

    def call(self, arguments):
        command = arguments.get("command")
        if command not in ("view", "create", "str_replace", "insert", "delete", "rename"):
            raise MemoryError("Unknown notes command")
        return getattr(self, command)(SimpleNamespace(**{"view_range": None, **arguments}))

    def _read(self, path):
        files = self.store.files(self.user)
        if path not in files:
            raise MemoryError(f"No such notes file: {path}")
        return files[path]

    def view(self, command):
        path = _path(command.path, root_ok=True)
        files = self.store.files(self.user)
        if path == ROOT or path not in files:
            listed = [f"{p} ({len(c)} chars)" for p, c in files.items() if p.startswith(path.rstrip("/") + "/")]
            if path != ROOT and not listed:
                raise MemoryError(f"No such notes file: {path}")
            return f"Directory {path}:\n" + ("\n".join(listed) or "(empty)")
        lines = files[path].split("\n")
        start, end = command.view_range or (1, len(lines))
        end = len(lines) if end == -1 else end
        return "\n".join(f"{i:4d}\t{line}" for i, line in enumerate(lines, 1) if start <= i <= end)

    def create(self, command):
        path = _path(command.path)
        self.store.write(self.user, path, command.file_text)
        return f"Saved {path}"

    def str_replace(self, command):
        path = _path(command.path)
        content = self._read(path)
        count = content.count(command.old_str)
        if count != 1:
            raise MemoryError(f"old_str must appear exactly once in {path} (found {count})")
        self.store.write(self.user, path, content.replace(command.old_str, command.new_str))
        return f"Edited {path}"

    def insert(self, command):
        path = _path(command.path)
        lines = self._read(path).split("\n")
        if not 0 <= command.insert_line <= len(lines):
            raise MemoryError(f"insert_line must be between 0 and {len(lines)}")
        lines[command.insert_line:command.insert_line] = command.insert_text.rstrip("\n").split("\n")
        self.store.write(self.user, path, "\n".join(lines))
        return f"Inserted into {path}"

    def delete(self, command):
        path = _path(command.path)
        self.store.remove(self.user, path)
        return f"Deleted {path}"

    def rename(self, command):
        old, new = _path(command.old_path), _path(command.new_path)
        content = self._read(old)
        if new in self.store.files(self.user):
            raise MemoryError(f"{new} already exists")
        self.store.write(self.user, new, content)
        self.store.remove(self.user, old)
        return f"Renamed {old} to {new}"
