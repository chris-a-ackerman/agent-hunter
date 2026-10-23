"""The Scorer (AGE-19, plan §8): grades every provider output against the answer key.

`score` satisfies `contracts.steps.Score`; `mocks/fake_scorer` (AGE-16) has the same signature.
Each field matcher returns a score in [0, 1]; a document's score is the weighted sum.
"""

import re
from collections.abc import Sequence

import numpy as np
from dateutil import parser as dateparser
from pydantic import ValidationError
from rapidfuzz import fuzz, utils
from scipy.optimize import linear_sum_assignment

from contracts import DocScore, Invoice, LineItem, ScoreReport, ScoringConfig, Submission
from contracts.rubric import SCORED_FIELDS, Tolerances
from core.eval.stats import bootstrap_ci, seed_for

# Float slack so values exactly on a tolerance (e.g. off by 0.01 with tolerance 0.01) match.
_EPS = 1e-9

# Legal-form suffixes dropped from the end of vendor names. The issue lists inc|llc|ltd|gmbh|co|
# corp; the others are forms the synthetic generator produces (plan §6), so a provider that
# writes "Lewis" for "Lewis PLC" isn't penalised.
_VENDOR_SUFFIXES = re.compile(r"(?:\s+(?:inc|llc|ltd|limited|gmbh|co|corp|plc|ag|kg|gbr|ohg))+$")


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol + _EPS


def normalize_vendor(name: str) -> str:
    s = re.sub(r"[^\w\s]", " ", name.lower())
    s = " ".join(s.split())
    stripped = _VENDOR_SUFFIXES.sub("", s).strip()
    return stripped or s


def normalize_invoice_number(number: str) -> str:
    return re.sub(r"[\s#\-/]", "", number.upper())


def match_total(pred: float, true: float, tol: Tolerances) -> float:
    return float(_close(pred, true, tol.total_abs))


def match_vendor(pred: str, true: str, tol: Tolerances) -> float:
    ratio = fuzz.ratio(normalize_vendor(pred), normalize_vendor(true)) / 100
    return float(ratio >= tol.vendor_fuzzy_min)


def match_date(pred: str, true: str) -> float:
    try:
        return float(dateparser.parse(pred).date() == dateparser.parse(true).date())
    except (ValueError, OverflowError, TypeError):
        return 0.0


def match_invoice_number(pred: str | None, true: str | None) -> float:
    if pred is None or true is None:
        return float(pred is None and true is None)
    return float(normalize_invoice_number(pred) == normalize_invoice_number(true))


def match_currency(pred: str, true: str) -> float:
    return float(pred.strip().upper() == true.strip().upper())


def match_line_items(pred: Sequence[LineItem], true: Sequence[LineItem], tol: Tolerances) -> float:
    """F1 over items: Hungarian matching on description similarity, then a matched pair is a
    true positive only if the description is similar enough and all three numbers agree."""
    if not pred and not true:
        return 1.0
    if not pred or not true:
        return 0.0
    sim = np.array(
        [
            [
                fuzz.token_sort_ratio(p.description, t.description, processor=utils.default_process)
                / 100
                for t in true
            ]
            for p in pred
        ]
    )
    rows, cols = linear_sum_assignment(1 - sim)
    tp = sum(
        1
        for i, j in zip(rows, cols, strict=True)
        if sim[i, j] >= tol.line_item_desc_min
        and _close(pred[i].quantity, true[j].quantity, tol.amount_abs)
        and _close(pred[i].unit_price, true[j].unit_price, tol.amount_abs)
        and _close(pred[i].amount, true[j].amount, tol.amount_abs)
    )
    return 2 * tp / (len(pred) + len(true))


def sum_check(pred: Invoice, tol: Tolerances) -> bool:
    """Do the predicted line items add up to the predicted subtotal (or total − tax)?"""
    items = sum(i.amount for i in pred.line_items)
    base = pred.subtotal if pred.subtotal is not None else pred.total - (pred.tax or 0.0)
    return _close(items, base, tol.amount_abs)


def field_scores(pred: Invoice, true: Invoice, tol: Tolerances) -> dict[str, float]:
    return {
        "total": match_total(pred.total, true.total, tol),
        "line_items": match_line_items(pred.line_items, true.line_items, tol),
        "vendor_name": match_vendor(pred.vendor_name, true.vendor_name, tol),
        "invoice_date": match_date(pred.invoice_date, true.invoice_date),
        "invoice_number": match_invoice_number(pred.invoice_number, true.invoice_number),
        "currency": match_currency(pred.currency, true.currency),
    }


def score_doc(config: ScoringConfig, sub: Submission) -> DocScore:
    zeros = dict.fromkeys(SCORED_FIELDS, 0.0)
    if sub.status != "ok" or sub.output is None:
        return DocScore(
            doc_id=sub.doc_id, score=0.0, field_scores=zeros, status=sub.status, sum_check_ok=None
        )
    try:
        pred = Invoice.model_validate(sub.output)
    except ValidationError:
        return DocScore(
            doc_id=sub.doc_id, score=0.0, field_scores=zeros, status="invalid", sum_check_ok=None
        )

    fields = field_scores(pred, config.answer_keys[sub.doc_id], config.tolerances)
    total = sum(config.weights[f] * s for f, s in fields.items())
    checked = "line_items_sum" in config.consistency_checks
    return DocScore(
        doc_id=sub.doc_id,
        score=min(1.0, max(0.0, total)),  # weights sum to 1 only within ±1e-6
        field_scores=fields,
        status="ok",
        sum_check_ok=sum_check(pred, config.tolerances) if checked else None,
    )


def score(config: ScoringConfig, submissions: list[Submission]) -> list[ScoreReport]:
    """One ScoreReport per provider, in the order providers first appear in `submissions`."""
    unknown = {s.doc_id for s in submissions} - set(config.answer_keys)
    if unknown:
        raise ValueError(f"submissions for documents not in the answer key: {sorted(unknown)}")

    by_provider: dict[str, list[DocScore]] = {}
    for sub in submissions:
        by_provider.setdefault(sub.provider, []).append(score_doc(config, sub))

    seed = seed_for(config.trial_id)
    reports = []
    for provider, docs in by_provider.items():
        scores = [d.score for d in docs]
        reports.append(
            ScoreReport(
                provider=provider,
                doc_scores=docs,
                trial_score=min(1.0, sum(scores) / len(scores)),
                ci_95=bootstrap_ci(scores, seed=seed),
                n_docs=len(docs),
            )
        )
    return reports
