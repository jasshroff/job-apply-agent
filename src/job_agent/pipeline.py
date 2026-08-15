"""The daily run.

    fetch  →  dedupe  →  cheap filters  →  analyse JD  →  prescreen  →  tailor  →  render

Filtering is ordered by cost on purpose. Deterministic checks (title, age,
location) run first and are free; the LLM only ever sees postings that survived
them, and only the best-prescreening handful get the full tailoring loop. On a
typical morning that turns ~300 raw listings into ~10 tailored resumes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, Optional

from .brief import append_to_log, write_brief
from .config import Config
from .llm import Claude, LLMError
from .models import JobPosting, JobResult, MasterProfile, RunSummary
from .resume.tailor import analyze_job, profile_to_resume, tailor_for_job
from .resume.renderer import render
from .scoring import score_resume
from .sources import enabled_sources
from .store import Store
from .util import PoliteClient, slugify

log = logging.getLogger(__name__)

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


class Pipeline:
    def __init__(
        self,
        config: Config,
        claude: Optional[Claude],  # None is valid only for a dry run
        profile: MasterProfile,
        store: Optional[Store] = None,
        progress: Progress = _noop,
    ) -> None:
        self.config = config
        self.claude = claude
        self.profile = profile
        self.store = store
        self.progress = progress
        self.http = PoliteClient()

    # ------------------------------------------------------------------ #

    def run(self, limit: Optional[int] = None, dry_run: bool = False) -> RunSummary:
        summary = RunSummary(started_at=datetime.now())
        run_id = self.store.start_run(summary.started_at) if self.store else None

        postings = self._fetch_all(summary)
        summary.jobs_found = len(postings)
        self.progress(f"Found {len(postings)} postings across all sources")

        postings = self._deduplicate(postings)
        postings = [p for p in postings if self._passes_cheap_filters(p)]
        self.progress(f"{len(postings)} survive title/age/location filters")

        if self.store:
            fresh, already_seen = self.store.filter_new(postings)
            self.progress(
                f"{len(fresh)} are new ({len(already_seen)} seen in a previous run)"
            )
            postings = fresh
        summary.jobs_new = len(postings)

        if dry_run:
            summary.finished_at = datetime.now()
            for posting in postings:
                summary.results.append(JobResult(posting=posting))
            self.http.close()
            return summary

        if self.claude is None:
            raise RuntimeError("a non-dry run needs an Anthropic client")

        candidates = self._prescreen(postings)
        summary.jobs_prescreened = len(candidates)

        budget = limit or self.config.scoring.max_tailored_per_run
        selected = candidates[:budget]
        self.progress(
            f"{len(candidates)} clear the prescreen bar; tailoring the top "
            f"{len(selected)}"
        )

        for index, (posting, requirements, baseline) in enumerate(selected, start=1):
            self.progress(
                f"[{index}/{len(selected)}] {posting.label} "
                f"(baseline {baseline.total:.0f})"
            )
            try:
                result = tailor_for_job(
                    self.claude, self.config, self.profile,
                    posting, requirements, baseline,
                )
            except LLMError as exc:
                log.error("tailoring aborted for %s: %s", posting.label, exc)
                result = JobResult(
                    posting=posting, requirements=requirements,
                    base_score=baseline, error=str(exc),
                )

            if result.tailored:
                result.files = self._write_files(result, summary.started_at)
                mark = "reached" if result.reached_target else "capped at"
                self.progress(
                    f"      {mark} {result.final_score.total:.0f} "
                    f"(+{result.improvement:.0f} over baseline)"
                )

            summary.results.append(result)
            if self.store:
                self.store.record_result(
                    result, summary.started_at.date().isoformat()
                )

        summary.jobs_tailored = sum(1 for r in summary.results if r.tailored)
        summary.jobs_at_target = sum(1 for r in summary.results if r.reached_target)
        summary.finished_at = datetime.now()

        if self.store:
            self.store.record_seen(postings)
            if run_id:
                self.store.finish_run(
                    run_id,
                    finished_at=summary.finished_at,
                    jobs_found=summary.jobs_found,
                    jobs_new=summary.jobs_new,
                    jobs_tailored=summary.jobs_tailored,
                    jobs_at_target=summary.jobs_at_target,
                )

        self.http.close()
        return summary

    # ------------------------------------------------------------------ #
    #  Stages                                                             #
    # ------------------------------------------------------------------ #

    def _fetch_all(self, summary: RunSummary) -> list[JobPosting]:
        postings: list[JobPosting] = []
        sources = enabled_sources(self.config)
        if not sources:
            log.warning("no sources enabled — check the `sources:` block in config.yaml")

        for source in sources:
            self.progress(f"  querying {source.label}…")
            try:
                found = source.fetch(self.config, self.http)
                postings.extend(found)
                log.info("%s returned %d postings", source.label, len(found))
            except Exception as exc:  # one bad source must not end the run
                log.warning("%s failed: %s", source.label, exc)
                summary.source_errors[source.label] = str(exc)
        return postings

    @staticmethod
    def _deduplicate(postings: list[JobPosting]) -> list[JobPosting]:
        """Keep the copy with the richest description.

        The same job appears on several aggregators with wildly different
        description quality, and description quality drives the whole score.
        """
        best: dict[str, JobPosting] = {}
        for posting in postings:
            existing = best.get(posting.fingerprint)
            if existing is None or len(posting.description) > len(existing.description):
                best[posting.fingerprint] = posting
        return list(best.values())

    def _passes_cheap_filters(self, posting: JobPosting) -> bool:
        search = self.config.search

        if not posting.title or not posting.company:
            return False

        # A description this short can't be scored meaningfully.
        if len(posting.description) < 200:
            return False

        title = posting.title.lower()
        if any(bad.lower() in title for bad in search.exclude_title_keywords):
            return False

        if posting.posted_at:
            cutoff = datetime.now() - timedelta(days=search.max_age_days)
            if posting.posted_at < cutoff:
                return False

        return self._location_ok(posting)

    def _location_ok(self, posting: JobPosting) -> bool:
        search = self.config.search
        if not search.locations:
            return True
        if posting.remote and search.include_remote:
            return True
        if not posting.location:
            return True  # unknown location — let the JD analysis decide
        haystack = posting.location.lower()
        return any(
            loc.lower() in haystack
            for loc in search.locations
            if loc.lower() != "remote"
        )

    def _prescreen(
        self, postings: list[JobPosting]
    ) -> list[tuple[JobPosting, "object", "object"]]:
        """Analyse each JD and score the *untailored* resume against it.

        The baseline score answers "how far is this from a match before we do
        anything?" — which is both the selection signal and the number the
        report compares the tailored result against.
        """
        base_resume = profile_to_resume(self.profile)
        explicit = [i for g in base_resume.skill_groups for i in g.items]
        resume_text = base_resume.all_text()

        scored: list[tuple[JobPosting, object, object]] = []
        for posting in postings:
            try:
                requirements = analyze_job(self.claude, self.config, posting)
            except LLMError as exc:
                log.warning("could not analyse %s: %s", posting.label, exc)
                continue

            if (
                requirements.min_years_experience
                > self.config.search.max_years_experience_required
            ):
                log.info(
                    "skipping %s — wants %d years",
                    posting.label, requirements.min_years_experience,
                )
                continue

            baseline = score_resume(
                resume_text=resume_text,
                explicit_skills=explicit,
                headline=base_resume.headline,
                requirements=requirements,
                job_title=posting.title,
                weights=self.config.scoring.weights,
            )
            if baseline.total < self.config.scoring.min_prescreen_score:
                continue
            scored.append((posting, requirements, baseline))

        scored.sort(key=lambda item: item[2].total, reverse=True)  # type: ignore[attr-defined]
        return scored

    def _write_files(self, result: JobResult, run_started: datetime) -> dict[str, str]:
        directory = (
            self.config.output_dir
            / run_started.date().isoformat()
            / slugify(f"{result.posting.company}_{result.posting.title}")
        )
        stem = slugify(f"{self.profile.name}_{result.posting.company}", 50)

        files = render(
            result.tailored,  # type: ignore[arg-type]
            self.profile,
            directory,
            stem,
            self.config.output.formats,
        )

        report_path = directory / "match_report.json"
        report_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        files["report"] = str(report_path)

        if result.posting.url:
            (directory / "APPLY_HERE.txt").write_text(
                f"{result.posting.label}\n{result.posting.url}\n",
                encoding="utf-8",
            )

        # The prep brief needs `files` populated so it can name the PDF it's
        # briefing you on, hence writing it last.
        result.files = files
        files["brief"] = str(write_brief(result, self.profile, directory))
        append_to_log(
            result, self.config.output_dir / "applications.csv",
            run_started.date().isoformat(),
        )
        return files
