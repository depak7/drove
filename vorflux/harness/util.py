"""Helpers shared by adapters."""

from __future__ import annotations

import json
from typing import Any

JSON_ONLY_INSTRUCTION = (
    "\n\nRespond with a single JSON object matching this schema and nothing else — "
    "no prose, no markdown fence:\n{schema}"
)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the last complete top-level JSON object out of a model's text.

    For harnesses with no structured-output flag (opencode), the object is requested in the prompt
    and has to be recovered from prose. Scanning from the last ``{`` backwards finds the final
    object even when the model prefixed it with commentary or wrapped it in a code fence.
    """
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]

    starts = [i for i, ch in enumerate(text) if ch == "{"]
    for start in reversed(starts):
        try:
            parsed = json.loads(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    # A trailing object may be followed by prose; try progressively shorter suffixes.
    for start in starts:
        decoder = json.JSONDecoder()
        try:
            parsed, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
