"""Builds synthetic invoice data. Every number is computed here, so the answer key
is correct by construction; the templates only display it."""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from faker import Faker

from contracts import Invoice, LineItem

LAYOUTS = ("classic", "modern", "compact")
CURRENCIES = {"USD": "$", "EUR": "€", "GBP": "£"}
FAKER_LOCALE = {"USD": "en_US", "EUR": "de_DE", "GBP": "en_GB"}
TAX_RATES = {
    "USD": (Decimal("0.0625"), Decimal("0.08"), Decimal("0.0875")),
    "EUR": (Decimal("0.19"), Decimal("0.07")),
    "GBP": (Decimal("0.20"),),
}

CATALOG = [
    ("Printer paper A4, 500 sheets", 3, 9),
    ("Toner cartridge, black", 45, 120),
    ("Ballpoint pens, box of 50", 6, 18),
    ("Ergonomic office chair", 140, 420),
    ("27-inch monitor", 160, 380),
    ("USB-C docking station", 90, 240),
    ("Wireless keyboard and mouse set", 25, 80),
    ("Laptop sleeve, 14-inch", 15, 40),
    ("Network switch, 24-port", 120, 450),
    ("Cat6 patch cable, 3 m", 2, 9),
    ("Cloud storage, 1 TB monthly", 8, 25),
    ("Software licence, annual seat", 60, 600),
    ("IT support, hourly", 65, 160),
    ("Consulting services, hourly", 90, 300),
    ("Data migration, fixed fee", 400, 2500),
    ("Cleaning supplies kit", 20, 60),
    ("Coffee beans, 1 kg", 12, 30),
    ("Shipping and handling", 8, 45),
    ("Whiteboard markers, set of 12", 6, 15),
    ("Desk lamp, LED", 18, 55),
    ("Filing cabinet, 3-drawer", 110, 260),
    ("Safety gloves, pair", 2, 8),
    ("Courier delivery, same day", 15, 60),
    ("Training workshop, per attendee", 150, 600),
]


@dataclass(frozen=True)
class Settings:
    layout: str = "classic"
    currency: str = "USD"
    min_items: int = 1
    max_items: int = 8
    with_tax: bool = True
    scanned: bool = False  # image-only PDF, like a paper invoice run through a scanner

    def __post_init__(self):
        if self.layout not in LAYOUTS:
            raise ValueError(f"unknown layout {self.layout!r}; choose from {LAYOUTS}")
        if self.currency not in CURRENCIES:
            raise ValueError(f"unknown currency {self.currency!r}; choose from {tuple(CURRENCIES)}")
        if not 1 <= self.min_items <= self.max_items:
            raise ValueError("need 1 <= min_items <= max_items")


@dataclass
class Document:
    """One synthetic invoice: the answer key plus the extra text the templates show."""

    doc_id: str
    settings: Settings
    truth: Invoice
    vendor_address: list[str]
    vendor_contact: str
    buyer_name: str
    buyer_address: list[str]
    due_date: date
    po_number: str
    tax_rate: Decimal
    extras: dict = field(default_factory=dict)


def _money(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _d(x: float) -> Decimal:
    return Decimal(str(x))


def check_answer_key(inv: Invoice) -> None:
    """Raises ValueError unless the numbers add up: each amount = quantity x unit price,
    subtotal = sum of amounts, total = subtotal + tax (all to 2 dp)."""
    for i in inv.line_items:
        if _money(_d(i.quantity) * _d(i.unit_price)) != _d(i.amount):
            raise ValueError(f"line item {i.description!r}: quantity x unit price != amount")
    items = sum((_d(i.amount) for i in inv.line_items), Decimal("0"))
    if inv.subtotal is not None and items != _d(inv.subtotal):
        raise ValueError(f"line items sum to {items}, subtotal is {inv.subtotal}")
    base = _d(inv.subtotal) if inv.subtotal is not None else items
    tax = _d(inv.tax) if inv.tax is not None else Decimal("0")
    if base + tax != _d(inv.total):
        raise ValueError(f"subtotal + tax = {base + tax}, total is {inv.total}")


def make_document(doc_id: str, settings: Settings, seed: int) -> Document:
    rng = random.Random(seed)
    fake = Faker(FAKER_LOCALE[settings.currency])
    fake.seed_instance(seed)

    vendor = fake.company()
    buyer = fake.company()
    while buyer == vendor:
        buyer = fake.company()

    n = rng.randint(settings.min_items, settings.max_items)
    picks = rng.sample(CATALOG, k=min(n, len(CATALOG)))
    while len(picks) < n:  # long tables reuse products with a variant suffix
        name, lo, hi = rng.choice(CATALOG)
        picks.append((f"{name} (batch {len(picks) + 1})", lo, hi))

    items = []
    for name, lo, hi in picks:
        hourly = "hourly" in name
        qty = (
            Decimal(rng.choice([1, 1.5, 2, 4, 6, 7.5, 8]))
            if hourly
            else Decimal(rng.randint(1, 25))
        )
        price = _money(Decimal(str(rng.uniform(lo, hi))))
        items.append(
            LineItem(
                description=name,
                quantity=float(qty),
                unit_price=float(price),
                amount=float(_money(qty * price)),
            )
        )

    subtotal = sum((Decimal(str(i.amount)) for i in items), Decimal("0"))
    rate = rng.choice(TAX_RATES[settings.currency]) if settings.with_tax else Decimal("0")
    tax = _money(subtotal * rate)

    issued = date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))
    truth = Invoice(
        vendor_name=vendor,
        invoice_number=f"{rng.choice(['INV', 'IN', 'BILL', 'F'])}-{rng.randint(1000, 99999)}",
        invoice_date=issued.isoformat(),
        currency=settings.currency,
        line_items=items,
        subtotal=float(subtotal),
        tax=float(tax) if settings.with_tax else None,
        total=float(subtotal + tax),
    )
    check_answer_key(truth)
    return Document(
        doc_id=doc_id,
        settings=settings,
        truth=truth,
        vendor_address=fake.address().splitlines(),
        vendor_contact=fake.company_email(),
        buyer_name=buyer,
        buyer_address=fake.address().splitlines(),
        due_date=issued + timedelta(days=rng.choice([14, 30, 45, 60])),
        po_number=f"PO-{rng.randint(10000, 99999)}",
        tax_rate=rate,
    )
