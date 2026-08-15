"""Configuration: `config.yaml` for behaviour, `.env` for secrets."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ProfileConfig(BaseModel):
    master_resume: str = "data/master_resume.docx"
    profile_cache: str = "data/profile.json"


class SearchConfig(BaseModel):
    titles: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    country: str = "in"
    include_remote: bool = True
    max_age_days: int = 14
    max_jobs_per_source: int = 40
    exclude_title_keywords: list[str] = Field(default_factory=list)
    max_years_experience_required: int = 3

    @field_validator("max_years_experience_required", "max_age_days",
                     "max_jobs_per_source")
    @classmethod
    def _must_be_positive(cls, value: int, info) -> int:
        # A range like "0-1" coerces to -1, which silently drops every posting
        # instead of failing. Catch it at load time rather than at 3am.
        if value < 0:
            raise ValueError(
                f"{info.field_name} must be a single whole number ≥ 0 "
                f"(got {value!r}). Write `1`, not a range like `0-1`."
            )
        return value


class ScoringWeights(BaseModel):
    hard_skill_coverage: float = 0.40
    keyword_coverage: float = 0.25
    title_alignment: float = 0.15
    responsibility_match: float = 0.20


class ScoringConfig(BaseModel):
    target_score: float = 90.0
    min_prescreen_score: float = 45.0
    max_tailored_per_run: int = 10
    weights: ScoringWeights = Field(default_factory=ScoringWeights)


class TailoringConfig(BaseModel):
    max_iterations: int = 4
    verify_truthfulness: bool = True
    generate_cover_letter: bool = False


class ModelEffort(BaseModel):
    resume_parse: str = "medium"
    jd_analysis: str = "medium"
    tailoring: str = "high"
    verification: str = "medium"


class ModelsConfig(BaseModel):
    resume_parse: str = "claude-opus-5"
    jd_analysis: str = "claude-opus-5"
    tailoring: str = "claude-opus-5"
    verification: str = "claude-opus-5"
    effort: ModelEffort = Field(default_factory=ModelEffort)


class OutputConfig(BaseModel):
    directory: str = "output"
    formats: list[str] = Field(default_factory=lambda: ["docx", "pdf"])
    html_report: bool = True
    open_report_after_run: bool = False


class Config(BaseModel):
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    sources: dict[str, dict[str, Any]] = Field(default_factory=dict)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    tailoring: TailoringConfig = Field(default_factory=TailoringConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    root: Path = PROJECT_ROOT

    # -- secrets, read from the environment --------------------------------- #
    @property
    def anthropic_api_key(self) -> Optional[str]:
        return os.getenv("ANTHROPIC_API_KEY")

    @property
    def api_key_source(self) -> str:
        """Where the key came from — shell export or .env.

        Worth surfacing on auth failures: a key exported in your shell works
        interactively and is invisible to the scheduled launchd run, which is a
        confusing way to discover that the two disagree.
        """
        if os.environ.get("_JOB_AGENT_KEY_FROM_SHELL") == "1":
            return "your shell environment (not .env)"
        return ".env"

    @property
    def adzuna_credentials(self) -> Optional[tuple[str, str]]:
        app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
        return (app_id, app_key) if app_id and app_key else None

    @property
    def rapidapi_key(self) -> Optional[str]:
        return os.getenv("RAPIDAPI_KEY")

    # -- path helpers -------------------------------------------------------- #
    def path(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else self.root / p

    @property
    def master_resume_path(self) -> Path:
        return self.path(self.profile.master_resume)

    @property
    def profile_cache_path(self) -> Path:
        return self.path(self.profile.profile_cache)

    @property
    def output_dir(self) -> Path:
        return self.path(self.output.directory)

    @property
    def db_path(self) -> Path:
        return self.path("data/jobs.db")

    def source_enabled(self, name: str) -> bool:
        return bool(self.sources.get(name, {}).get("enabled", False))

    def source_option(self, name: str, key: str, default: Any = None) -> Any:
        return self.sources.get(name, {}).get(key, default)


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader — avoids a dependency for six lines of parsing.

    Existing environment variables win, so `ANTHROPIC_API_KEY=... job-agent run`
    overrides the file.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


def load_config(config_path: Optional[Path] = None) -> Config:
    root = PROJECT_ROOT
    # Record the key's origin before .env loading blurs the distinction.
    if os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["_JOB_AGENT_KEY_FROM_SHELL"] = "1"
    _load_dotenv(root / ".env")

    path = config_path or (root / "config.yaml")
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    cfg = Config(**data)
    cfg.root = root
    return cfg
