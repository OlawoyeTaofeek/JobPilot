"""Shared tool error handling: the model gets a safe message, logs get details."""

import json
import logging
from typing import Any

from agents import RunContextWrapper
from agents import function_tool as _function_tool

logger = logging.getLogger("app.tools")


class ToolInputError(ValueError):
    """Bad input the model can fix by trying again (message shown to the model)."""


def tool_failure(ctx: RunContextWrapper[Any], error: Exception) -> str:
    logger.warning("Tool failure: %r", error)
    return json.dumps({
        "ok": False,
        "error": "Invalid or missing arguments. Re-read the tool's parameters and "
                 "try again, or ask the user for the missing detail.",
    })


def function_tool(func=None, /, **kwargs):
    """The SDK's @function_tool with our safe default error handler."""
    kwargs.setdefault("failure_error_function", tool_failure)
    if func is None:
        return _function_tool(**kwargs)
    return _function_tool(func, **kwargs)


def ok(**data) -> str:
    return json.dumps({"ok": True, **data}, default=str)


def err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})
