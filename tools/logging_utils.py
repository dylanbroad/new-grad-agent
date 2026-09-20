"""Wraps every tool call with structured logging to tool_call_log.

This is what makes the system debuggable per the "build observable systems"
principle: you can't just look at a stack trace when an LLM tool-use loop
goes wrong, you need to see exactly which tools were called, with what
arguments, how long they took, and whether they succeeded.
"""

import functools
import json
import time

from db import get_conn


def logged_tool(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        success = True
        error = None
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as exc:  # noqa: BLE001 - we want to log and re-raise
            success = False
            error = str(exc)
            raise
        finally:
            latency_ms = int((time.perf_counter() - start) * 1000)
            try:
                with get_conn() as conn:
                    conn.execute(
                        """INSERT INTO tool_call_log
                           (tool_name, args_json, success, latency_ms, error)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            func.__name__,
                            json.dumps({"args": args, "kwargs": kwargs}, default=str),
                            1 if success else 0,
                            latency_ms,
                            error,
                        ),
                    )
            except Exception:
                # Logging must never break the actual tool call path.
                pass

    return wrapper
