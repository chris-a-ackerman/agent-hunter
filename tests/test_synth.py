import re

import pytest

from contracts import Invoice
from data.synth import LAYOUTS, Settings, check_answer_key, generate
from data.synth.generator import make_document
from data.synth.render import format_date, format_money, to_html


def _render_libs_missing() -> str | None:
    try:
        import pypdfium2  # noqa: F401
        import weasyprint  # noqa: F401
    except (ImportError, OSError) as e:  # OSError: pango system library not installed
        return f"rendering needs WeasyPrint + pango: {e}"
    return None


needs_render = pytest.mark.skipif(
    _render_libs_missing() is not None, reason=_render_libs_missing() or ""
)


def _dirs(tmp_path):
    return {"docs_dir": tmp_path / "docs", "truth_dir": tmp_path / "truth"}


@needs_render
@pytest.mark.parametrize("layout", LAYOUTS)
def test_each_layout_renders_pdf_png_and_valid_truth(tmp_path, layout):
    docs = generate(layout=layout, n=2, seed=7, **_dirs(tmp_path))
    for d in docs:
        assert d.pdf_path == tmp_path / "docs" / f"{d.doc_id}.pdf"
        assert d.png_path == tmp_path / "docs" / f"{d.doc_id}.png"
        assert d.pdf_path.stat().st_size > 0 and d.png_path.stat().st_size > 0
        truth = Invoice.model_validate_json((tmp_path / "truth" / f"{d.doc_id}.json").read_text())
        assert truth == d.truth


@needs_render
def test_long_invoice_spans_pages(tmp_path):
    import pypdfium2 as pdfium

    [d] = generate(layout="classic", n=1, seed=3, line_items=(40, 40), **_dirs(tmp_path))
    pdf = pdfium.PdfDocument(d.pdf_path)
    try:
        assert len(pdf) >= 2
    finally:
        pdf.close()


def test_truth_only_writes_no_documents(tmp_path):
    docs = generate(layout="compact", n=5, seed=1, render=False, **_dirs(tmp_path))
    assert all(d.pdf_path is None and d.png_path is None for d in docs)
    assert not (tmp_path / "docs").exists()
    assert sorted(p.stem for p in (tmp_path / "truth").iterdir()) == sorted(d.doc_id for d in docs)


def test_doc_ids_are_opaque_and_unique(tmp_path):
    ids = [
        d.doc_id
        for layout in LAYOUTS
        for d in generate(layout=layout, n=50, seed=1, render=False, **_dirs(tmp_path))
    ]
    assert all(re.fullmatch(r"[0-9a-f]{12}", i) for i in ids)  # contracts.DocId (D17)
    assert len(set(ids)) == len(ids)  # same seed, different layout: no collisions


def test_same_seed_gives_same_documents(tmp_path):
    a = generate(layout="modern", n=3, seed=42, render=False, **_dirs(tmp_path / "a"))
    b = generate(layout="modern", n=3, seed=42, render=False, **_dirs(tmp_path / "b"))
    c = generate(layout="modern", n=3, seed=43, render=False, **_dirs(tmp_path / "c"))
    assert [(d.doc_id, d.truth) for d in a] == [(d.doc_id, d.truth) for d in b]
    assert {d.doc_id for d in a}.isdisjoint(d.doc_id for d in c)


@pytest.mark.parametrize("currency", ["USD", "EUR", "GBP"])
def test_totals_add_up(currency):
    for seed in range(50):
        t = make_document("x", Settings(currency=currency, max_items=25), seed).truth
        check_answer_key(t)
        Invoice.model_validate(t.model_dump())
        assert t.currency == currency


def test_no_tax_leaves_tax_null():
    t = make_document("x", Settings(with_tax=False), seed=1).truth
    assert t.tax is None and t.total == t.subtotal


def test_item_count_range_is_respected():
    settings = Settings(min_items=15, max_items=25)
    counts = {len(make_document("x", settings, s).truth.line_items) for s in range(30)}
    assert min(counts) >= 15 and max(counts) <= 25


def test_bad_answer_key_is_rejected():
    t = make_document("x", Settings(), seed=1).truth
    with pytest.raises(ValueError):
        check_answer_key(t.model_copy(update={"total": t.total + 1}))


def test_rendered_page_shows_the_answers():
    doc = make_document("x", Settings(layout="modern", currency="EUR"), seed=5)
    html = to_html(doc)
    t = doc.truth
    assert t.invoice_number in html
    assert format_money(t.total, "EUR") in html
    assert format_date(t.invoice_date, "modern", "EUR") in html
    assert all(i.description in html for i in t.line_items)


def test_money_formats():
    assert format_money(1234.5, "USD") == "$1,234.50"
    assert format_money(1234.5, "EUR") == "1.234,50 €"
    assert format_money(1234.5, "GBP") == "£1,234.50"
