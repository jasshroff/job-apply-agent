# job-apply-agent

Every morning this searches nine job boards, scores each posting against your
real resume, and rewrites that resume for the best matches — without inventing
anything you haven't actually done.

```
fetch → dedupe → cheap filters → analyse JD → prescreen → tailor loop → DOCX + PDF + brief
```

## One real number

From the first live run on this machine, against a **Software Engineer –
Full Stack Development** role at Kyndryl in Pune:

> ### 86 → 91
> The untailored resume scored 86.1. The tailored one scored **91.3** and cleared
> the bar on the first attempt. Hard-skill coverage and responsibility match both
> hit 100%; nothing was fabricated to get there.

The whole run: **70 postings found, 23 new, 2 tailored, 1 cleared 90 — in 5
minutes 6 seconds.**

The second role is the more honest illustration. A SecurityBoat internship asked
for Next.js, Express.js, Azure App Service and Azure Functions — none of which
appear anywhere in the profile. Four rewrite attempts could not manufacture them,
so the agent stopped at 78 and wrote down exactly which four requirements were
missing. That list is a study plan, not a failure.

## What it actually does

**1. Searches.** Nine sources, in parallel with your morning coffee. Of the 23
new postings in that first run: Remotive 8, RemoteOK 6, Adzuna 5, LinkedIn 3,
Greenhouse 1. Both roles worth tailoring came from Adzuna — if you're job hunting
in India, that's the source that earns its keep.

**2. Filters before it spends anything.** Title exclusions, posting age, location,
and minimum years of experience are all deterministic and free. Claude only sees
what survives. In that run, 70 postings became 23.

**3. Reads each job description** and extracts structured requirements — hard
skills weighted by importance, ATS keywords, responsibilities, seniority, hard
gates.

**4. Scores your untailored resume** against each one and ranks them. Only the
best few get the expensive treatment.

**5. Rewrites, scores, repeats** until the resume clears 90 or the attempts run
out. Then it verifies every claim against your real profile before anything is
written to disk.

**6. Writes the package** — ATS-safe DOCX and PDF, an interview brief, the score
breakdown, and the application link.

## How the score works

The score is **deterministic, not an LLM opinion**. Asking a model to grade its
own resume gives you a number that drifts between runs and can be talked upward
by a confident rewrite. Real screeners do weighted string matching, so this does
weighted string matching — the model optimises against a yardstick it cannot
argue with.

| Component | Weight | What it measures |
|---|---:|---|
| Hard skill coverage | 40% | Required skills present, weighted by how central the JD makes them |
| ATS keyword coverage | 25% | Exact keyword presence |
| Title alignment | 15% | Your headline against the target job title |
| Responsibility match | 20% | Experience bullets against what the job asks you to do |

"React" / "React.js" / "ReactJS" count as one skill. The alias table lives in
[`scoring/skills.py`](src/job_agent/scoring/skills.py) — extend it as you meet new
stacks.

## The truthfulness guard

Hitting 90 is trivial if you let the model invent a Kubernetes background. That
resume wins you an interview you then fail, and in the worst case an offer that
gets revoked. So the rewrite is fenced in.

**It will** reorder and re-prioritise, rewrite bullets in the job's vocabulary for
work you genuinely did, promote skills buried in projects or certifications up
into the skills section, drop irrelevant content, and write a headline and summary
aimed at the specific role.

**It will not** add a skill you don't evidence, change employers, titles, dates,
degrees or metrics, claim years the dates don't support, move an accomplishment
between roles, or turn "familiar with" into "expert in".

A separate verification pass then checks every claim in the draft against your
profile and repairs anything unsupported — even when that costs score. The
SecurityBoat result above is this working as intended.

## Setup

```bash
uv venv --python 3.12 && uv pip install -e .
cp .env.example .env          # then put your real key in .env, NOT in .env.example
.venv/bin/job-agent init ~/Downloads/your_resume.docx
```

`init` parses your resume into `data/profile.json` — the **fact base**. Everything
downstream is constrained to it, so a skill missing from that file can never
legitimately appear on a tailored resume. Open it once and check. The current
profile holds 45 skills across 4 roles, 3 projects and 3 certifications.

Get a key at [console.anthropic.com](https://console.anthropic.com/settings/keys).
API billing is separate from a Claude Pro subscription — Pro does not include API
credit.

## Commands

```bash
.venv/bin/job-agent run                  # the daily run
.venv/bin/job-agent run --dry-run        # search only — no Claude calls, no cost
.venv/bin/job-agent run -n 2             # tailor at most 2
.venv/bin/job-agent score <job-url>      # score + tailor one specific posting
.venv/bin/job-agent log                  # every company and role you have a resume for
.venv/bin/job-agent log --brief kyndryl  # read one interview brief
.venv/bin/job-agent status               # totals across all runs
.venv/bin/job-agent schedule --at 08:00  # run itself every morning
```

## What you get

Each run writes `output/YYYY-MM-DD/<company>_<role>/`:

| File | What it's for |
|---|---|
| `jas_shroff_<company>.pdf` / `.docx` | the tailored resume, ATS-safe single column |
| `interview_brief.md` | **read this before the interview** |
| `match_report.json` | full score breakdown |
| `APPLY_HERE.txt` | the posting link |

Plus `index.html` (the day's dashboard) and `output/applications.csv` — a ledger
across every run of which company, which role, which score, which PDF.

### The interview brief

Tailoring rewords your real work into the posting's vocabulary. That's the whole
advantage, right up until an interviewer asks about a bullet you don't remember
writing. So every resume ships with a brief containing:

- Company, role, location, seniority, and the exact PDF that was sent
- **Lead with these** — matched skills ranked by how central the posting makes
  them; your opening talking points
- **Gaps** — what the posting wanted that your profile doesn't evidence. These
  were deliberately left off rather than faked, so you aren't claiming them. The
  brief frames the honest answer for when it comes up.
- **What your resume claims** — every bullet on the outgoing PDF
- **Expect questions on** — straight from the posting's responsibilities

It costs nothing extra; it's derived from data the run already has.

## Sources

| Source | Key needed | Status |
|---|---|---|
| Remotive | no | working — 8 jobs in the first run |
| RemoteOK | no | working — 6 |
| Adzuna | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | working — 5, and **both tailored roles came from here** |
| LinkedIn | no | working — 3, via the logged-out guest search |
| Greenhouse boards | no | working — 1; list companies in `config.yaml` |
| Lever boards | no | working; list companies in `config.yaml` |
| Arbeitnow | no | working, but mostly EU-onsite — contributes little from India |
| JSearch (RapidAPI) | `RAPIDAPI_KEY` | optional aggregator |
| Naukri | no | **blocked** — the endpoint requires reCAPTCHA |

**On LinkedIn and Naukri.** These use the public endpoints the sites' own
front-ends call for signed-out visitors. Nothing logs in, nothing stores a cookie,
nothing touches your account — there is no session to ban. But they are not
sanctioned APIs, both sites' terms prohibit automated collection, and both change
without notice. Naukri now gates its search behind reCAPTCHA and returns nothing;
the adapter detects that, says so once, and moves on. Getting past a CAPTCHA is
not something this tool will do. Adzuna is the reliable India answer.

To add a company board, take the slug from its careers URL
(`job-boards.greenhouse.io/<token>`, `jobs.lever.co/<token>`) and add it under
`sources.greenhouse.boards` / `sources.lever.boards`. A wrong guess just 404s and
is skipped.

## Cost

Two things drive the bill:

- **One JD-analysis call per surviving posting.** 23 postings meant 23 calls. This
  is the volume driver.
- **Four to six calls per tailored job.** Capped by `scoring.max_tailored_per_run`.

Your master profile is byte-identical across every job in a run, so it sits behind
a prompt-cache breakpoint and bills at roughly 10% after the first call.
`--dry-run` costs nothing at all.

Current setup in `config.yaml` puts the one-time resume parse on `claude-opus-5`
(it sets the quality ceiling for everything downstream) and the rest on
`claude-haiku-4-5`. Moving `tailoring` and `verification` back to a larger model
is the single biggest quality lever, since that's where the judgment lives.

## Tuning

Everything lives in `config.yaml`.

- **Too few results?** Lower `scoring.min_prescreen_score`, widen `search.titles`,
  add locations, raise `search.max_age_days` or `max_jobs_per_source`.
- **Too much noise?** Raise `min_prescreen_score`, tighten `search.titles`, extend
  `exclude_title_keywords`.
- **Scores stuck below 90?** Usually real — read the gap list. If the same skill
  keeps appearing and you *do* have it, it's missing from `data/profile.json`; add
  it there and re-run.
- **Costs too much?** Lower `max_tailored_per_run`, raise `min_prescreen_score` to
  cut JD-analysis volume, or use a smaller model for `jd_analysis`.

## Known issues

- **The tailored resume can score below your original.** On the SecurityBoat run
  the baseline scored 82.9 and the best rewrite 77.9. The loop picks the best
  *tailored* draft but never compares it against the untailored baseline, so when
  focusing the resume costs more keyword matches than it gains, you get the worse
  document. Check `match_report.json` — if `final_score` is below `base_score`,
  send your original.
- **Naukri is blocked** behind reCAPTCHA and returns nothing.
- `sample_output/` contains synthetic data from the build, not a real run.

## What it deliberately doesn't do

It doesn't submit applications. Auto-submitting breaks most job boards' terms,
dies on CAPTCHAs, and fires unreviewed applications under your name that you can't
take back. The agent takes you to a ready-to-send package and leaves the send
button to you. The `applied` column in `applications.csv` is yours to fill in.

## Layout

```
src/job_agent/
  config.py       config.yaml + .env loading
  models.py       every schema that moves through the agent
  llm.py          Claude wrapper: structured output, caching, refusal + fallback handling
  store.py        sqlite — cross-day dedupe and history
  pipeline.py     the daily run, stage by stage
  brief.py        interview brief + applications ledger
  report.py       HTML dashboard
  cli.py          commands
  sources/        nine job-board adapters
  scoring/        skill aliases + the deterministic ATS scorer
  resume/         parser (resume → facts), tailor (the loop), renderer (docx/pdf)
```
