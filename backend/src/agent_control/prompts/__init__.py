from __future__ import annotations

from importlib import resources
from string import Template
from typing import Any


def prompt_text(name: str) -> str:
    path = resources.files(__package__).joinpath(name)
    return path.read_text(encoding="utf-8").strip()


def render_prompt(name: str, **values: Any) -> str:
    substitutions = {key: "" if value is None else str(value) for key, value in values.items()}
    return Template(prompt_text(name)).safe_substitute(substitutions)
