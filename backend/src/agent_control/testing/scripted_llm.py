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
import tempfile
from difflib import SequenceMatcher
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
_SCENARIO_SCRATCH_PATTERN = (
    r"(?:[A-Za-z]:\\(?:[^\\\r\n]+\\)*AppData\\Local\\Temp\\ybm_scenario_scratch|"
    r"/tmp/ybm_scenario_scratch)"
)
_SCENARIO_SCRATCH_ROOT = re.compile(
    _SCENARIO_SCRATCH_PATTERN,
    re.IGNORECASE,
)
_SCENARIO_SCRATCH_PATH = re.compile(
    _SCENARIO_SCRATCH_PATTERN + r"(?:[\\/][^\s'\"),\]}]+)*",
    re.IGNORECASE,
)
_CURRENT_SCENARIO_SCRATCH_ROOT = str(
    Path(tempfile.gettempdir()) / "ybm_scenario_scratch"
)


def _normalize(text: str) -> str:
    # Prompt history renders dict values with escaped Windows separators
    # (``C:\\Users``), while objectives and tool output contain ordinary
    # separators (``C:\Users``). Collapse the rendered form before replacing
    # scenario roots so recordings replay across both users and platforms.
    text = text.replace("\\\\", "\\")

    def replace_path(match: re.Match[str]) -> str:
        matched_path = match.group(0)
        root_match = _SCENARIO_SCRATCH_ROOT.match(matched_path)
        suffix = matched_path[root_match.end():] if root_match else ""
        normalized_suffix = suffix.replace("\\", "/")
        return f"<scenario_scratch_root>{normalized_suffix}"

    text = _SCENARIO_SCRATCH_PATH.sub(replace_path, text)
    return _HEX_RUN.sub("<id>", _TEMPDIR_RUN.sub("<tmpdir>", text))


def fixture_key(method: str, system_prompt: str, user_prompt: str) -> str:
    normalized = f"{method}\n{_normalize(system_prompt)}\n{_normalize(user_prompt)}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:16]


def _load(fixture_path: Path) -> dict[str, dict[str, Any]]:
    if not fixture_path.exists():
        return {}
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _reindex(entries: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Recompute keys when a stored fixture includes its original prompts."""
    indexed = {}
    for stored_key, entry in entries.items():
        method = entry.get("method")
        system_prompt = entry.get("system_prompt")
        user_prompt = entry.get("user_prompt")
        key = (
            fixture_key(method, system_prompt, user_prompt)
            if all(isinstance(value, str) for value in (method, system_prompt, user_prompt))
            else stored_key
        )
        indexed[key] = entry
    return indexed


def _rebase_scenario_paths(value: Any) -> Any:
    if isinstance(value, str):
        def replace_path(match: re.Match[str]) -> str:
            matched_path = match.group(0)
            root_match = _SCENARIO_SCRATCH_ROOT.match(matched_path)
            suffix = matched_path[root_match.end():] if root_match else ""
            parts = [part for part in re.split(r"[\\/]", suffix) if part]
            return str(Path(_CURRENT_SCENARIO_SCRATCH_ROOT, *parts))

        return _SCENARIO_SCRATCH_PATH.sub(replace_path, value)
    if isinstance(value, list):
        return [_rebase_scenario_paths(item) for item in value]
    if isinstance(value, dict):
        return {key: _rebase_scenario_paths(item) for key, item in value.items()}
    return value


def _closest_prompt_difference(
    entries: dict[str, dict[str, Any]],
    method: str,
    user_prompt: str,
) -> str:
    actual = _normalize(user_prompt)
    candidates = [
        (key, _normalize(str(entry["user_prompt"])))
        for key, entry in entries.items()
        if entry.get("method") == method
        and isinstance(entry.get("user_prompt"), str)
    ]
    if not candidates:
        return "no stored prompts use this method"

    closest_key, expected = max(
        candidates,
        key=lambda item: SequenceMatcher(None, item[1], actual).ratio(),
    )
    matcher = SequenceMatcher(None, expected, actual)
    difference = next(
        (opcode for opcode in matcher.get_opcodes() if opcode[0] != "equal"),
        None,
    )
    if difference is None:
        return f"closest fixture {closest_key} has identical normalized user prompt"

    _, expected_start, expected_end, actual_start, actual_end = difference
    expected_excerpt = expected[max(0, expected_start - 80) : expected_end + 80]
    actual_excerpt = actual[max(0, actual_start - 80) : actual_end + 80]
    return (
        f"closest fixture {closest_key} first difference: "
        f"expected={expected_excerpt!r}; actual={actual_excerpt!r}"
    )


def _save(fixture_path: Path, entries: dict[str, dict[str, Any]]) -> None:
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8")


class ScriptedLLMProvider:
    """Replays recorded (prompt -> response) pairs from a JSON fixture file."""

    def __init__(self, fixture_path: str | Path) -> None:
        self.fixture_path = Path(fixture_path)
        self._entries = _reindex(_load(self.fixture_path))
        self.calls: list[dict[str, str]] = []  # for test assertions on call order/count

    def _lookup(self, method: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        key = fixture_key(method, system_prompt, user_prompt)
        entry = self._entries.get(key)
        if entry is None:
            difference = _closest_prompt_difference(
                self._entries, method, user_prompt
            )
            raise ScriptedLLMError(
                f"{difference}\n"
                f"No recorded '{method}' fixture (key={key}) in {self.fixture_path}.\n"
                f"system_prompt[:200]={system_prompt[:200]!r}\n"
                f"user_prompt[:200]={user_prompt[:200]!r}\n"
                "Record it with RecordingLLMProvider against a live LLM, or the prompt "
                "text changed and every fixture using it needs re-recording."
            )
        self.calls.append({"method": method, "key": key})
        return _rebase_scenario_paths(entry)

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
        self._entries = _reindex(_load(self.fixture_path))
        # Same interface as ScriptedLLMProvider.calls - scenario tests that
        # assert on call count/order (e.g. "this objective needs zero LLM
        # calls") should work identically whether replaying or recording.
        self.calls: list[dict[str, str]] = []

    def _store(self, method: str, system_prompt: str, user_prompt: str, response: Any) -> None:
        key = fixture_key(method, system_prompt, user_prompt)
        self._entries[key] = {
            "method": method,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response": response,
        }
        _save(self.fixture_path, self._entries)
        self.calls.append({"method": method, "key": key})

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
