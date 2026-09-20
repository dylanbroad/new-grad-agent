"""Application WORKFLOW - predefined code path, per the workflow/agent split.

Steps always run in this order. The LLM (inside diff_resume and
draft_cover_letter) generates content, but never decides what happens
next - that's decided here, in code. This is the correct shape for a
task where the path forward is already fully known.

The human-approval gate is the one deliberate pause: applying is a
real-world action, so the workflow stops and waits for explicit
confirmation before advancing status to "applied".
"""

from tools.application_tools import (
    fetch_job_posting,
    diff_resume,
    draft_cover_letter,
    upsert_application,
    set_application_status,
)


def run_application_intake(company: str, role: str, url: str, resume_text: str) -> dict:
    # Step 1: fetch
    jd_text = fetch_job_posting(url)

    # Step 2: analyze
    gap_analysis = diff_resume(jd_text, resume_text)

    # Step 3: generate
    cover_letter = draft_cover_letter(jd_text, resume_text, gap_analysis)

    # Step 4: persist (idempotent on url_hash)
    application = upsert_application(
        company=company, role=role, url=url, jd_text=jd_text, resume_diff=gap_analysis
    )

    print("\n--- Cover letter draft ---")
    print(cover_letter)
    print("\n--- Gap analysis ---")
    print(gap_analysis)

    # Step 5: human-approval gate - the workflow does NOT auto-advance status.
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
        resume_text="[paste or load your resume text here]",
    )
