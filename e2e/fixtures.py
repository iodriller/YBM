"""Shared E2E test fixtures.

Provides the file/folder layout that case messages reference via
``{{documents_folder}}`` / ``{{episode_url}}`` / ``{{form_url}}`` templates,
plus an optional local static-file web server for cases that need an http://
target without going to the public internet.

This module exists so the active runner (``scripts/run_all_e2e_tests.py``)
doesn't depend on the legacy ``live_telegram_e2e.py`` runner — only on the
fixture setup itself.
"""
from __future__ import annotations

import shutil
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


class Fixtures:
    def __init__(self, values: dict[str, str], server: ThreadingHTTPServer | None = None) -> None:
        self.values = values
        self.server = server

    def public(self) -> dict[str, str]:
        return dict(self.values)


def prepare_fixtures(*, start_web_server: bool) -> Fixtures:
    fixture_root = ROOT / ".agent_control" / "e2e_fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)

    desktop_folder = Path.home() / "Desktop" / "AgentControlE2E"
    desktop_folder.mkdir(parents=True, exist_ok=True)
    pdf_path = desktop_folder / "agent-control-sample.pdf"
    _write_minimal_pdf(
        pdf_path,
        "Agent Control E2E PDF. This file describes desktop automation, browser testing, and artifact delivery.",
    )

    resumes_folder = Path.home() / "Desktop" / "resumes"
    resumes_folder.mkdir(parents=True, exist_ok=True)
    (resumes_folder / "oney-resume-notes.txt").write_text(
        "Oney resume notes: Python automation, local LLM orchestration, desktop control, browser automation.",
        encoding="utf-8",
    )
    (resumes_folder / "cover-letter-draft.md").write_text(
        "# Cover Letter Draft\n\nFocus on autonomous agent-control systems and pragmatic software engineering.",
        encoding="utf-8",
    )

    documents_folder = fixture_root / "documents"
    if documents_folder.exists():
        shutil.rmtree(documents_folder)
    documents_folder.mkdir(parents=True)
    (documents_folder / "notes.txt").write_text("notes for e2e organization", encoding="utf-8")
    (documents_folder / "budget.csv").write_text("name,amount\nsample,10\n", encoding="utf-8")
    shutil.copy2(pdf_path, documents_folder / "sample.pdf")

    mixed_content_folder = fixture_root / "mixed_content"
    if mixed_content_folder.exists():
        shutil.rmtree(mixed_content_folder)
    mixed_content_folder.mkdir(parents=True)
    (mixed_content_folder / "automation-notes.txt").write_text(
        "Alpha automation notes. This text file describes desktop inspection and folder summaries.",
        encoding="utf-8",
    )
    (mixed_content_folder / "budget-data.csv").write_text(
        "category,amount\nbrowser-testing,120\nocr-review,45\n", encoding="utf-8"
    )
    (mixed_content_folder / "release-summary.md").write_text(
        "# Release Summary\n\nThe folder contains notes, budget data, a PDF, an HTML page, and an image fixture.",
        encoding="utf-8",
    )
    (mixed_content_folder / "landing-page.html").write_text(
        "<html><body><h1>Fixture Page</h1><p>This HTML file is part of the mixed content folder.</p></body></html>",
        encoding="utf-8",
    )
    _write_minimal_pdf(
        mixed_content_folder / "mixed-folder-summary.pdf",
        "Mixed folder PDF. It covers OCR, documents, and local file explanation.",
    )

    image_folder = fixture_root / "images"
    if image_folder.exists():
        shutil.rmtree(image_folder)
    image_folder.mkdir(parents=True)
    tiny_png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    (image_folder / "desktop-screenshot.png").write_bytes(tiny_png)
    (image_folder / "receipt-sample.png").write_bytes(tiny_png)
    try:
        from PIL import Image, ImageDraw

        ocr_image = Image.new("RGB", (320, 100), color="white")
        draw = ImageDraw.Draw(ocr_image)
        draw.text((16, 38), "OCR SAMPLE TEXT", fill="black")
        ocr_image.save(mixed_content_folder / "ocr-sample.png")
    except Exception:
        (mixed_content_folder / "ocr-sample.png").write_bytes(tiny_png)

    voice_ogg_path = fixture_root / "voice-command.ogg"
    voice_ogg_path.write_bytes(_fake_ogg_voice_bytes())

    fake_mcp_server_path = fixture_root / "fake_mcp_server.py"
    fake_mcp_server_path.write_text(
        """from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake")


@mcp.tool()
def echo(text: str) -> str:
    return text


if __name__ == "__main__":
    mcp.run()
""",
        encoding="utf-8",
    )

    values: dict[str, str] = {
        "desktop_folder": str(desktop_folder),
        "resumes_folder": str(resumes_folder),
        "pdf_path": str(pdf_path),
        "documents_folder": str(documents_folder),
        "mixed_content_folder": str(mixed_content_folder),
        "image_folder": str(image_folder),
        "voice_ogg_path": str(voice_ogg_path),
        "fake_mcp_server_path": str(fake_mcp_server_path),
    }

    server: ThreadingHTTPServer | None = None
    if start_web_server:
        web_root = fixture_root / "web"
        web_root.mkdir(parents=True, exist_ok=True)
        (web_root / "index.html").write_text(
            "<html><head><title>Agent Control E2E Site</title></head>"
            "<body><h1>Agent Control E2E Site</h1>"
            "<p>This page exists for browser screenshot tests.</p></body></html>",
            encoding="utf-8",
        )
        (web_root / "episode.html").write_text(
            "<html><head><title>E2E Episode Tracker</title></head>"
            "<body><h1>New Episode Released</h1>"
            "<p>Episode 4 came out on May 21, 2026.</p></body></html>",
            encoding="utf-8",
        )
        (web_root / "form.html").write_text(
            "<html><head><title>E2E Contact Form</title></head>"
            "<body><form><label>Name <input name='name'></label>"
            "<label>Email <input name='email'></label>"
            "<label>Message <textarea name='message'></textarea></label>"
            "<button type='submit'>Submit</button></form></body></html>",
            encoding="utf-8",
        )
        server, base_url = _start_static_server(web_root)
        values.update(
            {
                "fixture_base_url": base_url,
                "episode_url": f"{base_url}/episode.html",
                "form_url": f"{base_url}/form.html",
            }
        )
    else:
        values.update({"fixture_base_url": "", "episode_url": "", "form_url": ""})

    return Fixtures(values, server)


# ---------- internal helpers ----------


def _start_static_server(root: Path) -> tuple[ThreadingHTTPServer, str]:
    port = _free_port()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_minimal_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        f"5 0 obj << /Length {len(stream.encode('latin-1'))} >> stream\n{stream}\nendstream endobj\n",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(output))
        output.extend(obj.encode("latin-1"))
    xref_at = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    output.extend(
        f"trailer << /Root 1 0 R /Size {len(objects) + 1} >>\nstartxref\n{xref_at}\n%%EOF\n".encode("latin-1")
    )
    path.write_bytes(output)


def _fake_ogg_voice_bytes() -> bytes:
    return (
        b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x01\x00\x00\x00\x00\x00\x00\x00\x1e\x01OpusHead\x01\x01"
        b"\x38\x01\x80\xbb\x00\x00\x00\x00\x00OpusTags\r\x00\x00\x00"
        b"AgentControl\x00\x00\x00\x00"
    )
