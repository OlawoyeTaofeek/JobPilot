"""Tool guardrail for query_market: allow a single SELECT statement only,
against an allow-list of tables, with a row LIMIT. Reject anything else.
"""
