"""Tools exposed to the Interview Prep AGENT.

The agent (not this code) decides which of these to call, in what order,
and when it's done. Keep every function honest about what it does today
(stub vs. real) so the agent loop's behavior is easy to reason about while
you're still filling things in.
"""

from datetime import datetime, timedelta

from db import get_conn
from tools.logging_utils import logged_tool


@logged_tool
def fetch_company_info(company: str) -> dict:
    """Pull recent, relevant info about a company (eng blog, news, product focus).

    TODO: replace with a real web_search / web_fetch call. Returning a stub
    for now so the agent loop is runnable end-to-end before that's wired up.
    """
    return {
        "company": company,
        "summary": f"[STUB] Recent engineering focus areas for {company} go here.",
        "source": "stub",
    }


@logged_tool
def generate_questions(company: str, role: str, topic: str | None = None, n: int = 3) -> list[dict]:
    """Generate n likely interview questions, optionally focused on one topic.

    TODO: replace with an LLM call (can be a *separate*, cheaper model call -
    this is content generation, not agent reasoning, so it doesn't need to be
    part of the agent's own tool-use loop).
    """
    focus = topic or "general"
    return [
        {"id": f"q{i}", "topic": focus, "prompt": f"[STUB] {focus} question #{i} for {role} at {company}"}
        for i in range(1, n + 1)
    ]


@logged_tool
def quiz_user(question: dict) -> dict:
    """Ask the user a question via the CLI and capture their answer.

    This is the one tool with a real side effect (blocks on user input) -
    keep it separate from generate_questions so the agent can generate a
    batch and only quiz on what it decides is worth asking.
    """
    print(f"\n[{question['topic']}] {question['prompt']}")
    answer = input("Your answer: ")
    return {"question_id": question["id"], "answer": answer}


@logged_tool
def record_result(topic: str, result: str, company: str | None = None, review_in_days: int = 3) -> dict:
    """Upsert a weak_spots row. result should be 'correct' | 'partial' | 'incorrect'.

    Idempotent on (company, topic): repeated calls update attempts/last_result
    rather than creating duplicate rows.
    """
    next_review = (datetime.utcnow() + timedelta(days=review_in_days)).date().isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO weak_spots (company, topic, last_result, attempts, next_review_date)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(company, topic) DO UPDATE SET
                   last_result = excluded.last_result,
                   attempts = attempts + 1,
                   next_review_date = excluded.next_review_date,
                   updated_at = datetime('now')""",
            (company, topic, result, next_review),
        )
    return {"company": company, "topic": topic, "result": result, "next_review_date": next_review}


@logged_tool
def get_weak_spots(company: str | None = None, due_only: bool = False) -> list[dict]:
    """Read back tracked weak spots - this is what lets the agent's decisions
    be informed by history instead of starting cold every session.
    """
    query = "SELECT * FROM weak_spots"
    conditions = []
    params: list = []
    if company:
        conditions.append("company = ?")
        params.append(company)
    if due_only:
        conditions.append("next_review_date <= date('now')")
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY next_review_date ASC"

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]
