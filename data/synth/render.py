"""Renders a Document to HTML, PDF and a PNG of page 1.

WeasyPrint needs the pango system library, so it is imported only when rendering:
truth-only generation (render=False) works without it.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .generator import CURRENCIES, Document

TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), undefined=StrictUndefined, autoescape=True)


def format_money(value: float | None, currency: str) -> str:
    if value is None:
        return ""
    s = f"{Decimal(str(value)):,.2f}"
    if currency == "EUR":  # German style: 1.234,56 €
        s = s.replace(",", "_").replace(".", ",").replace("_", ".")
        return f"{s} €"
    return f"{CURRENCIES[currency]}{s}"


def format_qty(value: float) -> str:
    return f"{value:g}"


def format_date(d: date | str, layout: str, currency: str) -> str:
    if isinstance(d, str):
        d = date.fromisoformat(d)
    if layout == "classic":
        return d.strftime("%B %-d, %Y")  # October 14, 2026
    if layout == "modern":
        return d.strftime("%-d %b %Y")  # 14 Oct 2026
    return d.strftime("%d.%m.%Y" if currency == "EUR" else "%m/%d/%Y")  # 14.10.2026 or 10/14/2026


def to_html(doc: Document) -> str:
    cur, layout = doc.settings.currency, doc.settings.layout
    return _env.get_template(f"{layout}.html.j2").render(
        doc=doc,
        t=doc.truth,
        money=lambda v: format_money(v, cur),
        qty=format_qty,
        when=lambda d: format_date(d, layout, cur),
        tax_pct=f"{(doc.tax_rate * 100).normalize():f}%",
        tax_label="Sales tax" if cur == "USD" else "VAT",
    )


def render(
    doc: Document, docs_dir: Path, png: bool = True, dpi: int = 150
) -> tuple[Path, Path | None]:
    """Writes {doc_id}.pdf and, if png, {doc_id}.png (page 1). Returns both paths."""
    import pypdfium2 as pdfium
    from weasyprint import HTML

    docs_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = docs_dir / f"{doc.doc_id}.pdf"
    HTML(string=to_html(doc), base_url=str(TEMPLATES)).write_pdf(pdf_path)
    if not png:
        return pdf_path, None

    png_path = docs_dir / f"{doc.doc_id}.png"
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        pdf[0].render(scale=dpi / 72).to_pil().save(png_path)
    finally:
        pdf.close()
    return pdf_path, png_path
