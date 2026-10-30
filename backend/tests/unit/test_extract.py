"""Unit tests for the extractor: input building, grounding check, money, extract_one.

extract_one runs a real Agent on agents.testing.ScriptedModel: the whole SDK path
(schema validation, error handling) runs, but no API call is made.
"""

from decimal import Decimal

import httpx
import openai
import pytest
from agents import Agent
from agents.testing import ScriptedModel, assistant_message

from app.collector.extract import (
    MAX_DESCRIPTION_CHARS,
    _money,
    build_input,
    extract_one,
    is_grounded,
)
from app.collector.facts import PostingFacts
from app.db.models import Posting


def make_facts(**overrides) -> PostingFacts:
    data = dict(
        role_family="ml_engineer", seniority="mid", years_experience_min=2, work_mode="remote",
        remote_scope="worldwide", visa_sponsorship="not_mentioned", relocation_support="not_mentioned",
        clearance_required="not_mentioned", language_requirements=[], skills=["PyTorch", "AWS"],
        salary=None, quality_flag="ok", confidence=0.9, evidence="fully remote",
    )
    data.update(overrides)
    return PostingFacts(**data)


def scripted_agent(*steps) -> Agent:
    """An extractor-shaped agent whose 'model' replays the given steps."""
    return Agent(name="test_extractor", instructions="test", model=ScriptedModel(list(steps)),
                 output_type=PostingFacts)


# ---- build_input ---------------------------------------------------------------------

def test_build_input_has_the_facts_and_wraps_the_description():
    posting = Posting(id=1, title="ML Engineer", company="Acme", city="Lagos", country="NG",
                      is_remote_source=None, description="Build models.")
    text = build_input(posting)

    assert "Title: ML Engineer" in text
    assert "Company: Acme" in text
    assert "Location: Lagos, NG" in text                 # empty parts skipped, no "None"
    assert "Source says remote: unknown" in text         # None is not "no"
    assert "Source salary" not in text                   # no salary line when the source had none
    assert "<description>\nBuild models.\n</description>" in text


def test_build_input_salary_line_and_missing_end():
    posting = Posting(id=1, title="t", salary_min=Decimal("50000"), salary_currency="GBP",
                      salary_period="year", is_remote_source=True)
    text = build_input(posting)
    assert "Source salary: 50000-? GBP/year" in text
    assert "Source says remote: yes" in text


def test_build_input_caps_the_description():
    posting = Posting(id=1, title="t", description="x" * (MAX_DESCRIPTION_CHARS + 500))
    assert build_input(posting).count("x") == MAX_DESCRIPTION_CHARS


def test_build_input_handles_no_description():
    assert "<description>\n\n</description>" in build_input(Posting(id=1, title="t", description=None))


# ---- grounding and money ------------------------------------------------------------

@pytest.mark.parametrize("evidence, grounded", [
    ("fully remote", True),
    ("FULLY   REMOTE", True),                         # case and spacing don't matter
    ('"fully remote."', True),                        # quote marks and trailing dot are stripped
    ("remote within the EU", False),                  # invented
    ("", False),                                      # empty quote never counts
])
def test_is_grounded(evidence, grounded):
    description = "This role is fully\nremote. We use PyTorch."
    assert is_grounded(evidence, description) is grounded


def test_is_grounded_with_no_description():
    assert is_grounded("anything", None) is False


@pytest.mark.parametrize("value, expected", [
    (65000.0, Decimal("65000.00")),
    (1234.567, Decimal("1234.57")),
    (None, None),
    (float("nan"), None),
])
def test_money(value, expected):
    result = _money(value)
    assert result == expected if expected is not None else result is None


# ---- extract_one ---------------------------------------------------------------------

POSTING = Posting(id=7, title="ML Engineer", description="This role is fully remote.")


async def test_extract_one_returns_validated_facts():
    agent = scripted_agent([assistant_message(make_facts().model_dump_json())])
    result = await extract_one(agent, POSTING)

    assert result.error is None
    assert result.posting_id == 7
    assert result.facts.work_mode == "remote"


async def test_extract_one_records_a_per_posting_error_instead_of_raising():
    result = await extract_one(scripted_agent(ValueError("model exploded")), POSTING)
    assert result.facts is None
    assert result.error == "ValueError: model exploded"


async def test_extract_one_records_invalid_model_output():
    result = await extract_one(scripted_agent([assistant_message("not json")]), POSTING)
    assert result.facts is None
    assert result.error.startswith("ModelBehaviorError")


async def test_extract_one_raises_config_errors_so_the_batch_stops():
    response = httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com"))
    bad_key = openai.AuthenticationError("bad key", response=response, body=None)
    with pytest.raises(openai.AuthenticationError):
        await extract_one(scripted_agent(bad_key), POSTING)