from __future__ import annotations

import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_wix_payload.py"
NS = {"w": "http://wixtoolset.org/schemas/v4/wxs"}


def _load_generator():
    spec = importlib.util.spec_from_file_location("ybm_generate_wix_payload_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_per_user_components_use_hkcu_keypaths(tmp_path) -> None:
    generator = _load_generator()
    payload = tmp_path / "payload"
    (payload / "backend" / "src").mkdir(parents=True)
    (payload / "YBM.bat").write_text("@echo off\n", encoding="utf-8")
    (payload / "backend" / "src" / "main.py").write_text("pass\n", encoding="utf-8")
    output = tmp_path / "payload.wxs"

    generator.generate(payload, output)

    root = ET.parse(output).getroot()
    file_components = [
        component for component in root.findall(".//w:Component", NS) if component.find("w:File", NS) is not None
    ]
    assert len(file_components) == 2
    for component in file_components:
        file = component.find("w:File", NS)
        registry = component.find("w:RegistryValue", NS)
        assert component.attrib["Guid"] != "*"
        assert file is not None and file.attrib["KeyPath"] == "no"
        assert registry is not None
        assert registry.attrib["Root"] == "HKCU"
        assert registry.attrib["KeyPath"] == "yes"

    remove_folders = root.findall(".//w:RemoveFolder", NS)
    assert len(remove_folders) == 3  # INSTALLFOLDER plus backend/ and backend/src/
