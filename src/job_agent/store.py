"""SQLite persistence: which jobs we've already seen, and what we did with them.

The point of this is dedupe across days. Aggregators re-list the same job for
weeks; without a store, every run would re-tailor the same twenty postings and
burn tokens producing files you already have.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from .models import JobPosting, JobResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    fingerprint   TEXT PRIMARY KEY,
    company       TEXT NOT NULL,
    title         TEXT NOT NULL,
    source        TEXT NOT NULL,
    url           TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    times_seen    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint   TEXT NOT NULL,
    run_date      TEXT NOT NULL,
    base_score    REAL,
    final_score   REAL,
    reached       INTEGER NOT NULL DEFAULT 0,
    iterations    INTEGER NOT NULL DEFAULT 0,
    files         TEXT,
    payload       TEXT,
    UNIQUE(fingerprint, run_date)
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    jobs_found    INTEGER DEFAULT 0,
    jobs_new      INTEGER DEFAULT 0,
    jobs_tailored INTEGER DEFAULT 0,
    jobs_at_target INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_seen_last ON seen_jobs(last_seen);
CREATE INDEX IF NOT EXISTS idx_results_fp ON results(fingerprint);
"""


class Store:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- job dedupe ---------------------------------------------------------- #

    def filter_new(
        self, postings: list[JobPosting], recheck_after_days: int = 30
    ) -> tuple[list[JobPosting], list[JobPosting]]:
        """Split postings into (new, already-seen).

        A job seen longer ago than ``recheck_after_days`` counts as new again —
        companies do repost, and a stale exclusion is worse than a duplicate.
        """
        if not postings:
            return [], []

        cutoff = (datetime.now() - timedelta(days=recheck_after_days)).isoformat()
        fingerprints = [p.fingerprint for p in postings]

        with self._conn() as conn:
            placeholders = ",".join("?" * len(fingerprints))
            rows = conn.execute(
                f"SELECT fingerprint FROM seen_jobs "
                f"WHERE fingerprint IN ({placeholders}) AND last_seen > ?",
                (*fingerprints, cutoff),
            ).fetchall()
        known = {row["fingerprint"] for row in rows}

        new = [p for p in postings if p.fingerprint not in known]
        seen = [p for p in postings if p.fingerprint in known]
        return new, seen

    def record_seen(self, postings: list[JobPosting]) -> None:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            for p in postings:
                conn.execute(
                    """
                    INSERT INTO seen_jobs
                        (fingerprint, company, title, source, url, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fingerprint) DO UPDATE SET
                        last_seen  = excluded.last_seen,
                        times_seen = times_seen + 1
                    """,
                    (p.fingerprint, p.company, p.title, p.source, p.url, now, now),
                )

    # -- results ------------------------------------------------------------- #

    def record_result(self, result: JobResult, run_date: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO results
                    (fingerprint, run_date, base_score, final_score, reached,
                     iterations, files, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint, run_date) DO UPDATE SET
                    base_score  = excluded.base_score,
                    final_score = excluded.final_score,
                    reached     = excluded.reached,
                    iterations  = excluded.iterations,
                    files       = excluded.files,
                    payload     = excluded.payload
                """,
                (
                    result.posting.fingerprint,
                    run_date,
                    result.base_score.total if result.base_score else None,
                    result.final_score.total if result.final_score else None,
                    int(result.reached_target),
                    result.iterations,
                    json.dumps(result.files),
                    result.model_dump_json(),
                ),
            )

    # -- runs ---------------------------------------------------------------- #

    def start_run(self, started_at: datetime) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO runs (started_at) VALUES (?)", (started_at.isoformat(),)
            )
            return int(cur.lastrowid or 0)

    def finish_run(
        self,
        run_id: int,
        *,
        finished_at: datetime,
        jobs_found: int,
        jobs_new: int,
        jobs_tailored: int,
        jobs_at_target: int,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE runs SET finished_at = ?, jobs_found = ?, jobs_new = ?,
                                jobs_tailored = ?, jobs_at_target = ?
                WHERE id = ?
                """,
                (
                    finished_at.isoformat(),
                    jobs_found,
                    jobs_new,
                    jobs_tailored,
                    jobs_at_target,
                    run_id,
                ),
            )

    def recent_results(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT r.run_date, r.final_score, r.reached, s.company, s.title, s.url
                FROM results r JOIN seen_jobs s ON s.fingerprint = r.fingerprint
                ORDER BY r.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM seen_jobs").fetchone()["c"]
            tailored = conn.execute("SELECT COUNT(*) c FROM results").fetchone()["c"]
            hit = conn.execute(
                "SELECT COUNT(*) c FROM results WHERE reached = 1"
            ).fetchone()["c"]
            runs = conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]
        return {
            "jobs_seen": total,
            "resumes_tailored": tailored,
            "reached_target": hit,
            "runs": runs,
        }


def optional_store(db_path: Optional[Path]) -> Optional[Store]:
    return Store(db_path) if db_path else None
