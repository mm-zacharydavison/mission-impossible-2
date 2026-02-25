"""Digit recognition from video frames using Claude's vision API."""

import asyncio
import base64
import re
from pathlib import Path

import anthropic

client = anthropic.AsyncAnthropic()


async def read_digit_from_frame(frame_path: str) -> str:
    """Read a single digit from a frame image using Claude's vision."""
    path = Path(frame_path)
    image_data = path.read_bytes()
    b64 = base64.standard_b64encode(image_data).decode("ascii")

    ext = path.suffix.lstrip(".").lower()
    if ext == "png":
        media_type = "image/png"
    elif ext in ("jpg", "jpeg"):
        media_type = "image/jpeg"
    else:
        media_type = "image/png"

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=16,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This image contains a single digit (0-9) displayed "
                            "in large monospace font on a white background. "
                            "What digit is shown? Reply with ONLY the single "
                            "digit, nothing else."
                        ),
                    },
                ],
            }
        ],
    )

    block = response.content[0]
    text = block.text.strip() if block.type == "text" else ""

    # Extract just the digit from the response
    match = re.search(r"\d", text)
    return match.group(0) if match else text


async def read_digits_from_frames(frame_paths: list[str]) -> str:
    """Read digits from an ordered array of frame images.

    Processes frames concurrently for speed, but preserves order.
    """
    if not frame_paths:
        return ""

    results = await asyncio.gather(
        *(read_digit_from_frame(fp) for fp in frame_paths)
    )
    return "".join(results)


async def read_digits_with_validation(
    frame_paths: list[str], sequence_length: int
) -> str:
    """Read digits with cross-validation across loops.

    If the video loops N times, we expect the same sequence repeated N times.
    Differences are resolved by majority vote.
    """
    all_digits = await read_digits_from_frames(frame_paths)

    if len(all_digits) <= sequence_length:
        return all_digits

    # Split into loops
    loops: list[str] = []
    for i in range(0, len(all_digits), sequence_length):
        loops.append(all_digits[i : i + sequence_length])

    # Majority vote per position
    result: list[str] = []
    for pos in range(sequence_length):
        votes: dict[str, int] = {}
        for loop in loops:
            if pos < len(loop):
                digit = loop[pos]
                votes[digit] = votes.get(digit, 0) + 1

        best_digit = ""
        best_count = 0
        for digit, count in votes.items():
            if count > best_count:
                best_digit = digit
                best_count = count
        result.append(best_digit)

    return "".join(result)
