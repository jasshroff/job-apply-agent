"""Command line interface.

    job-agent init ~/Downloads/resume.docx   one-time setup
    job-agent run                            the daily run
    job-agent run --dry-run                  search only, no Claude calls
    job-agent score <url-or-file>            score against a single JD
    job-agent status                         what the agent has done so far
    job-agent schedule                       install/remove the daily macOS job
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .brief import read_log
from .config import load_config
from .llm import Claude, LLMError
from .models import JobPosting
from .pipeline import Pipeline
from .report import write_report
from .resume.parser import load_profile
from .resume.tailor import analyze_job, profile_to_resume, tailor_for_job
from .scoring import score_resume
from .store import Store
from .util import PoliteClient, strip_html

app = typer.Typer(
    add_completion=False,
    help="Daily job search + evidence-grounded resume tailoring.",
    no_args_is_help=True,
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(console=console, show_path=False, markup=False)],
    )
    # httpx logs every request at INFO, which buries our own progress output.
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _claude(config) -> Claude:
    try:
        client = Claude(api_key=config.anthropic_api_key)
    except LLMError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    client.key_source = config.api_key_source
    return client


# --------------------------------------------------------------------------- #


@app.command()
def init(
    resume: Path = typer.Argument(..., help="Path to your resume (.docx/.pdf/.txt)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Install your master resume and parse it into the fact base."""
    _setup_logging(verbose)
    config = load_config()

    if not resume.exists():
        console.print(f"[red]No such file: {resume}[/red]")
        raise typer.Exit(1)

    destination = config.root / "data" / f"master_resume{resume.suffix.lower()}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resume, destination)
    console.print(f"[green]✓[/green] Copied resume to {destination.relative_to(config.root)}")

    if destination.suffix.lower() != Path(config.profile.master_resume).suffix.lower():
        console.print(
            f"[yellow]![/yellow] Set profile.master_resume to "
            f"'data/{destination.name}' in config.yaml"
        )

    config.profile.master_resume = str(destination)
    try:
        profile = load_profile(_claude(config), config, force_reparse=True)
    except LLMError as exc:
        console.print(f"\n[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Parsed profile for [bold]{profile.name}[/bold]")
    console.print(
        f"  {len(profile.skills)} skills · {len(profile.experience)} roles · "
        f"{len(profile.projects)} projects · {len(profile.certifications)} certs"
    )
    console.print("\nNext: [bold]job-agent run[/bold]")


@app.command()
def run(
    limit: Optional[int] = typer.Option(None, "--limit", "-n",
                                        help="Max jobs to tailor this run"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Search only — no Claude calls, no files"),
    reparse: bool = typer.Option(False, "--reparse",
                                 help="Re-parse the master resume first"),
    no_store: bool = typer.Option(False, "--no-store",
                                  help="Ignore the seen-jobs database"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", "-v/-q"),
) -> None:
    """Search today's jobs and tailor your resume to each strong match."""
    _setup_logging(verbose)
    config = load_config()

    # A dry run makes no Claude calls, so it shouldn't demand a key. It only
    # needs an already-parsed profile, and can proceed without even that.
    claude: Optional[Claude] = None
    if not (dry_run and not reparse):
        claude = _claude(config)

    try:
        profile = load_profile(claude, config, force_reparse=reparse)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    except LLMError as exc:
        if not dry_run:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        console.print("[dim]No parsed profile yet — dry run will search only.[/dim]")
        from .models import MasterProfile

        profile = MasterProfile(name="(not parsed yet)")

    store = None if no_store else Store(config.db_path)
    pipeline = Pipeline(
        config, claude, profile, store,
        progress=lambda msg: console.print(msg),
    )

    try:
        summary = pipeline.run(limit=limit, dry_run=dry_run)
    except LLMError as exc:
        console.print(f"\n[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print()
    _print_summary(summary, config.scoring.target_score)

    if dry_run:
        console.print("\n[dim]Dry run — nothing tailored or written.[/dim]")
        return

    if config.output.html_report:
        report = write_report(summary, config.output_dir, config.scoring.target_score)
        console.print(f"\n[bold]Report:[/bold] {report}")
        if config.output.open_report_after_run:
            webbrowser.open(f"file://{report}")

    console.print(f"[dim]{claude.usage_summary()}[/dim]")


@app.command()
def score(
    target: str = typer.Argument(..., help="Job posting URL, or a file of JD text"),
    tailor: bool = typer.Option(True, "--tailor/--no-tailor",
                                help="Also rewrite the resume for this job"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", "-v/-q"),
) -> None:
    """Score (and optionally tailor) against one specific job posting."""
    _setup_logging(verbose)
    config = load_config()
    claude = _claude(config)
    profile = load_profile(claude, config)

    posting = _posting_from(target)
    if not posting.description:
        console.print("[red]Could not read a job description from that input.[/red]")
        raise typer.Exit(1)

    console.print(f"Analysing [bold]{posting.label}[/bold]…")
    requirements = analyze_job(claude, config, posting)

    base_resume = profile_to_resume(profile)
    baseline = score_resume(
        resume_text=base_resume.all_text(),
        explicit_skills=[i for g in base_resume.skill_groups for i in g.items],
        headline=base_resume.headline,
        requirements=requirements,
        job_title=posting.title,
        weights=config.scoring.weights,
    )
    console.print(f"Current resume scores [bold]{baseline.total:.0f}[/bold]")

    if not tailor:
        if baseline.missing_skills:
            console.print(f"[yellow]Missing:[/yellow] {', '.join(baseline.missing_skills)}")
        return

    result = tailor_for_job(claude, config, profile, posting, requirements, baseline)
    if not result.tailored:
        console.print(f"[red]Tailoring failed: {result.error}[/red]")
        raise typer.Exit(1)

    directory = config.output_dir / "manual" / posting.fingerprint
    from .resume.renderer import render

    files = render(
        result.tailored, profile, directory,
        f"{profile.name.replace(' ', '_')}_{posting.company or 'job'}",
        config.output.formats,
    )

    console.print(
        f"Tailored resume scores [bold green]{result.final_score.total:.0f}[/bold green] "
        f"(+{result.improvement:.0f})"
    )
    if result.honest_ceiling_reason:
        console.print(f"\n[yellow]{result.honest_ceiling_reason}[/yellow]")
    for kind, path in files.items():
        console.print(f"  {kind}: {path}")


@app.command()
def status(limit: int = typer.Option(15, "--limit", "-n")) -> None:
    """Show what the agent has done across all runs."""
    config = load_config()
    store = Store(config.db_path)
    stats = store.stats()

    console.print(
        f"\n[bold]{stats['runs']}[/bold] runs · "
        f"[bold]{stats['jobs_seen']}[/bold] jobs seen · "
        f"[bold]{stats['resumes_tailored']}[/bold] resumes tailored · "
        f"[bold green]{stats['reached_target']}[/bold green] hit target\n"
    )

    rows = store.recent_results(limit)
    if not rows:
        console.print("[dim]No runs yet. Try: job-agent run[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Date", style="dim")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Score", justify="right")
    for row in rows:
        mark = "[green]" if row["reached"] else "[yellow]"
        value = f"{row['final_score']:.0f}" if row["final_score"] else "—"
        table.add_row(
            row["run_date"], row["company"], row["title"], f"{mark}{value}[/]"
        )
    console.print(table)


@app.command()
def log(
    limit: int = typer.Option(30, "--limit", "-n"),
    open_brief: Optional[str] = typer.Option(
        None, "--brief", help="Print the interview brief for a company"
    ),
) -> None:
    """Every company and role you have a tailored resume ready for."""
    config = load_config()
    log_path = config.output_dir / "applications.csv"
    rows = read_log(log_path)

    if not rows:
        console.print("[dim]Nothing prepared yet. Try: job-agent run[/dim]")
        return

    if open_brief:
        match = next(
            (r for r in reversed(rows)
             if open_brief.lower() in r["company"].lower()),
            None,
        )
        if not match:
            console.print(f"[red]No prepared application matching '{open_brief}'[/red]")
            raise typer.Exit(1)
        brief_path = Path(match["brief"])
        if not brief_path.exists():
            console.print(f"[red]Brief file is missing: {brief_path}[/red]")
            raise typer.Exit(1)
        console.print(brief_path.read_text(encoding="utf-8"))
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Date", style="dim")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Score", justify="right")
    table.add_column("Sent?", justify="center")

    for row in rows[-limit:]:
        colour = "green" if row["hit_target"] == "yes" else "yellow"
        sent = "[green]✓[/green]" if row.get("applied") else "[dim]—[/dim]"
        table.add_row(
            row["date"], row["company"], row["role"],
            f"[{colour}]{row['score']}[/{colour}]", sent,
        )

    console.print(table)
    console.print(f"\n[dim]Ledger: {log_path}[/dim]")
    console.print(
        "[dim]Mark the `applied` column once you've actually sent one. "
        "Read a brief with: job-agent log --brief <company>[/dim]"
    )


@app.command()
def schedule(
    at: str = typer.Option("08:00", "--at", help="Daily run time, HH:MM"),
    remove: bool = typer.Option(False, "--remove", help="Uninstall the daily job"),
) -> None:
    """Install (or remove) the daily macOS launchd job."""
    config = load_config()
    label = "com.jobagent.daily"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    if remove:
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False,
                       capture_output=True)
        plist_path.unlink(missing_ok=True)
        console.print("[green]✓[/green] Daily run removed")
        return

    try:
        hour, minute = (int(x) for x in at.split(":"))
    except ValueError:
        console.print("[red]--at must look like 08:00[/red]")
        raise typer.Exit(1)

    python = Path(sys.executable)
    logs = config.root / "data"
    logs.mkdir(parents=True, exist_ok=True)

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>job_agent.cli</string>
    <string>run</string>
  </array>
  <key>WorkingDirectory</key><string>{config.root}</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>{hour}</integer>
        <key>Minute</key><integer>{minute}</integer></dict>
  <key>StandardOutPath</key><string>{logs / 'daily.log'}</string>
  <key>StandardErrorPath</key><string>{logs / 'daily.error.log'}</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
""",
        encoding="utf-8",
    )

    subprocess.run(["launchctl", "unload", str(plist_path)], check=False,
                   capture_output=True)
    result = subprocess.run(["launchctl", "load", str(plist_path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]launchctl load failed:[/red] {result.stderr.strip()}")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Running daily at {at}")
    console.print(f"  logs: {logs / 'daily.log'}")
    console.print(f"  remove with: job-agent schedule --remove")


# --------------------------------------------------------------------------- #


def _posting_from(target: str) -> JobPosting:
    """Build a JobPosting from a URL or a local file of JD text."""
    path = Path(target)
    if path.exists():
        return JobPosting(
            source="manual",
            title=path.stem.replace("_", " ").title(),
            company="",
            description=path.read_text(encoding="utf-8", errors="replace"),
        )

    http = PoliteClient()
    response = http.get(target)
    http.close()
    if response is None:
        return JobPosting(source="manual", title="", company="")

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(response.text, "lxml")
    title_el = soup.find("title")
    main = soup.find("main") or soup.find("article") or soup.body
    return JobPosting(
        source="manual",
        title=title_el.get_text(strip=True) if title_el else target,
        company="",
        url=target,
        description=strip_html(str(main)) if main else "",
    )


def _print_summary(summary, target: float) -> None:
    table = Table(show_header=True, header_style="bold", title=None)
    table.add_column("Score", justify="right", width=7)
    table.add_column("Was", justify="right", width=5, style="dim")
    table.add_column("Company")
    table.add_column("Role")

    ranked = sorted(
        summary.results,
        key=lambda r: (r.final_score.total if r.final_score else -1),
        reverse=True,
    )
    for result in ranked:
        if not result.final_score:
            continue
        colour = "green" if result.reached_target else "yellow"
        table.add_row(
            f"[{colour}]{result.final_score.total:.0f}[/{colour}]",
            f"{result.base_score.total:.0f}" if result.base_score else "—",
            result.posting.company,
            result.posting.title,
        )

    if table.row_count:
        console.print(table)
    console.print(
        f"{summary.jobs_found} found · {summary.jobs_new} new · "
        f"{summary.jobs_tailored} tailored · "
        f"[green]{summary.jobs_at_target}[/green] at {target:.0f}+"
    )


if __name__ == "__main__":
    app()
