"""Wrapper around Runner.run / Runner.run_streamed:
- sets max_turns, RunConfig (tracing, tool_error_formatter)
- catches MaxTurnsExceeded, guardrail tripwires, API errors
- returns either a finished result or a paused RunState (interruptions)
"""
