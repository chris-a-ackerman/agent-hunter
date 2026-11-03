from datetime import date

import pytest

from contracts import RankingRow, Verdict
from core.eval.stats import Decision, bootstrap_ci, decide, seed_for


def row(provider, accuracy, ci, cost=0.001, latency=1000.0, timeouts=0) -> RankingRow:
    return RankingRow(
        provider=provider,
        accuracy=accuracy,
        ci_95=ci,
        cost_per_doc=cost,
        timeouts=timeouts,
        mean_latency_ms=latency,
    )


def never_answered(provider) -> RankingRow:
    return RankingRow(
        provider=provider,
        accuracy=0.0,
        ci_95=(0.0, 0.0),
        cost_per_doc=None,
        timeouts=6,
        mean_latency_ms=None,
    )


# --- bootstrap_ci ----------------------------------------------------------------------------


def test_ci_is_reproducible_with_a_fixed_seed():
    scores = [0.9, 0.7, 1.0, 0.8, 0.95, 0.6]
    assert bootstrap_ci(scores, seed=7) == bootstrap_ci(scores, seed=7)
    assert bootstrap_ci(scores, seed=7) != bootstrap_ci(scores, seed=8)


def test_ci_brackets_the_mean_and_stays_in_bounds():
    scores = [0.9, 0.7, 1.0, 0.8, 0.95, 0.6]
    low, high = bootstrap_ci(scores, seed=1)
    assert 0 <= low < sum(scores) / len(scores) < high <= 1


def test_ci_narrows_with_more_documents():
    small = bootstrap_ci([0.5, 1.0] * 3, seed=1)
    large = bootstrap_ci([0.5, 1.0] * 50, seed=1)
    assert large[1] - large[0] < small[1] - small[0]


@pytest.mark.parametrize("scores", [[0.8], [1.0, 1.0, 1.0], [0.0, 0.0]])
def test_ci_collapses_for_one_document_or_constant_scores(scores):
    low, high = bootstrap_ci(scores)
    assert low == high == scores[0]


def test_ci_rejects_bad_input():
    with pytest.raises(ValueError):
        bootstrap_ci([])
    with pytest.raises(ValueError):
        bootstrap_ci([0.5, 0.6], level=1.0)


def test_seed_for_is_stable():
    assert seed_for("trial-8841") == seed_for("trial-8841")
    assert seed_for("trial-8841") != seed_for("trial-8842")
    # Pinned: must not change between processes or Python versions (hash() would).
    assert seed_for("trial-8841") == 1113083427836745285


# --- decide ----------------------------------------------------------------------------------


def test_clearly_separated_providers_give_a_recommendation():
    d = decide(
        [
            row("acme@1", 0.95, (0.91, 0.98)),
            row("beta@1", 0.70, (0.60, 0.80)),
            row("gamma@1", 0.50, (0.40, 0.62)),
        ]
    )
    assert d == Decision("recommended", "acme@1", [])


def test_overlapping_cis_are_a_tie_broken_on_cost():
    d = decide(
        [
            row("acme@1", 0.92, (0.85, 0.97), cost=0.004),
            row("beta@1", 0.89, (0.80, 0.95), cost=0.001),
            row("gamma@1", 0.60, (0.50, 0.70), cost=0.0001),  # cheapest, but not tied
        ]
    )
    assert d.status == "tie"
    assert d.tied == ["acme@1", "beta@1"]
    assert d.winner == "beta@1"


def test_tie_on_cost_is_broken_on_latency_then_provider():
    rows = [
        row("acme@1", 0.9, (0.8, 1.0), cost=0.002, latency=900.0),
        row("beta@1", 0.9, (0.8, 1.0), cost=0.002, latency=400.0),
    ]
    assert decide(rows).winner == "beta@1"
    rows = [
        row("zeta@1", 0.9, (0.8, 1.0), cost=0.002, latency=400.0),
        row("beta@1", 0.9, (0.8, 1.0), cost=0.002, latency=400.0),
    ]
    assert decide(rows).winner == "beta@1"


def test_unknown_cost_loses_a_tie_break():
    d = decide(
        [
            row("acme@1", 0.92, (0.85, 0.97), cost=None),
            row("beta@1", 0.89, (0.80, 0.95), cost=0.004),
        ]
    )
    assert d.winner == "beta@1"


def test_tied_set_is_everyone_overlapping_the_leader():
    d = decide(
        [
            row("a@1", 0.90, (0.82, 0.96)),
            row("b@1", 0.85, (0.75, 0.83)),  # touches the leader's low end
            row("c@1", 0.80, (0.70, 0.81)),  # misses it
        ]
    )
    assert d.tied == ["a@1", "b@1"]


def test_ci_touching_exactly_counts_as_overlap():
    d = decide([row("a@1", 0.9, (0.8, 0.95)), row("b@1", 0.7, (0.6, 0.8))])
    assert d.status == "tie"


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [row("acme@1", 0.9, (0.8, 1.0))],
        [row("acme@1", 0.9, (0.8, 1.0)), never_answered("dead@1"), never_answered("slow@1")],
    ],
)
def test_fewer_than_two_answering_providers_is_insufficient(rows):
    assert decide(rows) == Decision("insufficient_candidates", None, [])


def test_providers_that_never_answered_are_ignored():
    d = decide(
        [
            row("acme@1", 0.95, (0.91, 0.98)),
            row("beta@1", 0.60, (0.50, 0.70)),
            never_answered("dead@1"),
        ]
    )
    assert d == Decision("recommended", "acme@1", [])


def test_input_order_does_not_matter():
    rows = [
        row("a@1", 0.92, (0.85, 0.97), cost=0.004),
        row("b@1", 0.89, (0.80, 0.95), cost=0.001),
        row("c@1", 0.60, (0.50, 0.70)),
    ]
    assert decide(rows) == decide(rows[::-1])


@pytest.mark.parametrize(
    "rows",
    [
        [row("a@1", 0.95, (0.91, 0.98)), row("b@1", 0.6, (0.5, 0.7))],
        [row("a@1", 0.92, (0.85, 0.97)), row("b@1", 0.89, (0.80, 0.95), cost=0.0005)],
        [row("a@1", 0.9, (0.8, 1.0))],
    ],
)
def test_every_decision_builds_a_valid_verdict(rows):
    """The Decision fields drop straight into a Verdict without breaking its rules (D24)."""
    d = decide(rows)
    best = next((r for r in rows if r.provider == d.winner), None)
    Verdict(
        status=d.status,
        recommended=d.winner,
        tied=d.tied,
        trial_accuracy=best.accuracy if best else None,
        ci_95=best.ci_95 if best else None,
        cost_per_doc=best.cost_per_doc if best else None,
        valid_until=date(2026, 11, 22),
        tie=d.status == "tie",
        ranking=rows,
        rationale="",
        evidence="trial-1",
    )


# --- HW7 change (AGE-39): accuracy-first tie-break behind HW7_CHANGE ---------------------------

HW7_TIE = [
    row("acme@1", 0.92, (0.85, 0.97), cost=0.004),  # leader
    row("beta@1", 0.89, (0.80, 0.95), cost=0.001),  # cheaper, tied, less accurate
    row("gamma@1", 0.60, (0.50, 0.70), cost=0.0001),  # not tied
]


@pytest.mark.parametrize("value", [None, "", "off", "OFF"])
def test_hw7_change_off_keeps_the_cost_tie_break(value, monkeypatch):
    if value is None:
        monkeypatch.delenv("HW7_CHANGE", raising=False)
    else:
        monkeypatch.setenv("HW7_CHANGE", value)
    d = decide(HW7_TIE)
    assert d == Decision("tie", "beta@1", ["acme@1", "beta@1"])


def test_hw7_change_on_picks_the_trial_leader(monkeypatch):
    monkeypatch.setenv("HW7_CHANGE", "on")
    d = decide(HW7_TIE)
    assert d == Decision("tie", "acme@1", ["acme@1", "beta@1"])  # same status and tied list


def test_hw7_change_on_breaks_exact_accuracy_ties_on_cost_then_latency(monkeypatch):
    monkeypatch.setenv("HW7_CHANGE", "on")
    rows = [
        row("acme@1", 1.0, (1.0, 1.0), cost=0.004),
        row("beta@1", 1.0, (1.0, 1.0), cost=0.001),
        row("gamma@1", 0.95, (0.85, 1.0), cost=0.0001),
    ]
    assert decide(rows).winner == "beta@1"  # equal accuracy → cheaper; gamma is less accurate
    rows = [
        row("acme@1", 1.0, (1.0, 1.0), cost=0.002, latency=900.0),
        row("beta@1", 1.0, (1.0, 1.0), cost=0.002, latency=400.0),
    ]
    assert decide(rows).winner == "beta@1"


def test_hw7_change_does_not_affect_clear_wins_or_insufficient(monkeypatch):
    monkeypatch.setenv("HW7_CHANGE", "on")
    clear = [row("acme@1", 0.95, (0.90, 0.99)), row("beta@1", 0.60, (0.50, 0.70))]
    assert decide(clear) == Decision("recommended", "acme@1", [])
    lonely = [row("acme@1", 0.95, (0.90, 0.99)), row("beta@1", 0.0, (0.0, 0.0), latency=None)]
    assert decide(lonely).status == "insufficient_candidates"


def test_hw7_change_rejects_unknown_values(monkeypatch):
    monkeypatch.setenv("HW7_CHANGE", "yes")
    with pytest.raises(ValueError, match="HW7_CHANGE"):
        decide(HW7_TIE)
