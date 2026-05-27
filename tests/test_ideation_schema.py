"""Ideation tool schema and normalization (Anthropic key constraints)."""

from __future__ import annotations

import re

from workflows.ideation import (
    _IDEA_FUNCTION_SPEC,
    _IDEA_TOOL_PROPERTY_KEYS,
    _normalize_idea_from_tool,
)

_ANTHROPIC_KEY = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")


def test_ideation_tool_property_keys_match_anthropic_pattern() -> None:
    props = _IDEA_FUNCTION_SPEC["function"]["parameters"]["properties"]
    for key in _IDEA_TOOL_PROPERTY_KEYS:
        assert key in props
        assert _ANTHROPIC_KEY.match(key), f"invalid tool key: {key!r}"


def test_normalize_idea_from_tool_maps_snake_case() -> None:
    raw = {
        "name": "vfe_nca",
        "title": "VFE NCA",
        "short_hypothesis": "Free-energy updates beat MSE.",
        "related_work": "BraiNCA, MorphoNAS",
        "abstract": "Abstract text.",
        "experiments": ["Train on 32x32", "Ablate 25% damage"],
        "risk_factors_and_limitations": "Compute cost.",
    }
    idea = _normalize_idea_from_tool(raw)
    assert idea["Name"] == "vfe_nca"
    assert idea["Short Hypothesis"] == "Free-energy updates beat MSE."
    assert idea["Experiments"] == ["Train on 32x32", "Ablate 25% damage"]


def test_normalize_coerces_string_experiments_to_list() -> None:
    idea = _normalize_idea_from_tool(
        {
            "name": "x",
            "title": "T",
            "short_hypothesis": "h",
            "related_work": "r",
            "abstract": "a",
            "experiments": "single experiment line",
            "risk_factors_and_limitations": "none",
        }
    )
    assert idea["Experiments"] == ["single experiment line"]
