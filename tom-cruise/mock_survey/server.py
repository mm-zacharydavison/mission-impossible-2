#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.8"
# dependencies = ["aiohttp"]
# ///
"""Mock survey server using aiohttp — ported from server.ts (Bun.serve)."""

from __future__ import annotations

import json
import os
import random
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote

from aiohttp import web

PROJECT_DIR = Path(__file__).resolve().parent.parent  # tom-cruise/
PAGES_DIR = PROJECT_DIR / "mock-survey" / "pages"
ASSETS_DIR = PROJECT_DIR / "mock-survey"
GENERATOR_DIR = PROJECT_DIR.parent / "attention-video" / "generator-v1"


def _generate_random_sequence(length: int = 4) -> str:
    """Generate a random digit sequence, avoiding leading zeros."""
    first = str(random.randint(1, 9))
    rest = "".join(str(random.randint(0, 9)) for _ in range(length - 1))
    return first + rest


def _generate_attention_video(sequence: str) -> Path:
    """Run flicker.py + mp4ToGif.py via uv to produce a GIF for the given sequence."""
    work_dir = Path(tempfile.mkdtemp(prefix="attention-"))
    mp4_path = work_dir / "va.mp4"
    gif_path = work_dir / "va.gif"

    flicker_script = GENERATOR_DIR / "flicker.py"
    gif_script = GENERATOR_DIR / "mp4ToGif.py"

    subprocess.run(
        ["uv", "run", str(flicker_script), sequence, "-o", str(mp4_path)],
        check=True,
    )
    subprocess.run(
        ["uv", "run", str(gif_script), str(mp4_path), str(gif_path), "--min-ms", "20"],
        check=True,
    )

    return gif_path


EXPECTED_SEQUENCE = _generate_random_sequence()
ATTENTION_GIF_PATH = _generate_attention_video(EXPECTED_SEQUENCE)
COMPLETION_CODE = f"SURVEY-{secrets.token_hex(3).upper()}"

Submission = dict[str, Any]

submissions: list[Submission] = []


def parse_form_data(body: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for pair in body.split("&"):
        parts = pair.split("=", 1)
        key = unquote(parts[0]) if parts[0] else ""
        val = unquote(parts[1]) if len(parts) > 1 else ""
        if key:
            data[key] = val
    return data


def extract_tracking(form_data: dict[str, str]) -> Optional[dict[str, Any]]:
    raw = form_data.pop("__tracking", None)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def serve_page(page_name: str, error: Optional[str] = None) -> web.Response:
    file_path = PAGES_DIR / page_name
    html = file_path.read_text(encoding="utf-8")
    html = html.replace("{{COMPLETION_CODE}}", COMPLETION_CODE)
    if error:
        error_html = f'<div class="error">{error}</div>'
        html = html.replace("<h2>", f"{error_html}<h2>", 1)
    return web.Response(text=html, content_type="text/html", charset="utf-8")


async def handle_post(
    request: web.Request,
    current_page: str,
    next_page: str,
    validate: Optional[Callable[[dict[str, str]], Optional[str]]] = None,
) -> web.Response:
    body = await request.text()
    form_data = parse_form_data(body)
    tracking = extract_tracking(form_data)

    if validate:
        err = validate(form_data)
        if err:
            return await serve_page(current_page, err)

    submissions.append({
        "page": current_page,
        "formData": form_data,
        "tracking": tracking,
        "timestamp": int(time.time() * 1000),
    })
    return await serve_page(next_page)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def handle_styles(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(ASSETS_DIR / "styles.css", headers={"Content-Type": "text/css"})


async def handle_tracker(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(ASSETS_DIR / "tracker.js", headers={"Content-Type": "application/javascript"})


async def handle_attention_gif(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(ATTENTION_GIF_PATH, headers={"Content-Type": "image/gif"})


async def handle_results(_request: web.Request) -> web.Response:
    return web.json_response({
        "submissions": submissions,
        "expectedSequence": EXPECTED_SEQUENCE,
        "completionCode": COMPLETION_CODE,
    })


async def handle_index(_request: web.Request) -> web.Response:
    return await serve_page("consent.html")


async def handle_demographics(request: web.Request) -> web.Response:
    def validate(data: dict[str, str]) -> Optional[str]:
        if data.get("consent") != "yes":
            return "You must agree to the consent form to continue."
        return None
    return await handle_post(request, "consent.html", "demographics.html", validate)


async def handle_attention_video(request: web.Request) -> web.Response:
    def validate(data: dict[str, str]) -> Optional[str]:
        if not data.get("age"):
            return "Please select your age range."
        if not data.get("gender"):
            return "Please select your gender."
        if not data.get("education"):
            return "Please select your education level."
        if not data.get("state"):
            return "Please select your state."
        return None
    return await handle_post(request, "demographics.html", "attention-video.html", validate)


async def handle_opinion(request: web.Request) -> web.Response:
    def validate(data: dict[str, str]) -> Optional[str]:
        if not data.get("attention_answer"):
            return "Please enter the numbers from the video."
        if data["attention_answer"].strip() != EXPECTED_SEQUENCE:
            return f"Incorrect answer. Please watch the video again. (You entered: {data['attention_answer']})"
        return None
    return await handle_post(request, "attention-video.html", "opinion.html", validate)


async def handle_open_ended(request: web.Request) -> web.Response:
    def validate(data: dict[str, str]) -> Optional[str]:
        for i in range(1, 6):
            if not data.get(f"opinion_{i}"):
                return f"Please answer question {i}."
        return None
    return await handle_post(request, "opinion.html", "open-ended.html", validate)


async def handle_dictator(request: web.Request) -> web.Response:
    def validate(data: dict[str, str]) -> Optional[str]:
        if not data.get("open_ended") or len(data["open_ended"].strip()) < 50:
            return "Please write at least 3 sentences (minimum 50 characters)."
        return None
    return await handle_post(request, "open-ended.html", "dictator.html", validate)


async def handle_debrief(request: web.Request) -> web.Response:
    def validate(data: dict[str, str]) -> Optional[str]:
        try:
            amount = int(data.get("dictator_amount", ""))
        except (ValueError, TypeError):
            return "Please select a valid amount between $0 and $10."
        if amount < 0 or amount > 10:
            return "Please select a valid amount between $0 and $10."
        return None
    return await handle_post(request, "dictator.html", "debrief.html", validate)


# ---------------------------------------------------------------------------
# App factory and entry point
# ---------------------------------------------------------------------------

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/styles.css", handle_styles)
    app.router.add_get("/tracker.js", handle_tracker)
    app.router.add_get("/assets/attention.gif", handle_attention_gif)
    app.router.add_get("/results", handle_results)
    app.router.add_post("/demographics", handle_demographics)
    app.router.add_post("/attention-video", handle_attention_video)
    app.router.add_post("/opinion", handle_opinion)
    app.router.add_post("/open-ended", handle_open_ended)
    app.router.add_post("/dictator", handle_dictator)
    app.router.add_post("/debrief", handle_debrief)
    return app


async def start_server(
    host: str = "0.0.0.0",
    port: Optional[int] = None,
) -> web.AppRunner:
    if port is None:
        port = int(os.environ.get("PORT", "3456"))

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    print(f"Mock survey server running at http://localhost:{port}")
    print(f"Expected attention sequence: {EXPECTED_SEQUENCE}")
    print(f"Completion code: {COMPLETION_CODE}")
    print(f"Results endpoint: http://localhost:{port}/results")
    return runner


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3456"))
    app = create_app()
    print(f"Mock survey server running at http://localhost:{port}")
    print(f"Expected attention sequence: {EXPECTED_SEQUENCE}")
    print(f"Completion code: {COMPLETION_CODE}")
    print(f"Results endpoint: http://localhost:{port}/results")
    web.run_app(app, host="0.0.0.0", port=port, print=None)
