"""SDK stream events -> SSE events. Same mapping as the Chainlit app:
  response.created/completed -> llm_call, ResponseTextDeltaEvent -> token,
  tool_called/tool_output -> tool_call/tool_output, interruptions -> approval_required.
"""
