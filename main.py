"""Entry point. Deliberately a dumb router (if/elif), NOT a supervisor
agent - the choice between these two paths doesn't require reasoning,
just user intent, so a full LLM-driven supervisor would be the kind of
unnecessary complexity the architecture doc warns against.
"""

import json

from db import init_db
from workflow.application_workflow import run_application_intake
from agent.interview_agent import run_interview_prep_session
from tools.application_tools import (
    fetch_job_posting,
    list_applications,
    list_new_grad_postings,
    set_application_status,
)

STATUSES = ["found", "applied", "interviewing", "offer", "rejected"]


def _browse_new_grad_postings():
    """Browse SimplifyJobs postings, preview a JD, and optionally run full intake on it."""
    postings = list_new_grad_postings()
    print(f"Found {len(postings)} open postings\n")

    page_size = 15
    for i, p in enumerate(postings[:page_size]):
        locations = ", ".join(p["locations"])
        print(f"[{i}] {p['company']} - {p['role']} ({locations}) - {p['age']}")

    choice = input(f"\nPick a posting to fetch [0-{min(page_size, len(postings)) - 1}], or blank to skip: ").strip()
    if not choice:
        return
    posting = postings[int(choice)]

    print(f"\nFetching JD for {posting['company']} - {posting['role']}...")
    jd_text = fetch_job_posting(posting["url"])
    print(f"\n--- {len(jd_text)} chars ---")
    print(jd_text[:1000])

    run_full = input("\nRun full intake (similarity score + bullet tailoring)? [y/N]: ").strip().lower()
    if run_full == "y":
        run_application_intake(company=posting["company"], role=posting["role"], url=posting["url"])


def _review_applications():
    """Browse saved applications - interactive or batch-produced - with
    an option to drill into one and update its status."""
    status_filter = input(f"Filter by status ({'/'.join(STATUSES)}), or blank for all: ").strip() or None
    min_sim_input = input("Minimum similarity score, or blank for none: ").strip()
    min_similarity = int(min_sim_input) if min_sim_input else None

    applications = list_applications(status=status_filter, min_similarity=min_similarity)
    if not applications:
        print("No matching applications.")
        return

    print(f"\n{len(applications)} application(s), best match first:\n")
    for i, app in enumerate(applications):
        has_resume = "resume ready" if app.get("resume_pdf_path") else "no resume"
        score = app["similarity_score"] if app["similarity_score"] is not None else "?"
        print(f"[{i}] {app['company']} - {app['role']} | {app['status']} | score={score} | {has_resume}")

    choice = input("\nPick one to view details, or blank to go back: ").strip()
    if not choice:
        return
    app = applications[int(choice)]

    print(f"\n--- {app['company']} - {app['role']} ---")
    print(f"URL: {app['url']}")
    print(f"Status: {app['status']}")
    print(f"Similarity: {app['similarity_score']}")
    if app.get("resume_pdf_path"):
        print(f"Tailored resume: {app['resume_pdf_path']}")
    if app.get("resume_diff"):
        gap = json.loads(app["resume_diff"])
        print(f"Gap summary: {gap.get('summary', '')}")

    new_status = input(f"\nUpdate status ({'/'.join(STATUSES)}), or blank to skip: ").strip()
    if new_status:
        set_application_status(app["url"], new_status)
        print(f"Status updated to '{new_status}'.")


def main():
    init_db()
    print("Job Search Copilot")
    print("1. Add / update an application (workflow)")
    print("2. Run an interview prep session (agent)")
    print("3. Browse new-grad postings (SimplifyJobs) and fetch a JD")
    print("4. Review saved applications")
    choice = input("Choose 1-4: ").strip()

    if choice == "1":
        run_application_intake(
            company=input("Company: "),
            role=input("Role: "),
            url=input("Job posting URL: "),
        )
    elif choice == "2":
        run_interview_prep_session(
            company=input("Company: "),
            role=input("Role: "),
        )
    elif choice == "3":
        _browse_new_grad_postings()
    elif choice == "4":
        _review_applications()
    else:
        print("Unrecognized choice.")


if __name__ == "__main__":
    main()
