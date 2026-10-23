"""Pipeline wired to the mocks: fake providers in-process (ASGI), the real Trial Runner, fake
scorer, stub steps."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from contracts import JobPosting, RegisteredProvider
from core.eval.eval_builder import build_eval
from core.pipeline import stubs
from core.pipeline.runner import Steps
from core.runner.candidate_finder import find_candidates
from core.runner.trial_runner import TrialRunner
from core.store import JsonStore
from examples.make_examples import EXAMPLES_DIR
from mocks import fake_scorer
from mocks.fake_providers import MOCKS, card, create_app

MOCK_BASE_URL = "http://mock-providers"
DEADLINE_MS = 300  # mock-d sleeps 2 × deadline, then 504: it must time out


def load_example(name: str) -> JobPosting:
    return JobPosting.model_validate_json((EXAMPLES_DIR / f"{name}.json").read_text())


@pytest.fixture
def store(tmp_path: Path) -> JsonStore:
    return JsonStore(tmp_path / "store")


@pytest.fixture
def make_steps(tmp_path: Path):
    def make(*, fail_budget: bool = False, **overrides) -> Steps:
        docs, truth = tmp_path / "docs", tmp_path / "truth"
        providers_app = create_app(truth_dir=truth, fail_budget=fail_budget)
        registry = [RegisteredProvider(card=card(s), base_url=MOCK_BASE_URL) for s in MOCKS]
        steps: dict[str, Any] = {
            "review": stubs.review_job_stub,
            "rubric": stubs.build_rubric_stub,
            "trial": stubs.SampleTrialBuilder(EXAMPLES_DIR, docs, truth),
            "eval": build_eval,
            "candidates": find_candidates,
            "run_trial": TrialRunner(
                "http://docs",
                deadline_ms=DEADLINE_MS,
                max_delay_s=0,
                transport=httpx.ASGITransport(app=providers_app),
            ),
            "score": fake_scorer.score,
            "rank": stubs.rank_stub,
            "registry": lambda: registry,
        }
        return Steps(**(steps | overrides))

    return make
