"""uv run python -m data.synth --layout classic --n 20 --seed 1 [--currency EUR]
[--line-items 15 30] [--no-tax] [--scanned] [--no-render]"""

import argparse
from pathlib import Path

from . import CURRENCIES, LAYOUTS, generate


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="python -m data.synth", description="Generate synthetic invoices with answer keys."
    )
    p.add_argument("--layout", choices=LAYOUTS, required=True)
    p.add_argument("--n", type=int, default=10, help="number of invoices")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--currency", choices=list(CURRENCIES), default="USD")
    p.add_argument(
        "--line-items",
        type=int,
        nargs=2,
        default=(1, 8),
        metavar=("MIN", "MAX"),
        help="line-item count range",
    )
    p.add_argument("--no-tax", action="store_true")
    p.add_argument("--scanned", action="store_true", help="image-only PDFs, like paper scans")
    p.add_argument("--no-render", action="store_true", help="write answer keys only")
    p.add_argument("--docs-dir", type=Path, default=Path("data/docs"))
    p.add_argument("--truth-dir", type=Path, default=Path("data/truth"))
    a = p.parse_args(argv)

    docs = generate(
        layout=a.layout,
        n=a.n,
        seed=a.seed,
        currency=a.currency,
        line_items=tuple(a.line_items),
        with_tax=not a.no_tax,
        scanned=a.scanned,
        render=not a.no_render,
        docs_dir=a.docs_dir,
        truth_dir=a.truth_dir,
    )
    where = a.truth_dir if a.no_render else f"{a.docs_dir} and {a.truth_dir}"
    print(f"Wrote {len(docs)} invoices to {where}")


if __name__ == "__main__":
    main()
