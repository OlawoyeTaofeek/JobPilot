"""Unit tests for skill normalisation: aliases, cleaning, stopwords, categories."""

import pytest

from app.collector.skills import ALIASES, SKILL_CATEGORY, normalize_skill, skill_category


@pytest.mark.parametrize("raw, expected", [
    ("PyTorch", "pytorch"),
    ("Py-Torch", "pytorch"),
    ("pytorch", "pytorch"),
    ("Amazon Web Services (AWS)", "aws"),
    ("Large Language Models (LLMs)", "llm"),
    ("T-SQL", "sql"),
    ("CI/CD", "ci/cd"),
    ("Scikit-Learn", "scikit-learn"),
    ("W&B", "weights & biases"),
    ("Retrieval-Augmented Generation (RAG)", "rag"),
    ("Python.", "python"),
    ("  Docker  ", "docker"),
])
def test_known_spellings_map_to_one_name(raw, expected):
    assert normalize_skill(raw) == expected


@pytest.mark.parametrize("raw", ["C++", "C#", "Node.js"])
def test_symbols_that_matter_survive(raw):
    assert normalize_skill(raw) == raw.lower()


@pytest.mark.parametrize("raw", [None, "", "   ", "Communication", "Teamwork", "Bachelor's degree", "(AWS)"])
def test_junk_and_soft_skills_are_dropped(raw):
    assert normalize_skill(raw) is None


def test_unknown_skills_are_kept_cleaned():
    assert normalize_skill("Some New Tool") == "some new tool"


def test_too_long_is_dropped():
    assert normalize_skill("x" * 81) is None


def test_categories():
    assert skill_category("pytorch") == "ml_library"
    assert skill_category("rag") == "llm_genai"
    assert skill_category("never heard of it") == "other"


def test_every_alias_points_at_a_categorised_skill():
    assert set(ALIASES.values()) <= set(SKILL_CATEGORY)