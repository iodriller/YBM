from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.schemas import Capability, CapabilityAccessMode, CapabilityAccessSummary, RiskLevel


@dataclass(frozen=True)
class AccessGroup:
    name: str
    read_capabilities: tuple[Capability, ...] = ()
    write_capabilities: tuple[Capability, ...] = ()
    read_risk: RiskLevel = RiskLevel.LOW
    write_risk: RiskLevel = RiskLevel.HIGH


ACCESS_GROUPS = {
    "filesystem": AccessGroup(
        name="filesystem",
        read_capabilities=(Capability.FILESYSTEM_READ,),
        write_capabilities=(Capability.FILESYSTEM_WRITE,),
        write_risk=RiskLevel.HIGH,
    ),
    "vscode": AccessGroup(
        name="vscode",
        read_capabilities=(Capability.VSCODE_READ_STATE,),
        write_capabilities=(Capability.VSCODE_WRITE_FILES,),
        write_risk=RiskLevel.HIGH,
    ),
    "desktop": AccessGroup(
        name="desktop",
        read_capabilities=(Capability.DESKTOP_SCREENSHOT,),
        write_capabilities=(Capability.DESKTOP_CONTROL,),
        write_risk=RiskLevel.CRITICAL,
    ),
    "browser": AccessGroup(
        name="browser",
        read_capabilities=(Capability.BROWSER_OPEN,),
        write_capabilities=(Capability.BROWSER_CONTROL,),
        write_risk=RiskLevel.CRITICAL,
    ),
    "github": AccessGroup(
        name="github",
        read_capabilities=(Capability.GITHUB_READ,),
        write_capabilities=(Capability.GITHUB_PUSH,),
        write_risk=RiskLevel.CRITICAL,
    ),
    "terminal": AccessGroup(
        name="terminal",
        write_capabilities=(Capability.TERMINAL_RUN,),
        write_risk=RiskLevel.MEDIUM,
    ),
    "dependencies": AccessGroup(
        name="dependencies",
        write_capabilities=(Capability.DEPENDENCIES_INSTALL,),
        write_risk=RiskLevel.HIGH,
    ),
}


def summarize_access_modes(settings: AppSettings) -> dict[str, CapabilityAccessSummary]:
    return {name: _summarize_group(settings, group) for name, group in ACCESS_GROUPS.items()}


def apply_access_modes_to_config(config: dict[str, Any], modes: dict[str, CapabilityAccessMode]) -> dict[str, Any]:
    capabilities = config.setdefault("capabilities", {})
    for name, mode in modes.items():
        group = ACCESS_GROUPS.get(name)
        if group is None:
            continue
        _apply_group(capabilities, group, mode)
    return config


def _summarize_group(settings: AppSettings, group: AccessGroup) -> CapabilityAccessSummary:
    all_caps = [*group.read_capabilities, *group.write_capabilities]
    read_enabled = any(_enabled(settings, capability) for capability in group.read_capabilities)
    write_enabled = any(_enabled(settings, capability) for capability in group.write_capabilities)
    write_requires_approval = any(_requires_approval(settings, capability) for capability in group.write_capabilities)

    if write_enabled and not write_requires_approval:
        mode = CapabilityAccessMode.FULL_ACCESS
    elif write_enabled:
        mode = CapabilityAccessMode.WRITE_ACCESS
    elif read_enabled:
        mode = CapabilityAccessMode.READ_ONLY
    else:
        mode = CapabilityAccessMode.OFF

    return CapabilityAccessSummary(
        name=group.name,
        mode=mode,
        capabilities=all_caps,
        requires_approval=write_requires_approval,
    )


def _enabled(settings: AppSettings, capability: Capability) -> bool:
    policy = settings.capabilities.get(capability)
    return bool(policy and policy.enabled)


def _requires_approval(settings: AppSettings, capability: Capability) -> bool:
    policy = settings.capabilities.get(capability)
    return True if policy is None else policy.requires_approval


def _apply_group(
    capabilities: dict[str, Any],
    group: AccessGroup,
    mode: CapabilityAccessMode,
) -> None:
    for capability in group.read_capabilities:
        capabilities[capability.value] = _policy(
            enabled=mode in {CapabilityAccessMode.READ_ONLY, CapabilityAccessMode.WRITE_ACCESS, CapabilityAccessMode.FULL_ACCESS},
            requires_approval=False,
            max_risk_level=group.read_risk,
        )
    for capability in group.write_capabilities:
        capabilities[capability.value] = _policy(
            enabled=mode in {CapabilityAccessMode.WRITE_ACCESS, CapabilityAccessMode.FULL_ACCESS},
            requires_approval=mode != CapabilityAccessMode.FULL_ACCESS,
            max_risk_level=group.write_risk,
        )


def _policy(enabled: bool, requires_approval: bool, max_risk_level: RiskLevel) -> dict[str, Any]:
    return CapabilityPolicy(
        enabled=enabled,
        requires_approval=requires_approval,
        max_risk_level=max_risk_level,
    ).model_dump(mode="json")
