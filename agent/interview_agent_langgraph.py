"""Interview Prep AGENT - LangGraph version, for side-by-side comparison
against the hand-rolled tool-use loop in interview_agent.py.

Same tools, same system prompt, same philosophy (the model decides which
tool to call and when to stop - the code doesn't). The only thing that
changes is who drives the loop: interview_agent.py does it by hand with
a `for turn in range(MAX_TURNS)` loop; this file hands that same job to
LangGraph's graph runtime (agent node -> tools node -> back to agent,
until the model stops calling tools).

Worth comparing once you've run both: is the graph version actually
clearer, or is it the same loop with more ceremony (a StateGraph, a
TypedDict, bind_tools) for a control flow simple enough to hand-roll in
~50 lines? That's the real question this experiment is for.
"""

import json
import os
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from tools.interview_prep_tools import (
    fetch_company_info as _fetch_company_info,
    generate_questions as _generate_questions,
    quiz_user as _quiz_user,
    record_result as _record_result,
    get_weak_spots as _get_weak_spots,
)

load_dotenv()

# Graph steps, not agent turns - each agent<->tools round trip is 2 steps,
# so this is roughly double interview_agent.py's MAX_TURNS. Same role:
# a backstop against a runaway loop, not the intended stopping mechanism.
MAX_GRAPH_STEPS = 30

ANTHROPIC_MODEL = "claude-sonnet-4-6"
GROQ_MODEL = "openai/gpt-oss-120b"


def _json(value) -> str:
    """Force every tool's ToolMessage content to a non-empty JSON string.

    A tool returning a bare Python list/dict (e.g. get_weak_spots() -> []
    on a first-ever session) hits a real LangChain edge case: it treats a
    list return value as structured message content instead of stringifying
    it, so an empty list becomes content=[] - which both Groq and Anthropic
    reject outright ("minimum number of items is 1"). interview_agent.py's
    hand-rolled loop already avoids this by always doing
    json.dumps(result) before putting it in a tool_result block; matching
    that here is what makes this actually work instead of erroring on the
    very first empty weak-spots lookup.
    """
    return json.dumps(value, default=str)


# Thin @tool wrappers around the same functions interview_agent.py calls
# via DISPATCH - no logic duplication, just adapting them to LangChain's
# tool-calling convention (which reads name/args from the function
# signature and docstring instead of a hand-written JSON schema).
@tool
def fetch_company_info(company: str) -> str:
    """Get recent engineering/product info about a company to ground question generation."""
    return _json(_fetch_company_info(company))


@tool
def generate_questions(company: str, role: str, topic: str | None = None, n: int = 3) -> str:
    """Generate n likely interview questions, optionally focused on one topic."""
    return _json(_generate_questions(company, role, topic, n))


@tool
def quiz_user(question: dict) -> str:
    """Ask the user one question and get their answer. Call once per question.

    question must have id, topic, and prompt keys.
    """
    return _json(_quiz_user(question))


@tool
def record_result(topic: str, result: str, company: str | None = None, review_in_days: int = 3) -> str:
    """Record how the user did on a topic so future sessions can pick up on weak spots.

    result must be 'correct', 'partial', or 'incorrect'.
    """
    return _json(_record_result(topic, result, company, review_in_days))


@tool
def get_weak_spots(company: str | None = None, due_only: bool = False) -> str:
    """Look up previously tracked weak spots, optionally filtered to ones due for review."""
    return _json(_get_weak_spots(company, due_only))


TOOLS = [fetch_company_info, generate_questions, quiz_user, record_result, get_weak_spots]

# Identical to interview_agent.py's SYSTEM_PROMPT - keeping it byte-for-byte
# the same is what makes the comparison meaningful.
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


def _chat_model():
    """Same Anthropic-preferred / Groq-fallback pattern as
    tools/application_tools.py's _llm_completion, so this graph runs on
    whichever key you already have set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=ANTHROPIC_MODEL, max_tokens=1024)
    elif os.environ.get("GROQ_API_KEY"):
        from langchain_groq import ChatGroq

        return ChatGroq(model=GROQ_MODEL, max_tokens=1024)
    else:
        raise RuntimeError(
            "no LLM provider configured - set ANTHROPIC_API_KEY (or GROQ_API_KEY for ad hoc testing)"
        )


class State(TypedDict):
    messages: Annotated[list, add_messages]


def build_graph():
    model_with_tools = _chat_model().bind_tools(TOOLS)

    def agent_node(state: State) -> dict:
        return {"messages": [model_with_tools.invoke(state["messages"])]}

    graph = StateGraph(State)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(TOOLS))
    graph.set_entry_point("agent")
    # tools_condition reads the last message's tool_calls: routes to "tools"
    # if the model asked for any, otherwise to END - this is the graph
    # equivalent of interview_agent.py's `if response.stop_reason != "tool_use"`.
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


def run_interview_prep_session(company: str, role: str) -> None:
    app = build_graph()
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Run a prep session for a {role} interview at {company}."),
    ]

    try:
        final_state = app.invoke({"messages": messages}, config={"recursion_limit": MAX_GRAPH_STEPS})
    except Exception as exc:
        if "recursion" in str(exc).lower():
            print(f"\n--- Hit MAX_GRAPH_STEPS ({MAX_GRAPH_STEPS}) safety cap without the model stopping itself. ---")
            print("If this triggers often, the stopping-condition prompt likely needs tightening.")
            return
        raise

    final_text = final_state["messages"][-1].content
    print(f"\n--- Session complete ---\n{final_text}")


if __name__ == "__main__":
    company = input("Company: ")
    role = input("Role: ")
    run_interview_prep_session(company, role)
