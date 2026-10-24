"""Document pools (plan §5.3) and the guard that keeps `job_real` away from the Trial Builder.

Each pool is a manifest at `data/pools/{pool}.json`: a JSON list of doc IDs. A missing manifest
is an empty pool (`trial_real` is empty in the MVP; it comes with DocILE in M2).

    requester_samples   Trial Builder (anchors); everyone
    trial_real          Trial Builder (mixed into trials so they look real)
    job_real            real jobs and the experiment harness only — never the Trial Builder

The Trial Builder gets documents only through `load_doc` / `check_access`, which raise
`PoolViolation` for any doc ID listed in `job_real`.
"""

import json
from pathlib import Path

from contracts import Invoice

POOLS = ("requester_samples", "trial_real", "job_real")
POOLS_DIR = Path("data/pools")
# Callers that must never see job_real documents.
FORBIDDEN = {"job_real": frozenset({"trial_builder"})}


class PoolViolation(Exception):
    """A caller asked for a document from a pool it may not read."""


def load_pool(pool: str, *, pools_dir: Path = POOLS_DIR) -> frozenset[str]:
    if pool not in POOLS:
        raise ValueError(f"unknown pool {pool!r}; expected one of {POOLS}")
    path = Path(pools_dir) / f"{pool}.json"
    if not path.is_file():
        return frozenset()
    return frozenset(json.loads(path.read_text()))


def check_access(doc_id: str, *, caller: str, pools_dir: Path = POOLS_DIR) -> None:
    """Raise `PoolViolation` if `caller` may not read `doc_id`."""
    for pool, callers in FORBIDDEN.items():
        if caller in callers and doc_id in load_pool(pool, pools_dir=pools_dir):
            raise PoolViolation(f"{caller} may not read {doc_id}: it is in the {pool} pool")


def load_doc(
    doc_id: str,
    *,
    caller: str = "trial_builder",
    docs_dir: Path = Path("data/docs"),
    truth_dir: Path = Path("data/truth"),
    pools_dir: Path = POOLS_DIR,
) -> tuple[Path, Invoice]:
    """The document's PDF path and answer key, after the pool check."""
    check_access(doc_id, caller=caller, pools_dir=pools_dir)
    answer = Invoice.model_validate_json((Path(truth_dir) / f"{doc_id}.json").read_text())
    return Path(docs_dir) / f"{doc_id}.pdf", answer
