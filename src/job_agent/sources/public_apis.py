"""Sources with official, public, keyless JSON APIs.

Remotive, RemoteOK and Arbeitnow all publish job feeds intended for
programmatic use. Remote-heavy, so they complement the India-local sources
rather than replacing them.
"""

from __future__ import annotations

from ..config import Config
from ..models import JobPosting
from ..util import PoliteClient, parse_date, strip_html
from .base import Source


class RemotiveSource(Source):
    name = "remotive"
    label = "Remotive"

    URL = "https://remotive.com/api/remote-jobs"

    def fetch(self, config: Config, http: PoliteClient) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for query in self._queries(config)[:4]:
            data = http.get_json(self.URL, params={"search": query, "limit": 50})
            if not data:
                continue
            for job in data.get("jobs", []):
                postings.append(
                    JobPosting(
                        source=self.label,
                        title=job.get("title", ""),
                        company=job.get("company_name", ""),
                        location=job.get("candidate_required_location", "Remote"),
                        remote=True,
                        url=job.get("url", ""),
                        description=strip_html(job.get("description", "")),
                        posted_at=parse_date(job.get("publication_date")),
                        salary=job.get("salary", "") or "",
                    )
                )
        return self._cap(postings, config)


class RemoteOKSource(Source):
    name = "remoteok"
    label = "RemoteOK"

    URL = "https://remoteok.com/api"

    def fetch(self, config: Config, http: PoliteClient) -> list[JobPosting]:
        data = http.get_json(self.URL)
        if not isinstance(data, list):
            return []

        # The first element is a legal notice, not a job.
        wanted = {t.lower() for t in config.search.titles}
        wanted |= {k.lower() for k in config.search.keywords}

        postings: list[JobPosting] = []
        for job in data[1:]:
            if not isinstance(job, dict):
                continue
            title = job.get("position") or job.get("title") or ""
            haystack = f"{title} {' '.join(job.get('tags') or [])}".lower()
            if wanted and not any(w in haystack for w in wanted):
                continue
            postings.append(
                JobPosting(
                    source=self.label,
                    title=title,
                    company=job.get("company", ""),
                    location=job.get("location") or "Remote",
                    remote=True,
                    url=job.get("url") or job.get("apply_url") or "",
                    description=strip_html(job.get("description", "")),
                    posted_at=parse_date(job.get("date") or job.get("epoch")),
                    salary=self._salary(job),
                )
            )
        return self._cap(postings, config)

    @staticmethod
    def _salary(job: dict) -> str:
        lo, hi = job.get("salary_min"), job.get("salary_max")
        return f"${lo:,}–${hi:,}" if lo and hi else ""


class ArbeitnowSource(Source):
    name = "arbeitnow"
    label = "Arbeitnow"

    URL = "https://www.arbeitnow.com/api/job-board-api"

    def fetch(self, config: Config, http: PoliteClient) -> list[JobPosting]:
        wanted = {t.lower() for t in config.search.titles}
        wanted |= {k.lower() for k in config.search.keywords}

        postings: list[JobPosting] = []
        for page in (1, 2):
            data = http.get_json(self.URL, params={"page": page})
            if not data:
                break
            for job in data.get("data", []):
                title = job.get("title", "")
                haystack = f"{title} {' '.join(job.get('tags') or [])}".lower()
                if wanted and not any(w in haystack for w in wanted):
                    continue
                postings.append(
                    JobPosting(
                        source=self.label,
                        title=title,
                        company=job.get("company_name", ""),
                        location=job.get("location", ""),
                        remote=bool(job.get("remote")),
                        url=job.get("url", ""),
                        description=strip_html(job.get("description", "")),
                        posted_at=parse_date(job.get("created_at")),
                    )
                )
        return self._cap(postings, config)
