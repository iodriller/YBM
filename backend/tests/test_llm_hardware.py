"""Hardware probing and preset fit.

These exist because the preset labels used to say "runs on this machine"
without anyone having looked at the machine.
"""

from __future__ import annotations

import pytest

from agent_control.llm import hardware as hw


@pytest.fixture(autouse=True)
def _no_localdeploy(monkeypatch):
    """probe() asks LocalDeploy first, so a developer who happens to have it
    running would otherwise get different results from CI."""
    monkeypatch.setattr(hw, "probe_localdeploy", lambda timeout=2.5: None)


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


# -- LocalDeploy is preferred when it is running -----------------------------

_REAL_PAYLOAD = {
    "success": True,
    "gpu_available": True,
    "gpus": [
        {
            "name": "NVIDIA GeForce RTX 3080 Laptop GPU",
            "vendor": "NVIDIA",
            "vram_total_mb": 8192,
            "vram_free_mb": 5703,
            "vram_estimated": False,
        },
        {
            "name": "AMD Radeon(TM) Graphics",
            "vendor": "AMD",
            "vram_total_mb": 512,
            "vram_free_mb": None,
            "vram_estimated": True,
        },
    ],
    "system": {"memory_total_mb": 32768},
}


def test_localdeploy_payload_picks_the_gpu_a_model_would_load_onto() -> None:
    """Two GPUs, and the integrated one must not win. Fit is judged against
    free VRAM, so a card already busy does not get a model recommended into
    memory it does not have."""
    machine = hw._from_localdeploy(_REAL_PAYLOAD)
    assert machine is not None
    assert "3080" in (machine.gpu_name or "")
    assert machine.vram_gb == 5.6  # free, not the 8 GB total
    assert machine.ram_gb == 32.0
    assert any("free right now" in n for n in machine.notes)
    assert any("2 GPUs" in n for n in machine.notes)


def test_localdeploy_with_no_gpu_reports_cpu_rather_than_failure() -> None:
    machine = hw._from_localdeploy({"success": True, "gpus": [], "system": {"memory_total_mb": 16384}})
    assert machine is not None and machine.detected is True
    assert machine.vram_gb is None
    assert any("CPU" in n for n in machine.notes)


def test_an_unsuccessful_localdeploy_response_is_not_trusted() -> None:
    assert hw._from_localdeploy({"success": False, "gpus": [{"vram_total_mb": 99999}]}) is None


def test_probe_prefers_localdeploy_over_local_detection(monkeypatch) -> None:
    monkeypatch.setattr(hw, "probe_localdeploy", lambda timeout=2.5: hw.Hardware(detected=True, gpu_name="from-ld", vram_gb=5.6))
    # Local detection would say something different; it must not be consulted.
    monkeypatch.setattr(hw, "_in_container", lambda: True)
    assert hw.probe().gpu_name == "from-ld"


def test_probe_falls_back_when_localdeploy_is_not_running(monkeypatch) -> None:
    monkeypatch.setattr(hw, "probe_localdeploy", lambda timeout=2.5: None)
    monkeypatch.setattr(hw, "_in_container", lambda: False)
    monkeypatch.setattr(hw, "_nvidia_gpu", lambda: ("RTX 3080", 8.0))
    monkeypatch.setattr(hw, "_system_ram_gb", lambda: 32.0)
    assert hw.probe().vram_gb == 8.0


def test_a_free_vram_gpu_that_cannot_fit_a_model_is_reported_honestly() -> None:
    """5.6 GB free against a 7 GB model: exactly the case the old label got
    wrong by claiming it 'runs on this machine'."""
    machine = hw._from_localdeploy(_REAL_PAYLOAD)
    assert machine is not None
    verdict = hw.fit("localdeploy_qwen3vl_8b", machine)
    assert verdict["status"] == "too_big"
    assert "5.6" in verdict["reason"]
