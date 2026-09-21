"""MCP server exposing the job-search copilot's tools to any MCP client
(Claude Desktop, Claude Code, etc.) over stdio.

This is a thin adapter, not a reimplementation: every tool here just
calls straight into tools/application_tools.py, so there's exactly one
place the actual scraping/LLM/DB logic lives. Two differences from
workflow/application_workflow.py's run_application_intake:

1. These tools are composable, not a fixed sequence - an MCP client
   (a model) decides which to call and in what order, which is the
   whole point of exposing them this way rather than one big function.
2. No input() approval gate - a client can't answer a blocking stdin
   prompt, so marking an application "applied" is just its own tool
   call (set_application_status) instead of an inline y/N prompt.
"""

from mcp.server.mcpserver import MCPServer

from tools.application_tools import (
    fetch_job_posting as _fetch_job_posting,
    list_new_grad_postings as _list_new_grad_postings,
    load_experience_bank,
    diff_resume,
    optimize_resume_bullets,
    render_resume_text,
    upsert_application as _upsert_application,
    set_application_status as _set_application_status,
)

server = MCPServer("new-grad-agent")


@server.tool()
def list_new_grad_postings() -> list[dict]:
    """List currently open new-grad postings scraped from the SimplifyJobs
    New-Grad-Positions GitHub repo. Each entry has company, role,
    locations, url, and age (e.g. "2d")."""
    return _list_new_grad_postings()


@server.tool()
def fetch_job_posting(url: str) -> str:
    """Scrape a job posting URL and return its job description text."""
    return _fetch_job_posting(url)


@server.tool()
def analyze_job_fit(jd_text: str) -> dict:
    """Score how well the candidate's experience bank matches a job
    description. Returns similarity_score (0-100), matched_skills,
    missing_skills, and a short summary."""
    experience_bank = load_experience_bank()
    return diff_resume(jd_text, experience_bank)


@server.tool()
def tailor_resume_bullets(jd_text: str, gap_analysis: dict) -> dict:
    """Pick and lightly rewrite the most relevant resume bullets for a JD,
    given the gap_analysis returned by analyze_job_fit. Only worth calling
    when similarity_score is reasonably high. Returns the selected/reworded
    bullets per job/project plus a plain-text rendering of the resume."""
    experience_bank = load_experience_bank()
    tailored = optimize_resume_bullets(jd_text, experience_bank, gap_analysis)
    return {**tailored, "resume_text": render_resume_text(tailored, experience_bank)}


@server.tool()
def save_application(
    company: str,
    role: str,
    url: str,
    jd_text: str = "",
    gap_analysis: dict | None = None,
    tailored_resume: dict | None = None,
) -> dict:
    """Save (or update, if the url was already saved) an application record,
    including its similarity score and tailored bullets if computed."""
    import json

    return _upsert_application(
        company=company,
        role=role,
        url=url,
        jd_text=jd_text,
        resume_diff=json.dumps(gap_analysis) if gap_analysis else "",
        similarity_score=(gap_analysis or {}).get("similarity_score"),
        tailored_resume=json.dumps(tailored_resume) if tailored_resume else "",
    )


@server.tool()
def set_application_status(url: str, status: str) -> dict:
    """Move a saved application through found -> applied -> interviewing ->
    offer/rejected."""
    return _set_application_status(url, status)


if __name__ == "__main__":
    server.run()
