"""uv run python -m mocks.fake_providers [--port 8101] [--truth-dir data/truth] [--fail-budget]"""

import argparse
import os
from pathlib import Path

import uvicorn

from .app import create_app
from .corruption import MOCKS


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m mocks.fake_providers")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8101)
    p.add_argument(
        "--truth-dir", type=Path, default=Path(os.environ.get("TRUTH_DIR", "data/truth"))
    )
    p.add_argument(
        "--slugs",
        default=os.environ.get("MOCK_PROVIDER_SLUGS", ",".join(MOCKS)),
        help="comma-separated mock slugs to serve",
    )
    p.add_argument("--fail-budget", action="store_true", help="every task returns 402 (D18)")
    a = p.parse_args(argv)

    slugs = [s.strip() for s in a.slugs.split(",") if s.strip()]
    app = create_app(slugs=slugs, truth_dir=a.truth_dir, fail_budget=a.fail_budget)
    uvicorn.run(app, host=a.host, port=a.port)


if __name__ == "__main__":
    main()
