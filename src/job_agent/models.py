"""Schemas for every structure that moves through the agent.

The models split into three groups:

  * ``MasterProfile`` and friends — the *fact base*, parsed once from the user's
    real resume. Nothing downstream may assert anything that isn't traceable
    back to here.
  * ``JobPosting`` / ``JobRequirements`` — what we learn about a job.
  * ``TailoredResume`` / ``ScoreBreakdown`` / ``JobResult`` — what we produce.

Models marked "LLM-facing" are passed to ``client.messages.parse()`` as output
schemas, so they avoid JSON-Schema features structured outputs doesn't support
(no recursion, no numeric/length constraints).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
#  Master profile — the fact base                                             #
# --------------------------------------------------------------------------- #


# Structured outputs compile these schemas into a grammar server-side, and that
# compiler rejects anything past a complexity ceiling. Every field below earns
# its place by being rendered or scored — fields that were only ever stored
# (role location, project team size, per-role skill lists duplicating the master
# skills list) pushed MasterProfile over the limit for no benefit.


class ExperienceItem(BaseModel):
    """One job, internship, or role held."""

    title: str
    organization: str
    start_date: str = ""
    end_date: str = ""
    bullets: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    name: str
    start_date: str = ""
    end_date: str = ""
    bullets: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class MasterProfile(BaseModel):
    """Structured parse of the user's real resume. LLM-facing.

    Every claim the agent later makes must be groundable in this object.
    """

    name: str
    headline: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: list[str] = Field(default_factory=list)
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    #: One preformatted line per qualification, e.g.
    #: "D. Y. Patil International University | B.Tech CSE (AI & ML) | CGPA 7.04 | 2023–2027"
    education: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    activities: list[str] = Field(default_factory=list)

    def all_evidence_text(self) -> str:
        """Every fact in one blob — used as grounding context and for scoring."""
        parts: list[str] = [self.headline, self.summary, " ".join(self.skills)]
        for e in self.experience:
            parts += [e.title, e.organization, " ".join(e.bullets)]
        for p in self.projects:
            parts += [p.name, " ".join(p.bullets), " ".join(p.skills)]
        parts += self.education + self.certifications + self.activities
        return "\n".join(x for x in parts if x)


# --------------------------------------------------------------------------- #
#  Jobs                                                                        #
# --------------------------------------------------------------------------- #


class JobPosting(BaseModel):
    """A job as returned by a source adapter, before any analysis."""

    source: str
    title: str
    company: str
    location: str = ""
    remote: bool = False
    url: str = ""
    description: str = ""
    posted_at: Optional[datetime] = None
    salary: str = ""

    @property
    def fingerprint(self) -> str:
        """Stable identity for dedupe across sources.

        Keyed on company + title rather than URL, since the same role shows up
        with different URLs on every aggregator.
        """
        key = f"{self.company.strip().lower()}::{self.title.strip().lower()}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    @property
    def label(self) -> str:
        return f"{self.company} — {self.title}"


class SkillRequirement(BaseModel):
    name: str
    # 1 = nice to have, 5 = the job is about this
    importance: int = 3
    required: bool = True


class JobRequirements(BaseModel):
    """What the JD is actually asking for. LLM-facing."""

    role_title: str = ""
    seniority: str = ""
    min_years_experience: int = 0
    hard_skills: list[SkillRequirement] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    ats_keywords: list[str] = Field(default_factory=list)
    domain: str = ""
    deal_breakers: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Scoring                                                                     #
# --------------------------------------------------------------------------- #


class ScoreBreakdown(BaseModel):
    """Deterministic ATS score. No LLM involved — same input, same number."""

    hard_skill_coverage: float = 0.0
    keyword_coverage: float = 0.0
    title_alignment: float = 0.0
    responsibility_match: float = 0.0
    total: float = 0.0

    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)

    def summary_line(self) -> str:
        return (
            f"total={self.total:.1f} "
            f"(skills={self.hard_skill_coverage:.1f}, kw={self.keyword_coverage:.1f}, "
            f"title={self.title_alignment:.1f}, resp={self.responsibility_match:.1f})"
        )


# --------------------------------------------------------------------------- #
#  Tailored output                                                             #
# --------------------------------------------------------------------------- #


class SkillGroup(BaseModel):
    label: str
    items: list[str] = Field(default_factory=list)


class TailoredExperience(BaseModel):
    title: str
    organization: str
    dates: str = ""
    bullets: list[str] = Field(default_factory=list)


class TailoredProject(BaseModel):
    name: str
    dates: str = ""
    tech: str = ""
    bullets: list[str] = Field(default_factory=list)


class TailoredResume(BaseModel):
    """The rewritten resume. LLM-facing.

    Contact details are copied from the master profile rather than regenerated,
    so the model can never mangle a phone number or email.
    """

    headline: str
    summary: str
    skill_groups: list[SkillGroup] = Field(default_factory=list)
    experience: list[TailoredExperience] = Field(default_factory=list)
    projects: list[TailoredProject] = Field(default_factory=list)
    education_lines: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    activities: list[str] = Field(default_factory=list)

    def all_text(self) -> str:
        parts: list[str] = [self.headline, self.summary]
        for g in self.skill_groups:
            parts += [g.label, " ".join(g.items)]
        for e in self.experience:
            parts += [e.title, e.organization, " ".join(e.bullets)]
        for p in self.projects:
            parts += [p.name, p.tech, " ".join(p.bullets)]
        parts += self.education_lines + self.certifications + self.activities
        return "\n".join(x for x in parts if x)


# --------------------------------------------------------------------------- #
#  Truthfulness verification                                                   #
# --------------------------------------------------------------------------- #


class VerificationIssue(BaseModel):
    claim: str
    where: str = ""
    severity: str = "medium"  # low | medium | high
    reason: str = ""
    suggested_fix: str = ""


class VerificationResult(BaseModel):
    """Whether every claim in the tailored resume traces to the master. LLM-facing."""

    grounded: bool = True
    issues: list[VerificationIssue] = Field(default_factory=list)

    @property
    def blocking_issues(self) -> list[VerificationIssue]:
        return [i for i in self.issues if i.severity == "high"]


# --------------------------------------------------------------------------- #
#  Per-job result                                                              #
# --------------------------------------------------------------------------- #


class JobResult(BaseModel):
    posting: JobPosting
    requirements: Optional[JobRequirements] = None
    base_score: Optional[ScoreBreakdown] = None
    final_score: Optional[ScoreBreakdown] = None
    tailored: Optional[TailoredResume] = None
    verification: Optional[VerificationResult] = None
    iterations: int = 0
    reached_target: bool = False
    honest_ceiling_reason: str = ""
    files: dict[str, str] = Field(default_factory=dict)
    error: str = ""

    @property
    def improvement(self) -> float:
        if self.base_score and self.final_score:
            return self.final_score.total - self.base_score.total
        return 0.0


class RunSummary(BaseModel):
    started_at: datetime
    finished_at: Optional[datetime] = None
    jobs_found: int = 0
    jobs_new: int = 0
    jobs_prescreened: int = 0
    jobs_tailored: int = 0
    jobs_at_target: int = 0
    results: list[JobResult] = Field(default_factory=list)
    source_errors: dict[str, str] = Field(default_factory=dict)
