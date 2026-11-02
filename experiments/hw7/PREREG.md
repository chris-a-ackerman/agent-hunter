# HW7 pre-registration: Trial-then-Hire vs. profile-reading

Written and merged **before the first real benchmark run** (AGE-37), so the success rules can't move
after we see results. Drafted by Chris, approved by Nathalie (AGE-33). Any later change to this file
must be a separate, dated PR that says why, and the write-up reports it as a deviation.

## 1. Claim

An agent that uses Trial-then-Hire picks a better invoice-extraction provider than an agent that picks
by reading the providers' public profiles (agent cards).

## 2. Cases

12 jobs, defined in `experiments/hw7/jobs/manifest.json` (AGE-34).

| Jobs | Type | Split |
| -- | -- | -- |
| j01 | clean digital, short tables (1–8 rows), synthetic | tuning |
| j02 | clean, short tables (1–8 rows), **real DocILE invoices** | tuning |
| j03, j04 | scanned (image-only PDF) | tuning |
| j05, j06 | long tables (15+ rows) | tuning |
| j07, j08 | non-USD (EUR / GBP) | tuning |
| j09 · j10 · j11 · j12 | one of each type, same order; j09 uses **real DocILE invoices** like j02 | fresh |

- Type 4 is **non-USD only**. The generator has English templates only, so non-English invoices are
  out of scope (reported as a limitation).
- **Real invoices (DocILE) in j02 and j09.**
  - Both the 3 requester samples and the 12 held-out invoices are real business invoices from DocILE
    (AGE-26, PR #27).
  - They are chosen only by `data.docile_loader.select_for_job(1, 8, seed=1102, n=30)`, applied to
    `data/pools/docile_candidates.json` as merged in PR #27: the 103 of 5,680 docs whose answer keys
    passed our quality filter. The first 15 go to j02 and the next 15 to j09; in each job, the first 3
    are samples and the last 12 held-out. Nobody picks documents by hand.
  - The trial's other 9 invoices are synthetic, made by the Trial Builder to match the samples.
  - Every other job stays synthetic. Only 12 DocILE docs with 15+ line items passed the filter, and
    102 of the 103 are USD.
- Hard cases named in advance: long tables (5/42 timeouts at integration), scanned, and one near-tie
  job (flagged `near_tie: true` in the manifest).
- Fresh jobs j09–j12 are not run or looked at until the AGE-39 change is merged.

## 3. Ground truth ("real accuracy"), independent of the trial

- Each job has 12 held-out invoices, listed in `data/pools/job_real.json` so the Trial Builder can
  never read them.
- All 7 providers process them through their normal `POST /p/{slug}/tasks` endpoint, via the same
  Trial Runner, requester ID and pacing as a real job. They are sent as **a separate batch after the
  trial**, like a real hire.
- Graded by the real Scorer with the job's saved rubric (§4). A timeout or invalid output scores 0.
- A provider's **real accuracy** = its mean held-out document score. **Best in hindsight** = the highest
  real accuracy for that job.

## 4. Rubric

The Rubric Builder runs **once per job**. The result is saved to `experiments/hw7/rubrics/{job}.json`
and reused for every trial score, every repeat, every method and the held-out scoring.

## 5. Methods compared

1. **Trial-then-Hire as shipped (6 invoices).** The harness runs a 12-invoice trial. The 6-invoice
   result is computed from subsets that keep the 3 requester samples plus 3 of the 9 synthetic
   invoices (200 seeded draws), re-ranked with the real Ranker. This matches the shipped trial's mix
   (3 samples + 3 synthetic).
2. **Profile-reading AI** (AGE-35), run 5× per job; its per-job score is the mean of the 5 runs.
3. **Cheapest:** lowest `price_per_doc_usd` on the card, ties broken by slug.
4. **Random:** the expected value over all 7 providers (the mean of their selection qualities), not
   one draw.

## 6. Main metric: selection quality (SQ)

For each job:

```
SQ = real accuracy of the picked provider / real accuracy of the best provider
```

Averaged over jobs; 1.0 means the method always picked the best.

- Trial-then-Hire's pick is `Verdict.recommended`. On a tie, that is the tie-break winner.
- `insufficient_candidates` (no recommendation) scores 0 and counts as a bad pick.
- For Trial-then-Hire, per-job SQ = the mean over both trial repeats and all 200 subset draws.

## 7. Pass rule

Judged on **all 12 jobs**, as-shipped system: the tuning round plus the fresh jobs' "before" results.
The claim is **supported** if both hold:

1. Trial-then-Hire's mean SQ is **≥ 0.90**.
2. Trial-then-Hire beats profile-reading by **≥ 0.05 mean SQ**, and the **95% CI of that difference
   excludes 0**. The CI is a paired bootstrap over jobs of the per-job difference
   (Trial-then-Hire − profile-reading), 10,000 resamples, seed 0.

Otherwise we report the claim as **not supported**. Negative results still earn full credit if
analysed honestly. **Random's expected SQ is always shown next to the 0.90 line**, so readers can see how hard
the bar really is.

## 8. Secondary metrics (always reported)

- **Hit rate:** the share of jobs where the pick's real accuracy is ≥ the best provider's − 0.02.
- **Bad picks:** jobs with SQ < 0.80.
- **Cost per correct invoice** = (selection cost + the hired provider's actual `cost_usd` on the 12
  held-out invoices) ÷ the number of held-out invoices it scored ≥ 0.90 on.
  - Selection cost for Trial-then-Hire = the trial's provider `cost_usd` for the invoices in the trial
    size being reported, plus the platform model cost for that job.
  - For profile-reading it is that call's model cost. For cheapest and random it is $0.
- **Time to hire:**
  - Trial-then-Hire: wall-clock from review start to verdict, measured on the 12-invoice run. Labelled
    as an **upper bound** for the 6-invoice trial.
  - Profile-reading: the model call's latency. Cheapest and random: 0.

## 9. Real vs. synthetic (descriptive only)

SQ on the real-invoice jobs (j02, j09) is reported next to the synthetic jobs, especially the synthetic
clean job j01. With 2 real jobs there is no significance test.

## 10. Repetitions and noise

- The trial step runs **twice per job** (repeats r1 and r2, on tuning and fresh jobs). The repeats use
  different job IDs, so the synthetic trial invoices differ between them.
- Profile-reading runs 5× per job. The held-out pile runs once.

## 11. Limit we'll probe: trial size 3 / 6 / 12

Derived from the 12-invoice runs:

- **3** = the 3 requester samples only (what the Trial Builder ships at size 3).
- **6** = 3 samples + 3 synthetic (200 draws).
- **12** = everything.

## 12. Frozen inputs

Provider profiles (agent cards) are frozen at `agent-hunter-providers` commit `5d9536a`. The write-up
says our team wrote them.

## 13. Fairness control

Chris knows the providers' hidden traits, so he doesn't edit selection logic or the analysis. Any
selection-side change is Nathalie's.
