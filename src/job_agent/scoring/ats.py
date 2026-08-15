"""The ATS scorer.

This is deliberately **not** an LLM judge. Asking a model "score this resume out
of 100" gives you a number that drifts between calls and can be talked upward by
a confident rewrite. Real screeners do weighted string matching, so this does
weighted string matching — same inputs always produce the same number, and the
tailoring loop optimises against something that can't flatter itself.

Four components, weighted in config.yaml:

  hard_skill_coverage  — required skills present, weighted by JD importance
  keyword_coverage     — ATS keyword presence
  title_alignment      — resume headline vs. the job title
  responsibility_match — experience bullets vs. what the job asks you to do
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from ..config import ScoringWeights
from ..models import JobRequirements, ScoreBreakdown
from .skills import SkillMatcher

_STOPWORDS = {
    "and", "or", "the", "a", "an", "to", "of", "in", "on", "for", "with", "by",
    "from", "at", "as", "is", "are", "be", "will", "you", "your", "we", "our",
    "that", "this", "it", "using", "use", "used", "work", "working", "across",
    "including", "etc", "strong", "good", "excellent", "ability", "experience",
}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9+#.]{2,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def score_resume(
    resume_text: str,
    explicit_skills: list[str],
    headline: str,
    requirements: JobRequirements,
    job_title: str,
    weights: ScoringWeights,
) -> ScoreBreakdown:
    """Score one resume against one job. Pure function, no I/O."""
    matcher = SkillMatcher(resume_text, explicit_skills)

    hard_pct, matched_skills, missing_skills = _hard_skills(matcher, requirements)
    kw_pct, matched_kw, missing_kw = _keywords(matcher, requirements)
    title_pct = _title(headline, job_title, requirements)
    resp_pct = _responsibilities(resume_text, requirements)

    total = (
        hard_pct * weights.hard_skill_coverage
        + kw_pct * weights.keyword_coverage
        + title_pct * weights.title_alignment
        + resp_pct * weights.responsibility_match
    )

    return ScoreBreakdown(
        hard_skill_coverage=round(hard_pct, 1),
        keyword_coverage=round(kw_pct, 1),
        title_alignment=round(title_pct, 1),
        responsibility_match=round(resp_pct, 1),
        total=round(total, 1),
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        matched_keywords=matched_kw,
        missing_keywords=missing_kw,
    )


# --------------------------------------------------------------------------- #
#  Components                                                                  #
# --------------------------------------------------------------------------- #


def _hard_skills(
    matcher: SkillMatcher, requirements: JobRequirements
) -> tuple[float, list[str], list[str]]:
    """Importance-weighted coverage of the JD's hard skills.

    Nice-to-haves count for less than must-haves, so missing one Kubernetes
    mention doesn't sink a role that's really about React.
    """
    skills = requirements.hard_skills
    if not skills:
        return 100.0, [], []

    earned = possible = 0.0
    matched: list[str] = []
    missing: list[str] = []

    for skill in skills:
        weight = max(1, min(5, skill.importance))
        if skill.required:
            weight *= 1.5
        possible += weight
        if matcher.has(skill.name):
            earned += weight
            matched.append(skill.name)
        else:
            missing.append(skill.name)

    pct = (earned / possible * 100) if possible else 100.0
    return pct, matched, missing


def _keywords(
    matcher: SkillMatcher, requirements: JobRequirements
) -> tuple[float, list[str], list[str]]:
    keywords = requirements.ats_keywords
    if not keywords:
        return 100.0, [], []
    matched, missing = matcher.present_and_missing(keywords)
    return len(matched) / len(keywords) * 100, matched, missing


def _title(headline: str, job_title: str, requirements: JobRequirements) -> float:
    """How close the resume's headline reads to the target title.

    Uses token-set ratio so word order and extra qualifiers don't matter:
    "Full Stack Developer" vs "Developer, Full-Stack" should score high.
    """
    target = requirements.role_title or job_title
    if not target:
        return 100.0
    if not headline:
        return 0.0
    direct = fuzz.token_set_ratio(headline.lower(), target.lower())
    # Credit a partial hit on the head noun ("Engineer" in "Engineer, Platform").
    partial = fuzz.partial_ratio(headline.lower(), target.lower())
    return float(max(direct, partial * 0.85))


def _responsibilities(resume_text: str, requirements: JobRequirements) -> float:
    """Fraction of JD responsibilities with real lexical support in the resume.

    Token overlap rather than embeddings: it's explainable, has no extra
    dependency, and correlates well enough with what keyword screeners reward.
    """
    responsibilities = requirements.responsibilities
    if not responsibilities:
        return 100.0

    resume_tokens = _tokens(resume_text)
    if not resume_tokens:
        return 0.0

    covered = 0.0
    for item in responsibilities:
        item_tokens = _tokens(item)
        if not item_tokens:
            continue
        overlap = len(item_tokens & resume_tokens) / len(item_tokens)
        # A responsibility is "covered" once about a third of its meaningful
        # words appear; beyond that, extra overlap adds proportionally.
        covered += min(1.0, overlap / 0.35)

    return covered / len(responsibilities) * 100


# --------------------------------------------------------------------------- #
#  Gap explanation                                                             #
# --------------------------------------------------------------------------- #


def explain_gaps(score: ScoreBreakdown, target: float) -> str:
    """Human-readable note on what is keeping the score below target.

    Fed back into the tailoring prompt, and shown in the report when a job
    genuinely can't reach the target honestly.
    """
    if score.total >= target:
        return ""

    lines: list[str] = []
    if score.missing_skills:
        lines.append(
            f"Required skills with no evidence in the resume: "
            f"{', '.join(score.missing_skills[:12])}"
        )
    if score.missing_keywords:
        lines.append(
            f"ATS keywords not present: {', '.join(score.missing_keywords[:15])}"
        )
    if score.title_alignment < 70:
        lines.append(
            f"Headline aligns only {score.title_alignment:.0f}% with the target title."
        )
    if score.responsibility_match < 60:
        lines.append(
            f"Only {score.responsibility_match:.0f}% of the listed responsibilities "
            "have supporting detail in the experience section."
        )
    return "\n".join(lines)
