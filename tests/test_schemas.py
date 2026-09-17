from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from drove.pipeline.schemas import PlanDoc, ReviewVerdict, json_schema


def test_schemas_are_self_contained():
    """Harnesses do not reliably resolve $ref, so nested models must be inlined."""
    for model in (PlanDoc, ReviewVerdict):
        text = json.dumps(json_schema(model))
        assert "$ref" not in text
        assert "$defs" not in text


def test_nested_model_survives_inlining():
    steps = json_schema(PlanDoc)["properties"]["steps"]
    assert steps["items"]["properties"]["title"]["type"] == "string"


def test_review_verdict_rejects_unknown_verdict():
    ReviewVerdict.model_validate({"verdict": "pass"})
    with pytest.raises(ValidationError):
        ReviewVerdict.model_validate({"verdict": "lgtm"})
