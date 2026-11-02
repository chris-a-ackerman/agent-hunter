"""Real invoices from DocILE (AGE-26), mapped to our Invoice schema (plan §6, D17).

DocILE (https://github.com/rossumai/docile) is research-use only: its PDFs, annotations and OCR
live in the gitignored `data/docile/` and never go into git or the HW7 evidence repo. Only
`data/pools/docile_candidates.json` and `data/pools/docile_map.json` (IDs and counts) are
committed.

    uv run python -m data.docile_loader candidates   # writes docile_candidates.json + stats

`to_invoice` maps one annotation file to an Invoice and raises `Rejected` unless the answer key
is trustworthy (every field parsed, line items consistent with the totals, <= 2 pages, an
invoice). Documents for a job are chosen only by `select_for_job` (a fixed, seeded rule) and
written out by `export_docs` under fresh opaque IDs.

The annotation JSON is read directly (no dependency on the `docile-benchmark` library), with
the field-type names from `docile.dataset.KILE_FIELDTYPES` / `LIR_FIELDTYPES`.
"""

import argparse
import json
import random
import re
import shutil
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from dateutil import parser as dateparser

from contracts import Invoice, LineItem, new_doc_id

DOCILE_DIR = Path("data/docile")
CANDIDATES_PATH = Path("data/pools/docile_candidates.json")
MAP_PATH = Path("data/pools/docile_map.json")

MAX_PAGES = 2
LINE_TOL = 0.02  # quantity x unit_price vs amount, per line
SUM_TOL = 0.05  # sum of line amounts vs subtotal or total

_CURRENCY_SYMBOLS = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP"}


class Rejected(Exception):
    """The document's answer key can't be trusted; the message says why (no DocILE content)."""


# --- parsing --------------------------------------------------------------------------------


def parse_number(text: str) -> float:
    """Parses '1,234.56', '1.234,56', '1 234,56', '$12.00', '(5.00)' and '-5' style numbers."""
    s = text.strip()
    negative = s.startswith("(") and s.endswith(")") or "-" in s or s.endswith("CR")
    s = re.sub(r"[^\d.,]", "", s.replace(" ", " ").replace(" ", "").replace("'", ""))
    if not re.search(r"\d", s):
        raise ValueError(f"not a number: {text!r}")
    if "," in s and "." in s:
        # the right-most separator is the decimal one
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        if s.count(",") > 1 or len(tail) == 3 and head:
            s = s.replace(",", "")  # 1,234 or 1,234,567: thousands
        else:
            s = f"{head}.{tail}"  # 12,5 or 1234,56: decimal comma
    elif s.count(".") > 1:
        s = s.replace(".", "")  # 1.234.567: thousands
    value = float(s)
    return -value if negative else value


def parse_date(text: str) -> str:
    """YYYY-MM-DD. Raises ValueError if unparseable, incomplete, or day/month ambiguous."""
    try:
        a = dateparser.parse(text, default=datetime(2000, 1, 1), dayfirst=False)
        b = dateparser.parse(text, default=datetime(2001, 2, 2), dayfirst=False)
        c = dateparser.parse(text, default=datetime(2000, 1, 1), dayfirst=True)
    except (ValueError, OverflowError) as e:
        raise ValueError(f"unparseable date: {text!r}") from e
    if a.date() != b.date():  # a part came from the default, so it's not on the document
        raise ValueError(f"incomplete date: {text!r}")
    year_first = re.match(r"\s*\d{4}\D", text)  # 2019-03-04 is Y-M-D, never Y-D-M
    if a != c and not year_first:
        raise ValueError(f"ambiguous day/month: {text!r}")
    return a.strftime("%Y-%m-%d")


def parse_currency(text: str) -> str:
    """ISO 4217 code: '$' -> USD, '€' -> EUR, '£' -> GBP, 3-letter codes as-is. No guessing."""
    s = text.strip()
    if s in _CURRENCY_SYMBOLS:
        return _CURRENCY_SYMBOLS[s]
    if re.fullmatch(r"[A-Za-z]{3}", s):
        return s.upper()
    raise ValueError(f"unknown currency: {text!r}")


# --- annotation access ------------------------------------------------------------------------


def _reading_order(field: Mapping[str, Any]) -> tuple[int, float, float]:
    bbox = field.get("bbox") or (0, 0, 0, 0)
    return (field.get("page", 0), bbox[1], bbox[0])


def _texts(fields: Iterable[Mapping[str, Any]], fieldtype: str) -> list[str]:
    hits = sorted((f for f in fields if f.get("fieldtype") == fieldtype), key=_reading_order)
    texts = (" ".join((f.get("text") or "").split()) for f in hits)  # newlines -> spaces
    return [t for t in texts if t]


def _one_number(fields: Iterable[Mapping[str, Any]], fieldtype: str) -> float | None:
    """The field's value, None if absent. Raises Rejected if it's unparseable or ambiguous."""
    texts = _texts(fields, fieldtype)
    if not texts:
        return None
    try:
        values = {round(parse_number(t), 2) for t in texts}
    except ValueError as e:
        raise Rejected(f"{fieldtype}: unparseable number") from e
    if len(values) > 1:
        raise Rejected(f"{fieldtype}: conflicting values")
    return values.pop()


def group_line_items(li_fields: Iterable[Mapping[str, Any]]) -> dict[int, list[Mapping[str, Any]]]:
    """Line-item fields grouped by line_item_id, in table order (first field's position)."""
    groups: dict[int, list[Mapping[str, Any]]] = {}
    for f in li_fields:
        if f.get("line_item_id") is not None:
            groups.setdefault(f["line_item_id"], []).append(f)
    return dict(sorted(groups.items(), key=lambda kv: min(_reading_order(f) for f in kv[1])))


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol + 1e-9


def _line_items(groups: Mapping[int, list[Mapping[str, Any]]], kind: str) -> list[LineItem]:
    """Line items using the `kind` ('net' or 'gross') amount for every line. Raises Rejected."""
    other = "gross" if kind == "net" else "net"
    items = []
    for fields in groups.values():
        description = " ".join(_texts(fields, "line_item_description"))
        quantity = _one_number(fields, "line_item_quantity")
        unit_price = _one_number(fields, f"line_item_unit_price_{kind}")
        if unit_price is None:
            unit_price = _one_number(fields, f"line_item_unit_price_{other}")
        amount = _one_number(fields, f"line_item_amount_{kind}")
        if not description or quantity is None or unit_price is None or amount is None:
            raise Rejected(f"line item missing a field ({kind})")
        if not _close(quantity * unit_price, amount, LINE_TOL):
            raise Rejected(f"line item quantity x unit_price != amount ({kind})")
        items.append(
            LineItem(
                description=description, quantity=quantity, unit_price=unit_price, amount=amount
            )
        )
    return items


def to_invoice(annotation: Mapping[str, Any]) -> Invoice:
    """Maps one DocILE annotation (the parsed annotations/{docid}.json) to an Invoice.

    Raises Rejected unless every check in the module docstring passes.
    """
    meta = annotation.get("metadata", {})
    doc_type = meta.get("document_type")
    if doc_type is not None and doc_type != "tax_invoice":
        raise Rejected(f"document type is {doc_type}")
    if meta.get("page_count", 1) > MAX_PAGES:
        raise Rejected(f"more than {MAX_PAGES} pages")

    fields = annotation.get("field_extractions", [])
    vendors = _texts(fields, "vendor_name")
    if not vendors:
        raise Rejected("vendor_name missing")
    numbers = _texts(fields, "document_id")
    dates = _texts(fields, "date_issue")
    if not dates:
        raise Rejected("date_issue missing")
    try:
        invoice_date = parse_date(dates[0])
    except ValueError as e:
        raise Rejected(f"date_issue: {str(e).split(':')[0]}") from e
    currencies = _texts(fields, "currency_code_amount_due")
    if not currencies:
        raise Rejected("currency missing")
    try:
        currency = parse_currency(currencies[0])
    except ValueError as e:
        raise Rejected("currency unknown") from e

    total = _one_number(fields, "amount_total_gross")
    if total is None:
        total = _one_number(fields, "amount_due")
    if total is None:
        raise Rejected("total missing")
    subtotal = _one_number(fields, "amount_total_net")
    tax = _one_number(fields, "amount_total_tax")

    groups = group_line_items(annotation.get("line_item_extractions", []))
    if not groups:
        raise Rejected("no line items")
    targets = [t for t in (subtotal, total) if t is not None]
    reasons = []
    for kind in ("net", "gross"):
        try:
            items = _line_items(groups, kind)
        except Rejected as e:
            reasons.append(str(e))
            continue
        line_sum = sum(i.amount for i in items)
        if any(_close(line_sum, t, SUM_TOL) for t in targets):
            break
        reasons.append(f"line amounts don't sum to subtotal or total ({kind})")
    else:
        raise Rejected(reasons[0] if len(set(reasons)) == 1 else "; ".join(reasons))

    invoice = {
        "vendor_name": vendors[0],
        "invoice_number": numbers[0] if numbers else None,
        "invoice_date": invoice_date,
        "currency": currency,
        "line_items": [i.model_dump() for i in items],
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
    }
    return Invoice.model_validate(invoice)


# --- dataset ----------------------------------------------------------------------------------


def load_annotation(docid: str, root: Path = DOCILE_DIR) -> dict[str, Any]:
    return json.loads((Path(root) / "annotations" / f"{docid}.json").read_text())


def all_docids(root: Path = DOCILE_DIR) -> list[str]:
    return sorted(p.stem for p in (Path(root) / "annotations").glob("*.json"))


def find_candidates(root: Path = DOCILE_DIR) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Every qualifying doc as {docid, n_line_items, currency, n_pages}, plus rejection counts."""
    rows, rejected = [], Counter[str]()
    for docid in all_docids(root):
        annotation = load_annotation(docid, root)
        try:
            invoice = to_invoice(annotation)
        except Rejected as e:
            rejected[str(e)] += 1
            continue
        rows.append(
            {
                "docid": docid,
                "n_line_items": len(invoice.line_items),
                "currency": invoice.currency,
                "n_pages": annotation["metadata"].get("page_count", 1),
            }
        )
    return rows, rejected


def select_for_job(
    lo: int, hi: int, seed: int, n: int = 15, *, candidates_path: Path = CANDIDATES_PATH
) -> list[str]:
    """The only way DocILE docs are chosen for a job: candidates with lo <= n_line_items <= hi,
    sorted by docid, shuffled with random.Random(seed), first n (3 samples + 12 held-out)."""
    rows = json.loads(Path(candidates_path).read_text())
    pool = sorted(r["docid"] for r in rows if lo <= r["n_line_items"] <= hi)
    if len(pool) < n:
        raise ValueError(f"only {len(pool)} candidates with {lo}-{hi} line items, need {n}")
    random.Random(seed).shuffle(pool)
    return pool[:n]


def export_docs(
    docids: Iterable[str],
    docs_dir: Path = Path("data/docs"),
    truth_dir: Path = Path("data/truth"),
    *,
    root: Path = DOCILE_DIR,
    map_path: Path = MAP_PATH,
) -> list[tuple[str, Invoice]]:
    """Copies each PDF to docs_dir/{opaque id}.pdf and writes truth_dir/{id}.json (D17).

    The opaque id -> DocILE docid mapping is recorded in map_path. A docid that's already mapped
    keeps its id, so reruns rewrite the same files instead of minting new ones.
    """
    map_path = Path(map_path)
    mapping: dict[str, str] = json.loads(map_path.read_text()) if map_path.is_file() else {}
    by_docid = {v: k for k, v in mapping.items()}
    Path(docs_dir).mkdir(parents=True, exist_ok=True)
    Path(truth_dir).mkdir(parents=True, exist_ok=True)
    out = []
    for docid in docids:
        invoice = to_invoice(load_annotation(docid, root))  # Rejected if not a candidate
        doc_id = by_docid.get(docid) or new_doc_id()
        shutil.copyfile(Path(root) / "pdfs" / f"{docid}.pdf", Path(docs_dir) / f"{doc_id}.pdf")
        (Path(truth_dir) / f"{doc_id}.json").write_text(invoice.model_dump_json(indent=2))
        mapping[doc_id] = by_docid[docid] = docid
        out.append((doc_id, invoice))
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(dict(sorted(mapping.items())), indent=2) + "\n")
    return out


# --- CLI --------------------------------------------------------------------------------------


def _histogram(values: Iterable[Any]) -> str:
    return ", ".join(f"{k}: {v}" for k, v in sorted(Counter(values).items()))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m data.docile_loader")
    p.add_argument("command", choices=["candidates"])
    p.add_argument("--root", type=Path, default=DOCILE_DIR)
    p.add_argument("--out", type=Path, default=CANDIDATES_PATH)
    a = p.parse_args(argv)

    rows, rejected = find_candidates(a.root)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rows, indent=1) + "\n")
    total = len(rows) + sum(rejected.values())
    print(f"{len(rows)} of {total} docs qualify -> {a.out}")
    print("line items:", _histogram(r["n_line_items"] for r in rows))
    print("currencies:", _histogram(r["currency"] for r in rows))
    print("pages:", _histogram(r["n_pages"] for r in rows))
    print("1-8 items:", sum(1 <= r["n_line_items"] <= 8 for r in rows))
    print("15+ items:", sum(r["n_line_items"] >= 15 for r in rows))
    print("rejections:")
    for reason, count in rejected.most_common():
        print(f"  {count:5d}  {reason}")


if __name__ == "__main__":
    main()
