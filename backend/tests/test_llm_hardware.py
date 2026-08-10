"""Hardware probing and preset fit.

These exist because the preset labels used to say "runs on this machine"
without anyone having looked at the machine.
"""

from __future__ import annotations

from agent_control.llm import hardware as hw


def test_container_refuses_to_report_the_hosts_hardware(monkeypatch) -> None:
    """Inside a container the host GPU is invisible; reporting the container's
    view would be the same mistake this module exists to prevent."""
    monkeypatch.setenv("YBM_HEADLESS", "1")
    probe = hw.probe()
    assert probe.detected is False
    assert "container" in (probe.reason or "").lower()
    assert probe.gpu_name is None and probe.vram_gb is None


def test_undetectable_hardware_says_so_rather_than_guessing(monkeypatch) -> None:
    monkeypatch.delenv("YBM_HEADLESS", raising=False)
    monkeypatch.setattr(hw, "_in_container", lambda: False)
    monkeypatch.setattr(hw, "_nvidia_gpu", lambda: None)
    monkeypatch.setattr(hw, "_system_ram_gb", lambda: None)
    probe = hw.probe()
    assert probe.detected is False
    assert probe.reason


def test_no_gpu_is_reported_as_slow_not_as_impossible(monkeypatch) -> None:
    monkeypatch.setattr(hw, "_in_container", lambda: False)
    monkeypatch.setattr(hw, "_nvidia_gpu", lambda: None)
    monkeypatch.setattr(hw, "_system_ram_gb", lambda: 32.0)
    probe = hw.probe()
    assert probe.detected is True
    assert probe.ram_gb == 32.0
    assert any("CPU" in note for note in probe.notes)


def test_a_detected_gpu_is_reported_with_its_vram(monkeypatch) -> None:
    monkeypatch.setattr(hw, "_in_container", lambda: False)
    monkeypatch.setattr(hw, "_nvidia_gpu", lambda: ("NVIDIA GeForce RTX 3080 Laptop GPU", 8.0))
    monkeypatch.setattr(hw, "_system_ram_gb", lambda: 32.0)
    probe = hw.probe()
    assert probe.detected is True
    assert probe.vram_gb == 8.0
    assert "3080" in (probe.gpu_name or "")


def test_a_model_that_does_not_fit_says_so_with_both_numbers() -> None:
    machine = hw.Hardware(detected=True, gpu_name="RTX 3080", vram_gb=8.0)
    verdict = hw.fit("localdeploy_gemma3_12b", machine)
    assert verdict["status"] == "too_big"
    assert "10" in verdict["reason"] and "8" in verdict["reason"]


def test_a_model_that_fits_is_marked_as_fitting() -> None:
    machine = hw.Hardware(detected=True, gpu_name="RTX 3080", vram_gb=8.0)
    assert hw.fit("localdeploy_qwen3vl_8b", machine)["status"] == "fits"


def test_unknown_hardware_yields_no_claim_either_way() -> None:
    """A choice with no verdict attached is correct; an unearned
    recommendation is not."""
    machine = hw.Hardware(detected=False, reason="no idea")
    verdict = hw.fit("localdeploy_qwen3vl_8b", machine)
    assert verdict["status"] == "unknown"
    assert verdict["reason"] is None


def test_every_local_preset_has_a_vram_estimate() -> None:
    """A preset with no estimate silently degrades to "unknown", which reads as
    a missing recommendation rather than a missing table entry."""
    from agent_control.admin import LLM_PRESETS

    for key in LLM_PRESETS:
        assert key in hw.PRESET_VRAM_GB, f"{key} has no VRAM estimate"
