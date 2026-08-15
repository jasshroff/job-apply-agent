# job-apply-agent

A daily agent that searches nine job sources, scores each posting against your
resume, and rewrites your resume for the strongest matches until it clears a 90+
ATS match score — without inventing anything you haven't actually done.

```
fetch  →  dedupe  →  cheap filters  →  analyse JD  →  prescreen  →  tailor loop  →  DOCX + PDF + report
```

---

## Setup

```bash
uv venv --python 3.12 && uv pip install -e .
cp .env.example .env          # then add your ANTHROPIC_API_KEY
.venv/bin/job-agent init ~/Downloads/your_resume.docx
```

`init` copies your resume to `data/` and parses it into `data/profile.json` —
the **fact base**. Everything the agent later writes must trace back to that
file. Open it once and check nothing was missed; a skill that isn't in there is
a skill your tailored resumes can never legitimately claim.

Get an API key at [console.anthropic.com](https://console.anthropic.com/settings/keys).

## Daily use

```bash
.venv/bin/job-agent run                  # the daily run
.venv/bin/job-agent run --dry-run        # search only, no Claude calls, no cost
.venv/bin/job-agent run -n 3             # tailor at most 3 jobs
.venv/bin/job-agent score <job-url>      # score + tailor against one posting
.venv/bin/job-agent log                  # every company + role you have a resume for
.venv/bin/job-agent log --brief postman  # read the prep brief for one company
.venv/bin/job-agent status               # what it has done across all runs
.venv/bin/job-agent schedule --at 08:00  # run automatically every morning
```

Each run writes `output/YYYY-MM-DD/` with one folder per job:

| File | What it's for |
|---|---|
| `<name>_<company>.pdf` / `.docx` | the tailored resume, ATS-safe |
| `interview_brief.md` | **prep sheet — read this before the interview** |
| `match_report.json` | full score breakdown |
| `APPLY_HERE.txt` | the posting link |

Plus `index.html` (the day's dashboard) and `output/applications.csv` — a running
ledger across every run of which company, which role, which score, and which PDF.

## The interview brief

Tailoring rewords your real work in the posting's vocabulary. That's an
advantage right up until an interviewer asks about a bullet you don't remember
writing. So every tailored resume ships with an `interview_brief.md`:

- **Company, role, location, seniority, and the exact PDF that was sent**
- **Lead with these** — matched skills ranked by how central the posting says
  they are; your opening talking points
- **Gaps** — what the posting wanted that your profile doesn't evidence, which
  were deliberately left off rather than faked, with framing for the honest
  answer if it comes up
- **What your resume claims** — every bullet on the outgoing PDF, so nothing you
  asserted is a surprise to you
- **Expect questions on** — straight from the posting's responsibilities

It's generated from data the run already has, so it costs nothing extra.

`job-agent log` lists everything you've prepared. The `applied` column in
`applications.csv` is yours to fill in — the agent tracks what it prepared, you
track what you actually sent.

## How the score works

The score is **deterministic**, not an LLM opinion. Asking a model to grade its
own resume gives you a number that drifts between runs and can be talked upward
by a confident rewrite. Real screeners do weighted string matching, so this does
weighted string matching — the model optimises against a yardstick it can't
argue with.

| Component | Weight | What it measures |
|---|---|---|
| Hard skill coverage | 40% | Required skills present, weighted by how central they are to the JD |
| ATS keyword coverage | 25% | Exact keyword presence |
| Title alignment | 15% | Your headline vs. the target job title |
| Responsibility match | 20% | Experience bullets vs. what the job asks you to do |

"React" / "React.js" / "ReactJS" count as one skill (`scoring/skills.py` holds
the alias table — extend it as you meet new stacks).

## The truthfulness guard

Hitting 90 is trivial if you let the model invent a Kubernetes background. That
resume gets you an interview you fail, and in the worst case an offer that's
revoked. So the tailoring loop is constrained to facts already in your profile:

**It will** reorder and re-prioritise, rewrite bullets in the JD's vocabulary
for work you actually did, promote skills buried in projects or certifications
up into the skills section, drop irrelevant content, and write a headline and
summary aimed at the specific role.

**It will not** add a skill you don't evidence, change employers, titles, dates,
degrees or metrics, claim years the dates don't support, move an accomplishment
between roles, or turn "familiar with X" into "expert in X".

A separate verification pass then checks every claim in the draft against your
profile before anything is written to disk, and repairs anything unsupported.
When a job genuinely can't reach 90 on real evidence, the run reports the honest
ceiling and *why* — that gap list is a study plan, not a failure.

In verification, this behaved exactly as intended: a Postman internship went
**83 → 91**, with keyword coverage 77→92 and title fit 74→100, while hard-skill
coverage stayed flat at 86 because Kubernetes and Go were genuinely missing and
were correctly left alone.

## Sources

| Source | Key needed | Status |
|---|---|---|
| Remotive | no | working |
| RemoteOK | no | working |
| Arbeitnow | no | working |
| Greenhouse company boards | no | working — list companies in `config.yaml` |
| Lever company boards | no | working — list companies in `config.yaml` |
| Adzuna | `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | best India/Pune coverage, free ~250/day |
| JSearch (RapidAPI) | `RAPIDAPI_KEY` | aggregates LinkedIn/Indeed/Glassdoor, free ~200/mo |
| LinkedIn | no | working via the logged-out guest search |
| Naukri | no | **blocked** — the endpoint requires reCAPTCHA |

**On LinkedIn and Naukri specifically.** These use the public endpoints the
sites' own front-ends call for signed-out visitors. Nothing logs in, nothing
stores a cookie, nothing touches your account — there is no session to ban. But
they are not sanctioned APIs, both sites' terms prohibit automated collection,
and both change without notice. LinkedIn currently returns ~40 jobs per run.
Naukri now gates its search endpoint behind reCAPTCHA and returns nothing; the
adapter detects that, says so once, and moves on. Getting past a CAPTCHA is not
something this tool will do. **Adzuna is the reliable India-coverage answer** —
it's an official API and a five-minute signup.

To add a company board, take the slug from its careers URL
(`job-boards.greenhouse.io/<token>`, `jobs.lever.co/<token>`) and add it under
`sources.greenhouse.boards` / `sources.lever.boards`. A wrong guess just 404s
and is skipped.

## Cost

Roughly 4–6 Claude calls per tailored job, plus one per posting that clears the
cheap filters. Your master profile is identical across every job in a run, so it
sits behind a prompt-cache breakpoint and is billed at ~10% after the first
call. `--dry-run` costs nothing. `max_tailored_per_run` in `config.yaml` is the
spend dial.

## Tuning

Everything lives in `config.yaml`:

- **Not enough results?** Lower `scoring.min_prescreen_score`, widen
  `search.titles`, add locations, raise `search.max_age_days`.
- **Too many irrelevant results?** Raise `min_prescreen_score`, tighten
  `search.titles`, extend `exclude_title_keywords`.
- **Scores plateau below 90?** That's usually real — read the gap list in the
  report. If the same skill keeps appearing and you *do* have it, it's missing
  from `data/profile.json`; add it and re-run.
- **Costs too much?** Drop `models.jd_analysis` and `models.verification` to
  `claude-sonnet-5`, and lower `max_tailored_per_run`.

## What it deliberately doesn't do

It doesn't submit applications. Auto-submitting breaks most job boards' terms,
dies on CAPTCHAs, and fires off unreviewed applications under your name that you
can't take back. The agent takes you to a ready-to-send package and leaves the
send button to you.

## Layout

```
src/job_agent/
  config.py         config.yaml + .env loading
  models.py         every schema that moves through the agent
  llm.py            Claude wrapper: structured output, caching, refusal handling
  store.py          sqlite — cross-day dedupe and history
  pipeline.py       the daily run, stage by stage
  report.py         HTML digest
  cli.py            commands
  sources/          nine job-board adapters
  scoring/          skill aliases + the deterministic ATS scorer
  resume/           parser (resume → facts), tailor (the loop), renderer (docx/pdf)
```
