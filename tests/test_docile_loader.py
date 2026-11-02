"""DocILE loader (AGE-26). Fixtures are tiny hand-written annotations in DocILE's format,
not real DocILE data. Tests that need the real download skip when data/docile/ is absent."""

import json
from pathlib import Path

import pytest

from data import docile_loader as dl
from data.docile_loader import (
    Rejected,
    export_docs,
    group_line_items,
    parse_currency,
    parse_date,
    parse_number,
    select_for_job,
    to_invoice,
)


def _f(fieldtype: str, text: str, top: float, left: float = 0.1, li: int | None = None) -> dict:
    field = {
        "fieldtype": fieldtype,
        "text": text,
        "page": 0,
        "bbox": [left, top, left + 0.1, top + 0.01],
    }
    if li is not None:
        field["line_item_id"] = li
    return field


def _line(li: int, top: float, desc: str, qty: str, price: str, amount: str) -> list[dict]:
    return [
        _f("line_item_description", desc, top, 0.1, li),
        _f("line_item_quantity", qty, top, 0.5, li),
        _f("line_item_unit_price_net", price, top, 0.6, li),
        _f("line_item_amount_net", amount, top, 0.8, li),
    ]


def _doc(lines: list[list[dict]], **meta) -> dict:
    return {
        "metadata": {"document_type": "tax_invoice", "page_count": 1, "cluster_id": 7, **meta},
        "field_extractions": [
            _f("vendor_name", "Acme Widgets GmbH", 0.05),
            _f("document_id", "INV-0042", 0.08),
            _f("date_issue", "14.03.2019", 0.10),
            _f("currency_code_amount_due", "€", 0.90),
            _f("amount_total_net", "1.250,00", 0.85),
            _f("amount_total_tax", "237,50", 0.87),
            _f("amount_total_gross", "1.487,50", 0.90),
        ],
        "line_item_extractions": [f for line in lines for f in line],
        "line_item_headers": [],
    }


# Line 2 sits above line 1 on the page, so grouping must order by position, not id.
GOOD = _doc(
    [
        _line(2, 0.40, "Gear, steel", "2", "125,00", "250,00"),
        _line(1, 0.30, "Widget", "10", "100,00", "1.000,00"),
    ]
)
INCONSISTENT = _doc(  # 3 x 100 != 250, and the lines don't sum to the totals
    [
        _line(1, 0.30, "Widget", "3", "100,00", "250,00"),
        _line(2, 0.40, "Gear", "1", "1.000,00", "1.000,00"),
    ]
)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1,234.56", 1234.56),
        ("1.234,56", 1234.56),
        ("1 234,56", 1234.56),
        ("$1,234.56", 1234.56),
        ("12,5", 12.5),
        ("1,000", 1000.0),
        ("1.234.567", 1234567.0),
        ("EUR 99.90", 99.9),
        ("(5.00)", -5.0),
        ("-3", -3.0),
    ],
)
def test_parse_number(text: str, value: float) -> None:
    assert parse_number(text) == pytest.approx(value)


def test_parse_number_rejects_text() -> None:
    with pytest.raises(ValueError):
        parse_number("n/a")


@pytest.mark.parametrize(
    ("text", "iso"),
    [("14.03.2019", "2019-03-14"), ("March 3, 2019", "2019-03-03"), ("2019-03-04", "2019-03-04")],
)
def test_parse_date(text: str, iso: str) -> None:
    assert parse_date(text) == iso


@pytest.mark.parametrize("text", ["03/04/2019", "March 2019", "soon"])
def test_parse_date_rejects_ambiguous_incomplete_and_garbage(text: str) -> None:
    with pytest.raises(ValueError):
        parse_date(text)


@pytest.mark.parametrize(
    ("text", "code"), [("$", "USD"), ("€", "EUR"), ("£", "GBP"), ("chf", "CHF"), ("USD", "USD")]
)
def test_parse_currency(text: str, code: str) -> None:
    assert parse_currency(text) == code


def test_parse_currency_does_not_guess() -> None:
    with pytest.raises(ValueError):
        parse_currency("Kč")


def test_group_line_items_orders_by_position() -> None:
    groups = group_line_items(GOOD["line_item_extractions"])
    assert list(groups) == [1, 2]
    assert all(len(fields) == 4 for fields in groups.values())


def test_to_invoice_maps_every_field() -> None:
    inv = to_invoice(GOOD)
    assert inv.vendor_name == "Acme Widgets GmbH"
    assert inv.invoice_number == "INV-0042"
    assert inv.invoice_date == "2019-03-14"
    assert inv.currency == "EUR"
    assert (inv.subtotal, inv.tax, inv.total) == (1250.0, 237.5, 1487.5)
    assert [(i.description, i.quantity, i.unit_price, i.amount) for i in inv.line_items] == [
        ("Widget", 10, 100, 1000),
        ("Gear, steel", 2, 125, 250),
    ]


def test_filter_rejects_inconsistent_doc() -> None:
    with pytest.raises(Rejected, match="quantity x unit_price"):
        to_invoice(INCONSISTENT)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"metadata": {"document_type": "order", "page_count": 1}}, "document type"),
        ({"metadata": {"document_type": "tax_invoice", "page_count": 3}}, "pages"),
        ({"line_item_extractions": []}, "no line items"),
    ],
)
def test_filter_rejects_by_metadata_and_shape(change: dict, reason: str) -> None:
    with pytest.raises(Rejected, match=reason):
        to_invoice({**GOOD, **change})


def test_filter_rejects_missing_currency() -> None:
    doc = {
        **GOOD,
        "field_extractions": [
            f for f in GOOD["field_extractions"] if f["fieldtype"] != "currency_code_amount_due"
        ],
    }
    with pytest.raises(Rejected, match="currency"):
        to_invoice(doc)


def test_filter_rejects_line_item_missing_a_field() -> None:
    lines = [f for f in GOOD["line_item_extractions"] if f["fieldtype"] != "line_item_quantity"]
    with pytest.raises(Rejected, match="missing"):
        to_invoice({**GOOD, "line_item_extractions": lines})


def test_select_for_job_is_deterministic(tmp_path: Path) -> None:
    rows = [
        {"docid": f"d{i:03d}", "n_line_items": i % 20 + 1, "currency": "USD", "n_pages": 1}
        for i in range(200)
    ]
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(rows[::-1]))  # input order must not matter
    a = select_for_job(1, 8, seed=3, candidates_path=path)
    assert a == select_for_job(1, 8, seed=3, candidates_path=path)
    assert a != select_for_job(1, 8, seed=4, candidates_path=path)
    assert len(a) == 15 and len(set(a)) == 15
    counts = {f"d{i:03d}": i % 20 + 1 for i in range(200)}
    assert all(1 <= counts[d] <= 8 for d in a)
    with pytest.raises(ValueError, match="need 15"):
        select_for_job(20, 30, seed=3, candidates_path=path)


def test_export_docs_writes_opaque_ids_and_reuses_them(tmp_path: Path) -> None:
    root = tmp_path / "docile"
    (root / "annotations").mkdir(parents=True)
    (root / "pdfs").mkdir()
    (root / "annotations" / "abc123.json").write_text(json.dumps(GOOD))
    (root / "pdfs" / "abc123.pdf").write_bytes(b"%PDF-1.4 fixture")
    docs, truth, map_path = tmp_path / "docs", tmp_path / "truth", tmp_path / "map.json"

    [(doc_id, inv)] = export_docs(["abc123"], docs, truth, root=root, map_path=map_path)
    assert "abc123" not in doc_id and len(doc_id) == 12
    assert (docs / f"{doc_id}.pdf").read_bytes() == b"%PDF-1.4 fixture"
    assert dl.Invoice.model_validate_json((truth / f"{doc_id}.json").read_text()) == inv
    assert json.loads(map_path.read_text()) == {doc_id: "abc123"}

    [(again, _)] = export_docs(["abc123"], docs, truth, root=root, map_path=map_path)
    assert again == doc_id


REAL = dl.DOCILE_DIR / "annotations"


@pytest.mark.skipif(not REAL.is_dir(), reason="needs the DocILE download in data/docile/")
def test_committed_candidates_match_the_download() -> None:
    rows, _ = dl.find_candidates()
    assert rows == json.loads(dl.CANDIDATES_PATH.read_text())
