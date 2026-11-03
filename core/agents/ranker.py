"""The Ranker (AGE-24, plan §3 #8, §5.1, §8): turns score reports into the `Verdict`.

1. One `RankingRow` per provider: accuracy and CI from its `ScoreReport`; cost per doc and mean
   latency over its `ok` submissions (D19); timeouts counted. Sorted by accuracy.
2. `core.eval.stats.decide` picks recommended / tie / insufficient_candidates.
3. One LLM call writes a 2–3 sentence rationale from the rows and the decision only. Every
   number in it must match a table value as shown (`allowed_numbers`); otherwise, or with no
   gateway, a fixed template is used and a warning logged. Insufficient candidates → template.

`rank` satisfies `contracts.steps.Rank`.
"""

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from statistics import fmean

from contracts import RankingRow, ScoreReport, Submission, Verdict
from core.eval.stats import Decision, accuracy_first, decide, is_eligible
from gateway import DEFAULT_MODEL, Gateway

log = logging.getLogger(__name__)

COMPONENT = "ranker"
MAX_TOKENS = 600  # gpt-6-luna spends tokens on reasoning before answering
VALID_DAYS = 30
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

SYSTEM_PROMPT = """\
You explain the result of a trial in which AI agents extracted invoices and were scored. \
Write 2 to 3 plain-English sentences for a non-technical requester: who is recommended (or \
that it's a tie, and who wins the tie-break), and why. Use only the numbers in the data you \
are given, written exactly as given (percentages and dollar amounts are pre-formatted). Do not \
add any other numbers, and do not make claims the data doesn't support. Reply with the \
sentences only."""


# --- formatting: one place, shared by the template, the prompt and the number check --------


def pct(x: float) -> str:
    return f"{x * 100:.1f}"


def usd(x: float) -> str:
    return f"{x:.4f}"


def _row_view(row: RankingRow) -> dict[str, object]:
    """A row as the model and the template see it."""
    return {
        "provider": row.provider,
        "accuracy_pct": pct(row.accuracy),
        "ci_95_pct": [pct(row.ci_95[0]), pct(row.ci_95[1])],
        "cost_per_doc_usd": None if row.cost_per_doc is None else usd(row.cost_per_doc),
        "timeouts": row.timeouts,
        "mean_latency_ms": None if row.mean_latency_ms is None else round(row.mean_latency_ms),
    }


def allowed_numbers(rows: list[RankingRow], decision: Decision) -> set[str]:
    """Every number the rationale may contain, in each way it may be written: percentages
    with 0 or 1 dp, costs with 2–4 dp, counts, latencies in whole ms, and the 95 of "95% CI"."""
    allowed = {
        "95",
        str(len(rows)),
        str(sum(is_eligible(r) for r in rows)),
        str(len(decision.tied)),
    }
    for r in rows:
        for x in (r.accuracy, *r.ci_95):
            allowed |= {f"{x * 100:.0f}", f"{x * 100:.1f}"}
        if r.cost_per_doc is not None:
            allowed |= {f"{r.cost_per_doc:.{dp}f}" for dp in (2, 3, 4)}
        if r.mean_latency_ms is not None:
            allowed.add(f"{r.mean_latency_ms:.0f}")
        allowed.add(str(r.timeouts))
    return allowed


def invented_numbers(text: str, rows: list[RankingRow], decision: Decision) -> list[str]:
    """Numbers in `text` that aren't table values. Provider IDs are removed first, since
    their versions ("@1.0.0") contain digits."""
    for r in rows:
        text = text.replace(r.provider, " ")
    allowed = allowed_numbers(rows, decision)
    return [n for n in _NUMBER.findall(text.replace(",", "")) if n not in allowed]


# --- rows and rationale -----------------------------------------------------------------------


def build_rows(reports: list[ScoreReport], submissions: list[Submission]) -> list[RankingRow]:
    rows = []
    for report in reports:
        subs = [s for s in submissions if s.provider == report.provider]
        ok = [s for s in subs if s.status == "ok"]
        costs = [s.cost_usd for s in ok if s.cost_usd is not None]
        latencies = [s.latency_ms for s in ok if s.latency_ms is not None]
        rows.append(
            RankingRow(
                provider=report.provider,
                accuracy=report.trial_score,
                ci_95=report.ci_95,
                cost_per_doc=fmean(costs) if costs else None,
                timeouts=sum(s.status == "timeout" for s in subs),
                mean_latency_ms=fmean(latencies) if latencies else None,
            )
        )
    rows.sort(key=lambda r: -r.accuracy)
    return rows


def template_rationale(rows: list[RankingRow], decision: Decision) -> str:
    if decision.status == "insufficient_candidates":
        answered = sum(is_eligible(r) for r in rows)
        return (
            f"Only {answered} of {len(rows)} providers answered the trial, so there is no "
            f"recommendation."
        )
    best = next(r for r in rows if r.provider == decision.winner)
    cost = "an unknown cost" if best.cost_per_doc is None else f"${usd(best.cost_per_doc)}/doc"
    scored = (
        f"{best.provider} scored {pct(best.accuracy)}% (95% CI {pct(best.ci_95[0])}–"
        f"{pct(best.ci_95[1])}%) at {cost} with {best.timeouts} timeouts."
    )
    if decision.status == "tie":
        rule = (
            "trial accuracy, then cost per document"
            if accuracy_first()
            else "cost per document, then latency"
        )
        return (
            f"{scored} {len(decision.tied)} providers have overlapping confidence intervals, so "
            f"it's a tie; {best.provider} wins it on {rule}."
        )
    return f"{scored} Its confidence interval clears the runner-up's among {len(rows)} providers."


def llm_rationale(gateway: Gateway, rows: list[RankingRow], decision: Decision) -> str | None:
    """The model's rationale, or None if it is empty or contains an invented number."""
    data = {
        "status": decision.status,
        "recommended": decision.winner,
        "tied": decision.tied,
        "n_providers": len(rows),
        "ranking": [_row_view(r) for r in rows],
    }
    resp = gateway.call(
        COMPONENT,
        DEFAULT_MODEL,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(data)},
        ],
        max_tokens=MAX_TOKENS,
    )
    text = resp.content.strip()
    if not text:
        log.warning("Ranker: empty rationale from the model; using the template")
        return None
    bad = invented_numbers(text, rows, decision)
    if bad:
        log.warning("Ranker: rationale has numbers not in the table %s; using the template", bad)
        return None
    return text


def rank(
    reports: list[ScoreReport],
    submissions: list[Submission],
    trial_id: str,
    *,
    gateway: Gateway | None = None,
) -> Verdict:
    rows = build_rows(reports, submissions)
    d = decide(rows)
    best = next((r for r in rows if r.provider == d.winner), None)

    rationale = None
    if gateway is not None and best is not None:
        rationale = llm_rationale(gateway, rows, d)
    if rationale is None:
        rationale = template_rationale(rows, d)

    today = datetime.now(UTC).date()
    return Verdict(
        status=d.status,
        recommended=d.winner,
        tied=d.tied,
        trial_accuracy=best.accuracy if best else None,
        ci_95=best.ci_95 if best else None,
        cost_per_doc=best.cost_per_doc if best else None,
        valid_until=today + timedelta(days=VALID_DAYS) if best else None,
        tie=d.status == "tie",
        ranking=rows,
        rationale=rationale,
        evidence=trial_id,
    )
