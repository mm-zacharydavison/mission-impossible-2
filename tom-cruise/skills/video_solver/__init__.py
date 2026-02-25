"""Video solver skill — browser-use custom action registration."""

from browser_use import ActionResult

from skills.video_solver.extract_frames import download_video, extract_distinct_frames
from skills.video_solver.read_digits import read_digits_from_frames


def detect_repeating_sequence(digits: str) -> str:
    """Detect the shortest repeating prefix in a digit string.

    e.g. "3169316931693169" -> "3169"
    """
    for length in range(1, len(digits) // 2 + 1):
        candidate = digits[:length]
        matches = True
        for i in range(length, len(digits), length):
            chunk = digits[i : i + length]
            if len(chunk) == length and chunk != candidate:
                matches = False
                break
        if matches:
            return candidate
    return digits


async def solve_attention_video_tool(gif_url: str) -> ActionResult:
    """Download the GIF from the given URL, extract distinct digit frames,
    OCR each digit via Claude vision, and detect the repeating sequence.

    Use this when you encounter a video or GIF showing scrolling digits
    as an attention check. Pass the full src URL of the image element.
    """
    video_path = await download_video(gif_url)
    frames = await extract_distinct_frames(video_path)

    if not frames:
        return ActionResult(error="No fully-visible digit frames found in the video")

    all_digits = await read_digits_from_frames(frames)
    sequence = detect_repeating_sequence(all_digits)
    return ActionResult(
        extracted_content=f"The digit sequence is: {sequence}"
    )
