# Job Search Copilot

A deliberately mixed system: an **Application workflow** (fixed control
flow) and an **Interview Prep agent** (model directs its own tool use).
See `agent/interview_agent.py` and `workflow/application_workflow.py` for
the two contrasting implementations — that contrast is the point of the
project, not an accident.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=your-key-here
python db.py            # creates copilot.db from schema.sql
python main.py           # run either path from the CLI
```

## What's real vs. stubbed right now

Everything **runs end-to-end**, including the actual agent tool-use loop
against the Anthropic API — but the content-generation tools return
`[STUB]` placeholders so you can verify the control flow / architecture
before spending effort on the generation quality. Fill these in roughly
in this order:

1. `tools/application_tools.py::fetch_job_posting` — real scraping
   (requests + trafilatura), wrapped in retry/backoff.
2. `tools/application_tools.py::diff_resume` and `draft_cover_letter` —
   real single LLM calls (these do NOT need to be agentic — see below).
3. `tools/interview_prep_tools.py::fetch_company_info` — real web search.
4. `tools/interview_prep_tools.py::generate_questions` — real LLM call.
5. Everything else (`upsert_application`, `record_result`,
   `get_weak_spots`, the agent loop itself) is already fully functional
   logic, not stubbed — it's the content-generation steps that are
   placeholders.

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
- Nothing here handles concurrent/parallel tool calls yet (e.g. batch-
  checking several job postings) — a good place to add the
  asyncio + bounded-semaphore pattern from the coding-practice sheet.
