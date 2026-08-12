"""Fail when a maintained Markdown file links to a missing local path."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def markdown_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted({REPO_ROOT / value for value in result.stdout.splitlines()})


def link_target(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1:value.index(">")]
    return value.split(maxsplit=1)[0]


def missing_links(path: Path) -> list[tuple[int, str]]:
    missing: list[tuple[int, str]] = []
    in_fence = False
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in LINK_RE.finditer(line):
            target = link_target(match.group(1))
            parsed = urlsplit(target)
            if not target or target.startswith("#") or parsed.scheme or parsed.netloc:
                continue
            local_value = unquote(parsed.path)
            local_path = (path.parent / local_value).resolve()
            if not local_path.exists():
                missing.append((line_number, target))
    return missing


def main() -> int:
    failures = [
        (path, line_number, target)
        for path in markdown_files()
        for line_number, target in missing_links(path)
    ]
    if failures:
        for path, line_number, target in failures:
            print(f"{path.relative_to(REPO_ROOT)}:{line_number}: missing local link: {target}")
        return 1
    print("Maintained Markdown links are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
