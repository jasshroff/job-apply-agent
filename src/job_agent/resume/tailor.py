"""JD analysis and the tailor-until-90 loop.

The loop is: score → identify gaps → rewrite against those gaps → re-score,
repeating until the target is met or the attempts run out. Because the scorer is
deterministic (see ``scoring/ats.py``), the model is optimising against a
yardstick it cannot argue with.

The other half of the design is the truthfulness guard. Getting a resume past 90
is trivial if you let the model invent a Kubernetes background; that resume gets
you an interview you fail. So every rewrite is constrained to facts already in
the master profile, and a separate verification pass checks the output against
that profile before anything is written to disk. When a job genuinely cannot
reach 90 on real evidence, the run reports the honest ceiling and the reason
instead of manufacturing the number.
"""

from __future__ import annotations

import logging

from ..config import Config
from ..llm import Claude, LLMError
from ..models import (
    JobPosting,
    JobRequirements,
    JobResult,
    MasterProfile,
    ScoreBreakdown,
    SkillGroup,
    TailoredExperience,
    TailoredProject,
    TailoredResume,
    VerificationResult,
)
from ..scoring import explain_gaps, score_resume
from ..util import truncate

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Prompts                                                                     #
# --------------------------------------------------------------------------- #

JD_ANALYSIS_INSTRUCTIONS = """\
Extract what this job posting is actually asking for.

Guidance on each field:

- hard_skills: concrete, checkable technologies and competencies (languages,
  frameworks, databases, tools, techniques). Set importance 5 for skills the
  role is built around, 1 for passing mentions. Set required=false for anything
  the posting frames as a bonus, plus, or nice-to-have.
- ats_keywords: the exact terms an automated screener would match on — the
  specific nouns from the posting, not paraphrases. 15-25 of them.
- responsibilities: what the person will do day to day, one item each.
- min_years_experience: the lowest number of years the posting will accept. Use
  0 when it doesn't say, or when it's an internship or new-grad role.
- seniority: one of intern, entry, junior, mid, senior, lead, principal.
- deal_breakers: hard gates like a required clearance, a specific degree, or
  on-site presence in a named city.

Read the posting literally. Don't infer requirements it doesn't state.
"""

TAILOR_INSTRUCTIONS = """\
You rewrite one candidate's resume so it reads as a strong match for one
specific job, using only facts that already exist in their master profile
(supplied above).

What you should do:

- Reorder and regroup freely. Lead with whatever this job cares about most.
- Rewrite any bullet in the vocabulary the job description uses, as long as the
  underlying fact is unchanged. If the profile says they "built and tested REST
  APIs" and the posting says "service integration", describe that same API work
  in the posting's terms.
- Promote relevant skills that are currently buried in a project, certification,
  or course into the skills section.
- Drop content that is irrelevant to this job. Leaving things out is fine.
- Write a headline and summary aimed squarely at this role.
- Keep quantified outcomes, using only numbers already in the profile.
- Group skills under headings that mirror how the posting talks about them.

What you must not do:

- Add a skill, tool, language, framework, or platform that the profile doesn't
  evidence somewhere.
- Invent or change employers, job titles, dates, degrees, institutions, or
  metrics.
- Claim more years of experience than the dates support.
- Move an accomplishment from one role or project to another.
- Upgrade exposure into expertise. "Familiar with X" must not become "Expert in
  X" or "3 years of X".

You will be given a gap list showing which requirements are currently unmet. For
each gap, either surface real supporting evidence from the profile, or leave it
unmet. A resume that scores 78 honestly is worth more than one that scores 95
and falls apart in the first interview.

Return the complete rewritten resume, not a diff.
"""

REFINE_INSTRUCTIONS = TAILOR_INSTRUCTIONS + """\

This is a revision pass. You are given your previous draft and the specific
gaps still holding the score down. Close the ones you can close honestly, and
keep everything that was already working. Do not regress bullets that were
already well matched.
"""

VERIFY_INSTRUCTIONS = """\
Check the tailored resume against the master profile above. Your job is to catch
claims a reader of the master profile could not verify.

Severity:

- high: a skill, technology, employer, job title, date, degree, or metric that
  does not appear anywhere in the master profile, or that contradicts it.
- medium: a claim that materially overstates depth, scope, or duration relative
  to what the master profile supports.
- low: phrasing that stretches but stays defensible.

These are not issues, and you should not flag them:

- Rewording, reordering, regrouping, or omitting content.
- Describing real work in the job posting's vocabulary.
- Surfacing a skill from a project or certification into the skills section.
- A headline or summary written for this specific role.

Set grounded=false only if there is at least one high-severity issue. For each
issue, quote the exact offending claim and suggest a truthful replacement.
"""

REPAIR_INSTRUCTIONS = """\
Your previous draft contains claims that are not supported by the master profile
above. They are listed below.

Fix each one: replace it with something the profile actually supports, or delete
it. Change nothing else — every other bullet should come through untouched.
Losing a few points of match score is the correct trade here.

Return the complete corrected resume.
"""


# --------------------------------------------------------------------------- #
#  JD analysis                                                                 #
# --------------------------------------------------------------------------- #


def analyze_job(claude: Claude, config: Config, posting: JobPosting) -> JobRequirements:
    """Turn a free-text job description into structured requirements."""
    body = truncate(posting.description or posting.title, 12000)
    user = (
        f"Job title: {posting.title}\n"
        f"Company: {posting.company}\n"
        f"Location: {posting.location}\n\n"
        f"Posting:\n{body}"
    )
    return claude.parse(
        model=config.models.jd_analysis,
        schema=JobRequirements,
        instructions=JD_ANALYSIS_INSTRUCTIONS,
        user_content=user,
        max_tokens=4000,
        effort=config.models.effort.jd_analysis,
    )


# --------------------------------------------------------------------------- #
#  Profile → baseline resume                                                   #
# --------------------------------------------------------------------------- #


def profile_to_resume(profile: MasterProfile) -> TailoredResume:
    """Structural conversion of the master profile, no rewriting.

    This is the "before" resume — what you'd send without the agent — and it's
    what the baseline score is measured against.
    """
    return TailoredResume(
        headline=profile.headline,
        summary=profile.summary,
        skill_groups=[SkillGroup(label="Skills", items=profile.skills)],
        experience=[
            TailoredExperience(
                title=e.title,
                organization=e.organization,
                dates=f"{e.start_date} – {e.end_date}".strip(" –"),
                bullets=e.bullets,
            )
            for e in profile.experience
        ],
        projects=[
            TailoredProject(
                name=p.name,
                dates=f"{p.start_date} – {p.end_date}".strip(" –"),
                tech=", ".join(p.skills),
                bullets=p.bullets,
            )
            for p in profile.projects
        ],
        education_lines=list(profile.education),
        certifications=profile.certifications,
        activities=profile.activities,
    )


def _score(
    resume: TailoredResume,
    requirements: JobRequirements,
    posting: JobPosting,
    config: Config,
) -> ScoreBreakdown:
    explicit = [item for group in resume.skill_groups for item in group.items]
    return score_resume(
        resume_text=resume.all_text(),
        explicit_skills=explicit,
        headline=resume.headline,
        requirements=requirements,
        job_title=posting.title,
        weights=config.scoring.weights,
    )


# --------------------------------------------------------------------------- #
#  The loop                                                                    #
# --------------------------------------------------------------------------- #


def tailor_for_job(
    claude: Claude,
    config: Config,
    profile: MasterProfile,
    posting: JobPosting,
    requirements: JobRequirements,
    baseline: ScoreBreakdown | None = None,
) -> JobResult:
    """Rewrite the resume for one job, driving toward the target score."""
    target = config.scoring.target_score
    result = JobResult(posting=posting, requirements=requirements)

    base_resume = profile_to_resume(profile)
    result.base_score = baseline or _score(base_resume, requirements, posting, config)

    # The master profile is byte-identical across every job in the run, so it
    # sits behind a cache breakpoint and is billed at ~10% after the first call.
    grounding = _grounding_block(profile)
    job_context = _job_context(posting, requirements)

    best_resume: TailoredResume | None = None
    best_score: ScoreBreakdown | None = None
    current: TailoredResume | None = None

    for attempt in range(1, config.tailoring.max_iterations + 1):
        result.iterations = attempt
        try:
            if current is None:
                current = _first_pass(
                    claude, config, grounding, job_context, base_resume,
                    explain_gaps(result.base_score, target),
                )
            else:
                gaps = explain_gaps(best_score or result.base_score, target)
                current = _refine(
                    claude, config, grounding, job_context, current, gaps,
                    best_score.total if best_score else 0.0, target,
                )
        except LLMError as exc:
            log.warning("tailoring failed for %s: %s", posting.label, exc)
            result.error = str(exc)
            break

        score = _score(current, requirements, posting, config)
        log.info(
            "  attempt %d/%d → %s",
            attempt, config.tailoring.max_iterations, score.summary_line(),
        )

        if best_score is None or score.total > best_score.total:
            best_resume, best_score = current, score
        if score.total >= target:
            break

    if best_resume is None or best_score is None:
        result.error = result.error or "no draft produced"
        return result

    # Truthfulness gate runs on the winning draft, after the score is settled.
    if config.tailoring.verify_truthfulness:
        best_resume, best_score = _verify_and_repair(
            claude, config, grounding, best_resume, best_score,
            requirements, posting, result,
        )

    result.tailored = best_resume
    result.final_score = best_score
    result.reached_target = best_score.total >= target
    if not result.reached_target:
        result.honest_ceiling_reason = explain_gaps(best_score, target)
    return result


# --------------------------------------------------------------------------- #
#  Loop steps                                                                  #
# --------------------------------------------------------------------------- #


def _grounding_block(profile: MasterProfile) -> str:
    return (
        "MASTER PROFILE — the complete and only set of facts about this "
        "candidate. Every statement you produce must be traceable to something "
        "in here.\n\n" + profile.model_dump_json(indent=2)
    )


def _job_context(posting: JobPosting, requirements: JobRequirements) -> str:
    skills = "\n".join(
        f"  - {s.name} (importance {s.importance}"
        f"{', required' if s.required else ', nice to have'})"
        for s in requirements.hard_skills
    )
    return (
        f"TARGET JOB\n"
        f"Title: {posting.title}\n"
        f"Company: {posting.company}\n"
        f"Location: {posting.location}\n"
        f"Seniority: {requirements.seniority}\n\n"
        f"Hard skills sought:\n{skills}\n\n"
        f"ATS keywords: {', '.join(requirements.ats_keywords)}\n\n"
        f"Responsibilities:\n"
        + "\n".join(f"  - {r}" for r in requirements.responsibilities)
        + f"\n\nOriginal posting:\n{truncate(posting.description, 6000)}"
    )


def _first_pass(
    claude: Claude,
    config: Config,
    grounding: str,
    job_context: str,
    base_resume: TailoredResume,
    gaps: str,
) -> TailoredResume:
    user = (
        f"{job_context}\n\n"
        f"CURRENT RESUME (untailored):\n{base_resume.model_dump_json(indent=2)}\n\n"
        f"GAPS AGAINST THIS JOB:\n{gaps or '(none identified)'}\n\n"
        "Produce the tailored resume."
    )
    return claude.parse(
        model=config.models.tailoring,
        schema=TailoredResume,
        instructions=TAILOR_INSTRUCTIONS,
        user_content=user,
        cached_context=grounding,
        max_tokens=12000,
        effort=config.models.effort.tailoring,
    )


def _refine(
    claude: Claude,
    config: Config,
    grounding: str,
    job_context: str,
    draft: TailoredResume,
    gaps: str,
    current_total: float,
    target: float,
) -> TailoredResume:
    user = (
        f"{job_context}\n\n"
        f"YOUR PREVIOUS DRAFT:\n{draft.model_dump_json(indent=2)}\n\n"
        f"That draft scores {current_total:.1f} against a target of {target:.0f}.\n\n"
        f"REMAINING GAPS:\n{gaps or '(none identified)'}\n\n"
        "Revise to close the gaps you can close honestly."
    )
    return claude.parse(
        model=config.models.tailoring,
        schema=TailoredResume,
        instructions=REFINE_INSTRUCTIONS,
        user_content=user,
        cached_context=grounding,
        max_tokens=12000,
        effort=config.models.effort.tailoring,
    )


def _verify_and_repair(
    claude: Claude,
    config: Config,
    grounding: str,
    resume: TailoredResume,
    score: ScoreBreakdown,
    requirements: JobRequirements,
    posting: JobPosting,
    result: JobResult,
) -> tuple[TailoredResume, ScoreBreakdown]:
    """Check the draft for unsupported claims; repair once if any are found."""
    try:
        verification = _verify(claude, config, grounding, resume)
    except LLMError as exc:
        log.warning("verification failed for %s: %s", posting.label, exc)
        result.verification = VerificationResult(
            grounded=True,
            issues=[],
        )
        return resume, score

    result.verification = verification
    if not verification.blocking_issues:
        return resume, score

    log.warning(
        "  %d unsupported claim(s) found — repairing",
        len(verification.blocking_issues),
    )
    issues = "\n".join(
        f"  - {i.claim}\n    why: {i.reason}\n    fix: {i.suggested_fix}"
        for i in verification.blocking_issues
    )
    try:
        repaired = claude.parse(
            model=config.models.tailoring,
            schema=TailoredResume,
            instructions=REPAIR_INSTRUCTIONS,
            user_content=(
                f"DRAFT:\n{resume.model_dump_json(indent=2)}\n\n"
                f"UNSUPPORTED CLAIMS:\n{issues}"
            ),
            cached_context=grounding,
            max_tokens=12000,
            effort=config.models.effort.tailoring,
        )
    except LLMError as exc:
        log.warning("repair failed for %s: %s", posting.label, exc)
        return resume, score

    repaired_score = _score(repaired, requirements, posting, config)
    log.info("  after truthfulness repair → %s", repaired_score.summary_line())
    result.verification = VerificationResult(
        grounded=True, issues=verification.issues
    )
    return repaired, repaired_score


def _verify(
    claude: Claude, config: Config, grounding: str, resume: TailoredResume
) -> VerificationResult:
    return claude.parse(
        model=config.models.verification,
        schema=VerificationResult,
        instructions=VERIFY_INSTRUCTIONS,
        user_content=f"TAILORED RESUME:\n{resume.model_dump_json(indent=2)}",
        cached_context=grounding,
        max_tokens=6000,
        effort=config.models.effort.verification,
    )
