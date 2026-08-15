"""Source adapter contract.

Every adapter takes the search config and returns raw ``JobPosting`` objects.
Adapters do no filtering, scoring, or dedupe — that all happens once, centrally,
so behaviour doesn't drift between sources.

An adapter that fails must raise; the pipeline catches per-source and carries on
with whatever the other sources returned.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import Config
from ..models import JobPosting
from ..util import PoliteClient


class Source(ABC):
    #: key used in config.yaml under `sources:`
    name: str = ""
    #: shown in the report so you know where a job came from
    label: str = ""
    #: whether this adapter needs credentials to do anything
    requires_key: bool = False

    @abstractmethod
    def fetch(self, config: Config, http: PoliteClient) -> list[JobPosting]:
        """Return postings for the configured search. May raise."""

    # -- helpers available to every adapter ---------------------------------- #

    @staticmethod
    def _cap(postings: list[JobPosting], config: Config) -> list[JobPosting]:
        return postings[: config.search.max_jobs_per_source]

    @staticmethod
    def _queries(config: Config) -> list[str]:
        """Search strings to issue, most specific first."""
        return config.search.titles or ["Software Engineer"]
