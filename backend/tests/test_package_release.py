from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tarfile
import zipfile


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "package_release.py"


def _load_package_release():
    spec = importlib.util.spec_from_file_location("ybm_package_release_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_archives_have_stable_download_names_and_versioned_roots(tmp_path, monkeypatch) -> None:
    package_release = _load_package_release()
    package_release.REPO_ROOT = tmp_path

    for relative in package_release.ROOT_FILES + package_release.ROOT_OF_TREE_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {relative}\n", encoding="utf-8")

    console = tmp_path / "backend/src/agent_control/static/admin/index.html"
    console.parent.mkdir(parents=True, exist_ok=True)
    console.write_text("<main>YBM</main>\n", encoding="utf-8")

    output = tmp_path / "output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--version",
            "1.2.3",
            "--output-dir",
            str(output),
            "--stage-dir",
            str(output / "payload"),
        ],
    )

    assert package_release.main() == 0
    assert sorted(path.name for path in output.glob("*.zip")) == ["YBM-windows.zip"]
    assert sorted(path.name for path in output.glob("*.tar.gz")) == ["YBM-unix.tar.gz"]

    with zipfile.ZipFile(output / "YBM-windows.zip") as archive:
        assert "YBM-1.2.3/backend/src/agent_control/static/admin/index.html" in archive.namelist()
        assert "YBM-1.2.3/Install-YBM.bat" in archive.namelist()
        assert archive.read("YBM-1.2.3/.ybm-release-version") == b"1.2.3"

    with tarfile.open(output / "YBM-unix.tar.gz") as archive:
        launcher = archive.getmember("YBM-1.2.3/ybm.sh")
        assert launcher.mode == 0o755
