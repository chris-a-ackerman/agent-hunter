import asyncio
from pathlib import Path

from mypy import api as mypy_api

from contracts import RunPaused
from tests.test_contracts import examples as ex


def test_step_stubs_type_check():
    stubs = Path(__file__).with_name("step_stubs.py")
    stdout, stderr, status = mypy_api.run(["--strict", "--no-incremental", str(stubs)])
    assert status == 0, stdout + stderr


def test_step_stubs_run():
    from tests.test_contracts import step_stubs as s

    spec = s.review_job(ex.JOB_POSTING)
    rubric = s.build_rubric(spec)
    trial = s.build_trial(spec, ex.JOB_POSTING)
    config = s.build_eval(rubric, trial)
    providers = s.find_candidates(spec, [ex.REGISTERED_PROVIDER])
    submissions = asyncio.run(s.run_trial(trial, providers, "req-1"))
    reports = s.score(config, submissions)
    verdict = s.rank(reports, submissions, trial.trial_id)
    assert verdict.evidence == trial.trial_id


def test_run_paused_carries_submissions():
    err = RunPaused([ex.SUBMISSION])
    assert err.submissions == [ex.SUBMISSION]
    assert err.reason == "budget_exhausted"
