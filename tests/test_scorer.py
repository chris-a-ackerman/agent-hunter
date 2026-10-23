import pytest

from contracts import Invoice, LineItem, ScoreReport, ScoringConfig, Submission, Tolerances
from contracts.rubric import SCORED_FIELDS
from core.eval.defaults import DEFAULT_WEIGHTS
from core.eval.scorer import (
    match_currency,
    match_date,
    match_invoice_number,
    match_line_items,
    match_total,
    match_vendor,
    normalize_vendor,
    score,
    sum_check,
)
from data.synth import LAYOUTS, generate
from mocks.fake_providers import MOCKS, mock_output

TOL = Tolerances()
DOC = "aaaaaaaaaaaa"


def item(desc, qty=1.0, price=10.0, amount=None):
    return LineItem(
        description=desc,
        quantity=qty,
        unit_price=price,
        amount=qty * price if amount is None else amount,
    )


TRUTH = Invoice(
    vendor_name="Acme Supplies, Inc.",
    invoice_number="INV-1042",
    invoice_date="2026-04-17",
    currency="USD",
    line_items=[item("Printer paper A4", 2, 5.0), item("Toner cartridge, black", 1, 80.0)],
    subtotal=90.0,
    tax=9.0,
    total=99.0,
)


def config(**kw) -> ScoringConfig:
    return ScoringConfig(
        trial_id=kw.pop("trial_id", "trial-1"),
        weights=kw.pop("weights", DEFAULT_WEIGHTS),
        tolerances=TOL,
        answer_keys=kw.pop("answer_keys", {DOC: TRUTH}),
        consistency_checks=kw.pop("consistency_checks", ["line_items_sum"]),
    )


def sub(output, status="ok", provider="acme@1", doc_id=DOC) -> Submission:
    return Submission(
        task_id=f"t-{provider}-{doc_id}",
        doc_id=doc_id,
        provider=provider,
        status=status,
        output=output if status == "ok" else None,
        latency_ms=None if status == "not_run" else 100,
        cost_usd=None,
    )


# --- field matchers --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pred", "expected"), [(99.0, 1), (99.01, 1), (98.99, 1), (99.02, 0), (0.0, 0)]
)
def test_total(pred, expected):
    assert match_total(pred, 99.0, TOL) == expected


@pytest.mark.parametrize(
    ("pred", "expected"),
    [
        ("Acme Supplies, Inc.", 1),
        ("ACME SUPPLIES INC", 1),
        ("Acme Supplies", 1),  # legal-form suffix dropped
        ("Acme  Supplies LLC.", 1),
        ("Acme Supplie", 1),  # one typo is above 0.9
        ("Acme Office Supplies", 0),
        ("Supplies Acme", 0),
        ("", 0),
    ],
)
def test_vendor(pred, expected):
    assert match_vendor(pred, "Acme Supplies, Inc.", TOL) == expected


@pytest.mark.parametrize(
    ("name", "normal"),
    [
        ("Bauer Stoll GbR", "bauer stoll"),
        ("Wernecke AG & Co. KG", "wernecke"),
        ("Lewis PLC", "lewis"),
        ("Co.", "co"),  # a name that is only a suffix is kept
        ("Müller-Lüdenscheidt GmbH", "müller lüdenscheidt"),
    ],
)
def test_normalize_vendor(name, normal):
    assert normalize_vendor(name) == normal


@pytest.mark.parametrize(
    ("pred", "expected"),
    [
        ("2026-04-17", 1),
        ("17 Apr 2026", 1),
        ("April 17, 2026", 1),
        ("2026/04/17", 1),
        ("04/17/2026", 1),
        ("2026-04-18", 0),
        ("not a date", 0),
        ("", 0),
    ],
)
def test_date(pred, expected):
    assert match_date(pred, "2026-04-17") == expected


@pytest.mark.parametrize(
    ("pred", "true", "expected"),
    [
        ("INV-1042", "INV-1042", 1),
        ("inv 1042", "INV-1042", 1),
        ("#INV/1042", "INV-1042", 1),
        ("INV-1043", "INV-1042", 0),
        (None, None, 1),
        (None, "INV-1042", 0),
        ("INV-1042", None, 0),
    ],
)
def test_invoice_number(pred, true, expected):
    assert match_invoice_number(pred, true) == expected


@pytest.mark.parametrize(("pred", "expected"), [("USD", 1), ("usd", 1), (" USD ", 1), ("EUR", 0)])
def test_currency(pred, expected):
    assert match_currency(pred, "USD") == expected


class TestLineItems:
    true = [
        item("Printer paper A4", 2, 5.0),
        item("Toner cartridge, black", 1, 80.0),
        item("Desk lamp, LED", 3, 20.0),
    ]

    def test_exact_and_reordered_items_match(self):
        assert match_line_items(self.true, self.true, TOL) == 1.0
        assert match_line_items(self.true[::-1], self.true, TOL) == 1.0

    def test_small_description_differences_still_match(self):
        pred = [
            item("printer paper a4", 2, 5.0),
            item("Black toner cartridge", 1, 80.0),
            item("LED desk lamp", 3, 20.0),
        ]
        assert match_line_items(pred, self.true, TOL) == 1.0

    def test_missing_and_extra_items_lower_f1(self):
        assert match_line_items(self.true[:2], self.true, TOL) == pytest.approx(2 * 2 / 5)
        extra = [*self.true, item("Coffee beans, 1 kg", 1, 12.0)]
        assert match_line_items(extra, self.true, TOL) == pytest.approx(2 * 3 / 7)

    def test_wrong_number_is_not_a_match(self):
        pred = [self.true[0], self.true[1], item("Desk lamp, LED", 3, 20.0, amount=61.0)]
        assert match_line_items(pred, self.true, TOL) == pytest.approx(2 * 2 / 6)
        pred = [self.true[0], self.true[1], item("Desk lamp, LED", 3.5, 20.0, amount=60.0)]
        assert match_line_items(pred, self.true, TOL) == pytest.approx(2 * 2 / 6)

    def test_unrelated_description_is_not_a_match(self):
        pred = [self.true[0], self.true[1], item("Consulting services", 3, 20.0)]
        assert match_line_items(pred, self.true, TOL) == pytest.approx(2 * 2 / 6)

    def test_empty_lists(self):
        assert match_line_items([], [], TOL) == 1.0
        assert match_line_items([], self.true, TOL) == 0.0
        assert match_line_items(self.true, [], TOL) == 0.0


def test_sum_check():
    assert sum_check(TRUTH, TOL)
    assert not sum_check(TRUTH.model_copy(update={"subtotal": 91.0}), TOL)
    # No subtotal: compare against total − tax.
    assert sum_check(TRUTH.model_copy(update={"subtotal": None}), TOL)
    assert not sum_check(TRUTH.model_copy(update={"subtotal": None, "tax": None}), TOL)


# --- documents and reports -------------------------------------------------------------------


def test_perfect_answer_scores_one():
    [r] = score(config(), [sub(TRUTH.model_dump())])
    [d] = r.doc_scores
    assert d.score == 1.0 and d.status == "ok" and d.sum_check_ok is True
    assert d.field_scores == dict.fromkeys(SCORED_FIELDS, 1.0)


def test_weights_come_from_the_config():
    wrong_total = TRUTH.model_copy(update={"total": 120.0}).model_dump()
    [r] = score(config(), [sub(wrong_total)])
    assert r.doc_scores[0].score == pytest.approx(1 - DEFAULT_WEIGHTS["total"])
    heavy = {**dict.fromkeys(SCORED_FIELDS, 0.05), "total": 0.75}
    [r] = score(config(weights=heavy), [sub(wrong_total)])
    assert r.doc_scores[0].score == pytest.approx(0.25)
    assert r.doc_scores[0].field_scores["total"] == 0.0


def test_sum_check_is_a_flag_not_part_of_the_score():
    off = TRUTH.model_copy(update={"subtotal": 95.0}).model_dump()  # subtotal isn't scored
    [r] = score(config(), [sub(off)])
    assert r.doc_scores[0].score == 1.0 and r.doc_scores[0].sum_check_ok is False
    [r] = score(config(consistency_checks=[]), [sub(off)])
    assert r.doc_scores[0].sum_check_ok is None


@pytest.mark.parametrize("status", ["timeout", "error", "not_run"])
def test_unanswered_documents_score_zero_with_their_status(status):
    [r] = score(config(), [sub(None, status=status)])
    [d] = r.doc_scores
    assert (d.score, d.status, d.sum_check_ok) == (0.0, status, None)
    assert d.field_scores == dict.fromkeys(SCORED_FIELDS, 0.0)


@pytest.mark.parametrize(
    "output",
    [
        {"text": "not json"},
        {**TRUTH.model_dump(), "total": "ninety-nine"},  # wrong type
        {k: v for k, v in TRUTH.model_dump().items() if k != "currency"},  # missing key
        {**TRUTH.model_dump(), "notes": "extra"},  # extra key
    ],
)
def test_output_that_isnt_an_invoice_is_invalid(output):
    [r] = score(config(), [sub(output)])
    assert (r.doc_scores[0].score, r.doc_scores[0].status) == (0.0, "invalid")


def test_badly_formatted_field_costs_only_that_field():
    """D24: validation is types-only, so a bad date format loses the date, not the document."""
    out = {**TRUTH.model_dump(), "invoice_date": "sometime in April"}
    [r] = score(config(), [sub(out)])
    assert r.doc_scores[0].status == "ok"
    assert r.doc_scores[0].score == pytest.approx(1 - DEFAULT_WEIGHTS["invoice_date"])


def test_one_report_per_provider_with_mean_and_ci():
    truths = {f"{i:012x}": TRUTH for i in range(4)}
    good, bad = TRUTH.model_dump(), {**TRUTH.model_dump(), "total": 1.0}
    subs = [sub(good if i % 2 else bad, provider="acme@1", doc_id=d) for i, d in enumerate(truths)]
    subs += [sub(good, provider="beta@2", doc_id=d) for d in truths]
    reports = score(config(answer_keys=truths), subs)
    assert [r.provider for r in reports] == ["acme@1", "beta@2"]
    acme, beta = reports
    assert acme.n_docs == 4 and acme.trial_score == pytest.approx(1 - 0.30 / 2)
    assert acme.ci_95[0] <= acme.trial_score <= acme.ci_95[1]
    assert beta.trial_score == 1.0 and beta.ci_95 == (1.0, 1.0)
    for r in reports:
        ScoreReport.model_validate(r.model_dump())


def test_cis_are_reproducible_per_trial():
    truths = {f"{i:012x}": TRUTH for i in range(6)}
    outs = [TRUTH.model_dump(), {**TRUTH.model_dump(), "total": 1.0}] * 3
    subs = [sub(o, doc_id=d) for o, d in zip(outs, truths, strict=True)]
    a = score(config(answer_keys=truths), subs)[0].ci_95
    assert a == score(config(answer_keys=truths), subs)[0].ci_95


def test_unknown_document_is_an_error():
    with pytest.raises(ValueError):
        score(config(), [sub(TRUTH.model_dump(), doc_id="bbbbbbbbbbbb")])


# --- acceptance: measured accuracy on the mocks ----------------------------------------------


def test_measured_accuracy_matches_each_mocks_known_accuracy(tmp_path):
    """AGE-19 acceptance: 300 truth-only answer keys, each mock's corruption, default weights;
    each mock's mean is within ±3 points of its documented p (mocks/README.md)."""
    docs = [
        d
        for layout in LAYOUTS
        for d in generate(layout=layout, n=100, seed=19, render=False, truth_dir=tmp_path)
    ]
    cfg = config(trial_id="trial-acceptance", answer_keys={d.doc_id: d.truth for d in docs})
    subs = [
        sub(mock_output(slug, d.doc_id, d.truth), provider=f"{slug}@1", doc_id=d.doc_id)
        for slug in ("mock-a", "mock-b", "mock-c", "mock-e")
        for d in docs
    ]
    for r in score(cfg, subs):
        p = MOCKS[r.provider.removesuffix("@1")].p
        assert abs(r.trial_score - p) <= 0.03, (r.provider, r.trial_score, p)
    mock_e = next(r for r in score(cfg, subs) if r.provider == "mock-e@1")
    assert {d.status for d in mock_e.doc_scores} == {"invalid"}
