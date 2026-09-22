"""Tools for the Application WORKFLOW.

These are plain functions called in a fixed order by
workflow/application_workflow.py. There is no tool-use loop here on
purpose: the sequence never varies, so a model deciding "what to call
next" would add cost and unpredictability without benefit.
"""

import hashlib
import json
import re
import time
from pathlib import Path

import requests
import trafilatura
from bs4 import BeautifulSoup

from db import get_conn
from tools.llm_utils import llm_json_completion  # also loads .env as a side effect
from tools.logging_utils import logged_tool

NEW_GRAD_README_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md"
)

_FETCH_RETRIES = 3
_FETCH_BACKOFF_SECONDS = 2
_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; new-grad-agent/1.0)"}

EXPERIENCE_BANK_PATH = Path(__file__).parent.parent / "experience_bank.json"


@logged_tool
def fetch_job_posting(url: str) -> str:
    """Scrape and extract the job description text from a posting URL.

    Retries with exponential backoff since posting sites occasionally
    time out or rate-limit.
    """
    last_error = None
    for attempt in range(_FETCH_RETRIES):
        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                text = trafilatura.extract(downloaded)
                if text:
                    return text
            resp = requests.get(url, headers=_REQUEST_HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text(separator="\n", strip=True)
            if text:
                return text
            last_error = ValueError("no extractable text on page")
        except (requests.RequestException, ValueError) as exc:
            last_error = exc

        if attempt < _FETCH_RETRIES - 1:
            time.sleep(_FETCH_BACKOFF_SECONDS * (2**attempt))

    raise RuntimeError(f"failed to fetch job posting from {url}: {last_error}")


@logged_tool
def list_new_grad_postings(readme_url: str = NEW_GRAD_README_URL) -> list[dict]:
    """Scrape open roles from the SimplifyJobs New-Grad-Positions README.

    That repo tracks postings as an HTML table (company, role, location,
    application links, age) inside its README.md rather than an API, so
    this pulls the raw markdown and parses the table directly. Closed
    listings (marked with a lock emoji instead of an apply link) are
    skipped since there's no URL to hand to fetch_job_posting.

    Returns one dict per open posting: company, role, locations, url, age.
    Feed each url into fetch_job_posting to pull the actual JD text.
    """
    resp = requests.get(readme_url, headers=_REQUEST_HEADERS, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    postings = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) != 5:
            continue  # header row or malformed row

        company_cell, role_cell, location_cell, application_cell, age_cell = cells
        apply_link = application_cell.find("a")
        if apply_link is None or not apply_link.get("href"):
            continue  # closed (locked) listing, no application URL

        locations = [
            loc.strip()
            for loc in location_cell.get_text(separator="|").split("|")
            if loc.strip()
        ]

        postings.append(
            {
                "company": company_cell.get_text(strip=True),
                "role": role_cell.get_text(strip=True),
                "locations": locations,
                "url": apply_link["href"],
                "age": age_cell.get_text(strip=True),
            }
        )

    return postings


def load_experience_bank(path: Path = EXPERIENCE_BANK_PATH) -> dict:
    """Load the master list of jobs/projects/ECs to pick resume bullets from.

    This is a hand-maintained JSON file, not a DB table - unlike
    applications/weak_spots (which the program writes), this is content
    only the user edits, so a plain file is simpler than a migration.
    """
    return json.loads(path.read_text())


def render_experience_bank_text(experience_bank: dict) -> str:
    """Flatten the experience bank into plain text for prompting the LLM."""
    lines = []

    skills = experience_bank.get("skills") or []
    if skills:
        lines.append("Skills: " + ", ".join(skills))

    for job in experience_bank.get("jobs", []):
        lines.append(f"\n{job['company']} - {job['title']} ({job['start']} - {job['end']})")
        for bullet in job.get("bullets", []):
            lines.append(f"- {bullet}")

    for project in experience_bank.get("projects", []):
        lines.append(f"\nProject: {project['name']}")
        for bullet in project.get("bullets", []):
            lines.append(f"- {bullet}")

    for ec in experience_bank.get("extracurriculars", []):
        lines.append(f"\n{ec['name']}")
        for bullet in ec.get("bullets", []):
            lines.append(f"- {bullet}")

    return "\n".join(lines)


@logged_tool
def diff_resume(jd_text: str, experience_bank: dict) -> dict:
    """Score how well the experience bank matches a JD and identify gaps.

    Single LLM call - this is content generation/analysis (summarize the
    match), not a decision about what happens next. The workflow decides
    what to do with the score.

    Returns: {"similarity_score": 0-100, "matched_skills": [...],
              "missing_skills": [...], "summary": "..."}.
    """
    resume_text = render_experience_bank_text(experience_bank)
    system_prompt = (
        "You compare a candidate's work experience against a job description "
        "and score the match. Respond with ONLY a JSON object, no prose, no "
        "code fence, matching this exact shape:\n"
        '{"similarity_score": <integer 0-100>, '
        '"matched_skills": [<strings>], '
        '"missing_skills": [<strings>], '
        '"summary": "<2-3 sentence gap analysis>"}'
    )
    user_content = f"Job description:\n{jd_text}\n\nCandidate experience:\n{resume_text}"
    return llm_json_completion(system_prompt, user_content, max_tokens=1024)


_NUMBER_PATTERN = re.compile(r"\d[\d,]*\.?\d*%?\+?")
_OPTIMIZE_MAX_ATTEMPTS = 3

# ~2 lines at the font/margins of the source resume template (measured off
# the longest bullet that's known to wrap to exactly 2 lines in the PDF).
_MAX_BULLET_CHARS = 220

# Total bullets already known to fit on one page in the source resume
# (3+3+3+3+2+2 across the 6 jobs, +2 for the one project = 18). This is a
# fixed budget, not derived from the bank, because the bank is meant to
# grow past one page's worth of content - once it does, staying on one
# page becomes "pick which jobs/projects to include," not just "trim
# bullets," which this function doesn't do yet.
_MAX_TOTAL_BULLETS = 18


def _extract_numbers(text: str) -> set[str]:
    """Numeric tokens (percentages, counts, durations) for checking that
    metrics survive a rewrite unchanged."""
    return set(_NUMBER_PATTERN.findall(text))


def _validate_tailored_resume(tailored: dict, experience_bank: dict) -> list[str]:
    """Check the model's bullet selection against the hard rules in code,
    rather than trusting the prompt alone. Returns violation descriptions
    (empty list means clean)."""
    violations = []
    source_jobs = {j["company"]: j for j in experience_bank.get("jobs", [])}
    source_projects = {p["name"]: p for p in experience_bank.get("projects", [])}
    total_bullets = 0

    def check_entries(entries: list[dict], sources: dict, key: str, label: str) -> None:
        nonlocal total_bullets
        for entry in entries:
            name = entry.get(key, "")
            source = sources.get(name)
            if source is None:
                violations.append(f"{label} '{name}' is not in the experience bank")
                continue

            bullets = entry.get("bullets", [])
            total_bullets += len(bullets)
            source_bullets = source.get("bullets", [])
            if len(bullets) > len(source_bullets):
                violations.append(
                    f"{name}: output {len(bullets)} bullets but source only has {len(source_bullets)}"
                )

            source_numbers = _extract_numbers(" ".join(source_bullets))
            for bullet in bullets:
                extra = _extract_numbers(bullet) - source_numbers
                if extra:
                    violations.append(
                        f"{name}: bullet has numbers not present in its source bullets "
                        f"({', '.join(sorted(extra))}): \"{bullet}\""
                    )
                if len(bullet) > _MAX_BULLET_CHARS:
                    violations.append(
                        f"{name}: bullet is {len(bullet)} chars, over the {_MAX_BULLET_CHARS}-char "
                        f"(~2 line) limit: \"{bullet}\""
                    )

    check_entries(tailored.get("jobs", []), source_jobs, "company", "job")
    check_entries(tailored.get("projects", []), source_projects, "name", "project")

    if total_bullets > _MAX_TOTAL_BULLETS:
        violations.append(
            f"resume has {total_bullets} bullets total, over the {_MAX_TOTAL_BULLETS}-bullet "
            "one-page budget - cut the least JD-relevant bullets, keeping each job/project's "
            "count in the same rough proportion"
        )

    return violations


@logged_tool
def optimize_resume_bullets(jd_text: str, experience_bank: dict, gap_analysis: dict) -> dict:
    """Pick and lightly rewrite the most relevant bullets for this JD.

    Selects a subset of each job/project's own bullets from the experience
    bank and tweaks wording/keywords to match the JD - never more bullets
    than the source has, never a claim/number not already present in it,
    each bullet short enough to hold to ~2 lines, and the whole resume
    within a one-page bullet budget. _validate_tailored_resume checks all
    of this in code and, if the model violates a rule, retries with the
    specific violations fed back to it (up to _OPTIMIZE_MAX_ATTEMPTS)
    rather than trusting the prompt alone.

    Only meaningful to call when diff_resume's similarity_score clears
    the workflow's threshold; the branch decision itself lives in
    workflow/application_workflow.py, not here.

    Returns: {"jobs": [{"company", "title", "bullets": [...]}],
              "projects": [{"name", "bullets": [...]}]}.

    Raises ValueError if the model still violates the rules after
    _OPTIMIZE_MAX_ATTEMPTS tries.
    """
    system_prompt = (
        "You tailor a candidate's resume bullets to a specific job description. "
        "For each job and project given, select the most relevant bullets from "
        "ONLY that job/project's own source bullets and lightly rewrite them to "
        "emphasize the wording/keywords the JD cares about.\n\n"
        "Hard rules:\n"
        "- Never output more bullets for a job/project than it has in the source "
        "data - if it has 2 source bullets, output at most 2, never 3.\n"
        "- Every output bullet must be a reworded version of exactly one source "
        "bullet. Do not merge two bullets into one, and do not add a bullet whose "
        "core claim isn't already in the source data.\n"
        "- Preserve every number, percentage, and metric from the source bullet "
        "you are rewording exactly as given - never drop, round, or alter them.\n"
        f"- Keep every bullet under {_MAX_BULLET_CHARS} characters, so it holds to "
        "about 2 lines on a resume - trim wording, don't drop the metric, if a "
        "rewrite runs long.\n"
        f"- Keep the total bullet count across every job and project at or under "
        f"{_MAX_TOTAL_BULLETS}, so the whole resume still fits one page - if the "
        "experience bank has more bullets available than that, favor the "
        "jobs/projects most relevant to this JD and drop the rest.\n"
        "- You may reorder, reword, and re-emphasize, but never invent new facts, "
        "technologies, or accomplishments.\n\n"
        "Respond with ONLY a JSON object, no prose, no code fence, matching this "
        "exact shape:\n"
        '{"jobs": [{"company": "...", "title": "...", "bullets": ["...", "..."]}], '
        '"projects": [{"name": "...", "bullets": ["...", "..."]}]}'
    )
    user_content = (
        f"Job description:\n{jd_text}\n\n"
        f"Gap analysis:\n{json.dumps(gap_analysis)}\n\n"
        f"Experience bank:\n{json.dumps(experience_bank)}"
    )

    violations = []
    for attempt in range(_OPTIMIZE_MAX_ATTEMPTS):
        if violations:
            user_content += (
                "\n\nYour previous attempt broke these hard rules - fix them and "
                "resend the full JSON object:\n" + "\n".join(f"- {v}" for v in violations)
            )
        tailored = llm_json_completion(system_prompt, user_content, max_tokens=2048)
        violations = _validate_tailored_resume(tailored, experience_bank)
        if not violations:
            return _reorder_to_match_bank(tailored, experience_bank)

    raise ValueError(
        f"optimize_resume_bullets still violated hard rules after {_OPTIMIZE_MAX_ATTEMPTS} attempts: {violations}"
    )


def _reorder_to_match_bank(tailored: dict, experience_bank: dict) -> dict:
    """Put jobs/projects back in the experience bank's own order.

    The model's JSON key order isn't guaranteed to match the bank's
    (reverse-chronological) order, and resume formatting should never
    depend on that - so re-sort by the bank's order rather than trusting
    whatever order came back.
    """
    job_order = [j["company"] for j in experience_bank.get("jobs", [])]
    project_order = [p["name"] for p in experience_bank.get("projects", [])]

    jobs = sorted(tailored.get("jobs", []), key=lambda j: job_order.index(j["company"]))
    projects = sorted(tailored.get("projects", []), key=lambda p: project_order.index(p["name"]))
    return {**tailored, "jobs": jobs, "projects": projects}


def render_resume_text(tailored_resume: dict, experience_bank: dict | None = None) -> str:
    """Format a tailored-bullets result (or the raw bank) into plain text."""
    lines = []

    if experience_bank:
        contact = experience_bank.get("contact", {})
        if contact:
            lines.append(contact.get("name", ""))
            lines.append(
                " | ".join(
                    v for v in (contact.get("email"), contact.get("phone"), contact.get("linkedin")) if v
                )
            )
            lines.append("")

    for job in tailored_resume.get("jobs", []):
        header = job.get("company", "")
        if job.get("title"):
            header += f" - {job['title']}"
        lines.append(header)
        for bullet in job.get("bullets", []):
            lines.append(f"- {bullet}")
        lines.append("")

    for project in tailored_resume.get("projects", []):
        lines.append(f"Project: {project.get('name', '')}")
        for bullet in project.get("bullets", []):
            lines.append(f"- {bullet}")
        lines.append("")

    return "\n".join(lines).strip()


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


@logged_tool
def upsert_application(
    company: str,
    role: str,
    url: str,
    jd_text: str = "",
    resume_diff: str = "",
    similarity_score: int | None = None,
    tailored_resume: str = "",
) -> dict:
    """Idempotent insert/update keyed on url_hash - re-adding the same
    posting updates the row instead of creating a duplicate.
    """
    url_hash = _hash_url(url)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO applications
                   (company, role, url, url_hash, jd_text, resume_diff, similarity_score, tailored_resume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url_hash) DO UPDATE SET
                   jd_text = excluded.jd_text,
                   resume_diff = excluded.resume_diff,
                   similarity_score = excluded.similarity_score,
                   tailored_resume = excluded.tailored_resume,
                   updated_at = datetime('now')""",
            (company, role, url, url_hash, jd_text, resume_diff, similarity_score, tailored_resume),
        )
        row = conn.execute("SELECT * FROM applications WHERE url_hash = ?", (url_hash,)).fetchone()
    return dict(row)


@logged_tool
def set_application_status(url: str, status: str) -> dict:
    """Move an application through found -> applied -> interviewing -> offer/rejected."""
    url_hash = _hash_url(url)
    with get_conn() as conn:
        conn.execute(
            "UPDATE applications SET status = ?, updated_at = datetime('now') WHERE url_hash = ?",
            (status, url_hash),
        )
        row = conn.execute("SELECT * FROM applications WHERE url_hash = ?", (url_hash,)).fetchone()
    return dict(row) if row else {}
