"""Run context: trusted, server-set data. The model never sees this object."""

from dataclasses import dataclass, field


@dataclass
class AppCtx:
    user_id: str
    tenant_id: str | None = None
    daily_budget_usd: float = 1.0
    spent_today_usd: float = 0.0
    flags: dict = field(default_factory=dict)   # feature toggles from settings page
