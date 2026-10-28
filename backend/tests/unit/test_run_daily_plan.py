"""Plan rotation in run_daily (no database, no network)."""

from datetime import date, timedelta

from app.collector.run_daily import SEARCHES, todays_plan
from app.collector.sources.base import PlanItem

SIX = [PlanItem(f"role{i}", "London", "gb") for i in range(6)]


def test_plan_has_requested_size():
    assert len(todays_plan(date(2026, 10, 28), SIX, 4)) == 4


def test_plan_never_exceeds_number_of_searches():
    assert len(todays_plan(date(2026, 10, 28), SIX, 50)) == 6


def test_plan_has_no_duplicates_within_a_day():
    plan = todays_plan(date(2026, 10, 28), SIX, 4)
    assert len(set(plan)) == len(plan)


def test_plan_is_deterministic_for_a_given_day():
    day = date(2026, 10, 28)
    assert todays_plan(day, SIX, 4) == todays_plan(day, SIX, 4)


def test_every_search_runs_within_a_few_days():
    start = date(2026, 10, 28)
    seen = set()
    for offset in range(3):  # 6 searches, 2 per day -> all covered in 3 days
        seen.update(todays_plan(start + timedelta(days=offset), SIX, 2))
    assert seen == set(SIX)


def test_consecutive_days_get_different_searches():
    day = date(2026, 10, 28)
    assert todays_plan(day, SIX, 2) != todays_plan(day + timedelta(days=1), SIX, 2)


def test_empty_inputs_give_empty_plan():
    assert todays_plan(date(2026, 10, 28), [], 4) == []
    assert todays_plan(date(2026, 10, 28), SIX, 0) == []


def test_real_search_list_uses_valid_lowercase_countries():
    from app.collector.sources.jsearch import ALLOWED_COUNTRIES
    assert all(item.country in ALLOWED_COUNTRIES for item in SEARCHES)