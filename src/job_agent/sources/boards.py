"""Company applicant-tracking boards: Greenhouse and Lever.

These are the highest-signal sources in the set. You're reading a company's own
board, so there's no aggregator lag, no duplicate reposts, and the description is
the real one. The trade-off is that you must name the companies you care about —
add them under `sources.greenhouse.boards` / `sources.lever.boards` in config.yaml.

To find a company's board token, look at its careers URL:
    boards.greenhouse.io/<token>      →  greenhouse
    jobs.lever.co/<token>             →  lever
"""

from __future__ import annotations

import logging

from ..config import Config
from ..models import JobPosting
from ..util import PoliteClient, parse_date, strip_html
from .base import Source

log = logging.getLogger(__name__)


def _title_matches(title: str, config: Config) -> bool:
    """Loose match — company boards list every role, most irrelevant."""
    haystack = title.lower()
    terms = {t.lower() for t in config.search.titles}
    terms |= {k.lower() for k in config.search.keywords}
    if not terms:
        return True
    if any(term in haystack for term in terms):
        return True
    # Catch "SDE-1", "Software Dev Engineer" style titles the term list misses.
    generic = ("engineer", "developer", "sde", "intern", "scientist")
    return any(g in haystack for g in generic)


class GreenhouseSource(Source):
    name = "greenhouse"
    label = "Greenhouse"

    URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"

    def fetch(self, config: Config, http: PoliteClient) -> list[JobPosting]:
        boards: list[str] = config.source_option(self.name, "boards", []) or []
        postings: list[JobPosting] = []

        for board in boards:
            data = http.get_json(
                self.URL.format(board=board), params={"content": "true"}
            )
            if not data:
                log.debug("greenhouse board %r returned nothing", board)
                continue

            for job in data.get("jobs", []):
                title = job.get("title", "")
                if not _title_matches(title, config):
                    continue
                offices = job.get("offices") or job.get("location") or {}
                location = (
                    offices.get("name", "")
                    if isinstance(offices, dict)
                    else ", ".join(o.get("name", "") for o in offices)
                )
                postings.append(
                    JobPosting(
                        source=f"{self.label}/{board}",
                        title=title,
                        company=board.replace("-", " ").title(),
                        location=location,
                        remote="remote" in location.lower(),
                        url=job.get("absolute_url", ""),
                        description=strip_html(job.get("content", "")),
                        posted_at=parse_date(job.get("updated_at")),
                    )
                )
        return self._cap(postings, config)


class LeverSource(Source):
    name = "lever"
    label = "Lever"

    URL = "https://api.lever.co/v0/postings/{board}"

    def fetch(self, config: Config, http: PoliteClient) -> list[JobPosting]:
        boards: list[str] = config.source_option(self.name, "boards", []) or []
        postings: list[JobPosting] = []

        for board in boards:
            data = http.get_json(
                self.URL.format(board=board), params={"mode": "json", "limit": 100}
            )
            if not isinstance(data, list):
                log.debug("lever board %r returned nothing", board)
                continue

            for job in data:
                title = job.get("text", "")
                if not _title_matches(title, config):
                    continue
                categories = job.get("categories") or {}
                location = categories.get("location", "") or ""
                description = strip_html(
                    job.get("descriptionPlain") or job.get("description") or ""
                )
                # Lever splits requirements into separate list blocks.
                for section in job.get("lists") or []:
                    description += f"\n\n{section.get('text', '')}\n" + strip_html(
                        section.get("content", "")
                    )
                postings.append(
                    JobPosting(
                        source=f"{self.label}/{board}",
                        title=title,
                        company=board.replace("-", " ").title(),
                        location=location,
                        remote="remote" in location.lower(),
                        url=job.get("hostedUrl", "") or job.get("applyUrl", ""),
                        description=description.strip(),
                        posted_at=parse_date(job.get("createdAt")),
                    )
                )
        return self._cap(postings, config)
