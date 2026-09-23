"""Application WORKFLOW - predefined code path, per the workflow/agent split.

Steps always run in this order. The LLM (inside diff_resume and
optimize_resume_bullets) generates content, but never decides what
happens next - that's decided here, in code. This is the correct shape
for a task where the path forward is already fully known.

The similarity-score branch is a plain if-statement, not the model
deciding: diff_resume returns a number, and the workflow (not the LLM)
decides whether that number clears the bar for spending an extra LLM
call optimizing bullets. Same principle as the human-approval gate below.

process_posting() is the reusable core (fetch -> analyze -> tailor+PDF ->
persist) with no interactive prompt, so both run_application_intake
(the CLI path, which adds the approval gate on top) and batch_runner.py
(unattended, can't block on input()) share the exact same pipeline
instead of two versions drifting apart.
"""

import json
import re
from pathlib import Path

from tools.application_tools import (
    fetch_job_posting,
    load_experience_bank,
    diff_resume,
    optimize_resume_bullets,
    upsert_application,
    set_application_status,
)
from tools.resume_render import render_resume_pdf

# Below this, don't bother spending the extra LLM call tailoring bullets -
# the experience bank just isn't a good match for this posting.
SIMILARITY_THRESHOLD = 60

RESUME_OUTPUT_DIR = Path(__file__).parent.parent / "output" / "resumes"


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "untitled"


def process_posting(company: str, role: str, url: str, experience_bank: dict | None = None) -> dict:
    """Fetch, analyze, optionally tailor + render a PDF, and persist one
    posting. No interactive gate - status stays 'found' until something
    else (the CLI's approval prompt, or a manual status update) advances it.
    """
    if experience_bank is None:
        experience_bank = load_experience_bank()

    # Step 1: fetch
    jd_text = fetch_job_posting(url)

    # Step 2: analyze - how well does the experience bank match this JD?
    gap_analysis = diff_resume(jd_text, experience_bank)
    similarity_score = gap_analysis.get("similarity_score", 0)

    # Step 3: only tailor bullets (and render a submittable resume) if the match is worth it
    tailored_resume = None
    resume_pdf_path = ""
    if similarity_score >= SIMILARITY_THRESHOLD:
        tailored_resume = optimize_resume_bullets(jd_text, experience_bank, gap_analysis)

        RESUME_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        pdf_path = RESUME_OUTPUT_DIR / f"{_slugify(company)}_{_slugify(role)}.pdf"
        render_resume_pdf(tailored_resume, experience_bank, pdf_path)
        resume_pdf_path = str(pdf_path)

    # Step 4: persist (idempotent on url_hash)
    application = upsert_application(
        company=company,
        role=role,
        url=url,
        jd_text=jd_text,
        resume_diff=json.dumps(gap_analysis),
        similarity_score=similarity_score,
        tailored_resume=json.dumps(tailored_resume) if tailored_resume else "",
        resume_pdf_path=resume_pdf_path,
    )

    return {
        "application": application,
        "gap_analysis": gap_analysis,
        "tailored_resume": tailored_resume,
        "resume_pdf_path": resume_pdf_path,
    }


def run_application_intake(company: str, role: str, url: str) -> dict:
    result = process_posting(company, role, url)
    application = result["application"]
    gap_analysis = result["gap_analysis"]
    similarity_score = gap_analysis.get("similarity_score", 0)

    print(f"\n--- Similarity score: {similarity_score}/100 ---")
    print(gap_analysis.get("summary", ""))
    if gap_analysis.get("missing_skills"):
        print(f"Missing: {', '.join(gap_analysis['missing_skills'])}")

    if result["resume_pdf_path"]:
        print(f"\nTailored resume written to {result['resume_pdf_path']}")
    else:
        print(f"\nSimilarity below {SIMILARITY_THRESHOLD} - skipped bullet tailoring and resume generation.")

    # Human-approval gate - the workflow does NOT auto-advance status.
    approve = input("\nMark this application as 'applied'? [y/N]: ").strip().lower()
    if approve == "y":
        application = set_application_status(url, "applied")
        print("Status updated to 'applied'.")
    else:
        print("Left as 'found' - nothing was sent.")

    return application


if __name__ == "__main__":
    run_application_intake(
        company=input("Company: "),
        role=input("Role: "),
        url=input("Job posting URL: "),
    )
