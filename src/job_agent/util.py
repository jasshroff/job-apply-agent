"""Shared helpers: polite HTTP, HTML stripping, text normalisation."""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class PoliteClient:
    """HTTP client with a per-host minimum delay and jitter.

    Every source adapter shares one of these. The delay is deliberate: several
    of these endpoints are unofficial, and hammering them is the fastest way to
    get an IP blocked.
    """

    def __init__(self, timeout: float = 25.0, default_delay: float = 1.0) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"},
        )
        self._default_delay = default_delay
        self._last_hit: dict[str, float] = {}

    def _wait(self, host: str, delay: float) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            elapsed = time.monotonic() - last
            remaining = delay - elapsed
            if remaining > 0:
                time.sleep(remaining + random.uniform(0, 0.4))
        self._last_hit[host] = time.monotonic()

    def get(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        delay: Optional[float] = None,
        tolerate: tuple[int, ...] = (),
    ) -> Optional[httpx.Response]:
        """GET a URL, returning None rather than raising on failure.

        A single flaky source should degrade the run, not end it. Status codes
        listed in ``tolerate`` are returned to the caller instead of swallowed,
        so an adapter can read the body of an error it knows how to explain.
        """
        host = httpx.URL(url).host or url
        self._wait(host, delay if delay is not None else self._default_delay)
        try:
            response = self._client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("request failed: %s (%s)", url, exc)
            return None

        if response.status_code in tolerate:
            return response
        if response.status_code == 429:
            log.warning("rate limited by %s — backing off", host)
            time.sleep(5)
            return None
        if response.status_code >= 400:
            log.warning("%s returned HTTP %s", host, response.status_code)
            return None
        return response

    def get_json(self, url: str, **kwargs: Any) -> Optional[Any]:
        response = self.get(url, **kwargs)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError:
            log.warning("non-JSON response from %s", url)
            return None

    def close(self) -> None:
        self._client.close()


# --------------------------------------------------------------------------- #
#  Text                                                                        #
# --------------------------------------------------------------------------- #

_WS = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")


def strip_html(raw: str) -> str:
    """Turn a JD's HTML into readable plain text.

    Job descriptions arrive as HTML from most sources. Keeping list structure
    matters — bullet points are where requirements live.
    """
    if not raw:
        return ""
    if "<" not in raw:
        return clean_text(raw)

    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for br in soup.find_all(["br", "p", "div", "h1", "h2", "h3", "h4"]):
        br.insert_after("\n")

    return clean_text(soup.get_text())


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


def truncate(text: str, limit: int = 12000) -> str:
    """Cap JD length so one pathological posting can't blow up a request."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[...description truncated...]"


def parse_date(value: Any) -> Optional[datetime]:
    """Best-effort date parsing across the many shapes sources return."""
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(
                tzinfo=None
            )
        except (ValueError, OSError, OverflowError):
            return None

    text = str(value).strip()
    if text.isdigit():
        return parse_date(int(text))

    normalised = text.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            dt = (
                datetime.fromisoformat(normalised)
                if fmt is None
                else datetime.strptime(text, fmt)
            )
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            continue
    return None


def slugify(text: str, max_length: int = 60) -> str:
    """Filesystem-safe name for an output directory."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return (slug[:max_length].rstrip("_") or "job").lower()
