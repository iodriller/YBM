"""Assemble the README demo GIF from the frames the Playwright recorder wrote.

Two steps on purpose: Playwright owns "what the console looked like", this owns
"how long each beat is held". Re-timing the demo does not mean re-recording it.

    cd frontend && YBM_RECORD_DEMO=1 npx playwright test demo.spec.ts
    backend/.venv/Scripts/python scripts/make_demo_gif.py

Pillow only - no ffmpeg, no imagemagick, nothing a contributor has to install
beyond what `ybm setup` already put in the backend venv.
"""

from __future__ import annotations

from pathlib import Path
import sys

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
FRAME_DIR = REPO_ROOT / "frontend" / ".demo-frames"
OUTPUT = REPO_ROOT / "docs" / "screenshots" / "demo.gif"

# Rendered at 1280 wide; GitHub shows the README at roughly half that, and
# every pixel here is paid for on every page load.
TARGET_WIDTH = 960

# How long each beat holds, in milliseconds, keyed by the frame's name suffix.
# The approval frame is the one the whole demo exists for, so it gets the
# longest hold - it is the difference between "an agent ran a command" and "an
# agent asked first".
HOLD_MS = {
    "empty": 2000,
    "typing": 450,
    "typed": 900,
    "scanning": 1500,
    "approval": 3000,
    "done": 3000,
    "tasks": 2600,
}
DEFAULT_HOLD_MS = 1500


def load_frames() -> list[tuple[Path, Image.Image]]:
    paths = sorted(FRAME_DIR.glob("*.png"))
    if not paths:
        raise SystemExit(
            f"No frames in {FRAME_DIR}.\n"
            "Record them first: cd frontend && YBM_RECORD_DEMO=1 npx playwright test demo.spec.ts"
        )
    frames = []
    for path in paths:
        image = Image.open(path).convert("RGB")
        if image.width != TARGET_WIDTH:
            height = round(image.height * TARGET_WIDTH / image.width)
            image = image.resize((TARGET_WIDTH, height), Image.LANCZOS)
        frames.append((path, image))
    widths = {image.size for _, image in frames}
    if len(widths) != 1:
        raise SystemExit(f"Frames disagree on size ({widths}); re-record them in one run.")
    return frames


def hold_for(path: Path) -> int:
    # "03-typed.png" -> "typed"
    beat = path.stem.split("-", 1)[-1]
    return HOLD_MS.get(beat, DEFAULT_HOLD_MS)


def main() -> int:
    frames = load_frames()
    durations = [hold_for(path) for path, _ in frames]
    images = [image for _, image in frames]

    # ADAPTIVE keeps the console's flat UI colors clean at 256 entries; the
    # default web palette visibly banded the card backgrounds.
    quantized = [
        image.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        for image in images
    ]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    quantized[0].save(
        OUTPUT,
        save_all=True,
        append_images=quantized[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )

    size_kb = OUTPUT.stat().st_size / 1024
    print(f"Wrote {OUTPUT.relative_to(REPO_ROOT)}")
    print(f"  {len(images)} frames, {images[0].width}x{images[0].height}, {size_kb:.0f} KB")
    for path, duration in zip((p for p, _ in frames), durations):
        print(f"  {path.name:<20} {duration:>5} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
