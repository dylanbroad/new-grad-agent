# Job Search Copilot

A deliberately mixed system: an **Application workflow** (fixed control
flow) and an **Interview Prep agent** (model directs its own tool use).
See `agent/interview_agent.py` and `workflow/application_workflow.py` for
the two contrasting implementations — that contrast is the point of the
project, not an accident.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your-key-here   # or GROQ_API_KEY for free ad hoc testing
python db.py            # creates copilot.db from schema.sql
python main.py           # run either path from the CLI

python batch_runner.py   # one-shot: scrape SimplifyJobs, process anything not seen before
```

## What's real vs. stubbed right now

Everything **runs end-to-end** against a real LLM (Anthropic if
`ANTHROPIC_API_KEY` is set, Groq as a free fallback otherwise — see
`tools/llm_utils.py`). No content-generation stubs remain:

- `tools/application_tools.py::fetch_job_posting` — real scraping
  (requests + trafilatura), wrapped in retry/backoff.
- `tools/application_tools.py::diff_resume` — real LLM call, scores JD
  fit against `experience_bank.json` (a hand-maintained list of
  jobs/projects/ECs to pick bullets from — not just a single resume).
- `tools/application_tools.py::optimize_resume_bullets` — real LLM call,
  tailors bullets to a JD with code-level validation (no fabricated
  bullets/numbers, ~2-line cap, one-page budget) and automatic retry.
- `tools/resume_render.py::render_resume_pdf` — renders the tailored
  bullets into an actual submittable one-page PDF (xhtml2pdf, pure
  Python, no system deps), written to `output/resumes/`. Dates,
  locations, and job titles are always pulled from `experience_bank.json`
  rather than the model's output - only the bullets themselves are
  LLM-generated.
- `batch_runner.py` — one-shot automation: scrapes SimplifyJobs, skips
  postings already in the DB (`get_seen_urls`), processes whatever's new
  (capped per run, rate-limited between postings), no interactive gate.
  Not a daemon - run it yourself or schedule it with cron/launchd.
- `tools/interview_prep_tools.py::fetch_company_info` — real LLM call
  grounded in the model's own knowledge (not live search - a live search
  needs a paid API or gets bot-blocked on the free options).
- `tools/interview_prep_tools.py::generate_questions` — real LLM call.
- `upsert_application`, `record_result`, `get_weak_spots`, both agent
  loops — already fully functional logic, not stubbed.

`draft_cover_letter` was removed rather than implemented — decided it
wasn't worth building.

`agent/interview_agent_langgraph.py` is a from-scratch LangGraph
reimplementation of the interview agent's tool-use loop, kept side by
side with the original hand-rolled version for comparison.

## Why the workflow/agent split

Per Anthropic's own definition: workflows are predefined code paths;
agents are systems where the model directs its own process and tool
use. `application_workflow.py` is a fixed `step1 -> step2 -> step3`
function — correct, because the path never varies. `interview_agent.py`
is a `while` loop where the model picks which tool to call and decides
when the session is over — correct, because a real prep session's shape
genuinely depends on how the conversation goes.

Resist the urge to make `application_workflow.py` "agentic" for its own
sake — that would be exactly the kind of over-engineering the source
architecture doc warns against for a task whose steps are already fully
known in advance.

## Observability

Every tool call (both workflow and agent side) is logged to
`tool_call_log` via the `@logged_tool` decorator in
`tools/logging_utils.py` — tool name, args, latency, success/failure.
Query it directly for now:

```bash
sqlite3 copilot.db "SELECT tool_name, success, latency_ms, created_at FROM tool_call_log ORDER BY id DESC LIMIT 20;"
```

## Next steps / open design questions

- Interview agent stopping condition currently relies entirely on the
  system prompt telling the model to use judgment. Watch for it hitting
  `MAX_TURNS` in practice — if it does often, that's a real signal the
  prompt needs tightening, and a good thing to have observed and fixed
  before the interview.
- `main.py`'s router is intentionally a plain if/elif, not a supervisor
  agent — see the module docstring for why.
- `batch_runner.py` processes postings sequentially with a fixed delay
  between them - nothing here does concurrent/parallel tool calls yet.
  A good place to add the asyncio + bounded-semaphore pattern from the
  coding-practice sheet if the backlog ever makes sequential too slow.
