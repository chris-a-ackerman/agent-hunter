"""`JsonStore`: the MVP `Store` as local JSON files (D21).

Layout under `root` (default `STORE_DIR`, else `.data/store`):
    jobs/{job_id}.json   one JobRecord per job
    providers.json       registered providers, keyed by provider ID
    llm_calls.jsonl      the gateway's cost log (AGE-8); read by `platform_spend`
"""

import os
import re
import tempfile
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from contracts import (
    JobPosting,
    JobSpec,
    JobStatus,
    RegisteredProvider,
    Rubric,
    ScoreReport,
    Submission,
    TrialPackage,
    Verdict,
)
from core.store.base import JobNotFound, JobRecord, StatusChange, StepTiming, check_transition
from gateway import LLMCall

DEFAULT_ROOT = ".data/store"
_JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_Providers = TypeAdapter(dict[str, RegisteredProvider])


def _atomic_write(path: Path, text: str) -> None:
    """Write to a temp file in the same directory, then rename over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class JsonStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or os.environ.get("STORE_DIR") or DEFAULT_ROOT)
        self.llm_calls_path = self.root / "llm_calls.jsonl"
        self._lock = threading.RLock()

    # --- jobs -------------------------------------------------------------------------------

    def _job_path(self, job_id: str) -> Path:
        if not _JOB_ID.match(job_id):
            raise JobNotFound(job_id)
        return self.root / "jobs" / f"{job_id}.json"

    def _write_job(self, record: JobRecord) -> None:
        _atomic_write(self._job_path(record.job_id), record.model_dump_json(indent=2))

    def _update(self, job_id: str, change: Callable[[JobRecord], None]) -> None:
        with self._lock:
            record = self.get_job(job_id)
            change(record)
            self._write_job(record)

    def create_job(self, posting: JobPosting) -> str:
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        record = JobRecord(job_id=job_id, posting=posting)
        record.status_history.append(StatusChange(status=record.status, at=record.created_at))
        with self._lock:
            self._write_job(record)
        return job_id

    def get_job(self, job_id: str) -> JobRecord:
        path = self._job_path(job_id)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise JobNotFound(job_id) from None
        return JobRecord.model_validate_json(text)

    def set_status(self, job_id: str, status: JobStatus, note: str | None = None) -> None:
        def change(r: JobRecord) -> None:
            check_transition(job_id, r.status, status)
            r.status = status
            r.status_history.append(StatusChange(status=status, note=note))

        self._update(job_id, change)

    def save_job_spec(self, job_id: str, spec: JobSpec) -> None:
        self._update(job_id, lambda r: setattr(r, "spec", spec))

    def save_rubric(self, job_id: str, rubric: Rubric) -> None:
        self._update(job_id, lambda r: setattr(r, "rubric", rubric))

    def save_trial(self, job_id: str, trial: TrialPackage) -> None:
        self._update(job_id, lambda r: setattr(r, "trial", trial))

    def save_submissions(self, job_id: str, subs: list[Submission]) -> None:
        self._update(job_id, lambda r: setattr(r, "submissions", list(subs)))

    def save_scores(self, job_id: str, reports: list[ScoreReport]) -> None:
        self._update(job_id, lambda r: setattr(r, "scores", list(reports)))

    def save_verdict(self, job_id: str, verdict: Verdict) -> None:
        self._update(job_id, lambda r: setattr(r, "verdict", verdict))

    def record_step(
        self, job_id: str, step: str, started_at: datetime, finished_at: datetime
    ) -> None:
        timing = StepTiming(step=step, started_at=started_at, finished_at=finished_at)
        self._update(job_id, lambda r: r.timings.append(timing))

    # --- providers --------------------------------------------------------------------------

    @property
    def _providers_path(self) -> Path:
        return self.root / "providers.json"

    def _read_providers(self) -> dict[str, RegisteredProvider]:
        try:
            return _Providers.validate_json(self._providers_path.read_bytes())
        except FileNotFoundError:
            return {}

    def upsert_provider(self, p: RegisteredProvider) -> None:
        with self._lock:
            providers = self._read_providers()
            providers[p.provider_id] = p
            _atomic_write(self._providers_path, _Providers.dump_json(providers, indent=2).decode())

    def list_providers(self) -> list[RegisteredProvider]:
        return list(self._read_providers().values())

    # --- spend ------------------------------------------------------------------------------

    def platform_spend(self, job_id: str) -> float:
        """USD the platform's own model calls cost for this job, from the gateway's cost log."""
        try:
            lines = self.llm_calls_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return 0.0
        total = 0.0
        for line in lines:
            if line.strip():
                call = LLMCall.model_validate_json(line)
                if call.job_id == job_id:
                    total += call.cost_usd
        return total
