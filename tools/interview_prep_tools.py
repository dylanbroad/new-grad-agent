"""Tools exposed to the Interview Prep AGENT.

The agent (not this code) decides which of these to call, in what order,
and when it's done. Keep every function honest about what it does today
(stub vs. real) so the agent loop's behavior is easy to reason about while
you're still filling things in.
"""

from datetime import datetime, timedelta

from db import get_conn
from tools.llm_utils import llm_json_completion
from tools.logging_utils import logged_tool


@logged_tool
def fetch_company_info(company: str) -> dict:
    """Pull recent, relevant info about a company (eng blog, news, product focus).

    This is the model's own knowledge, not a live web search - an actual
    live search needs either a paid search API or scraping search-result
    pages, and a quick test of the free option (DuckDuckGo's HTML
    endpoint, no API key) got flagged as "anomalous traffic" and blocked
    outright rather than returning real results. So this is honest about
    what it is: good enough to ground question generation for well-known
    companies, but not fresh news, and unreliable for obscure ones.
    """
    system_prompt = (
        "You are grounding interview-question generation with what you already "
        "know about a company's engineering org and recent product focus. "
        "Answer from your own knowledge only - you have no live web access, so "
        "don't claim anything is 'recent' or 'current' as of today. If you "
        "don't know much about this company, say so plainly instead of "
        "inventing specifics. Respond with ONLY a JSON object, no prose, no "
        "code fence, matching this exact shape:\n"
        '{"summary": "<3-5 sentences on engineering org, tech stack, product '
        'focus, anything relevant to interview prep>", '
        '"confidence": "<high|medium|low - how well you actually know this company>"}'
    )
    user_content = f"Company: {company}"
    result = llm_json_completion(system_prompt, user_content, max_tokens=512)
    return {
        "company": company,
        "summary": result.get("summary", ""),
        "confidence": result.get("confidence", "low"),
        "source": "model knowledge (not live search)",
    }


@logged_tool
def generate_questions(company: str, role: str, topic: str | None = None, n: int = 3) -> list[dict]:
    """Generate n likely interview questions, optionally focused on one topic.

    Single LLM call - content generation, not agent reasoning, so it
    doesn't need to be part of the agent's own tool-use loop.
    """
    system_prompt = (
        "You write technical interview questions. Respond with ONLY a JSON "
        "object, no prose, no code fence, matching this exact shape:\n"
        '{"questions": [{"topic": "<short topic label>", "prompt": '
        '"<the full question text>"}, ...]}\n'
        "Each question should be answerable out loud in a few minutes, "
        "appropriate for a new-grad-level candidate, and specific enough to "
        "actually evaluate - not generic filler."
    )
    user_content = f"Company: {company}\nRole: {role}\nNumber of questions: {n}"
    if topic:
        user_content += f"\nFocus specifically on: {topic}"

    result = llm_json_completion(system_prompt, user_content, max_tokens=1024)
    questions = result.get("questions", [])[:n]
    return [
        {"id": f"q{i}", "topic": q.get("topic", topic or "general"), "prompt": q.get("prompt", "")}
        for i, q in enumerate(questions, start=1)
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
