"""Entry point. Deliberately a dumb router (if/elif), NOT a supervisor
agent - the choice between these two paths doesn't require reasoning,
just user intent, so a full LLM-driven supervisor would be the kind of
unnecessary complexity the architecture doc warns against.
"""

from db import init_db
from workflow.application_workflow import run_application_intake
from agent.interview_agent import run_interview_prep_session


def main():
    init_db()
    print("Job Search Copilot")
    print("1. Add / update an application (workflow)")
    print("2. Run an interview prep session (agent)")
    choice = input("Choose 1 or 2: ").strip()

    if choice == "1":
        run_application_intake(
            company=input("Company: "),
            role=input("Role: "),
            url=input("Job posting URL: "),
            resume_text="[paste or load your resume text here]",
        )
    elif choice == "2":
        run_interview_prep_session(
            company=input("Company: "),
            role=input("Role: "),
        )
    else:
        print("Unrecognized choice.")


if __name__ == "__main__":
    main()
