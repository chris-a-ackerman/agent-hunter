from contracts import Submission
from core.pipeline import stubs
from tests.test_contracts.examples import SCORE_REPORT


def test_rank_stub_with_one_provider_is_insufficient():
    sub = Submission(
        task_id="t",
        doc_id=SCORE_REPORT.doc_scores[0].doc_id,
        provider=SCORE_REPORT.provider,
        status="ok",
        output={},
        latency_ms=10,
        cost_usd=0.01,
    )
    v = stubs.rank_stub([SCORE_REPORT], [sub], "trial-x")
    assert v.status == "insufficient_candidates" and v.recommended is None
    assert v.ranking[0].cost_per_doc == 0.01 and v.evidence == "trial-x"
