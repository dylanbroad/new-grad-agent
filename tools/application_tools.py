"""Tools for the Application WORKFLOW.

These are plain functions called in a fixed order by
workflow/application_workflow.py. There is no tool-use loop here on
purpose: the sequence never varies, so a model deciding "what to call
next" would add cost and unpredictability without benefit.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

import anthropic
import requests
import trafilatura
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from db import get_conn
from tools.logging_utils import logged_tool

load_dotenv()

NEW_GRAD_README_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md"
)

_FETCH_RETRIES = 3
_FETCH_BACKOFF_SECONDS = 2
_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; new-grad-agent/1.0)"}

EXPERIENCE_BANK_PATH = Path(__file__).parent.parent / "experience_bank.json"

ANTHROPIC_MODEL = "claude-sonnet-4-6"
GROQ_MODEL = "openai/gpt-oss-120b"


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


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response.

    Models occasionally wrap JSON in prose or a code fence even when
    told not to, so this is a defensive fallback around json.loads.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"no JSON object found in model response: {text[:200]}")
        return json.loads(match.group(0))


def _llm_completion(system_prompt: str, user_content: str, max_tokens: int) -> str:
    """Single LLM call, on whichever provider is configured.

    Prefers Anthropic (the project's real target) when ANTHROPIC_API_KEY is
    set; falls back to Groq (OpenAI-compatible, cheap/free) when only
    GROQ_API_KEY is set, for ad hoc testing without an Anthropic key.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        return "".join(block.text for block in response.content if block.type == "text")
    elif os.environ.get("GROQ_API_KEY"):
        from groq import Groq  # optional dep, only needed for this fallback path

        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        return response.choices[0].message.content
    else:
        raise RuntimeError(
            "no LLM provider configured - set ANTHROPIC_API_KEY (or GROQ_API_KEY for ad hoc testing)"
        )


def _llm_json_completion(system_prompt: str, user_content: str, max_tokens: int) -> dict:
    """_llm_completion, parsed as JSON, with one retry at double the token
    budget if the response got cut off mid-object (truncated JSON)."""
    text = _llm_completion(system_prompt, user_content, max_tokens)
    try:
        return _extract_json(text)
    except (ValueError, json.JSONDecodeError):
        text = _llm_completion(system_prompt, user_content, max_tokens * 2)
        return _extract_json(text)


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
    return _llm_json_completion(system_prompt, user_content, max_tokens=1024)


@logged_tool
def optimize_resume_bullets(jd_text: str, experience_bank: dict, gap_analysis: dict) -> dict:
    """Pick and lightly rewrite the most relevant bullets for this JD.

    Selects a subset of each job/project's own bullets from the experience
    bank and tweaks wording/keywords to match the JD - never more bullets
    than the source has, never a claim/number not already present in it.

    Only meaningful to call when diff_resume's similarity_score clears
    the workflow's threshold; the branch decision itself lives in
    workflow/application_workflow.py, not here.

    Returns: {"jobs": [{"company", "title", "bullets": [...]}],
              "projects": [{"name", "bullets": [...]}]}.
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
    return _llm_json_completion(system_prompt, user_content, max_tokens=2048)


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


# @logged_tool
# def draft_cover_letter(jd_text: str, resume_text: str, gap_analysis: str) -> str:
#     """Draft a tailored cover letter. TODO: single LLM call."""
#     return "[STUB] cover letter draft"


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
