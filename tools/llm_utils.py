"""Shared LLM-call plumbing for both the Application workflow's tools and
the Interview Prep agent's tools.

This is provider wiring, not business logic - which model, which key,
how to parse its output - so it lives here instead of being duplicated
in (or imported oddly between) application_tools.py and
interview_prep_tools.py.
"""

import json
import os
import re

import anthropic
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_MODEL = "claude-sonnet-4-6"
GROQ_MODEL = "openai/gpt-oss-120b"


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response.

    Models occasionally wrap JSON in prose or a code fence even when
    told not to, so this is a defensive fallback around json.loads.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"no JSON object found in model response: {text[:200]}")
        return json.loads(match.group(0))


def llm_completion(system_prompt: str, user_content: str, max_tokens: int) -> str:
    """Single LLM call, on whichever provider is configured.

    Prefers Anthropic (the project's real target) when ANTHROPIC_API_KEY is
    set; falls back to Groq (OpenAI-compatible, cheap/free) when only
    GROQ_API_KEY is set, for ad hoc testing without an Anthropic key.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        return "".join(block.text for block in response.content if block.type == "text")
    elif os.environ.get("GROQ_API_KEY"):
        from groq import Groq  # optional dep, only needed for this fallback path

        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        return response.choices[0].message.content
    else:
        raise RuntimeError(
            "no LLM provider configured - set ANTHROPIC_API_KEY (or GROQ_API_KEY for ad hoc testing)"
        )


def llm_json_completion(system_prompt: str, user_content: str, max_tokens: int) -> dict:
    """llm_completion, parsed as JSON, with one retry at double the token
    budget if the response got cut off mid-object (truncated JSON)."""
    text = llm_completion(system_prompt, user_content, max_tokens)
    try:
        return extract_json(text)
    except (ValueError, json.JSONDecodeError):
        text = llm_completion(system_prompt, user_content, max_tokens * 2)
        return extract_json(text)
