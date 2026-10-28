"""Tracing setup: send OpenAI Agents SDK traces to Langfuse.

Call setup_tracing() ONCE at startup, before any agent runs:
  - scripts (extractor, run_daily):  at the top of _main()
  - FastAPI:                         in the lifespan / startup of main.py
and call flush_tracing() before a short script exits, so buffered spans are sent.

If the Langfuse keys are empty, tracing to Langfuse is simply skipped (the app still runs).
"""

from __future__ import annotations

import logging
import os

from app.config import get_settings

log = logging.getLogger(__name__)

_langfuse = None  # the Langfuse client, once set up


def setup_tracing() -> bool:
    """Instrument the Agents SDK for Langfuse. Returns True if Langfuse is active."""
    global _langfuse
    if _langfuse is not None:
        return True  # already set up

    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        log.info("Langfuse keys not set: skipping Langfuse tracing")
        return False

    # The Langfuse client reads these from the environment. Our .env is loaded into
    # Settings (not os.environ), so copy them across before creating the client.
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_base_url)

    from langfuse import get_client
    from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

    OpenAIAgentsInstrumentor().instrument()
    client = get_client()
    if not client.auth_check():
        log.warning("Langfuse auth failed: check LANGFUSE_* keys and LANGFUSE_BASE_URL")
        return False

    _langfuse = client
    log.info("Langfuse tracing active (%s)", settings.langfuse_base_url)
    return True


def flush_tracing() -> None:
    """Send any buffered spans. Call before a short-lived script exits."""
    if _langfuse is not None:
        _langfuse.flush()