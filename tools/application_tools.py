"""Tools for the Application WORKFLOW.

These are plain functions called in a fixed order by
workflow/application_workflow.py. There is no tool-use loop here on
purpose: the sequence never varies, so a model deciding "what to call
next" would add cost and unpredictability without benefit.
"""

import hashlib

from db import get_conn
from tools.logging_utils import logged_tool


@logged_tool
def fetch_job_posting(url: str) -> str:
    """Scrape and extract the job description text from a posting URL.

    TODO: implement with requests + trafilatura/BeautifulSoup, with a
    retry/backoff wrapper around the network call (sites will occasionally
    time out or rate-limit).
    """
    return f"[STUB] job description text fetched from {url}"


@logged_tool
def diff_resume(jd_text: str, resume_text: str) -> str:
    """Identify gaps between the JD's requirements and the resume's bullets.

    TODO: single LLM call, not agentic - this is content generation
    (summarize gaps), not a decision about what to do next.
    """
    return "[STUB] gap analysis: JD asks for X, Y, Z - resume covers X, Y, missing Z"


@logged_tool
def draft_cover_letter(jd_text: str, resume_text: str, gap_analysis: str) -> str:
    """Draft a tailored cover letter. TODO: single LLM call."""
    return "[STUB] cover letter draft"


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


@logged_tool
def upsert_application(company: str, role: str, url: str, jd_text: str = "", resume_diff: str = "") -> dict:
    """Idempotent insert/update keyed on url_hash - re-adding the same
    posting updates the row instead of creating a duplicate.
    """
    url_hash = _hash_url(url)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO applications (company, role, url, url_hash, jd_text, resume_diff)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(url_hash) DO UPDATE SET
                   jd_text = excluded.jd_text,
                   resume_diff = excluded.resume_diff,
                   updated_at = datetime('now')""",
            (company, role, url, url_hash, jd_text, resume_diff),
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
