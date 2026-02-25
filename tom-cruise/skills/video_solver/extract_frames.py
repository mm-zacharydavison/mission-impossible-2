import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp


@dataclass
class FrameAnalysis:
    path: str
    is_blank: bool
    top_margin: float
    bottom_margin: float
    is_fully_visible: bool
    content_hash: float


@dataclass
class ExtractionConfig:
    # @default 5
    fps: int = 5
    # @default 0.999
    blank_threshold: float = 0.999
    # @default 250
    bright_luminance: int = 250
    # @default 0.10
    edge_margin_threshold: float = 0.10
    # @default 200
    content_luminance_threshold: int = 200
    # @default 0.005
    content_row_threshold: float = 0.005


async def download_video(url: str) -> str:
    temp_dir = tempfile.mkdtemp(prefix="video-dl-")
    ext = "gif" if url.endswith(".gif") else "mp4"
    output_path = Path(temp_dir) / f"video.{ext}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            response.raise_for_status()
            data = await response.read()

    output_path.write_bytes(data)
    return str(output_path)


async def extract_frames(
    video_path: str,
    config: ExtractionConfig | None = None,
) -> list[str]:
    cfg = config or ExtractionConfig()
    temp_dir = tempfile.mkdtemp(prefix="frames-")
    output_pattern = str(Path(temp_dir) / "frame_%04d.png")

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", video_path,
        "-vf", f"fps={cfg.fps}",
        output_pattern,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()

    frame_paths = sorted(
        str(p)
        for p in Path(temp_dir).iterdir()
        if p.name.startswith("frame_") and p.suffix == ".png"
    )
    return frame_paths


async def _get_grayscale_pixels(frame_path: str) -> tuple[bytes, int, int]:
    # Get dimensions via ffprobe
    probe_proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        frame_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    probe_stdout, _ = await probe_proc.communicate()
    dims = probe_stdout.decode().strip().split("x")
    width = int(dims[0])
    height = int(dims[1])

    # Extract raw grayscale pixels
    raw_proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", frame_path,
        "-vf", "format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    raw_stdout, _ = await raw_proc.communicate()

    return raw_stdout, width, height


async def analyze_frame(
    frame_path: str,
    config: ExtractionConfig | None = None,
) -> FrameAnalysis:
    cfg = config or ExtractionConfig()
    data, width, height = await _get_grayscale_pixels(frame_path)

    bright_pixels = 0
    total_pixels = width * height
    for i in range(len(data)):
        if data[i] >= cfg.bright_luminance:
            bright_pixels += 1

    bright_fraction = bright_pixels / total_pixels
    is_blank = bright_fraction >= cfg.blank_threshold

    if is_blank:
        return FrameAnalysis(
            path=frame_path,
            is_blank=True,
            top_margin=1.0,
            bottom_margin=1.0,
            is_fully_visible=False,
            content_hash=0,
        )

    y_min = height
    y_max = 0

    for y in range(height):
        dark_pixels_in_row = 0
        for x in range(width):
            pixel = data[y * width + x]
            if pixel < cfg.content_luminance_threshold:
                dark_pixels_in_row += 1
        dark_fraction = dark_pixels_in_row / width
        if dark_fraction >= cfg.content_row_threshold:
            y_min = min(y_min, y)
            y_max = max(y_max, y)

    content_darkness = 0
    content_pixel_count = 0

    if y_min <= y_max:
        for y in range(y_min, y_max + 1):
            for x in range(width):
                pixel = data[y * width + x]
                if pixel < cfg.content_luminance_threshold:
                    content_darkness += 255 - pixel
                    content_pixel_count += 1

    top_margin = y_min / height
    bottom_margin = (height - 1 - y_max) / height
    is_fully_visible = (
        top_margin >= cfg.edge_margin_threshold
        and bottom_margin >= cfg.edge_margin_threshold
    )

    return FrameAnalysis(
        path=frame_path,
        is_blank=False,
        top_margin=top_margin,
        bottom_margin=bottom_margin,
        is_fully_visible=is_fully_visible,
        content_hash=content_darkness / content_pixel_count if content_pixel_count > 0 else 0,
    )


def deduplicate_frames(
    frames: list[FrameAnalysis],
    original_indices: list[int],
) -> list[FrameAnalysis]:
    if not frames:
        return []

    groups: list[list[FrameAnalysis]] = [[frames[0]]]

    for i in range(1, len(frames)):
        prev_idx = original_indices[i - 1]
        curr_idx = original_indices[i]
        if curr_idx - prev_idx <= 1:
            groups[-1].append(frames[i])
        else:
            groups.append([frames[i]])

    result: list[FrameAnalysis] = []
    for group in groups:
        best = group[0]
        best_center_score = abs(best.top_margin - best.bottom_margin)
        for frame in group[1:]:
            score = abs(frame.top_margin - frame.bottom_margin)
            if score < best_center_score:
                best = frame
                best_center_score = score
        result.append(best)

    return result


async def extract_distinct_frames(
    video_path: str,
    config: ExtractionConfig | None = None,
) -> list[str]:
    frame_paths = await extract_frames(video_path, config)
    analyses = await asyncio.gather(
        *[analyze_frame(fp, config) for fp in frame_paths]
    )

    fully_visible: list[FrameAnalysis] = []
    original_indices: list[int] = []

    for i, analysis in enumerate(analyses):
        if not analysis.is_blank and analysis.is_fully_visible:
            fully_visible.append(analysis)
            original_indices.append(i)

    representatives = deduplicate_frames(fully_visible, original_indices)
    return [r.path for r in representatives]
