"""The card variant generator (AGE-63): mechanical, deterministic, and matching the committed files."""

import json
import random
import shutil
from collections import Counter
from pathlib import Path

import pytest

from experiments.hw7 import card_variants as cv
from experiments.hw7.analyze import load_cards


@pytest.fixture
def root(tmp_path) -> Path:
    """A cards root holding a copy of the frozen original cards."""
    shutil.copytree(cv.CARDS_ROOT / "original", tmp_path / "original")
    return tmp_path


def _read(root: Path, variant: str) -> dict[str, dict]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((root / variant).glob("*.json"))
    }


def _bytes(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for v in cv.VARIANTS
        for p in sorted((root / v).glob("*.json"))
    }


def test_running_twice_gives_byte_identical_files(root):
    assert cv.main(["--cards-root", str(root)]) == 0
    first = _bytes(root)
    assert cv.main(["--cards-root", str(root)]) == 0
    assert _bytes(root) == first
    assert len(first) == 14  # 7 cards × 2 variants


def test_committed_variants_match_the_generator(root):
    cv.write_variants(root)
    assert _bytes(root) == _bytes(cv.CARDS_ROOT)


def test_inflated_cards_have_the_source_description_and_their_own_price(root):
    cv.write_variants(root)
    original, inflated = _read(root, "original"), _read(root, "inflated")
    assert inflated.keys() == original.keys()
    description = original[cv.DESCRIPTION_SOURCE]["description"]
    for slug, card in inflated.items():
        assert card["description"] == description
        assert card == dict(original[slug], description=description)
        assert card["price_per_doc_usd"] == original[slug]["price_per_doc_usd"]


def test_price_shuffled_keeps_the_prices_and_descriptions(root):
    cv.write_variants(root)
    original, shuffled = _read(root, "original"), _read(root, "price_shuffled")
    assert shuffled.keys() == original.keys()
    assert Counter(c["price_per_doc_usd"] for c in shuffled.values()) == Counter(
        c["price_per_doc_usd"] for c in original.values()
    )
    for slug, card in shuffled.items():
        assert card["description"] == original[slug]["description"]
        assert {k: v for k, v in card.items() if k != "price_per_doc_usd"} == {
            k: v for k, v in original[slug].items() if k != "price_per_doc_usd"
        }


def test_price_shuffle_is_seed_zero_over_the_slug_sorted_list(root):
    cv.write_variants(root)
    original, shuffled = _read(root, "original"), _read(root, "price_shuffled")
    slugs = sorted(original)
    prices = [original[s]["price_per_doc_usd"] for s in slugs]
    random.Random(0).shuffle(prices)
    assert [shuffled[s]["price_per_doc_usd"] for s in slugs] == prices


def test_variant_cards_load_as_agent_cards():
    for variant in cv.VARIANTS:
        cards = load_cards(cv.CARDS_ROOT / variant)
        assert [c.slug for c in cards] == [c.slug for c in load_cards()]


def test_stale_variant_files_are_removed(root):
    (root / "inflated").mkdir()
    (root / "inflated" / "gone.json").write_text("{}")
    cv.write_variants(root)
    assert not (root / "inflated" / "gone.json").exists()


def test_missing_originals_is_a_clear_error(tmp_path):
    assert cv.main(["--cards-root", str(tmp_path)]) == 1
