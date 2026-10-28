"""Synthetic invoice generator (AGE-11).

    from data.synth import generate
    docs = generate(layout="modern", n=10, seed=1)

Writes data/docs/{doc_id}.pdf (+ .png of page 1) and data/truth/{doc_id}.json. The doc_id is
opaque (D17): it never encodes layout, pool or seed.
"""

import random
import uuid
from dataclasses import dataclass
from pathlib import Path

from contracts import Invoice

from .generator import CURRENCIES, LAYOUTS, Settings, check_answer_key, make_document

__all__ = ["CURRENCIES", "LAYOUTS", "GeneratedDoc", "Settings", "check_answer_key", "generate"]


@dataclass
class GeneratedDoc:
    doc_id: str
    pdf_path: Path | None  # None when render=False
    png_path: Path | None
    truth: Invoice


def _doc_rng(settings: Settings, seed: int, index: int) -> random.Random:
    """One RNG per document, keyed on every setting, so different calls never share doc_ids."""
    s = settings
    key = f"{seed}:{index}:{s.layout}:{s.currency}:{s.min_items}-{s.max_items}:{s.with_tax}"
    if s.scanned:  # only when set, so digital documents keep their existing doc_ids
        key += ":scanned"
    return random.Random(key)


def generate(
    *,
    layout: str,
    n: int,
    seed: int,
    currency: str = "USD",
    line_items: tuple[int, int] = (1, 8),
    with_tax: bool = True,
    scanned: bool = False,
    render: bool = True,
    docs_dir: Path = Path("data/docs"),
    truth_dir: Path = Path("data/truth"),
) -> list[GeneratedDoc]:
    """Generates n invoices with answer keys. render=False writes the truth JSON only (fast).
    scanned=True renders image-only PDFs (no text layer), like scanned paper invoices."""
    settings = Settings(
        layout=layout,
        currency=currency,
        min_items=line_items[0],
        max_items=line_items[1],
        with_tax=with_tax,
        scanned=scanned,
    )
    truth_dir = Path(truth_dir)
    truth_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        rng = _doc_rng(settings, seed, i)
        doc_id = uuid.UUID(int=rng.getrandbits(128), version=4).hex[:12]
        doc = make_document(doc_id, settings, rng.getrandbits(63))
        (truth_dir / f"{doc_id}.json").write_text(doc.truth.model_dump_json(indent=2))
        pdf_path = png_path = None
        if render:
            from .render import render as render_doc

            pdf_path, png_path = render_doc(doc, Path(docs_dir))
        out.append(GeneratedDoc(doc_id, pdf_path, png_path, doc.truth))
    return out
