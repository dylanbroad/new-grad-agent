"""Application WORKFLOW - predefined code path, per the workflow/agent split.

Steps always run in this order. The LLM (inside diff_resume,
optimize_resume_bullets, and draft_cover_letter) generates content, but
never decides what happens next - that's decided here, in code. This is
the correct shape for a task where the path forward is already fully
known.

The similarity-score branch is a plain if-statement, not the model
deciding: diff_resume returns a number, and the workflow (not the LLM)
decides whether that number clears the bar for spending an extra LLM
call optimizing bullets. Same principle as the human-approval gate below.
"""

import json

from tools.application_tools import (
    fetch_job_posting,
    load_experience_bank,
    diff_resume,
    optimize_resume_bullets,
    render_resume_text,
    draft_cover_letter,
    upsert_application,
    set_application_status,
)

# Below this, don't bother spending the extra LLM call tailoring bullets -
# the experience bank just isn't a good match for this posting.
SIMILARITY_THRESHOLD = 60


def run_application_intake(company: str, role: str, url: str) -> dict:
    experience_bank = load_experience_bank()

    # Step 1: fetch
    jd_text = fetch_job_posting(url)

    # Step 2: analyze - how well does the experience bank match this JD?
    gap_analysis = diff_resume(jd_text, experience_bank)
    similarity_score = gap_analysis.get("similarity_score", 0)

    print(f"\n--- Similarity score: {similarity_score}/100 ---")
    print(gap_analysis.get("summary", ""))
    if gap_analysis.get("missing_skills"):
        print(f"Missing: {', '.join(gap_analysis['missing_skills'])}")

    # Step 3: only tailor bullets if the match is worth it
    if similarity_score >= SIMILARITY_THRESHOLD:
        tailored_resume = optimize_resume_bullets(jd_text, experience_bank, gap_analysis)
        resume_text = render_resume_text(tailored_resume, experience_bank)
        print("\n--- Tailored resume bullets ---")
        print(resume_text)
    else:
        tailored_resume = None
        resume_text = render_resume_text({"jobs": experience_bank.get("jobs", []),
                                           "projects": experience_bank.get("projects", [])})
        print(f"\nSimilarity below {SIMILARITY_THRESHOLD} - skipping bullet optimization.")

    # Step 4: generate
    # cover_letter = draft_cover_letter(jd_text, resume_text, json.dumps(gap_analysis))

    # Step 5: persist (idempotent on url_hash)
    application = upsert_application(
        company=company,
        role=role,
        url=url,
        jd_text=jd_text,
        resume_diff=json.dumps(gap_analysis),
        similarity_score=similarity_score,
        tailored_resume=json.dumps(tailored_resume) if tailored_resume else "",
    )

    # print("\n--- Cover letter draft ---")
    # print(cover_letter)

    # Step 6: human-approval gate - the workflow does NOT auto-advance status.
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
