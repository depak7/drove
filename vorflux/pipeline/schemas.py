"""Typed stage contracts.

Stages exchange objects, not prose. Each model here is both the JSON Schema handed to the harness
(`--json-schema` / `--output-schema`) and the validator applied to what comes back. If a stage
cannot produce a conforming object the run fails loudly rather than degrading to regex parsing.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    title: str = Field(description="Imperative one-line description of the change")
    files: list[str] = Field(default_factory=list, description="Repo-relative paths touched")
    detail: str = Field(default="", description="What changes and why")


class PlanDoc(BaseModel):
    summary: str = Field(description="One paragraph: what will be built and the approach")
    files_to_touch: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(
        default_factory=list, description="Observable conditions that mean this is done"
    )
    test_plan: str = Field(default="", description="How to verify, as commands where possible")


class BlockingIssue(BaseModel):
    file: str = ""
    line: int | None = None
    severity: Literal["critical", "major", "minor"] = "major"
    why: str = ""


class ReviewVerdict(BaseModel):
    verdict: Literal["pass", "changes_requested"]
    summary: str = ""
    blocking: list[BlockingIssue] = Field(default_factory=list)


def json_schema(model: type[BaseModel], strict: bool = True) -> dict[str, Any]:
    """Pydantic schema, inlined and (by default) in OpenAI strict mode.

    Two harness-driven transforms:

    * ``$ref``/``$defs`` are inlined, because harness ``--json-schema`` implementations do not
      reliably resolve references.
    * strict mode adds ``additionalProperties: false`` to every object and lists every property
      in ``required``. Codex rejects anything else outright::

          invalid_json_schema: In context=(), 'additionalProperties' is required to be
          supplied and to be false.

      Claude accepts loose schemas, but strict is a superset that both accept, so there is one
      schema shape for all harnesses rather than one per vendor.
    """
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def inline(node: Any, depth: int = 0) -> Any:
        if depth > 20:
            return node
        if isinstance(node, dict):
            if ref := node.get("$ref"):
                name = ref.rsplit("/", 1)[-1]
                target = inline(defs.get(name, {}), depth + 1)
                return {**target, **{k: v for k, v in node.items() if k != "$ref"}}
            return {k: inline(v, depth + 1) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(v, depth + 1) for v in node]
        return node

    schema = inline(raw)

    if strict:
        def tighten(node: Any, depth: int = 0) -> Any:
            if depth > 20 or not isinstance(node, dict):
                if isinstance(node, list):
                    return [tighten(v, depth + 1) for v in node]
                return node
            node = {k: tighten(v, depth + 1) for k, v in node.items()}
            if isinstance(node.get("properties"), dict):
                node["additionalProperties"] = False
                # Strict mode requires *every* property listed; optionality is expressed by
                # nullable types, not by omission.
                node["required"] = list(node["properties"])
            return node

        schema = tighten(schema)

    return schema
