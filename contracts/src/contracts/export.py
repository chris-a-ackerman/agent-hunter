"""Export every contract's JSON Schema to contracts/schemas/.

uv run python -m contracts.export
"""

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from contracts import MODELS

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"


def render(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n"


def export(out_dir: Path = SCHEMAS_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for model in MODELS:
        path = out_dir / f"{model.__name__}.json"
        path.write_text(render(model))
        written.append(path)
    return written


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else SCHEMAS_DIR
    for path in export(out):
        print(path)
