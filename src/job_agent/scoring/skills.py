"""Skill vocabulary and matching.

Real ATS software matches on surface strings, so "React" and "React.js" and
"ReactJS" are three different tokens to a naive matcher and one skill to a human.
This module collapses that difference so the score reflects whether you actually
have the skill, not whether you happened to spell it the way the JD did.

Two matching tiers:

  1. **Vocabulary match** — the skill is in ``ALIASES``, so we know every surface
     form and can look for all of them.
  2. **Fallback match** — an unknown skill (new framework, niche tool). We
     normalise it and look for the phrase with word boundaries, then fall back to
     fuzzy matching for multi-word phrases.
"""

from __future__ import annotations

import re
from functools import lru_cache

from rapidfuzz import fuzz

# Variant  ->  canonical. Canonical forms are the alphanumeric-collapsed
# lowercase spelling (plus `+` and `#`, which carry meaning in language names).
ALIASES: dict[str, str] = {
    # languages
    "js": "javascript",
    "ecmascript": "javascript",
    "ts": "typescript",
    "py": "python",
    "python3": "python",
    "golang": "go",
    "cpp": "c++",
    "cplusplus": "c++",
    "csharp": "c#",
    "dotnet": ".net",
    "objectivec": "objective-c",
    # frontend
    "react": "reactjs",
    "reactjs": "reactjs",
    "reactnative": "reactnative",
    "vue": "vuejs",
    "vuejs": "vuejs",
    "angularjs": "angular",
    "nextjs": "nextjs",
    "next": "nextjs",
    "tailwind": "tailwindcss",
    "tailwindcss": "tailwindcss",
    "bootstrap5": "bootstrap",
    "html5": "html",
    "css3": "css",
    "sass": "scss",
    "materialui": "mui",
    # backend / runtime
    "node": "nodejs",
    "nodejs": "nodejs",
    "expressjs": "express",
    "nestjs": "nestjs",
    "restapi": "rest",
    "restful": "rest",
    "restfulapi": "rest",
    "restapis": "rest",
    "webservices": "rest",
    "graphqlapi": "graphql",
    "websockets": "websocket",
    "socketio": "socketio",
    "microservice": "microservices",
    "fastapiframework": "fastapi",
    # databases
    "postgres": "postgresql",
    "psql": "postgresql",
    "mongo": "mongodb",
    "ms sql": "sqlserver",
    "mssql": "sqlserver",
    "sqlite3": "sqlite",
    "elasticsearch": "elasticsearch",
    "rdbms": "sql",
    # cloud / infra
    "amazonwebservices": "aws",
    "googlecloud": "gcp",
    "googlecloudplatform": "gcp",
    "microsoftazure": "azure",
    "k8s": "kubernetes",
    "cicd": "ci/cd",
    "continuousintegration": "ci/cd",
    "githubactions": "githubactions",
    "iac": "terraform",
    # AI / ML
    "ml": "machinelearning",
    "machinelearning": "machinelearning",
    "dl": "deeplearning",
    "deeplearning": "deeplearning",
    "ai": "artificialintelligence",
    "artificialintelligence": "artificialintelligence",
    "cv": "computervision",
    "computervision": "computervision",
    "nlp": "nlp",
    "naturallanguageprocessing": "nlp",
    "llm": "llm",
    "llms": "llm",
    "largelanguagemodel": "llm",
    "largelanguagemodels": "llm",
    "genai": "generativeai",
    "generativeai": "generativeai",
    "rag": "rag",
    "retrievalaugmentedgeneration": "rag",
    "cnn": "cnn",
    "convolutionalneuralnetwork": "cnn",
    "convolutionalneuralnetworks": "cnn",
    "rnn": "rnn",
    "vit": "visiontransformer",
    "visiontransformers": "visiontransformer",
    "visiontransformer": "visiontransformer",
    "transformers": "transformer",
    "pytorch": "pytorch",
    "torch": "pytorch",
    "tf": "tensorflow",
    "sklearn": "scikitlearn",
    "scikitlearn": "scikitlearn",
    "opencv": "opencv",
    "yolo": "yolo",
    "yolov8": "yolo",
    "langchain": "langchain",
    "llamaindex": "llamaindex",
    "vectordb": "vectordatabase",
    "vectordatabases": "vectordatabase",
    "embeddings": "embeddings",
    "huggingface": "huggingface",
    "objectdetection": "objectdetection",
    "imageclassification": "imageclassification",
    "transferlearning": "transferlearning",
    "anomalydetection": "anomalydetection",
    # practices / tools
    "versioncontrol": "git",
    "github": "git",
    "gitlab": "git",
    "unittesting": "testing",
    "unittest": "testing",
    "integrationtesting": "testing",
    "tdd": "testing",
    "jest": "jest",
    "pytest": "pytest",
    "agilemethodology": "agile",
    "scrum": "agile",
    "codereview": "codereview",
    "codereviews": "codereview",
    "debugging": "debugging",
    "troubleshooting": "debugging",
    "datastructures": "datastructures",
    "algorithms": "algorithms",
    "dsa": "datastructures",
    "oop": "oop",
    "objectorientedprogramming": "oop",
    "systemdesign": "systemdesign",
    "problemsolving": "problemsolving",
    "docker": "docker",
    "linux": "linux",
    "bash": "bash",
    "shellscripting": "bash",
    "mqtt": "mqtt",
    "iot": "iot",
    "edgecomputing": "edgecomputing",
    "figma": "figma",
    "jira": "jira",
    "postman": "postman",
}

#: Skills whose canonical form is too short to match safely without exact
#: boundaries (a bare "r" or "go" would match half the resume).
SHORT_SKILLS = {"c", "r", "go", "c++", "c#", "ai", "ml", "cv", "js", "ts"}

_NON_TOKEN = re.compile(r"[^a-z0-9+#/.]+")
_STRIP_SUFFIX = re.compile(
    r"\s+(developer|development|engineer|engineering|programming|framework|"
    r"library|language|skills?|experience)$"
)


@lru_cache(maxsize=4096)
def canonical(skill: str) -> str:
    """Reduce a skill name to its canonical comparison form."""
    text = skill.strip().lower()
    text = _STRIP_SUFFIX.sub("", text)
    # Collapse separators, but keep the characters that distinguish C++ / C# / .NET.
    collapsed = _NON_TOKEN.sub("", text).replace(".", "")
    if not collapsed:
        return ""
    # ".net" loses its dot above; restore the few forms where it matters.
    if collapsed == "net":
        collapsed = ".net"
    return ALIASES.get(collapsed, collapsed)


def surface_forms(canon: str) -> set[str]:
    """Every spelling that should count as this canonical skill."""
    forms = {canon}
    forms |= {variant for variant, target in ALIASES.items() if target == canon}
    # Re-introduce readable spacings/punctuation so the regex can find them in prose.
    expanded: set[str] = set()
    for form in forms:
        expanded.add(form)
        if form.endswith("js") and len(form) > 3:
            stem = form[:-2]
            expanded |= {f"{stem}.js", f"{stem} js", stem}
    return {f for f in expanded if f}


def _boundary_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    # `\b` doesn't fire next to `+`/`#`, so guard with explicit lookarounds.
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


class SkillMatcher:
    """Answers "does this resume demonstrate skill X?" for one resume."""

    def __init__(self, resume_text: str, explicit_skills: list[str] | None = None):
        self.text = resume_text.lower()
        self.explicit: set[str] = {
            canonical(s) for s in (explicit_skills or []) if canonical(s)
        }
        # Pre-split into phrases once; fuzzy fallback scans these.
        self._phrases = [
            line.strip() for line in re.split(r"[\n.;•|]", self.text) if line.strip()
        ]

    def has(self, skill: str) -> bool:
        canon = canonical(skill)
        if not canon:
            return False
        if canon in self.explicit:
            return True

        # Tier 1: known vocabulary — check every surface form.
        for form in surface_forms(canon):
            if len(form) < 2 and form not in SHORT_SKILLS:
                continue
            if _boundary_pattern(form).search(self.text):
                return True

        # Tier 2: unknown skill — look for the literal phrase, then fuzzily.
        phrase = skill.strip().lower()
        if len(phrase) >= 3 and _boundary_pattern(phrase).search(self.text):
            return True
        if " " in phrase and len(phrase) >= 8:
            return any(
                fuzz.partial_ratio(phrase, candidate) >= 90
                for candidate in self._phrases
            )
        return False

    def present_and_missing(self, skills: list[str]) -> tuple[list[str], list[str]]:
        present, missing = [], []
        for skill in skills:
            (present if self.has(skill) else missing).append(skill)
        return present, missing
