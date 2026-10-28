"""Regenerates the 4 example job postings (AGE-25, plan §9.0, §10.2) from fixed seeds.

    uv run python -m examples.make_examples

Writes examples/{name}.json, the requester samples at examples/samples/{name}/sample_{n}.pdf
(committed; small), and for each invoice posting 15 "real job" documents into data/docs +
data/truth (gitignored) listed in the pool manifest data/pools/job_real.json (committed), so
the Trial Builder (AGE-22) can prove it never reads them. Output is byte-identical on rerun.
"""

import argparse
import json
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from contracts import INVOICE_JSON_SCHEMA, JobPosting, SampleDoc
from data.synth import GeneratedDoc, generate

EXAMPLES_DIR = Path(__file__).parent
POOLS_DIR = Path("data/pools")
REQUESTER_ID = "req-demo-01"
N_SAMPLES = 3
N_REAL_DOCS = 15

BUDGETS = {
    "n_real_docs": N_REAL_DOCS,
    "job_budget_usd": 5.00,  # ≈ $0.33/doc, above every card price, so all 7 pass the filter
    "trial_budget_usd": 0.50,
    "deadline": None,
    "deadline_open_ack": True,
    "region": None,
}

INVOICE_OUTPUT = (
    "One JSON object per invoice, matching output_schema: dates as YYYY-MM-DD, currency as an "
    "ISO 4217 code, amounts as plain numbers. Use null for an invoice number, subtotal or tax "
    "that isn't printed."
)


@dataclass(frozen=True)
class InvoiceExample:
    name: str
    description: str
    layout: str
    line_items: tuple[int, int]
    sample_seed: int
    real_seed: int
    # (with_tax, scanned) per document, used in turn across the samples and the real-job docs
    profiles: tuple[tuple[bool, bool], ...] = ((True, False),)

    def generate(self, *, n: int, seed: int, **kw) -> list[GeneratedDoc]:
        counts = Counter(self.profiles[i % len(self.profiles)] for i in range(n))
        return [
            doc
            for (with_tax, scanned), k in counts.items()
            for doc in generate(
                layout=self.layout,
                n=k,
                seed=seed,
                line_items=self.line_items,
                with_tax=with_tax,
                scanned=scanned,
                **kw,
            )
        ]


INVOICE_EXAMPLES = [
    InvoiceExample(
        name="invoice_clean",
        description=(
            "Extract the vendor, invoice number, invoice date, currency, line items, subtotal, "
            "tax and total from our office-supply invoices. They are clean digital PDFs with "
            "short tables of a few lines each."
        ),
        layout="classic",
        line_items=(2, 5),
        sample_seed=2501,
        real_seed=2511,
    ),
    InvoiceExample(
        name="invoice_long",
        description=(
            "Extract the vendor, invoice number, invoice date, currency, line items, subtotal, "
            "tax and total from our IT equipment and services invoices. They are digital PDFs "
            "with long line-item tables (15 to 30 rows) that often run onto a second page; "
            "every line item must be captured."
        ),
        layout="modern",
        line_items=(15, 30),
        sample_seed=2502,
        real_seed=2512,
    ),
    InvoiceExample(
        name="invoice_mixed",
        description=(
            "Extract the vendor, invoice number, invoice date, currency, line items, subtotal, "
            "tax and total from invoices our small US suppliers send us. Some arrive as paper "
            "invoices we scan, others as PDFs, and many suppliers don't charge sales tax, so "
            "there is often no tax line."
        ),
        layout="compact",
        line_items=(3, 12),
        sample_seed=2503,
        real_seed=2513,
        profiles=((True, True), (False, False), (False, True)),
    ),
]


def build_invoice_posting(
    ex: InvoiceExample, *, examples_dir: Path = EXAMPLES_DIR, render: bool = True
) -> JobPosting:
    """Generates the posting's samples into examples_dir/samples/{name}/ (PDFs only if render)
    and returns the posting, with each sample's answer inline."""
    samples_dir = examples_dir / "samples" / ex.name
    samples = []
    with tempfile.TemporaryDirectory() as tmp:
        docs = ex.generate(
            n=N_SAMPLES,
            seed=ex.sample_seed,
            render=render,
            docs_dir=Path(tmp) / "docs",
            truth_dir=Path(tmp) / "truth",
        )
        for n, doc in enumerate(docs, start=1):
            rel = Path("samples") / ex.name / f"sample_{n}.pdf"
            if doc.pdf_path is not None:
                samples_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(doc.pdf_path, examples_dir / rel)
            samples.append(SampleDoc(path=rel.as_posix(), answer=doc.truth))
    return JobPosting(
        requester_id=REQUESTER_ID,
        description=ex.description,
        output_description=INVOICE_OUTPUT,
        output_schema=INVOICE_JSON_SCHEMA,
        samples=samples,
        **BUDGETS,
    )


def build_reject_posting() -> JobPosting:
    """A job with no single right answer: it passes the deterministic pre-checks (valid budgets,
    an output schema) and must be rejected by the Job Reviewer's LLM step (AGE-13)."""
    return JobPosting(
        requester_id=REQUESTER_ID,
        description=(
            "Write a persuasive cover letter for a senior product manager role at a climate-tech "
            "startup. It should make a hiring manager want to interview me."
        ),
        output_description="A one-page cover letter of 300 to 400 words, warm but professional.",
        output_schema={"type": "string"},
        samples=[],
        **BUDGETS,
    )


def build_job_real(
    ex: InvoiceExample,
    *,
    docs_dir: Path = Path("data/docs"),
    truth_dir: Path = Path("data/truth"),
    render: bool = True,
) -> list[str]:
    """The posting's held-out "real job" documents (pool job_real, plan §5.3). Returns doc IDs."""
    docs = ex.generate(
        n=N_REAL_DOCS,
        seed=ex.real_seed,
        render=render,
        docs_dir=docs_dir,
        truth_dir=truth_dir,
    )
    return [d.doc_id for d in docs]


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m examples.make_examples")
    p.add_argument("--no-render", action="store_true", help="skip PDFs (answers and IDs only)")
    a = p.parse_args(argv)
    render = not a.no_render

    for ex in INVOICE_EXAMPLES:
        posting = build_invoice_posting(ex, render=render)
        write_json(EXAMPLES_DIR / f"{ex.name}.json", posting.model_dump(mode="json"))
    write_json(
        EXAMPLES_DIR / "reject_unscorable.json", build_reject_posting().model_dump(mode="json")
    )

    job_real = [i for ex in INVOICE_EXAMPLES for i in build_job_real(ex, render=render)]
    write_json(POOLS_DIR / "job_real.json", job_real)
    print(
        f"Wrote {len(INVOICE_EXAMPLES) + 1} postings to {EXAMPLES_DIR}/ and {len(job_real)} "
        f"job_real docs ({POOLS_DIR / 'job_real.json'})"
    )


if __name__ == "__main__":
    main()
