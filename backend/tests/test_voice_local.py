from __future__ import annotations

from types import SimpleNamespace
import sys
import types

import pytest

from agent_control.config import AppSettings, CapabilityPolicy, STTAdapterConfig, TTSAdapterConfig
from agent_control.schemas import Capability, RiskLevel, ToolCallRequest
from agent_control.tools.registry import build_tool_registry
from agent_control.tools.stt import FasterWhisperSTTAdapter, build_stt_adapter
from agent_control.tools.tts import KokoroOnnxTTSAdapter


@pytest.mark.asyncio
async def test_faster_whisper_stt_transcribes_with_local_runtime(monkeypatch, tmp_path) -> None:
    class Segment:
        text = " tell me what is on my desktop right now "

    class FakeWhisperModel:
        calls = []

        def __init__(self, model: str, *, device: str, compute_type: str) -> None:
            self.model = model
            self.device = device
            self.compute_type = compute_type

        def transcribe(self, path: str, **kwargs):
            self.calls.append((path, kwargs))
            return [Segment()], SimpleNamespace(language="en", language_probability=0.91, duration=1.2)

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    adapter = build_stt_adapter(
        STTAdapterConfig(
            enabled=True,
            provider="faster_whisper",
            model="base",
            temp_dir=str(tmp_path),
            language="en",
        )
    )

    result = await adapter.transcribe(b"audio", file_name="voice.ogg", mime_type="audio/ogg")

    assert isinstance(adapter, FasterWhisperSTTAdapter)
    assert result.text == "tell me what is on my desktop right now"
    assert result.language == "en"
    assert result.metadata["provider"] == "faster_whisper"
    assert result.metadata["segment_count"] == 1


@pytest.mark.asyncio
async def test_kokoro_tts_synthesizes_with_local_runtime(monkeypatch, tmp_path) -> None:
    class FakeKokoro:
        def __init__(self, model_path: str, voices_path: str) -> None:
            self.model_path = model_path
            self.voices_path = voices_path

        def create(self, text: str, *, voice: str, speed: float, lang: str):
            return [0, 1000, -1000], 24000

    module = types.ModuleType("kokoro_onnx")
    module.Kokoro = FakeKokoro
    monkeypatch.setitem(sys.modules, "kokoro_onnx", module)

    def fake_write_wav(path, samples, sample_rate):
        path.write_bytes(b"wav")

    monkeypatch.setattr("agent_control.tools.tts._write_wav", fake_write_wav)
    model_path = tmp_path / "kokoro.onnx"
    voices_path = tmp_path / "voices.bin"
    model_path.write_bytes(b"model")
    voices_path.write_bytes(b"voices")

    adapter = KokoroOnnxTTSAdapter(
        TTSAdapterConfig(
            enabled=True,
            model_path=str(model_path),
            voices_path=str(voices_path),
            output_dir=str(tmp_path / "out"),
        )
    )

    result = await adapter.synthesize("Hello from local TTS.", output_name="hello")

    assert result["provider"] == "kokoro_onnx"
    assert result["sample_rate"] == 24000
    assert result["path"].endswith("hello.wav")


def test_registry_exposes_tts_when_capability_and_adapter_are_enabled(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.TTS_SYNTHESIZE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.LOW,
            ),
        },
        adapters={
            "tts": {
                "enabled": True,
                "provider": "kokoro_onnx",
                "model_path": str(tmp_path / "kokoro.onnx"),
                "voices_path": str(tmp_path / "voices.bin"),
                "output_dir": str(tmp_path / "tts"),
            }
        },
    )

    registry = build_tool_registry(settings, "http://127.0.0.1:8765")
    definition = next(item for item in registry.definitions if item.name == "tts.synthesize")

    assert definition.enabled is True
    assert "tts.synthesize" in registry.adapters
    validated = definition.validate_input({"operation": "synthesize", "text": "hello"})
    assert validated["text"] == "hello"


@pytest.mark.asyncio
async def test_tts_execute_returns_valid_tool_output(monkeypatch, tmp_path) -> None:
    adapter = KokoroOnnxTTSAdapter(
        TTSAdapterConfig(
            enabled=True,
            model_path=str(tmp_path / "kokoro.onnx"),
            voices_path=str(tmp_path / "voices.bin"),
            output_dir=str(tmp_path),
        )
    )
    async def fake_synthesize(text, voice=None, output_name=None):
        return {
            "path": str(tmp_path / "voice.wav"),
            "voice": voice or "af_sarah",
            "provider": "kokoro_onnx",
            "sample_rate": 24000,
            "summary": "ok",
        }

    monkeypatch.setattr(adapter, "synthesize", fake_synthesize)

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_1",
            tool_name="tts.synthesize",
            capability=Capability.TTS_SYNTHESIZE,
            input={"operation": "synthesize", "text": "hello"},
        )
    )

    assert result.output["operation"] == "synthesize"
    assert result.output["path"].endswith("voice.wav")
