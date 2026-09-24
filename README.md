# Job Search Copilot

A local, single-user CLI tool that automates the new-grad software engineering job search:

1. **Scrapes** open new-grad postings from [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions), a public GitHub repo updated daily.
2. **Scores** how well my actual work experience matches each job description, using an LLM.
3. **Tailors** resume bullets to the specific JD when the match is strong enough — by selecting and rewording bullets I actually have, never inventing new claims.
4. **Renders** the tailored resume into an actual submittable one-page PDF.
5. **Tracks** everything in a local SQLite database, so I can review results, track application status, and avoid reprocessing postings I've already seen.

Separately, it runs an **interview-prep agent** — an adaptive practice session that generates questions, grades my answers, and tracks weak spots to resurface in future sessions.

No cloud hosting, no server — everything runs against a local SQLite file.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Pick one:
export ANTHROPIC_API_KEY=your-key-here   # primary target
export GROQ_API_KEY=your-key-here        # free fallback, for ad hoc testing

python db.py             # creates copilot.db from schema.sql
python main.py            # interactive CLI: add an application, run interview prep,
                           # browse new postings, or review saved applications

python batch_runner.py    # one-shot: scrape SimplifyJobs, process anything not seen before
```

`experience_bank.json` holds the actual content the resume gets built from — jobs, projects, education, skills. Edit it by hand to add more to pick from.

## Architecture: workflow vs. agent

The core design idea of this project is having one clean example of each of Anthropic's two agentic patterns, built side by side on purpose:

- **`workflow/application_workflow.py`** — the job-application pipeline. A **fixed control-flow function**: fetch the JD, score the match, then a plain `if` statement (not the model) decides whether to spend the extra LLM call tailoring bullets and rendering a resume, then persist. The LLM generates *content* (a score, tailored text) but never decides what the program does next — that's hardcoded, because the sequence never actually varies.
- **`agent/interview_agent.py`** — the interview-prep session. A **while-loop where the model decides** which tool to call, in what order, and when to stop. Handed a system prompt and five tools (check weak spots, generate questions, quiz the user, grade an answer, record the result), it runs a genuinely adaptive session, because a real prep session's shape depends on how the conversation actually goes.

Workflows are for tasks whose steps are already fully known in advance; agents are for tasks where the path genuinely depends on what happens as you go. Forcing the application pipeline to be "agentic" would just add cost and unpredictability for a sequence that never changes.

## Project structure

```
main.py                          # interactive CLI — plain if/elif router
batch_runner.py                  # one-shot automation: process new postings unattended
mcp_server.py                    # exposes the tools as an MCP server
db.py / schema.sql                # SQLite: applications, weak_spots, tool_call_log
experience_bank.json             # hand-maintained: jobs/projects/education/skills

workflow/application_workflow.py       # fixed application pipeline
agent/interview_agent.py               # hand-rolled interview-prep agent loop
agent/interview_agent_langgraph.py     # same agent, reimplemented in LangGraph (comparison)

tools/application_tools.py       # scraping, similarity scoring, bullet tailoring, DB writes
tools/interview_prep_tools.py    # company research, question generation, quizzing, weak spots
tools/resume_render.py           # tailored bullets -> actual PDF
tools/llm_utils.py               # shared Anthropic/Groq dual-provider LLM-call plumbing
tools/logging_utils.py           # @logged_tool decorator - wraps every call with observability
```

## Tech stack

- **LLM:** Anthropic API (Claude), with Groq (free, OpenAI-compatible) as an automatic fallback for testing without a paid key — see `tools/llm_utils.py`
- **Scraping:** `requests` + `trafilatura` (falls back to raw `BeautifulSoup` extraction), retried with backoff
- **PDF generation:** `xhtml2pdf` — pure Python, no system dependencies
- **DB:** SQLite, no ORM
- **Agent frameworks:** a hand-rolled Anthropic tool-use loop and a LangGraph (`StateGraph`/`ToolNode`) reimplementation of the same agent, kept side by side
- **MCP:** the application-workflow tools are also exposed as an MCP server, so any MCP client can drive the pipeline without importing this codebase directly

## A few notable design decisions

- **Bullet tailoring is validated in code, not just by prompt instruction.** The model is checked (not trusted) to never output more bullets than the source data has, never introduce a number/metric that isn't in the original bullet, stay under a length cap (~2 lines), and keep the whole resume under a one-page bullet budget. Violations trigger an automatic retry with the specific failures fed back to the model.
- **Dates, locations, and job titles in the rendered PDF always come from `experience_bank.json`, never from the model.** Only the bullet text itself is ever LLM-authored in the final document.
- **`fetch_company_info` is honest about not being live search.** A live web-search attempt (scraping DuckDuckGo's HTML results, no API key) got blocked as "anomalous traffic," so the tool uses the model's own knowledge instead and labels itself accordingly rather than pretending to be fresher than it is.
- **`batch_runner.py` is a one-shot script, not a daemon.** It dedupes against what's already in the DB, rate-limits itself between postings, and never blocks on interactive input — run it manually or schedule it yourself (cron/launchd).

## Observability

Every tool call (both workflow and agent side) is logged to `tool_call_log` via the `@logged_tool` decorator — tool name, args, latency, success/failure:

```bash
sqlite3 copilot.db "SELECT tool_name, success, latency_ms, created_at FROM tool_call_log ORDER BY id DESC LIMIT 20;"
```
