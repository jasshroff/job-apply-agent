"""Per-job interview prep brief, plus a running applications log.

Two problems this solves.

First, the tailored resume is *reworded* — real work, described in the posting's
vocabulary. That's an advantage right up until an interviewer asks about a bullet
you don't remember writing. The brief lists every claim the outgoing PDF makes so
you can walk in knowing exactly what you've asserted.

Second, after a few weeks of daily runs you have dozens of tailored resumes and
no memory of which company got which. ``applications.csv`` is the ledger.

All of this is derived from data the run already has — no extra Claude calls, so
it adds nothing to the bill.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .models import JobResult, MasterProfile

LOG_COLUMNS = [
    "date", "company", "role", "location", "score", "baseline",
    "hit_target", "source", "url", "resume_pdf", "brief", "applied",
]


def write_brief(result: JobResult, profile: MasterProfile, directory: Path) -> Path:
    """Write `interview_brief.md` next to the tailored resume."""
    posting = result.posting
    requirements = result.requirements
    score = result.final_score

    lines: list[str] = [
        f"# {posting.company} — {posting.title}",
        "",
        f"**Prepared** {datetime.now():%d %b %Y}  ",
    ]
    if score:
        verdict = "clears the target" if result.reached_target else "honest ceiling"
        lines.append(
            f"**Match score** {score.total:.0f}/100 ({verdict}, up from "
            f"{result.base_score.total:.0f})  "
        )
    if posting.location:
        lines.append(f"**Location** {posting.location}  ")
    if posting.salary:
        lines.append(f"**Salary** {posting.salary}  ")
    if requirements and requirements.seniority:
        lines.append(f"**Seniority** {requirements.seniority}  ")
    lines.append(f"**Source** {posting.source}  ")
    if posting.url:
        lines.append(f"**Posting** {posting.url}  ")

    pdf = result.files.get("pdf") or result.files.get("docx")
    if pdf:
        lines.append(f"**Resume sent** `{Path(pdf).name}`  ")

    lines += ["", "---", ""]

    # -- 1. strengths ------------------------------------------------------- #
    if score and score.matched_skills:
        lines += [
            "## Lead with these",
            "",
            "The posting asks for these and your background genuinely covers them. "
            "These are your opening talking points.",
            "",
        ]
        ranked = _rank_by_importance(score.matched_skills, result)
        lines += [f"- **{skill}**" for skill in ranked]
        lines.append("")

    # -- 2. gaps ------------------------------------------------------------ #
    if score and score.missing_skills:
        lines += [
            "## Gaps — have an answer ready",
            "",
            "The posting asks for these and nothing in your profile evidences them. "
            "They were deliberately left off your resume rather than faked, so you "
            "are not claiming them. If asked, the honest answer lands better than a "
            "bluff: what's the nearest thing you *have* done, and how quickly do you "
            "pick things up?",
            "",
        ]
        lines += [f"- {skill}" for skill in score.missing_skills]
        lines.append("")

    # -- 3. what you claimed ------------------------------------------------ #
    if result.tailored:
        lines += [
            "## What your resume claims",
            "",
            "Every line below is on the PDF going to this company, written in their "
            "vocabulary. Be ready to talk through any of it.",
            "",
            f"**Headline** — {result.tailored.headline}",
            "",
        ]
        if result.tailored.summary:
            lines += [f"> {result.tailored.summary}", ""]

        for role in result.tailored.experience:
            lines.append(f"### {role.title} — {role.organization} ({role.dates})")
            lines += [f"- {b}" for b in role.bullets]
            lines.append("")

        for project in result.tailored.projects:
            lines.append(f"### Project: {project.name} ({project.dates})")
            if project.tech:
                lines.append(f"*{project.tech}*")
            lines += [f"- {b}" for b in project.bullets]
            lines.append("")

    # -- 4. likely questions ------------------------------------------------ #
    if requirements and requirements.responsibilities:
        lines += [
            "## Expect questions on",
            "",
            "Straight from the posting's responsibilities — have a concrete story "
            "for each, ideally one already on your resume.",
            "",
        ]
        lines += [f"- {r}" for r in requirements.responsibilities]
        lines.append("")

    # -- 5. hard gates ------------------------------------------------------ #
    if requirements and requirements.deal_breakers:
        lines += [
            "## Hard requirements flagged in the posting",
            "",
            *[f"- {d}" for d in requirements.deal_breakers],
            "",
        ]

    if result.honest_ceiling_reason:
        lines += [
            "## Why this stops short of the target",
            "",
            "```",
            result.honest_ceiling_reason,
            "```",
            "",
        ]

    lines += [
        "---",
        "",
        "*This resume was tailored from your master profile — reworded and "
        "re-prioritised, never invented. You have not applied yet; the agent "
        "prepares the package and leaves the send to you.*",
    ]

    path = directory / "interview_brief.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _rank_by_importance(matched: list[str], result: JobResult) -> list[str]:
    """Order matched skills by how central the JD says they are."""
    if not result.requirements:
        return matched
    weights = {s.name: s.importance for s in result.requirements.hard_skills}
    return sorted(matched, key=lambda s: weights.get(s, 0), reverse=True)


# --------------------------------------------------------------------------- #
#  Applications log                                                            #
# --------------------------------------------------------------------------- #


def append_to_log(result: JobResult, log_path: Path, run_date: str) -> None:
    """Append one row to the cross-run applications ledger.

    The `applied` column is left blank for you to fill in — the agent tracks
    what it prepared, you track what you actually sent.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_path.exists()

    with log_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(LOG_COLUMNS)
        writer.writerow(
            [
                run_date,
                result.posting.company,
                result.posting.title,
                result.posting.location,
                f"{result.final_score.total:.0f}" if result.final_score else "",
                f"{result.base_score.total:.0f}" if result.base_score else "",
                "yes" if result.reached_target else "no",
                result.posting.source,
                result.posting.url,
                result.files.get("pdf", ""),
                result.files.get("brief", ""),
                "",
            ]
        )


def read_log(log_path: Path) -> list[dict[str, str]]:
    if not log_path.exists():
        return []
    with log_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
