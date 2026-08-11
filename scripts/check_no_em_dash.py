"""Fail when repository-owned text contains an em dash or encoded equivalent."""

from __future__ import annotations

from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
EM_DASH = chr(0x2014)
ENCODED_FORMS = (
    chr(92) + "u2014",
    chr(92) + "u{2014}",
    chr(92) + "U00002014",
    chr(92) + "2014",
    "&" + "mdash;",
    "&#" + "8212;",
    "&#x" + "2014;",
)


def repository_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return [REPO_ROOT / value.decode("utf-8") for value in result.stdout.split(b"\0") if value]


def occurrences(path: Path) -> list[tuple[int, int, str]]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    found: list[tuple[int, int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for column_number, character in enumerate(line, start=1):
            if character == EM_DASH:
                found.append((line_number, column_number, "Unicode em dash"))
        folded = line.casefold()
        for token in ENCODED_FORMS:
            start = 0
            while (index := folded.find(token, start)) != -1:
                found.append((line_number, index + 1, "encoded em dash"))
                start = index + len(token)
    return found


def main() -> int:
    failures = [
        (path, line_number, column_number, description)
        for path in repository_files()
        for line_number, column_number, description in occurrences(path)
    ]
    if failures:
        for path, line_number, column_number, description in failures:
            relative = path.relative_to(REPO_ROOT)
            print(f"{relative}:{line_number}:{column_number}: {description} is not allowed")
        return 1
    print("No Unicode em dashes found in repository text.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
