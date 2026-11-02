"""Builds the HW7 benchmark job set (AGE-34, experiments/hw7/PREREG.md §2–3) from fixed seeds.

    uv run python -m experiments.hw7.make_jobs

For each of the 12 jobs it writes the posting `experiments/hw7/jobs/jNN.json` with 3 answered
samples at `experiments/hw7/jobs/samples/jNN/sample_{1..3}.pdf` (paths in the posting are
relative to `experiments/hw7/jobs`), plus a held-out pile of 12 invoices into data/docs +
data/truth (gitignored). The held-out IDs are appended to `data/pools/job_real.json`, so the
Trial Builder can never read them, and every job is listed in `experiments/hw7/jobs/manifest.json`.
The trial invoices themselves are built by the real Trial Builder at run time, not here.

Synthetic jobs: the held-out pile uses the samples' settings with a different seed.

Real-invoice jobs (j02, j09): `select_for_job(1, 8, seed=1102, n=30)` (PREREG §2); the first 15
go to j02 and the next 15 to j09, and within each job the first 3 are samples and the last 12
held-out. DocILE is research-use only, so their sample PDFs and postings (which carry answers
from DocILE annotations) are gitignored and regenerated from `data/docile/`; only their manifest
entries (DocILE IDs) and `data/pools/docile_map.json` are committed. Without `data/docile/`
those two jobs are skipped with a warning and their committed manifest entries are kept.

Output is byte-identical on rerun.
"""

import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median_low
from typing import Any

from contracts import INVOICE_JSON_SCHEMA, Invoice, JobPosting, SampleDoc
from data import docile_loader
from data.synth import GeneratedDoc, generate

JOBS_DIR = Path(__file__).parent / "jobs"
MANIFEST = "manifest.json"
POOLS_DIR = Path("data/pools")
DOCS_DIR = Path("data/docs")
TRUTH_DIR = Path("data/truth")
REQUESTER_ID = "req-hw7-01"
N_SAMPLES = 3
N_HELDOUT = 12

# The same budgets as examples/make_examples.py, so all 7 providers pass the Candidate Finder.
BUDGETS: dict[str, Any] = {
    "n_real_docs": 15,
    "job_budget_usd": 5.00,
    "trial_budget_usd": 0.50,
    "deadline": None,
    "deadline_open_ack": True,
    "region": None,
}

FIELDS = (
    "Extract the vendor, invoice number, invoice date, currency, line items, subtotal, tax and "
    "total from"
)
INVOICE_OUTPUT = (
    "One JSON object per invoice, matching output_schema: dates as YYYY-MM-DD, currency as an "
    "ISO 4217 code, amounts as plain numbers. Use null for an invoice number, subtotal or tax "
    "that isn't printed."
)

# PREREG §2: the only rule that picks DocILE documents. Do not change.
DOCILE_LINE_ITEMS = (1, 8)
DOCILE_SEED = 1102
DOCILE_N = 30

Profile = tuple[bool, bool]  # (with_tax, scanned)


@dataclass(frozen=True)
class SynthJob:
    job: str
    type: str
    split: str
    description: str
    layout: str
    currency: str
    line_items: tuple[int, int]
    sample_seed: int
    heldout_seed: int
    # (with_tax, scanned) per document, used in turn across the samples and the held-out pile
    profiles: tuple[Profile, ...] = ((True, False),)
    near_tie: bool = False

    def generate(self, *, n: int, seed: int, **kw: Any) -> list[GeneratedDoc]:
        counts = Counter(self.profiles[i % len(self.profiles)] for i in range(n))
        return [
            doc
            for (with_tax, scanned), k in counts.items()
            for doc in generate(
                layout=self.layout,
                n=k,
                seed=seed,
                currency=self.currency,
                line_items=self.line_items,
                with_tax=with_tax,
                scanned=scanned,
                **kw,
            )
        ]


@dataclass(frozen=True)
class DocileJob:
    job: str
    type: str
    split: str
    start: int  # this job's slice of the 30 selected DocILE docs: [start, start + 15)

    def docids(self, selected: list[str]) -> list[str]:
        return selected[self.start : self.start + N_SAMPLES + N_HELDOUT]


SYNTH_JOBS = {
    j.job: j
    for j in [
        SynthJob(
            job="j01",
            type="clean",
            split="tuning",
            description=(
                f"{FIELDS} our office-supply invoices. They are clean, one-page digital PDFs "
                "with a classic layout and short tables of 1 to 4 lines each, all in US "
                "dollars, and every invoice shows its sales tax."
            ),
            layout="classic",
            currency="USD",
            line_items=(1, 4),
            sample_seed=101,
            heldout_seed=201,
            near_tie=True,  # easy clean invoices that most providers should ace
        ),
        SynthJob(
            job="j03",
            type="scanned",
            split="tuning",
            description=(
                f"{FIELDS} paper invoices from our US office and IT suppliers that we scan "
                "in. Every file is a scanned image with no text layer, slightly tilted and "
                "blurred, with 2 to 6 line items each, in US dollars, and every invoice "
                "shows its sales tax."
            ),
            layout="modern",
            currency="USD",
            line_items=(2, 6),
            sample_seed=103,
            heldout_seed=203,
            profiles=((True, True),),
        ),
        SynthJob(
            job="j04",
            type="scanned",
            split="tuning",
            description=(
                f"{FIELDS} paper invoices our small US suppliers mail us, which we scan in. "
                "Every file is a scanned image with no text layer, slightly tilted and "
                "blurred, in a compact layout with 1 to 8 line items each, in US dollars. "
                "About a third of the suppliers don't charge sales tax, so there is often "
                "no tax line."
            ),
            layout="compact",
            currency="USD",
            line_items=(1, 8),
            sample_seed=104,
            heldout_seed=204,
            profiles=((True, True), (False, True)),
        ),
        SynthJob(
            job="j05",
            type="long",
            split="tuning",
            description=(
                f"{FIELDS} our IT equipment and services invoices. They are digital PDFs with "
                "long line-item tables (15 to 30 rows) that often run onto a second page, in "
                "US dollars with sales tax; every line item must be captured."
            ),
            layout="classic",
            currency="USD",
            line_items=(15, 30),
            sample_seed=105,
            heldout_seed=205,
        ),
        SynthJob(
            job="j06",
            type="long",
            split="tuning",
            description=(
                f"{FIELDS} monthly invoices from our facilities and office suppliers. They "
                "are digital PDFs in a compact layout with long tables of 15 to 30 line "
                "items that often continue onto a second page, in US dollars with sales "
                "tax; every line item must be captured."
            ),
            layout="compact",
            currency="USD",
            line_items=(15, 30),
            sample_seed=106,
            heldout_seed=206,
        ),
        SynthJob(
            job="j07",
            type="non_usd",
            split="tuning",
            description=(
                f"{FIELDS} invoices from our German suppliers. They are digital PDFs in "
                "English, priced in euros (EUR) with VAT, with 1 to 8 line items each."
            ),
            layout="modern",
            currency="EUR",
            line_items=(1, 8),
            sample_seed=107,
            heldout_seed=207,
        ),
        SynthJob(
            job="j08",
            type="non_usd",
            split="tuning",
            description=(
                f"{FIELDS} invoices from our UK suppliers. They are digital PDFs priced in "
                "pounds sterling (GBP) with 20% VAT, with 1 to 8 line items each."
            ),
            layout="classic",
            currency="GBP",
            line_items=(1, 8),
            sample_seed=108,
            heldout_seed=208,
        ),
        SynthJob(
            job="j10",
            type="scanned",
            split="fresh",
            description=(
                f"{FIELDS} paper invoices from our US vendors that the mailroom scans. Every "
                "file is a scanned image with no text layer, slightly tilted and blurred, "
                "with 1 to 8 line items each, in US dollars. Some vendors don't charge "
                "sales tax, so there is sometimes no tax line."
            ),
            layout="classic",
            currency="USD",
            line_items=(1, 8),
            sample_seed=110,
            heldout_seed=210,
            profiles=((True, True), (True, True), (False, True)),
        ),
        SynthJob(
            job="j11",
            type="long",
            split="fresh",
            description=(
                f"{FIELDS} invoices from our IT and consulting contractors. They are digital "
                "PDFs with long line-item tables (15 to 30 rows) that often run onto a "
                "second page, in US dollars with sales tax; every line item must be captured."
            ),
            layout="modern",
            currency="USD",
            line_items=(15, 30),
            sample_seed=111,
            heldout_seed=211,
        ),
        SynthJob(
            job="j12",
            type="non_usd",
            split="fresh",
            description=(
                f"{FIELDS} invoices from our suppliers in Germany. They are digital PDFs in "
                "English, in a compact layout, priced in euros (EUR) with VAT, with 1 to 8 "
                "line items each."
            ),
            layout="compact",
            currency="EUR",
            line_items=(1, 8),
            sample_seed=112,
            heldout_seed=212,
        ),
    ]
}

DOCILE_JOBS = {
    j.job: j
    for j in [
        DocileJob(job="j02", type="clean", split="tuning", start=0),
        DocileJob(job="j09", type="clean", split="fresh", start=N_SAMPLES + N_HELDOUT),
    ]
}

JOB_IDS = [f"j{n:02d}" for n in range(1, 13)]
assert sorted(SYNTH_JOBS.keys() | DOCILE_JOBS.keys()) == JOB_IDS


def posting(description: str, samples: list[SampleDoc]) -> JobPosting:
    return JobPosting(
        requester_id=REQUESTER_ID,
        description=description,
        output_description=INVOICE_OUTPUT,
        output_schema=INVOICE_JSON_SCHEMA,
        samples=samples,
        **BUDGETS,
    )


def sample_path(job: str, n: int) -> Path:
    """A sample's path relative to the jobs directory (what the posting stores)."""
    return Path("samples") / job / f"sample_{n}.pdf"


# --- synthetic jobs ---------------------------------------------------------------------------


def build_synth_posting(
    job: SynthJob, *, jobs_dir: Path = JOBS_DIR, render: bool = True
) -> JobPosting:
    """Generates the job's samples into jobs_dir/samples/{job}/ (PDFs only if render) and returns
    the posting, with each sample's answer inline."""
    samples = []
    with tempfile.TemporaryDirectory() as tmp:
        docs = job.generate(
            n=N_SAMPLES,
            seed=job.sample_seed,
            render=render,
            docs_dir=Path(tmp) / "docs",
            truth_dir=Path(tmp) / "truth",
        )
        for n, doc in enumerate(docs, start=1):
            rel = sample_path(job.job, n)
            if doc.pdf_path is not None:
                (jobs_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(doc.pdf_path, jobs_dir / rel)
            samples.append(SampleDoc(path=rel.as_posix(), answer=doc.truth))
    return posting(job.description, samples)


def build_synth_heldout(
    job: SynthJob,
    *,
    docs_dir: Path = DOCS_DIR,
    truth_dir: Path = TRUTH_DIR,
    render: bool = True,
) -> list[str]:
    """The job's 12 held-out invoices: the samples' settings, a different seed. Returns doc IDs."""
    docs = job.generate(
        n=N_HELDOUT, seed=job.heldout_seed, render=render, docs_dir=docs_dir, truth_dir=truth_dir
    )
    return [d.doc_id for d in docs]


def synth_entry(job: SynthJob, heldout: list[str]) -> dict[str, Any]:
    return {
        "job": job.job,
        "type": job.type,
        "split": job.split,
        "posting": f"{job.job}.json",
        "source": "synthetic",
        "layout": job.layout,
        "currency": job.currency,
        "line_items": list(job.line_items),
        "profiles": [list(p) for p in job.profiles],
        "sample_seed": job.sample_seed,
        "heldout_seed": job.heldout_seed,
        "heldout_doc_ids": heldout,
        "near_tie": job.near_tie,
    }


# --- real-invoice (DocILE) jobs ---------------------------------------------------------------


def docile_selection(candidates_path: Path = docile_loader.CANDIDATES_PATH) -> list[str]:
    lo, hi = DOCILE_LINE_ITEMS
    return docile_loader.select_for_job(
        lo, hi, seed=DOCILE_SEED, n=DOCILE_N, candidates_path=candidates_path
    )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def describe_real(answers: list[Invoice], scanned: list[bool], pages: list[int]) -> str:
    """A plain-words description of a DocILE job's 15 invoices, computed from their answer keys
    and PDFs, so the profile-reading baseline is told what the invoices are really like."""
    n = len(answers)
    counts = sorted(len(a.line_items) for a in answers)
    vendors = {a.vendor_name.casefold() for a in answers}
    currencies = Counter(a.currency for a in answers)
    n_scanned = sum(scanned)
    n_no_tax = sum(a.tax is None for a in answers)

    lines = (
        f"{_plural(counts[0], 'line item')} each"
        if counts[0] == counts[-1]
        else f"{counts[0]} to {counts[-1]} line items each, typically {median_low(counts)}"
    )
    if n_scanned == 0:
        kind = "all digital PDFs"
    elif n_scanned == n:
        kind = "all scanned paper invoices with no text layer"
    else:
        kind = (
            f"a mix of digital PDFs and scanned paper invoices with no text layer "
            f"({n_scanned} of {n} scanned)"
        )
    page_text = "one page each" if max(pages) == 1 else "one or two pages each"
    who = (
        "one business vendor" if len(vendors) == 1 else f"{len(vendors)} different business vendors"
    )
    if len(currencies) == 1:
        money = f"all in {next(iter(currencies))}"
    else:
        money = "mostly in " + ", ".join(c for c, _ in currencies.most_common())
    if n_no_tax == 0:
        tax = "Every invoice shows a tax amount."
    elif n_no_tax == n:
        tax = "None of them shows a separate tax amount."
    else:
        tax = f"{n_no_tax} of {n} show no separate tax amount."
    return (
        f"{FIELDS} real invoices we received from {who}. "
        f"They have {lines}, are {kind}, {page_text}, and are "
        f"{money}. {tax}"
    )


def build_docile_job(
    job: DocileJob,
    selected: list[str],
    *,
    jobs_dir: Path = JOBS_DIR,
    docs_dir: Path = DOCS_DIR,
    truth_dir: Path = TRUTH_DIR,
    root: Path = docile_loader.DOCILE_DIR,
    map_path: Path = docile_loader.MAP_PATH,
) -> tuple[JobPosting, list[str]]:
    """Exports the job's 15 DocILE docs, copies the first 3 as samples and returns the posting
    and the 12 held-out doc IDs. Needs data/docile/."""
    import pypdfium2 as pdfium

    from core.agents.trial_builder import has_text_layer

    exported = docile_loader.export_docs(
        job.docids(selected), docs_dir, truth_dir, root=root, map_path=map_path
    )
    scanned, pages = [], []
    for doc_id, _ in exported:
        pdf_path = Path(docs_dir) / f"{doc_id}.pdf"
        scanned.append(not has_text_layer(pdf_path))
        pdf = pdfium.PdfDocument(pdf_path)
        pages.append(len(pdf))
        pdf.close()

    samples = []
    for n, (doc_id, answer) in enumerate(exported[:N_SAMPLES], start=1):
        rel = sample_path(job.job, n)
        (jobs_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(docs_dir) / f"{doc_id}.pdf", jobs_dir / rel)
        samples.append(SampleDoc(path=rel.as_posix(), answer=answer))
    description = describe_real([a for _, a in exported], scanned, pages)
    return posting(description, samples), [doc_id for doc_id, _ in exported[N_SAMPLES:]]


def docile_entry(
    job: DocileJob,
    selected: list[str],
    heldout: list[str],
    *,
    candidates_path: Path = docile_loader.CANDIDATES_PATH,
) -> dict[str, Any]:
    ids = set(job.docids(selected))
    rows = json.loads(Path(candidates_path).read_text())
    currencies = sorted({r["currency"] for r in rows if r["docid"] in ids})
    return {
        "job": job.job,
        "type": job.type,
        "split": job.split,
        "posting": f"{job.job}.json",
        "source": "docile",
        "layout": None,
        "currency": currencies[0] if len(currencies) == 1 else currencies,
        "line_items": list(DOCILE_LINE_ITEMS),
        "profiles": None,
        "sample_seed": None,
        "heldout_seed": None,
        "heldout_doc_ids": heldout,
        "near_tie": False,
        "docile_ids": job.docids(selected),
    }


# --- files ------------------------------------------------------------------------------------


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_manifest(jobs_dir: Path = JOBS_DIR) -> dict[str, dict[str, Any]]:
    path = jobs_dir / MANIFEST
    if not path.is_file():
        return {}
    return {e["job"]: e for e in json.loads(path.read_text())["jobs"]}


def merge_job_real(existing: list[str], old: list[str], new: list[str]) -> list[str]:
    """The pool with this script's previous IDs (`old`) replaced by `new`, appended at the end;
    every other ID (e.g. the 45 from make_examples) keeps its place."""
    drop = set(old) | set(new)
    return [i for i in existing if i not in drop] + new


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m experiments.hw7.make_jobs")
    p.add_argument("--no-render", action="store_true", help="skip PDFs (answers and IDs only)")
    a = p.parse_args(argv)
    render = not a.no_render

    old_manifest = load_manifest()
    have_docile = (docile_loader.DOCILE_DIR / "annotations").is_dir()
    selected = docile_selection()
    entries: dict[str, dict[str, Any]] = {}
    for job_id in JOB_IDS:
        if job_id in SYNTH_JOBS:
            job = SYNTH_JOBS[job_id]
            post = build_synth_posting(job, jobs_dir=JOBS_DIR, render=render)
            write_json(JOBS_DIR / f"{job_id}.json", post.model_dump(mode="json"))
            heldout = build_synth_heldout(
                job, docs_dir=DOCS_DIR, truth_dir=TRUTH_DIR, render=render
            )
            entries[job_id] = synth_entry(job, heldout)
        elif have_docile:
            djob = DOCILE_JOBS[job_id]
            post, heldout = build_docile_job(
                djob, selected, jobs_dir=JOBS_DIR, docs_dir=DOCS_DIR, truth_dir=TRUTH_DIR
            )
            write_json(JOBS_DIR / f"{job_id}.json", post.model_dump(mode="json"))
            entries[job_id] = docile_entry(djob, selected, heldout)
        elif job_id in old_manifest:
            print(
                f"WARNING: {docile_loader.DOCILE_DIR}/ not found; skipping {job_id} (real "
                "DocILE invoices) and keeping its committed manifest entry.",
                file=sys.stderr,
            )
            entries[job_id] = old_manifest[job_id]
        else:
            print(
                f"WARNING: {docile_loader.DOCILE_DIR}/ not found; skipping {job_id} (real "
                "DocILE invoices). Download DocILE (see README) and rerun.",
                file=sys.stderr,
            )

    write_json(JOBS_DIR / MANIFEST, {"jobs": [entries[j] for j in JOB_IDS if j in entries]})
    job_real_path = POOLS_DIR / "job_real.json"
    existing = json.loads(job_real_path.read_text()) if job_real_path.is_file() else []
    old = [i for e in old_manifest.values() for i in e["heldout_doc_ids"]]
    new = [i for e in entries.values() for i in e["heldout_doc_ids"]]
    write_json(job_real_path, merge_job_real(existing, old, new))
    print(
        f"Wrote {len(entries)} jobs to {JOBS_DIR}/ and {len(new)} held-out docs "
        f"(appended to {job_real_path})"
    )


if __name__ == "__main__":
    main()
