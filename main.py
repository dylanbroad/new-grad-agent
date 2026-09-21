"""Entry point. Deliberately a dumb router (if/elif), NOT a supervisor
agent - the choice between these two paths doesn't require reasoning,
just user intent, so a full LLM-driven supervisor would be the kind of
unnecessary complexity the architecture doc warns against.
"""

from db import init_db
from workflow.application_workflow import run_application_intake
from agent.interview_agent import run_interview_prep_session
from tools.application_tools import fetch_job_posting, list_new_grad_postings


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

    run_full = input("\nRun full intake (similarity score + bullet tailoring + cover letter)? [y/N]: ").strip().lower()
    if run_full == "y":
        run_application_intake(company=posting["company"], role=posting["role"], url=posting["url"])


def main():
    init_db()
    print("Job Search Copilot")
    print("1. Add / update an application (workflow)")
    print("2. Run an interview prep session (agent)")
    print("3. Browse new-grad postings (SimplifyJobs) and fetch a JD")
    choice = input("Choose 1, 2, or 3: ").strip()

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
    else:
        print("Unrecognized choice.")


if __name__ == "__main__":
    main()
