"""One-shot automation pass: scrape SimplifyJobs, process whatever
postings haven't been seen before, then exit.

Deliberately NOT a long-running daemon - run it manually whenever you
want ("did anything new get posted?"), or schedule it yourself with cron
or launchd if you want it to happen automatically. A daemon would mean
a process idling most of the time for no benefit, plus the usual
headaches of keeping something alive unattended; a one-shot script that
you (or a scheduler) invokes is simpler and matches how the rest of this
project stays local-only.

No interactive approval gate - see process_posting() in
workflow/application_workflow.py. Every processed posting is left at
status 'found'; review the batch's output (or query the DB) and advance
to 'applied' yourself, same as the interactive path already requires
explicit confirmation before that happens.
"""

import sys
import time

from db import init_db
from tools.application_tools import get_seen_urls, list_new_grad_postings, load_experience_bank
from workflow.application_workflow import SIMILARITY_THRESHOLD, process_posting

# Safety valve against a first run trying to process all ~500 postings
# in the SimplifyJobs list at once (cost + time + hammering the source
# and the LLM API) - cap what one invocation will do. Run again to pick
# up more of the backlog.
MAX_POSTINGS_PER_RUN = 20

# Gentle pacing between postings - each one is already 1-2 LLM calls
# plus a JD fetch, so this just adds a floor against back-to-back bursts.
DELAY_BETWEEN_POSTINGS_SECONDS = 3


def run_batch() -> None:
    init_db()
    experience_bank = load_experience_bank()

    print("Checking SimplifyJobs for postings...")
    postings = list_new_grad_postings()
    seen_urls = get_seen_urls()
    unseen_postings = [p for p in postings if p["url"] not in seen_urls]
    new_postings = unseen_postings[:MAX_POSTINGS_PER_RUN]

    print(
        f"{len(postings)} open postings total, {len(unseen_postings)} not yet seen, "
        f"processing {len(new_postings)} this run.\n"
    )

    if not new_postings:
        print("Nothing new to process.")
        return

    tailored_count = 0
    errors = []

    for i, posting in enumerate(new_postings, start=1):
        label = f"{posting['company']} - {posting['role']}"
        print(f"[{i}/{len(new_postings)}] {label}...", end=" ", flush=True)
        try:
            result = process_posting(posting["company"], posting["role"], posting["url"], experience_bank)
            score = result["gap_analysis"].get("similarity_score", 0)
            if result["resume_pdf_path"]:
                tailored_count += 1
                print(f"score {score}/100 - tailored resume -> {result['resume_pdf_path']}")
            else:
                print(f"score {score}/100 - below {SIMILARITY_THRESHOLD}, skipped tailoring")
        except Exception as exc:  # noqa: BLE001 - one bad posting shouldn't kill the batch
            errors.append((label, str(exc)))
            print(f"ERROR: {exc}")

        if i < len(new_postings):
            time.sleep(DELAY_BETWEEN_POSTINGS_SECONDS)

    print(f"\n--- Done: {len(new_postings)} processed, {tailored_count} tailored, {len(errors)} errors ---")
    remaining = len(unseen_postings) - len(new_postings)
    if remaining > 0:
        print(f"{remaining} more unseen postings left in the backlog - run again to keep working through them.")
    if errors:
        print("Errors:")
        for label, error in errors:
            print(f"  {label}: {error}")


if __name__ == "__main__":
    try:
        run_batch()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
