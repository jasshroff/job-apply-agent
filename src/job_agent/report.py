"""The daily HTML digest.

One self-contained file per run: what was found, what was tailored, what score
each landed on, and — when something fell short — exactly which requirements it
fell short on. That last part is the useful bit; a gap list is a study plan.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Template

from .models import RunSummary

TEMPLATE = Template(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job matches — {{ date }}</title>
<style>
  :root {
    --bg: #f6f7f9; --surface: #ffffff; --text: #16191d; --muted: #5f6773;
    --line: #e2e5ea; --accent: #1f3b57; --good: #17784a; --warn: #a25c00;
    --good-bg: #e6f4ec; --warn-bg: #fdf1e0;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #14161a; --surface: #1c1f25; --text: #e8eaed; --muted: #9aa3af;
      --line: #2c313a; --accent: #7fb0dd; --good: #5fd39b; --warn: #e0a955;
      --good-bg: #163024; --warn-bg: #33270f;
    }
  }
  * { box-sizing: border-box; }
  body { margin:0; padding:28px 20px 60px; background:var(--bg); color:var(--text);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  .wrap { max-width: 1000px; margin: 0 auto; }
  h1 { font-size: 25px; margin: 0 0 4px; letter-spacing: -0.01em; }
  .sub { color: var(--muted); margin: 0 0 24px; font-size: 14px; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px,1fr));
    gap: 10px; margin-bottom: 26px; }
  .stat { background: var(--surface); border: 1px solid var(--line);
    border-radius: 10px; padding: 13px 15px; }
  .stat b { display: block; font-size: 26px; line-height: 1.15;
    font-variant-numeric: tabular-nums; }
  .stat span { color: var(--muted); font-size: 12px; text-transform: uppercase;
    letter-spacing: .05em; }
  .job { background: var(--surface); border: 1px solid var(--line);
    border-radius: 10px; padding: 17px 19px; margin-bottom: 13px; }
  .job-head { display:flex; justify-content:space-between; gap:16px;
    align-items:flex-start; flex-wrap:wrap; }
  .job h2 { font-size: 17px; margin: 0 0 3px; }
  .job h2 a { color: var(--accent); text-decoration: none; }
  .job h2 a:hover { text-decoration: underline; }
  .meta { color: var(--muted); font-size: 13px; }
  .score { text-align: right; white-space: nowrap; }
  .score b { font-size: 27px; font-variant-numeric: tabular-nums; }
  .badge { display:inline-block; padding:2px 9px; border-radius:999px;
    font-size:11.5px; font-weight:600; }
  .hit { background: var(--good-bg); color: var(--good); }
  .miss { background: var(--warn-bg); color: var(--warn); }
  .delta { color: var(--muted); font-size: 12.5px; }
  .bars { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
    gap:9px; margin:13px 0 4px; }
  .bar span { display:flex; justify-content:space-between; font-size:11.5px;
    color:var(--muted); margin-bottom:3px; }
  .track { height:5px; background:var(--line); border-radius:3px; overflow:hidden; }
  .fill { height:100%; background:var(--accent); border-radius:3px; }
  details { margin-top: 11px; }
  summary { cursor: pointer; font-size: 13px; color: var(--muted); }
  .gap { background: var(--warn-bg); color: var(--warn); border-radius: 7px;
    padding: 10px 13px; margin-top: 10px; font-size: 13px; white-space: pre-wrap; }
  .files { margin-top: 11px; font-size: 13px; }
  .files a { color: var(--accent); margin-right: 14px; }
  .chips { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 5px; }
  .chip { font-size: 11.5px; padding: 2px 8px; border-radius: 5px;
    background: var(--bg); border: 1px solid var(--line); color: var(--muted); }
  footer { margin-top: 34px; color: var(--muted); font-size: 12.5px; }
  .empty { background:var(--surface); border:1px solid var(--line);
    border-radius:10px; padding:34px; text-align:center; color:var(--muted); }
</style>
</head>
<body><div class="wrap">

<h1>Job matches — {{ date }}</h1>
<p class="sub">{{ started }} → {{ finished }} · target score {{ target }}</p>

<div class="stats">
  <div class="stat"><b>{{ s.jobs_found }}</b><span>found</span></div>
  <div class="stat"><b>{{ s.jobs_new }}</b><span>new</span></div>
  <div class="stat"><b>{{ s.jobs_tailored }}</b><span>tailored</span></div>
  <div class="stat"><b>{{ s.jobs_at_target }}</b><span>hit {{ target }}+</span></div>
</div>

{% if not results %}
  <div class="empty">
    No new postings cleared the filters today.<br>
    Loosen <code>min_prescreen_score</code> or widen <code>search.titles</code>
    in <code>config.yaml</code> to cast a wider net.
  </div>
{% endif %}

{% for r in results %}
<div class="job">
  <div class="job-head">
    <div>
      <h2>{% if r.posting.url %}<a href="{{ r.posting.url }}" target="_blank"
        rel="noopener">{{ r.posting.title }}</a>{% else %}{{ r.posting.title }}
        {% endif %}</h2>
      <div class="meta">
        {{ r.posting.company }}
        {% if r.posting.location %} · {{ r.posting.location }}{% endif %}
        {% if r.posting.salary %} · {{ r.posting.salary }}{% endif %}
        · <span class="chip">{{ r.posting.source }}</span>
      </div>
    </div>
    {% if r.final_score %}
    <div class="score">
      <b>{{ '%.0f'|format(r.final_score.total) }}</b><br>
      <span class="badge {{ 'hit' if r.reached_target else 'miss' }}">
        {{ 'match' if r.reached_target else 'honest ceiling' }}</span><br>
      <span class="delta">was {{ '%.0f'|format(r.base_score.total) }}
        (+{{ '%.0f'|format(r.improvement) }})</span>
    </div>
    {% endif %}
  </div>

  {% if r.final_score %}
  <div class="bars">
    {% for label, value in [
        ('Hard skills', r.final_score.hard_skill_coverage),
        ('ATS keywords', r.final_score.keyword_coverage),
        ('Title fit', r.final_score.title_alignment),
        ('Responsibilities', r.final_score.responsibility_match)] %}
    <div class="bar">
      <span>{{ label }}<em>{{ '%.0f'|format(value) }}%</em></span>
      <div class="track"><div class="fill" style="width:{{ value }}%"></div></div>
    </div>
    {% endfor %}
  </div>
  {% endif %}

  {% if r.honest_ceiling_reason %}
  <div class="gap"><strong>Why it stops short of {{ target }}:</strong>
{{ r.honest_ceiling_reason }}</div>
  {% endif %}

  {% if r.error %}<div class="gap"><strong>Error:</strong> {{ r.error }}</div>{% endif %}

  {% if r.files %}
  <div class="files">
    {% for kind, path in r.files.items() %}
      <a href="file://{{ path }}">{{ kind }}</a>
    {% endfor %}
  </div>
  {% endif %}

  {% if r.final_score and r.final_score.missing_skills %}
  <details>
    <summary>{{ r.final_score.missing_skills|length }} requirement(s) with no
      evidence in your profile</summary>
    <div class="chips">
      {% for skill in r.final_score.missing_skills %}
        <span class="chip">{{ skill }}</span>
      {% endfor %}
    </div>
  </details>
  {% endif %}
</div>
{% endfor %}

{% if s.source_errors %}
<details>
  <summary>{{ s.source_errors|length }} source(s) had problems</summary>
  <div class="chips">
    {% for name, err in s.source_errors.items() %}
      <span class="chip">{{ name }}: {{ err }}</span>
    {% endfor %}
  </div>
</details>
{% endif %}

<footer>
  Every claim in these resumes is grounded in your master profile — the agent
  rewrites and re-emphasises, it does not invent. Read before you send.
</footer>

</div></body></html>"""
)


def write_report(summary: RunSummary, output_dir: Path, target: float) -> Path:
    """Render the run to a self-contained HTML file and return its path."""
    date = summary.started_at.date().isoformat()
    directory = output_dir / date
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "index.html"

    ranked = sorted(
        summary.results,
        key=lambda r: (r.final_score.total if r.final_score else -1),
        reverse=True,
    )

    html = TEMPLATE.render(
        s=summary,
        results=ranked,
        date=date,
        target=int(target),
        started=summary.started_at.strftime("%H:%M:%S"),
        finished=(summary.finished_at or datetime.now()).strftime("%H:%M:%S"),
    )
    path.write_text(html, encoding="utf-8")
    return path
