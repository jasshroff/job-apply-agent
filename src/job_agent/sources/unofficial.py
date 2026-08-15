"""LinkedIn and Naukri via their public, logged-out listing endpoints.

Read this before enabling either one.

These are **not** sanctioned APIs. They are the JSON/HTML endpoints the sites'
own front-ends call to render results for a visitor who is not signed in. That
shapes the design here:

  * **No credentials, ever.** Nothing signs in, nothing stores a cookie, nothing
    touches your account. There is no session to ban — the worst realistic
    outcome is your IP getting rate-limited or served empty pages.
  * **Slow by default.** `rate_limit_seconds` in config.yaml gates every request
    and defaults higher than the other sources.
  * **Failure is silent and non-fatal.** When the markup shifts or a request is
    blocked, the adapter returns what it has and the run continues on the other
    sources.

Both sites' terms of service prohibit automated collection, and both change
their markup without notice, so treat these as a bonus tier rather than
something to depend on. They are `enabled: false` in config.yaml by default.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

from ..config import Config
from ..models import JobPosting
from ..util import PoliteClient, parse_date, strip_html
from .base import Source

log = logging.getLogger(__name__)


class LinkedInSource(Source):
    name = "linkedin"
    label = "LinkedIn"

    SEARCH_URL = (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    )
    DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    _JOB_ID = re.compile(r"-(\d{6,})(?:\?|$)")

    def fetch(self, config: Config, http: PoliteClient) -> list[JobPosting]:
        delay = float(config.source_option(self.name, "rate_limit_seconds", 4.0))
        # Freshness filter LinkedIn understands: seconds since posting.
        seconds = config.search.max_age_days * 86400

        postings: list[JobPosting] = []
        seen_urls: set[str] = set()

        for query in self._queries(config)[:3]:
            for location in (config.search.locations or ["India"])[:2]:
                params = {
                    "keywords": query,
                    "location": location,
                    "f_TPR": f"r{seconds}",
                    "start": "0",
                }
                response = http.get(self.SEARCH_URL, params=params, delay=delay)
                if response is None:
                    log.warning("linkedin: search blocked or unavailable — skipping")
                    continue

                for card in self._parse_cards(response.text, location):
                    if card.url in seen_urls:
                        continue
                    seen_urls.add(card.url)
                    postings.append(card)

        # Cards carry no description; fetch it for the ones we'll actually use.
        for posting in postings[: config.search.max_jobs_per_source]:
            posting.description = (
                self._fetch_description(posting.url, http, delay) or posting.title
            )

        return self._cap(postings, config)

    def _parse_cards(self, html: str, location_hint: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "lxml")
        results: list[JobPosting] = []

        for card in soup.select("li, div.base-card"):
            title_el = card.select_one(
                "h3.base-search-card__title, .base-search-card__title"
            )
            company_el = card.select_one(
                "h4.base-search-card__subtitle a, .base-search-card__subtitle"
            )
            link_el = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
            if not (title_el and link_el):
                continue

            location_el = card.select_one(".job-search-card__location")
            time_el = card.select_one("time")
            location = (
                location_el.get_text(strip=True) if location_el else location_hint
            )

            results.append(
                JobPosting(
                    source=self.label,
                    title=title_el.get_text(strip=True),
                    company=company_el.get_text(strip=True) if company_el else "",
                    location=location,
                    remote="remote" in location.lower(),
                    url=(link_el.get("href") or "").split("?")[0],
                    posted_at=parse_date(
                        time_el.get("datetime") if time_el else None
                    ),
                )
            )
        return results

    def _fetch_description(
        self, url: str, http: PoliteClient, delay: float
    ) -> Optional[str]:
        match = self._JOB_ID.search(url)
        if not match:
            return None
        response = http.get(
            self.DETAIL_URL.format(job_id=match.group(1)), delay=delay
        )
        if response is None:
            return None

        soup = BeautifulSoup(response.text, "lxml")
        block = soup.select_one(
            ".show-more-less-html__markup, .description__text, section.description"
        )
        return strip_html(str(block)) if block else None


class NaukriSource(Source):
    name = "naukri"
    label = "Naukri"

    SEARCH_URL = "https://www.naukri.com/jobapi/v3/search"
    DETAIL_URL = "https://www.naukri.com/jobapi/v4/job/{job_id}"

    # Headers Naukri's own front-end sends; without them the endpoint 400s.
    _HEADERS = {
        "appid": "109",
        "systemid": "Naukri",
        "Accept": "application/json",
        "Referer": "https://www.naukri.com/",
    }

    def fetch(self, config: Config, http: PoliteClient) -> list[JobPosting]:
        delay = float(config.source_option(self.name, "rate_limit_seconds", 3.0))
        location = next(
            (loc for loc in config.search.locations if loc.lower() != "remote"), ""
        )

        postings: list[JobPosting] = []
        for query in self._queries(config)[:3]:
            params = {
                "noOfResults": "20",
                "urlType": "search_by_keyword",
                "searchType": "adv",
                "keyword": query,
                "pageNo": "1",
                "seoKey": f"{query.lower().replace(' ', '-')}-jobs",
                "src": "jobsearchDesk",
                "latLong": "",
            }
            if location:
                params["location"] = location

            response = http.get(
                self.SEARCH_URL,
                params=params,
                headers=self._HEADERS,
                delay=delay,
                tolerate=(406,),
            )
            if response is None:
                log.warning("naukri: search unavailable — skipping")
                continue
            if response.status_code == 406:
                # Naukri gates this endpoint behind reCAPTCHA. There is no
                # header or pacing change that gets past it, and solving the
                # challenge is off the table, so stop rather than retry the
                # remaining queries.
                log.warning(
                    "naukri: endpoint requires reCAPTCHA — no results available. "
                    "Use Adzuna for India coverage, and set "
                    "sources.naukri.enabled=false to silence this."
                )
                return []
            try:
                data = response.json()
            except ValueError:
                log.warning("naukri: unexpected non-JSON response")
                continue

            for job in data.get("jobDetails") or []:
                job_id = job.get("jobId", "")
                placeholders = {
                    p.get("type"): p.get("label")
                    for p in (job.get("placeholders") or [])
                }
                url = job.get("jdURL") or ""
                if url.startswith("/"):
                    url = f"https://www.naukri.com{url}"

                postings.append(
                    JobPosting(
                        source=self.label,
                        title=job.get("title", ""),
                        company=job.get("companyName", ""),
                        location=placeholders.get("location", location),
                        remote="remote" in str(placeholders.get("location", "")).lower(),
                        url=url,
                        description=strip_html(job.get("jobDescription", "")),
                        posted_at=parse_date(job.get("createdDate")),
                        salary=placeholders.get("salary", "") or "",
                    )
                )
                # Search results carry a truncated description; the detail
                # endpoint has the full text and that's what scoring needs.
                if job_id and len(postings[-1].description) < 400:
                    full = self._fetch_description(job_id, http, delay)
                    if full:
                        postings[-1].description = full

        return self._cap(postings, config)

    def _fetch_description(
        self, job_id: str, http: PoliteClient, delay: float
    ) -> Optional[str]:
        data = http.get_json(
            self.DETAIL_URL.format(job_id=job_id),
            headers=self._HEADERS,
            delay=delay,
        )
        if not data:
            return None
        details = data.get("jobDetails") or {}
        description = strip_html(details.get("description", ""))
        for section in ("keySkills", "education"):
            block = details.get(section)
            if isinstance(block, dict):
                items = block.get("other") or block.get("preferred") or []
                labels = [i.get("label", "") for i in items if isinstance(i, dict)]
                if labels:
                    description += f"\n\n{section}: {', '.join(labels)}"
        return description.strip() or None
