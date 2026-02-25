#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "browser-use>=0.2.0",
#     "anthropic>=0.50.0",
#     "aiohttp>=3.11.0",
#     "pydantic>=2.0.0",
# ]
# ///
"""End-to-end demo: starts the mock survey server, runs the agent, and analyzes results."""

from __future__ import annotations

import asyncio
import math
import sys

import aiohttp

from agent import AgentConfig, run_survey
from mock_survey.server import (
    COMPLETION_CODE,
    EXPECTED_SEQUENCE,
    start_server,
)

PORT = 3457
SURVEY_URL = f"http://localhost:{PORT}"

PERSONA = (
    "A 42-year-old moderate Democrat from Colorado with a bachelor's degree in business. "
    "You generally support environmental protection but are concerned about the economic "
    "impact on small businesses. You believe in a balanced approach to climate policy."
)


async def analyze_results() -> None:
    """Fetch /results and print analysis."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{SURVEY_URL}/results") as resp:
            results = await resp.json()

    submissions = results["submissions"]
    print(f"\nTotal page submissions: {len(submissions)}")

    for sub in submissions:
        page = sub["page"]
        form_data: dict[str, str] = sub["formData"]
        tracking: dict | None = sub.get("tracking")

        print(f"\n--- {page} ---")

        for key, value in form_data.items():
            if key == "__tracking":
                continue
            display = value[:80] + "..." if len(value) > 80 else value
            print(f"  {key}: {display}")

        if tracking:
            print(f"  [tracking] time_on_page: {tracking.get('time_on_page')}s")
            print(f"  [tracking] mouse_moves: {tracking.get('mouse_move_count')}")
            print(f"  [tracking] clicks: {tracking.get('click_count')}")
            print(f"  [tracking] total_keys: {tracking.get('total_keys')}")
            print(f"  [tracking] paste_detected: {tracking.get('paste_detected')}")
            print(f"  [tracking] tab_hidden: {tracking.get('tab_hidden')}")

            key_log = tracking.get("key_log", [])
            if len(key_log) > 1:
                ikis: list[float] = []
                for i in range(1, len(key_log)):
                    ikis.append(key_log[i]["time"] - key_log[i - 1]["time"])

                mean_iki = sum(ikis) / len(ikis)
                variance = sum((x - mean_iki) ** 2 for x in ikis) / len(ikis)
                std_dev = math.sqrt(variance)
                cv = std_dev / mean_iki if mean_iki > 0 else 0

                print(f"  [keystrokes] count: {len(key_log)}")
                print(f"  [keystrokes] mean_IKI: {mean_iki:.0f}ms")
                print(f"  [keystrokes] std_dev: {std_dev:.0f}ms")
                print(f"  [keystrokes] CV: {cv:.3f}")

                jumps = [e for e in key_log if e.get("key") == "INPUT_JUMP"]
                print(f"  [keystrokes] input_jumps: {len(jumps)}")

                backspaces = [e for e in key_log if e.get("key") == "Backspace"]
                print(f"  [keystrokes] backspace_corrections: {len(backspaces)}")

                is_human_like = (
                    cv > 0.2
                    and len(jumps) == 0
                    and 50 < mean_iki < 500
                )
                print(f"  [assessment] human-like typing: {'PASS' if is_human_like else 'FAIL'}")

    # Check attention video answer
    attention_sub = next(
        (s for s in submissions if s["page"] == "attention-video.html"), None
    )
    if attention_sub:
        answer = attention_sub["formData"].get("attention_answer", "")
        correct = answer == EXPECTED_SEQUENCE
        print(f'\nAttention video: answer="{answer}", expected="{EXPECTED_SEQUENCE}", correct={correct}')
    else:
        print("\nAttention video: not reached")

    # Check open-ended response
    open_ended_sub = next(
        (s for s in submissions if s["page"] == "open-ended.html"), None
    )
    if open_ended_sub:
        text = open_ended_sub["formData"].get("open_ended", "")
        sentences = [s.strip() for s in text.split(".") if s.strip()]
        print(f"\nOpen-ended: {len(text)} chars, ~{len(sentences)} sentences")
        print(f'  First 150 chars: "{text[:150]}..."')


async def main() -> None:
    print("=== Mock Survey Demo ===")
    print(f"Survey URL: {SURVEY_URL}")
    print(f"Expected attention sequence: {EXPECTED_SEQUENCE}")
    print(f"Completion code: {COMPLETION_CODE}")
    print()
    print(f"Persona: {PERSONA}")
    print()

    # Start mock survey server
    runner = await start_server(port=PORT)

    try:
        # Run the agent
        completion_code = await run_survey(
            AgentConfig(
                survey_url=SURVEY_URL,
                persona=PERSONA,
                headless=False,
            )
        )

        if completion_code:
            print(f"\nAgent extracted completion code: {completion_code}")
        else:
            print("\nAgent did not extract a completion code.")

        # Analyze results
        print("\n=== Post-Run Analysis ===")
        await analyze_results()

    except Exception as e:
        print(f"\nAgent failed: {e}", file=sys.stderr)
        raise
    finally:
        await runner.cleanup()

    print("\n=== Demo Complete ===")


if __name__ == "__main__":
    asyncio.run(main())
