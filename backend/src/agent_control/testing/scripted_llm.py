"""A fake `LLMProvider` that replays recorded prompt/response pairs.

This is the deterministic test tier called for in docs/HISTORY.md P2: real DB,
real tool registry, real policy engine, real worker loop, real tools against a
temp filesystem - with this in place of a live LLM. No network, no GPU,
fully reproducible.

Recording key: prompts, not call order. A scenario test that changes the
config context (adds a capability, renames a tool) changes the recorded
prompt text, which changes the key, which means a cache miss - the test fails
loudly instead of silently replaying a now-inaccurate fixture. Re-record with
`RecordingLLMProvider` when that happens; don't hand-edit the fixture around it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ScriptedLLMError(RuntimeError):
    """Raised when a scenario test hits an LLM call with no recorded fixture."""


class _LiveProvider(Protocol):
    async def generate_text(self, system_prompt: str, user_prompt: str) -> str: ...
    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str: ...
    async def generate_structured(
        self, system_prompt: str, user_prompt: str, output_model: type[T], *, temperature: float | None = None
    ) -> T: ...


# schemas.new_id() mints "<prefix>_<uuid4().hex>", and some tools (e.g.
# code.interpreter's per-task workspace dir) embed that id straight into
# their LLM-facing prompt text. That id is fresh and different on every task
# creation - including two runs of the exact same scenario test - so an
# exact-text fixture key would never match twice. Collapse any run of 6+ hex
# characters before hashing so functionally-identical prompts key the same
# regardless of which random id they happened to embed.
_HEX_RUN = re.compile(r"[0-9a-f]{6,}", re.IGNORECASE)

# Python's tempfile.TemporaryDirectory()/mkdtemp() default naming scheme -
# "tmp" + 8 chars from [a-z0-9], not restricted to hex digits, so _HEX_RUN
# alone doesn't catch it. pytest's own tmp_path fixture uses this scheme too,
# and isolated_settings() chdir's the process into it. When a plan step's
# relative path resolves against that CWD and gets rejected by policy, the
# resulting error text embeds the random dir name - and that text feeds the
# next retry prompt on a replan, producing a fresh, unmatchable fixture key
# every run (real failure hit recording output_delivery.json: the planner's
# first attempt guessed a relative path, the policy-denial error embedded
# the run's random tmp dir, and replay could never match the replan prompt
# recorded under a different random name). Collapse it the same way.
_TEMPDIR_RUN = re.compile(r"\btmp[a-z0-9]{6,}\b", re.IGNORECASE)


def _normalize(text: str) -> str:
    return _HEX_RUN.sub("<id>", _TEMPDIR_RUN.sub("<tmpdir>", text))


def fixture_key(method: str, system_prompt: str, user_prompt: str) -> str:
    normalized = f"{method}\n{_normalize(system_prompt)}\n{_normalize(user_prompt)}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:16]


def _load(fixture_path: Path) -> dict[str, dict[str, Any]]:
    if not fixture_path.exists():
        return {}
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _save(fixture_path: Path, entries: dict[str, dict[str, Any]]) -> None:
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")


class ScriptedLLMProvider:
    """Replays recorded (prompt -> response) pairs from a JSON fixture file."""

    def __init__(self, fixture_path: str | Path) -> None:
        self.fixture_path = Path(fixture_path)
        self._entries = _load(self.fixture_path)
        self.calls: list[dict[str, str]] = []  # for test assertions on call order/count

    def _lookup(self, method: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        key = fixture_key(method, system_prompt, user_prompt)
        entry = self._entries.get(key)
        if entry is None:
            raise ScriptedLLMError(
                f"No recorded '{method}' fixture (key={key}) in {self.fixture_path}.\n"
                f"system_prompt[:200]={system_prompt[:200]!r}\n"
                f"user_prompt[:200]={user_prompt[:200]!r}\n"
                "Record it with RecordingLLMProvider against a live LLM, or the prompt "
                "text changed and every fixture using it needs re-recording."
            )
        self.calls.append({"method": method, "key": key})
        return entry

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        return str(self._lookup("generate_text", system_prompt, user_prompt)["response"])

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        return str(self._lookup("generate_multimodal_text", system_prompt, user_prompt)["response"])

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[T],
        *,
        temperature: float | None = None,
    ) -> T:
        entry = self._lookup("generate_structured", system_prompt, user_prompt)
        return output_model.model_validate(entry["response"])


class RecordingLLMProvider:
    """Wraps a live LLMProvider and persists every call into a fixture file.

    Point this at a real profile (e.g. LocalDeploy), run the scenario test
    once with it in place of ScriptedLLMProvider, and the fixture file is
    written ready to commit and replay from thereafter.
    """

    def __init__(self, live_provider: _LiveProvider, fixture_path: str | Path) -> None:
        self.live_provider = live_provider
        self.fixture_path = Path(fixture_path)
        self._entries = _load(self.fixture_path)

    def _store(self, method: str, system_prompt: str, user_prompt: str, response: Any) -> None:
        key = fixture_key(method, system_prompt, user_prompt)
        self._entries[key] = {
            "method": method,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response": response,
        }
        _save(self.fixture_path, self._entries)

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        response = await self.live_provider.generate_text(system_prompt, user_prompt)
        self._store("generate_text", system_prompt, user_prompt, response)
        return response

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        response = await self.live_provider.generate_multimodal_text(system_prompt, user_prompt, image_paths)
        self._store("generate_multimodal_text", system_prompt, user_prompt, response)
        return response

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[T],
        *,
        temperature: float | None = None,
    ) -> T:
        result = await self.live_provider.generate_structured(
            system_prompt, user_prompt, output_model, temperature=temperature
        )
        self._store("generate_structured", system_prompt, user_prompt, result.model_dump(mode="json"))
        return result
