"""Prompt registry. Prompts are markdown files, loaded once and content-hashed so every log line and eval
report can say exactly which prompt version produced an answer."""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def load_prompt(name: str) -> str:
    path = _DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {name}")
    return path.read_text(encoding="utf-8").strip()


@cache
def prompt_version(name: str) -> str:
    return hashlib.sha256(load_prompt(name).encode("utf-8")).hexdigest()[:10]


def render(name: str, **values: str) -> str:
    text = load_prompt(name)
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text
