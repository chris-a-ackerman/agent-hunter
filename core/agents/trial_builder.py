"""The Trial Builder (AGE-22, plan §3 #3, §5.3): requester samples + synthetic docs → trial.

1. **Samples:** up to `MAX_SAMPLES` samples that have an answer are copied to
   `docs_dir/{new opaque id}.pdf` with the answer at `truth_dir/{id}.json` (D17), so the
   original file name never reaches a provider. Samples without an answer are skipped (logged).
2. **Synthetic fill:** `size - n_samples` docs from `data.synth.generate`. One LLM call picks
   generator settings `{layout, currency, line_items: [min, max]}` from the samples' answer
   keys; bad output or no gateway → (first layout, USD, the samples' min–max line items).
   Tax and scanning follow the samples' mix: each sample's (has a tax line, is image-only)
   profile is used in turn, so a trial for scanned no-tax samples isn't padded with clean docs.
   The seed comes from `job_id`, so a rerun of a job builds the same synthetic docs.
3. Every doc ID goes through `data.pools.check_access`: anything in `job_real` raises
   `PoolViolation`. `trial_real` is skipped in the MVP.
4. Documents are shuffled (seeded) so samples aren't always first.

`build_trial` satisfies `contracts.steps.BuildTrial` once the pipeline binds the directories.
"""

import json
import logging
import os
import random
import re
import shutil
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator

from contracts import Invoice, JobPosting, JobSpec, TrialDoc, TrialPackage, new_doc_id
from core.eval.stats import seed_for
from data.pools import POOLS_DIR, check_access
from data.synth import CURRENCIES, LAYOUTS, generate
from gateway import DEFAULT_MODEL, Gateway

log = logging.getLogger(__name__)

COMPONENT = "trial_builder"
CALLER = "trial_builder"
TRIAL_SIZE = int(os.environ.get("TRIAL_SIZE") or 6)
MAX_SAMPLES = 3
MAX_TOKENS = 600  # gpt-6-luna spends tokens on reasoning before answering
MAX_LINE_ITEMS = 40
DEFAULT_LINE_ITEMS = (1, 8)  # the generator's default, used when there are no samples
_DOC_ID = re.compile(r"^[0-9a-f]{12}$")

SYSTEM_PROMPT = f"""\
You configure a synthetic invoice generator so its invoices look like a requester's samples. \
You get each sample's line-item count, currency and vendor name. Pick the layout, the currency \
and a line-item range [min, max] that match the samples.

Reply with a JSON object only:
{{"layout": "<one of {", ".join(LAYOUTS)}>", "currency": "<one of {", ".join(CURRENCIES)}>", \
"line_items": [<min>, <max>]}}
1 <= min <= max <= {MAX_LINE_ITEMS}."""


class GenSettings(BaseModel):
    """What the model must return; also the fallback."""

    layout: str
    currency: str
    line_items: tuple[int, int]

    @field_validator("layout")
    @classmethod
    def _layout(cls, v: str) -> str:
        if v not in LAYOUTS:
            raise ValueError(f"layout must be one of {LAYOUTS}")
        return v

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        if v not in CURRENCIES:
            raise ValueError(f"currency must be one of {sorted(CURRENCIES)}")
        return v

    @field_validator("line_items")
    @classmethod
    def _line_items(cls, v: tuple[int, int]) -> tuple[int, int]:
        lo, hi = v
        if not 1 <= lo <= hi <= MAX_LINE_ITEMS:
            raise ValueError(f"line_items must satisfy 1 <= min <= max <= {MAX_LINE_ITEMS}")
        return v


def fallback_settings(answers: list[Invoice]) -> GenSettings:
    counts = [len(a.line_items) for a in answers if a.line_items]
    lo, hi = (min(counts), max(counts)) if counts else DEFAULT_LINE_ITEMS
    hi = min(hi, MAX_LINE_ITEMS)
    return GenSettings(layout=LAYOUTS[0], currency="USD", line_items=(min(lo, hi), hi))


def pick_settings(gateway: Gateway | None, answers: list[Invoice], job_id: str) -> GenSettings:
    """The LLM's generator settings, or the fallback on bad output or no gateway."""
    fallback = fallback_settings(answers)
    if gateway is None:
        return fallback
    samples = [
        {"line_items": len(a.line_items), "currency": a.currency, "vendor_name": a.vendor_name}
        for a in answers
    ]
    resp = gateway.call(
        COMPONENT,
        DEFAULT_MODEL,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Samples:\n{json.dumps(samples)}"},
        ],
        response_format={"type": "json_object"},
        job_id=job_id,
        max_tokens=MAX_TOKENS,
    )
    try:
        if resp.parsed is None:
            raise ValueError("not a JSON object")
        return GenSettings.model_validate(resp.parsed)
    except (ValueError, ValidationError) as e:
        log.warning(
            "Trial Builder: unusable generator settings for job %s (%s); using %s",
            job_id,
            str(e).splitlines()[0],
            fallback.model_dump(),
        )
        return fallback


def has_text_layer(pdf_path: Path) -> bool:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_path)
    try:
        return any(page.get_textpage().get_text_range().strip() for page in pdf)
    finally:
        pdf.close()


def synth_profiles(docs: list[TrialDoc], docs_dir: Path, n: int) -> Counter[tuple[bool, bool]]:
    """How many of the n synthetic docs get each (with_tax, scanned) profile: the samples'
    profiles in turn, or all (with tax, digital) when there are no samples."""
    profiles = [
        (d.answer.tax is not None, not has_text_layer(docs_dir / f"{d.doc_id}.pdf")) for d in docs
    ] or [(True, False)]
    return Counter(profiles[i % len(profiles)] for i in range(n))


def _sample_docs(
    posting: JobPosting,
    *,
    limit: int,
    samples_dir: Path,
    docs_dir: Path,
    truth_dir: Path,
    pools_dir: Path,
) -> list[TrialDoc]:
    """Copy up to `limit` answered samples under new opaque IDs."""
    out: list[TrialDoc] = []
    for i, sample in enumerate(posting.samples, start=1):
        if sample.answer is None:
            log.info("Trial Builder: sample %d has no answer; skipped", i)
            continue
        if len(out) == limit:
            log.info("Trial Builder: more than %d answered samples; the rest are skipped", limit)
            break
        src = samples_dir / sample.path
        if _DOC_ID.match(src.stem):  # a sample that points at one of our own documents
            check_access(src.stem, caller=CALLER, pools_dir=pools_dir)
        doc_id = new_doc_id()
        check_access(doc_id, caller=CALLER, pools_dir=pools_dir)
        shutil.copyfile(src, docs_dir / f"{doc_id}.pdf")
        (truth_dir / f"{doc_id}.json").write_text(sample.answer.model_dump_json(indent=2))
        out.append(TrialDoc(doc_id=doc_id, source="sample", answer=sample.answer))
    return out


def build_trial(
    spec: JobSpec,
    posting: JobPosting,
    *,
    gateway: Gateway | None = None,
    size: int = TRIAL_SIZE,
    samples_dir: Path = Path("examples"),
    docs_dir: Path = Path("data/docs"),
    truth_dir: Path = Path("data/truth"),
    pools_dir: Path = POOLS_DIR,
    render: bool = True,
) -> TrialPackage:
    """A trial of `size` documents for `spec`. Sample paths resolve against `samples_dir`
    (the directory the posting file lives in). `render=False` skips the synthetic PDFs
    (answer keys only; for tests)."""
    if size < 1:
        raise ValueError("a trial needs at least one document")
    docs_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    docs = _sample_docs(
        posting,
        limit=min(MAX_SAMPLES, size),
        samples_dir=samples_dir,
        docs_dir=docs_dir,
        truth_dir=truth_dir,
        pools_dir=pools_dir,
    )
    seed = seed_for(spec.job_id)
    n_synth = size - len(docs)
    if n_synth:
        s = pick_settings(gateway, [d.answer for d in docs], spec.job_id)
        for (with_tax, scanned), n in synth_profiles(docs, docs_dir, n_synth).items():
            for g in generate(
                layout=s.layout,
                n=n,
                seed=seed,
                currency=s.currency,
                line_items=s.line_items,
                with_tax=with_tax,
                scanned=scanned,
                render=render,
                docs_dir=docs_dir,
                truth_dir=truth_dir,
            ):
                check_access(g.doc_id, caller=CALLER, pools_dir=pools_dir)
                docs.append(TrialDoc(doc_id=g.doc_id, source="synthetic", answer=g.truth))

    random.Random(seed).shuffle(docs)
    return TrialPackage(
        trial_id=f"trial-{uuid.uuid4().hex[:12]}",
        job_id=spec.job_id,
        documents=docs,
        created_at=datetime.now(UTC),
    )
