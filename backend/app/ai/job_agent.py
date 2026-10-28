"""build_job_agent(ctx) -> Agent[AppCtx]: assembles instructions, tools, guardrails.

Sketch:
    Agent[AppCtx](
        name="Job Agent",
        instructions=instructions.JOB_AGENT,
        model=settings.agent_model,
        tools=registry.tools_for(ctx) + [research_agent.as_tool(...)],
        input_guardrails=[input_scope.guardrail],
        output_guardrails=[...],
        mcp_servers=[...],          # from tools/mcp/client.py
    )
"""
