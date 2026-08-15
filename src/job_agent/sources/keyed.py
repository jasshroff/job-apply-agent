"""Sources that need an API key: Adzuna and JSearch.

Both are official, paid-tier APIs with usable free allowances. Adzuna is the
better India/Pune source; JSearch aggregates the big boards (LinkedIn, Indeed,
Glassdoor, ZipRecruiter) behind one endpoint.

Both degrade to an empty list with a warning when no key is configured, so the
run still completes on the keyless sources alone.
"""

from __future__ import annotations

import logging

from ..config import Config
from ..models import JobPosting
from ..util import PoliteClient, parse_date, strip_html
from .base import Source

log = logging.getLogger(__name__)


class AdzunaSource(Source):
    name = "adzuna"
    label = "Adzuna"
    requires_key = True

    URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"

    def fetch(self, config: Config, http: PoliteClient) -> list[JobPosting]:
        credentials = config.adzuna_credentials
        if not credentials:
            log.info("adzuna: no ADZUNA_APP_ID / ADZUNA_APP_KEY set — skipping")
            return []
        app_id, app_key = credentials

        # One call per (title x location) is the fastest way to burn a 250/day
        # quota, so cap the fan-out.
        locations = config.search.locations[:3] or [""]
        postings: list[JobPosting] = []

        for query in self._queries(config)[:3]:
            for location in locations:
                params = {
                    "app_id": app_id,
                    "app_key": app_key,
                    "what": query,
                    "results_per_page": 25,
                    "max_days_old": config.search.max_age_days,
                    "content-type": "application/json",
                }
                if location and location.lower() != "remote":
                    params["where"] = location

                data = http.get_json(
                    self.URL.format(country=config.search.country), params=params
                )
                if not data:
                    continue

                for job in data.get("results", []):
                    company = (job.get("company") or {}).get("display_name", "")
                    area = (job.get("location") or {}).get("display_name", "")
                    postings.append(
                        JobPosting(
                            source=self.label,
                            title=job.get("title", ""),
                            company=company,
                            location=area,
                            remote="remote" in f"{area} {job.get('title','')}".lower(),
                            url=job.get("redirect_url", ""),
                            description=strip_html(job.get("description", "")),
                            posted_at=parse_date(job.get("created")),
                            salary=self._salary(job),
                        )
                    )
        return self._cap(postings, config)

    @staticmethod
    def _salary(job: dict) -> str:
        lo, hi = job.get("salary_min"), job.get("salary_max")
        if lo and hi:
            return f"{int(lo):,}–{int(hi):,}"
        return f"{int(lo):,}" if lo else ""


class JSearchSource(Source):
    name = "jsearch"
    label = "JSearch"
    requires_key = True

    URL = "https://jsearch.p.rapidapi.com/search"

    def fetch(self, config: Config, http: PoliteClient) -> list[JobPosting]:
        key = config.rapidapi_key
        if not key:
            log.info("jsearch: no RAPIDAPI_KEY set — skipping")
            return []

        headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
        primary_location = next(
            (loc for loc in config.search.locations if loc.lower() != "remote"),
            "India",
        )
        postings: list[JobPosting] = []

        # Free tier is ~200 requests/month, so stay to two queries per run.
        for query in self._queries(config)[:2]:
            params = {
                "query": f"{query} in {primary_location}",
                "page": "1",
                "num_pages": "1",
                "country": config.search.country,
                "date_posted": "month" if config.search.max_age_days > 7 else "week",
            }
            data = http.get_json(self.URL, params=params, headers=headers, delay=1.5)
            if not data:
                continue

            for job in data.get("data") or []:
                city = job.get("job_city") or ""
                country = job.get("job_country") or ""
                postings.append(
                    JobPosting(
                        source=f"{self.label}/{job.get('job_publisher', '?')}",
                        title=job.get("job_title", ""),
                        company=job.get("employer_name", ""),
                        location=", ".join(x for x in (city, country) if x),
                        remote=bool(job.get("job_is_remote")),
                        url=job.get("job_apply_link", "") or "",
                        description=strip_html(job.get("job_description", "")),
                        posted_at=parse_date(job.get("job_posted_at_timestamp")),
                        salary=self._salary(job),
                    )
                )
        return self._cap(postings, config)

    @staticmethod
    def _salary(job: dict) -> str:
        lo, hi = job.get("job_min_salary"), job.get("job_max_salary")
        currency = job.get("job_salary_currency") or ""
        if lo and hi:
            return f"{currency} {int(lo):,}–{int(hi):,}".strip()
        return ""
