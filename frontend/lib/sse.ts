// Parse the backend SSE stream: token | llm_call | tool_call | tool_output |
// approval_required | error | done   (see backend/app/streaming/sse.py)
export type AgentEvent = { type: string; data: unknown };
// TODO: fetch-stream reader -> yields AgentEvent
