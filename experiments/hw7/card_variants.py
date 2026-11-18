"""Card variants for the misleading-card experiment (AGE-63): mechanical edits of the frozen cards.

    uv run python -m experiments.hw7.card_variants [--cards-root DIR]

Reads `cards/original/*.json` (and nothing else) and writes two variants next to it:

- `cards/inflated/`: every card keeps its slug, name, version, job types, price and region, but
  its `description` is replaced, unchanged, by the description of the `quillbase` card.
- `cards/price_shuffled/`: every card is unchanged except `price_per_doc_usd`. The prices of the
  slug-sorted cards are permuted with `random.Random(0).shuffle` and handed back in slug order.
  Whatever the shuffle gives is kept; it is never re-rolled.

No wording is written by hand. The output is deterministic: running it twice gives
byte-identical files. Commit the generated files; they are frozen once committed.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from contracts import AgentCard

CARDS_ROOT = Path(__file__).parent / "cards"
DESCRIPTION_SOURCE = "quillbase"  # the card whose description every inflated card gets
SHUFFLE_SEED = 0
VARIANTS = ("inflated", "price_shuffled")


def read_original(cards_root: Path = CARDS_ROOT) -> list[dict[str, Any]]:
    """The original cards as raw JSON objects (key order kept), sorted by slug. Each one is
    validated as an `AgentCard`."""
    paths = sorted((cards_root / "original").glob("*.json"))
    if not paths:
        raise ValueError(f"no agent cards in {cards_root / 'original'}")
    cards = []
    for p in paths:
        raw = json.loads(p.read_text(encoding="utf-8"))
        AgentCard.model_validate(raw)
        cards.append(raw)
    return sorted(cards, key=lambda c: c["slug"])


def inflated(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = next((c for c in cards if c["slug"] == DESCRIPTION_SOURCE), None)
    if source is None:
        raise ValueError(f"no {DESCRIPTION_SOURCE!r} card among the originals")
    return [dict(c, description=source["description"]) for c in cards]


def price_shuffled(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(cards, key=lambda c: c["slug"])
    prices = [c["price_per_doc_usd"] for c in ordered]
    random.Random(SHUFFLE_SEED).shuffle(prices)
    return [dict(c, price_per_doc_usd=p) for c, p in zip(ordered, prices, strict=True)]


def _dump(card: dict[str, Any]) -> str:
    return json.dumps(card, indent=2, ensure_ascii=False) + "\n"


def write_variants(cards_root: Path = CARDS_ROOT) -> dict[str, list[Path]]:
    """Write every variant to `cards_root/<variant>/<slug>.json`; returns the paths written.
    Stale `*.json` files in a variant dir (a slug no longer in `original/`) are removed."""
    originals = read_original(cards_root)
    out: dict[str, list[Path]] = {}
    for name, build in (("inflated", inflated), ("price_shuffled", price_shuffled)):
        target = cards_root / name
        target.mkdir(parents=True, exist_ok=True)
        cards = build(originals)
        for card in cards:
            AgentCard.model_validate(card)
        keep = {f"{c['slug']}.json" for c in cards}
        for stale in target.glob("*.json"):
            if stale.name not in keep:
                stale.unlink()
        paths = []
        for card in cards:
            path = target / f"{card['slug']}.json"
            path.write_text(_dump(card), encoding="utf-8")
            paths.append(path)
        out[name] = paths
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cards-root", type=Path, default=CARDS_ROOT, help="default: cards/")
    args = ap.parse_args(argv)
    try:
        written = write_variants(args.cards_root)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for name, paths in written.items():
        print(f"wrote {len(paths)} cards to {args.cards_root / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
