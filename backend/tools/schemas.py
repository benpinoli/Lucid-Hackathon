"""Generate/check contracts without changing the memory package."""
import json
import sys
from pathlib import Path

from backend.app.learning import LessonPlan
from backend.app.scene import export_schema

ROOT = Path(__file__).resolve().parents[2]


def schemas():
    return {"scene.v1.schema.json": export_schema(),
            "learning-plan.v1.schema.json": LessonPlan.model_json_schema(mode="serialization")}


if __name__ == "__main__":
    for name, schema in schemas().items():
        path = ROOT / "shared" / "schemas" / name
        if "--check" in sys.argv:
            if json.loads(path.read_text()) != schema:
                raise SystemExit(f"Schema drift: regenerate {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print("Scene and teaching schemas match Python contracts.")
