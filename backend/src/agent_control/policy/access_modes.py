from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.schemas import Capability, CapabilityAccessMode, CapabilityAccessSummary, RiskLevel


@dataclass(frozen=True)
class AccessGroup:
    name: str
    label: str
    read_capabilities: tuple[Capability, ...] = ()
    write_capabilities: tuple[Capability, ...] = ()
    read_risk: RiskLevel = RiskLevel.LOW
    write_risk: RiskLevel = RiskLevel.HIGH
    options: tuple[CapabilityAccessMode, ...] = (
        CapabilityAccessMode.OFF,
        CapabilityAccessMode.READ_ONLY,
        CapabilityAccessMode.WRITE_ACCESS,
        CapabilityAccessMode.FULL_ACCESS,
    )
    option_labels: dict[CapabilityAccessMode, str] | None = None


ACCESS_GROUPS = {
    "filesystem": AccessGroup(
        name="filesystem",
        label="File system",
        read_capabilities=(Capability.FILESYSTEM_READ,),
        write_capabilities=(Capability.FILESYSTEM_WRITE,),
        write_risk=RiskLevel.HIGH,
        option_labels={
            CapabilityAccessMode.OFF: "Off",
            CapabilityAccessMode.READ_ONLY: "Read-only",
            CapabilityAccessMode.WRITE_ACCESS: "Write with approval",
            CapabilityAccessMode.FULL_ACCESS: "Full write",
        },
    ),
    "vscode": AccessGroup(
        name="vscode",
        label="VS Code bridge",
        read_capabilities=(Capability.VSCODE_READ_STATE,),
        write_capabilities=(Capability.VSCODE_WRITE_FILES,),
        write_risk=RiskLevel.HIGH,
        option_labels={
            CapabilityAccessMode.OFF: "Off",
            CapabilityAccessMode.READ_ONLY: "Read state",
            CapabilityAccessMode.WRITE_ACCESS: "Write with approval",
            CapabilityAccessMode.FULL_ACCESS: "Full write",
        },
    ),
    "desktop_screenshot": AccessGroup(
        name="desktop_screenshot",
        label="Desktop screenshots",
        read_capabilities=(Capability.DESKTOP_SCREENSHOT,),
        options=(CapabilityAccessMode.OFF, CapabilityAccessMode.READ_ONLY),
        option_labels={
            CapabilityAccessMode.OFF: "Off",
            CapabilityAccessMode.READ_ONLY: "On",
        },
    ),
    "desktop_control": AccessGroup(
        name="desktop_control",
        label="Desktop control",
        write_capabilities=(Capability.DESKTOP_CONTROL,),
        write_risk=RiskLevel.CRITICAL,
        options=(CapabilityAccessMode.OFF, CapabilityAccessMode.WRITE_ACCESS, CapabilityAccessMode.FULL_ACCESS),
        option_labels={
            CapabilityAccessMode.OFF: "Off",
            CapabilityAccessMode.WRITE_ACCESS: "Control with approval",
            CapabilityAccessMode.FULL_ACCESS: "Full control",
        },
    ),
    "browser": AccessGroup(
        name="browser",
        label="Browser",
        read_capabilities=(Capability.BROWSER_OPEN,),
        write_capabilities=(Capability.BROWSER_CONTROL,),
        write_risk=RiskLevel.CRITICAL,
        option_labels={
            CapabilityAccessMode.OFF: "Off",
            CapabilityAccessMode.READ_ONLY: "Open only",
            CapabilityAccessMode.WRITE_ACCESS: "Control with approval",
            CapabilityAccessMode.FULL_ACCESS: "Full control",
        },
    ),
    "github": AccessGroup(
        name="github",
        label="GitHub",
        read_capabilities=(Capability.GITHUB_READ,),
        write_capabilities=(Capability.GITHUB_PUSH,),
        write_risk=RiskLevel.CRITICAL,
        option_labels={
            CapabilityAccessMode.OFF: "Off",
            CapabilityAccessMode.READ_ONLY: "Read-only",
            CapabilityAccessMode.WRITE_ACCESS: "Push with approval",
            CapabilityAccessMode.FULL_ACCESS: "Full push",
        },
    ),
    "terminal": AccessGroup(
        name="terminal",
        label="Terminal",
        write_capabilities=(Capability.TERMINAL_RUN,),
        write_risk=RiskLevel.MEDIUM,
        options=(CapabilityAccessMode.OFF, CapabilityAccessMode.WRITE_ACCESS, CapabilityAccessMode.FULL_ACCESS),
        option_labels={
            CapabilityAccessMode.OFF: "Off",
            CapabilityAccessMode.WRITE_ACCESS: "Run with approval",
            CapabilityAccessMode.FULL_ACCESS: "Full run",
        },
    ),
    "dependencies": AccessGroup(
        name="dependencies",
        label="Dependency installs",
        write_capabilities=(Capability.DEPENDENCIES_INSTALL,),
        write_risk=RiskLevel.HIGH,
        options=(CapabilityAccessMode.OFF, CapabilityAccessMode.WRITE_ACCESS, CapabilityAccessMode.FULL_ACCESS),
        option_labels={
            CapabilityAccessMode.OFF: "Off",
            CapabilityAccessMode.WRITE_ACCESS: "Install with approval",
            CapabilityAccessMode.FULL_ACCESS: "Full install",
        },
    ),
}


def summarize_access_modes(settings: AppSettings) -> dict[str, CapabilityAccessSummary]:
    return {name: _summarize_group(settings, group) for name, group in ACCESS_GROUPS.items()}


def apply_access_modes_to_config(config: dict[str, Any], modes: dict[str, CapabilityAccessMode]) -> dict[str, Any]:
    capabilities = config.setdefault("capabilities", {})
    for name, mode in modes.items():
        group = ACCESS_GROUPS.get(name)
        if group is None and name == "desktop":
            for legacy_group_name in ("desktop_screenshot", "desktop_control"):
                _apply_group(capabilities, ACCESS_GROUPS[legacy_group_name], mode)
                _sync_adapter_flag(config, legacy_group_name, mode)
            continue
        if group is None:
            continue
        _apply_group(capabilities, group, mode)
        _sync_adapter_flag(config, name, mode)
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
        label=group.label,
        mode=mode,
        capabilities=all_caps,
        options=[
            {
                "value": option.value,
                "label": (group.option_labels or {}).get(option, option.value.replace("_", " ").title()),
            }
            for option in group.options
        ],
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


def _sync_adapter_flag(config: dict[str, Any], group_name: str, mode: CapabilityAccessMode) -> None:
    if group_name == "desktop_screenshot":
        desktop = config.setdefault("adapters", {}).setdefault("desktop", {})
        desktop["screenshot_enabled"] = mode != CapabilityAccessMode.OFF
    elif group_name == "desktop_control":
        desktop = config.setdefault("adapters", {}).setdefault("desktop", {})
        desktop["control_enabled"] = mode in {CapabilityAccessMode.WRITE_ACCESS, CapabilityAccessMode.FULL_ACCESS}
        computer_use = config.setdefault("adapters", {}).setdefault("computer_use", {})
        computer_use["enabled"] = mode in {CapabilityAccessMode.WRITE_ACCESS, CapabilityAccessMode.FULL_ACCESS}
    elif group_name == "browser":
        browser = config.setdefault("adapters", {}).setdefault("browser", {})
        browser["enabled"] = mode != CapabilityAccessMode.OFF
