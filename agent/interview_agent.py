"""Interview Prep AGENT.

This is the one place in the project where control flow is NOT predefined
in Python. The model is given tools and a goal; IT decides which tool to
call, in what order, and when the session is done. Contrast with
workflow/application_workflow.py, where the sequence is hardcoded.

Safety valve: MAX_TURNS caps runaway loops during development. A real
stopping condition should come from the model choosing to stop (no more
tool calls) - MAX_TURNS is a backstop, not the intended mechanism.
"""

import json
import os

import anthropic

from tools.interview_prep_tools import (
    fetch_company_info,
    generate_questions,
    quiz_user,
    record_result,
    get_weak_spots,
)

MAX_TURNS = 15
MODEL = "claude-sonnet-4-6"

TOOLS = [
    {
        "name": "fetch_company_info",
        "description": "Get recent engineering/product info about a company to ground question generation.",
        "input_schema": {
            "type": "object",
            "properties": {"company": {"type": "string"}},
            "required": ["company"],
        },
    },
    {
        "name": "generate_questions",
        "description": "Generate likely interview questions, optionally focused on a specific topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "role": {"type": "string"},
                "topic": {"type": "string", "description": "Optional: focus area, e.g. a known weak spot."},
                "n": {"type": "integer", "default": 3},
            },
            "required": ["company", "role"],
        },
    },
    {
        "name": "quiz_user",
        "description": "Ask the user one question and get their answer. Call once per question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "topic": {"type": "string"},
                        "prompt": {"type": "string"},
                    },
                    "required": ["id", "topic", "prompt"],
                }
            },
            "required": ["question"],
        },
    },
    {
        "name": "record_result",
        "description": "Record how the user did on a topic so future sessions can pick up on weak spots.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "topic": {"type": "string"},
                "result": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
                "review_in_days": {"type": "integer", "default": 3},
            },
            "required": ["topic", "result"],
        },
    },
    {
        "name": "get_weak_spots",
        "description": "Look up previously tracked weak spots, optionally filtered to ones due for review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "due_only": {"type": "boolean", "default": False},
            },
        },
    },
]

DISPATCH = {
    "fetch_company_info": fetch_company_info,
    "generate_questions": generate_questions,
    "quiz_user": quiz_user,
    "record_result": record_result,
    "get_weak_spots": get_weak_spots,
}

SYSTEM_PROMPT = """You are an interview prep coach running a live practice session with the user.

Goal: run an effective, adaptive prep session for the given company and role.
You decide the plan - you are not following a fixed script. Concretely, you should:
- Check get_weak_spots first to see if anything is due for review before generating new questions.
- Decide how many questions to ask and on which topics based on what you learn as you go,
  not a fixed count set in advance.
- After each answer, judge the quality yourself and call record_result.
- Stop the session yourself (by responding with a wrap-up summary and no further tool calls)
  once you judge there's been a reasonable, useful session - don't wait to be told to stop,
  and don't go forever. Use your judgment on when "enough" has been covered.

At the end, give the user a short summary of what was covered and what to review next.
"""


def run_interview_prep_session(company: str, role: str) -> None:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    messages = [
        {
            "role": "user",
            "content": f"Run a prep session for a {role} interview at {company}.",
        }
    ]

    for turn in range(MAX_TURNS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        # The model decided it's done: no tool calls in this turn.
        if response.stop_reason != "tool_use":
            final_text = "".join(block.text for block in response.content if block.type == "text")
            print(f"\n--- Session complete (turn {turn + 1}) ---\n{final_text}")
            return

        # Execute whichever tool(s) the model chose - not a fixed sequence.
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            func = DISPATCH[block.name]
            try:
                result = func(**block.input)
            except Exception as exc:  # noqa: BLE001
                result = {"error": str(exc)}
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                }
            )

        messages.append({"role": "user", "content": tool_results})

    print(f"\n--- Hit MAX_TURNS ({MAX_TURNS}) safety cap without the model stopping itself. ---")
    print("If this triggers often, the stopping-condition prompt likely needs tightening.")


if __name__ == "__main__":
    company = input("Company: ")
    role = input("Role: ")
    run_interview_prep_session(company, role)
