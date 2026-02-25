import asyncio
import struct
import tempfile
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skills.video_solver.extract_frames import (
    ExtractionConfig,
    FrameAnalysis,
    analyze_frame,
    deduplicate_frames,
    download_video,
    extract_distinct_frames,
    extract_frames,
)

SAMPLE_VIDEO = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "attention-video"
    / "generator-v1"
    / "va.mp4"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_grayscale_png(path: Path, width: int, height: int, pixel_value: int) -> Path:
    """Create a valid single-colour grayscale PNG file."""
    raw_rows = b""
    for _ in range(height):
        # filter byte 0 (None) + pixel data
        raw_rows += b"\x00" + bytes([pixel_value] * width)

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    idat_data = zlib.compress(raw_rows)

    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", ihdr_data)
    png += _chunk(b"IDAT", idat_data)
    png += _chunk(b"IEND", b"")

    path.write_bytes(png)
    return path


def _make_frame_png(
    tmp_path: Path,
    name: str,
    width: int,
    height: int,
    rows: list[int] | None = None,
    fill: int = 255,
) -> Path:
    """Create a grayscale PNG with per-row luminance control.

    Args:
        rows: list of length ``height`` with per-row pixel values.
              If None, every row is filled with ``fill``.
    """
    if rows is None:
        rows = [fill] * height

    raw = b""
    for row_val in rows:
        raw += b"\x00" + bytes([row_val] * width)

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    idat_data = zlib.compress(raw)

    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", ihdr_data)
    png += _chunk(b"IDAT", idat_data)
    png += _chunk(b"IEND", b"")

    p = tmp_path / name
    p.write_bytes(png)
    return p


# ---------------------------------------------------------------------------
# Unit tests – FrameAnalysis / ExtractionConfig dataclasses
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_frame_analysis_fields(self) -> None:
        fa = FrameAnalysis(
            path="/tmp/f.png",
            is_blank=False,
            top_margin=0.2,
            bottom_margin=0.3,
            is_fully_visible=True,
            content_hash=42.5,
        )
        assert fa.path == "/tmp/f.png"
        assert fa.is_blank is False
        assert fa.top_margin == 0.2
        assert fa.bottom_margin == 0.3
        assert fa.is_fully_visible is True
        assert fa.content_hash == 42.5

    def test_extraction_config_defaults(self) -> None:
        cfg = ExtractionConfig()
        assert cfg.fps == 5
        assert cfg.blank_threshold == 0.999
        assert cfg.bright_luminance == 250
        assert cfg.edge_margin_threshold == 0.10
        assert cfg.content_luminance_threshold == 200
        assert cfg.content_row_threshold == 0.005

    def test_extraction_config_override(self) -> None:
        cfg = ExtractionConfig(fps=10, blank_threshold=0.95)
        assert cfg.fps == 10
        assert cfg.blank_threshold == 0.95
        # Other fields keep defaults
        assert cfg.bright_luminance == 250


# ---------------------------------------------------------------------------
# Unit tests – analyze_frame (synthetic PNGs, real ffmpeg)
# ---------------------------------------------------------------------------


class TestAnalyzeFrame:
    async def test_blank_white_frame(self, tmp_path: Path) -> None:
        p = _make_grayscale_png(tmp_path / "white.png", 100, 100, 255)
        result = await analyze_frame(str(p))
        assert result.is_blank is True
        assert result.content_hash == 0

    async def test_all_black_frame_is_not_blank(self, tmp_path: Path) -> None:
        p = _make_grayscale_png(tmp_path / "black.png", 100, 100, 0)
        result = await analyze_frame(str(p))
        assert result.is_blank is False

    async def test_content_centered_is_fully_visible(self, tmp_path: Path) -> None:
        """Content in the middle 60% with white margins should be fully visible."""
        height = 100
        rows = []
        for y in range(height):
            if 20 <= y <= 79:
                rows.append(50)  # dark content
            else:
                rows.append(255)  # white margin
        p = _make_frame_png(tmp_path, "centered.png", 100, height, rows=rows)
        result = await analyze_frame(str(p))
        assert result.is_blank is False
        assert result.is_fully_visible is True
        assert result.top_margin >= 0.1
        assert result.bottom_margin >= 0.1

    async def test_content_at_top_edge_not_fully_visible(self, tmp_path: Path) -> None:
        """Content starting at row 0 should not be fully visible."""
        height = 100
        rows = []
        for y in range(height):
            if y <= 50:
                rows.append(50)  # dark content
            else:
                rows.append(255)  # white margin
        p = _make_frame_png(tmp_path, "top_edge.png", 100, height, rows=rows)
        result = await analyze_frame(str(p))
        assert result.is_blank is False
        assert result.is_fully_visible is False
        assert result.top_margin < 0.1

    async def test_content_at_bottom_edge_not_fully_visible(self, tmp_path: Path) -> None:
        """Content extending to the last row should not be fully visible."""
        height = 100
        rows = []
        for y in range(height):
            if y >= 49:
                rows.append(50)  # dark content
            else:
                rows.append(255)  # white margin
        p = _make_frame_png(tmp_path, "bottom_edge.png", 100, height, rows=rows)
        result = await analyze_frame(str(p))
        assert result.is_blank is False
        assert result.is_fully_visible is False
        assert result.bottom_margin < 0.1

    async def test_content_hash_nonzero_for_dark_content(self, tmp_path: Path) -> None:
        p = _make_grayscale_png(tmp_path / "dark.png", 100, 100, 0)
        result = await analyze_frame(str(p))
        assert result.content_hash > 0

    async def test_path_is_preserved(self, tmp_path: Path) -> None:
        p = _make_grayscale_png(tmp_path / "test.png", 50, 50, 128)
        result = await analyze_frame(str(p))
        assert result.path == str(p)


# ---------------------------------------------------------------------------
# Unit tests – deduplicate_frames (pure logic, no I/O)
# ---------------------------------------------------------------------------


class TestDeduplicateFrames:
    def test_empty_input(self) -> None:
        assert deduplicate_frames([], []) == []

    def test_single_frame(self) -> None:
        fa = FrameAnalysis(
            path="a.png", is_blank=False, top_margin=0.2,
            bottom_margin=0.2, is_fully_visible=True, content_hash=100,
        )
        result = deduplicate_frames([fa], [0])
        assert len(result) == 1
        assert result[0].path == "a.png"

    def test_consecutive_indices_grouped(self) -> None:
        """Frames with consecutive original indices belong to the same group."""
        frames = [
            FrameAnalysis("a.png", False, 0.3, 0.2, True, 100),
            FrameAnalysis("b.png", False, 0.25, 0.25, True, 101),
            FrameAnalysis("c.png", False, 0.2, 0.3, True, 102),
        ]
        result = deduplicate_frames(frames, [5, 6, 7])
        assert len(result) == 1
        # b.png has the most centred score (|0.25 - 0.25| = 0)
        assert result[0].path == "b.png"

    def test_gap_creates_new_group(self) -> None:
        """A gap > 1 in original indices starts a new group."""
        frames = [
            FrameAnalysis("a.png", False, 0.3, 0.2, True, 100),
            FrameAnalysis("b.png", False, 0.25, 0.25, True, 101),
        ]
        result = deduplicate_frames(frames, [3, 10])
        assert len(result) == 2
        assert result[0].path == "a.png"
        assert result[1].path == "b.png"

    def test_best_centered_selected(self) -> None:
        """Within a group the most centered frame (smallest |top - bottom|) wins."""
        frames = [
            FrameAnalysis("off1.png", False, 0.4, 0.1, True, 50),
            FrameAnalysis("centered.png", False, 0.25, 0.25, True, 50),
            FrameAnalysis("off2.png", False, 0.1, 0.4, True, 50),
        ]
        result = deduplicate_frames(frames, [0, 1, 2])
        assert len(result) == 1
        assert result[0].path == "centered.png"

    def test_multiple_groups(self) -> None:
        frames = [
            FrameAnalysis("g1a.png", False, 0.3, 0.2, True, 10),
            FrameAnalysis("g1b.png", False, 0.25, 0.25, True, 11),
            FrameAnalysis("g2a.png", False, 0.2, 0.3, True, 20),
            FrameAnalysis("g2b.png", False, 0.15, 0.15, True, 21),
        ]
        result = deduplicate_frames(frames, [0, 1, 5, 6])
        assert len(result) == 2
        assert result[0].path == "g1b.png"
        assert result[1].path == "g2b.png"


# ---------------------------------------------------------------------------
# Unit tests – extract_frames (requires ffmpeg)
# ---------------------------------------------------------------------------


class TestExtractFrames:
    async def test_produces_sorted_png_files(self, tmp_path: Path) -> None:
        """Create a tiny synthetic video and check that frames are extracted."""
        # Generate a 1-second, 2-fps black video
        video_path = tmp_path / "test.mp4"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1:r=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        assert video_path.exists()

        frame_paths = await extract_frames(str(video_path), ExtractionConfig(fps=2))
        assert len(frame_paths) >= 1
        for fp in frame_paths:
            assert fp.endswith(".png")
            assert Path(fp).exists()
        # Paths should be sorted
        assert frame_paths == sorted(frame_paths)


# ---------------------------------------------------------------------------
# Unit tests – download_video
# ---------------------------------------------------------------------------


class TestDownloadVideo:
    @staticmethod
    def _mock_response(content: bytes, *, ok: bool = True) -> AsyncMock:
        resp = AsyncMock()
        resp.status = 200 if ok else 404
        resp.read = AsyncMock(return_value=content)
        # aiohttp's raise_for_status() is synchronous
        if not ok:
            resp.raise_for_status = MagicMock(side_effect=Exception("Not Found"))
        else:
            resp.raise_for_status = MagicMock()
        return resp

    @staticmethod
    def _build_session(resp: AsyncMock) -> MagicMock:
        # session.get() returns an async context manager (not a coroutine),
        # so session.get must be a regular MagicMock, not AsyncMock.
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get.return_value = ctx
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        return mock_session

    async def test_download_mp4(self, tmp_path: Path) -> None:
        """Mock fetch and verify that the file is written with .mp4 extension."""
        fake_content = b"fake mp4 data"
        resp = self._mock_response(fake_content)
        session = self._build_session(resp)

        with patch("skills.video_solver.extract_frames.aiohttp.ClientSession", return_value=session):
            result = await download_video("https://example.com/video.mp4")

        assert result.endswith(".mp4")
        assert Path(result).exists()
        assert Path(result).read_bytes() == fake_content

    async def test_download_gif(self, tmp_path: Path) -> None:
        fake_content = b"fake gif data"
        resp = self._mock_response(fake_content)
        session = self._build_session(resp)

        with patch("skills.video_solver.extract_frames.aiohttp.ClientSession", return_value=session):
            result = await download_video("https://example.com/animation.gif")

        assert result.endswith(".gif")

    async def test_download_raises_on_failure(self) -> None:
        resp = self._mock_response(b"", ok=False)
        session = self._build_session(resp)

        with patch("skills.video_solver.extract_frames.aiohttp.ClientSession", return_value=session):
            with pytest.raises(Exception):
                await download_video("https://example.com/missing.mp4")


# ---------------------------------------------------------------------------
# Integration tests (require va.mp4 and ffmpeg)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not SAMPLE_VIDEO.exists(),
    reason="Sample video va.mp4 not found",
)
class TestIntegrationBlankFrameFiltering:
    @pytest.mark.timeout(60)
    async def test_filters_out_blank_frames_and_identifies_content_frames(self) -> None:
        frame_paths = await extract_frames(str(SAMPLE_VIDEO))
        analyses = await asyncio.gather(
            *[analyze_frame(fp) for fp in frame_paths]
        )
        blank_frames = [a for a in analyses if a.is_blank]
        content_frames = [a for a in analyses if not a.is_blank]
        assert len(blank_frames) > 0
        assert len(content_frames) > 0


@pytest.mark.skipif(
    not SAMPLE_VIDEO.exists(),
    reason="Sample video va.mp4 not found",
)
class TestIntegrationPartialFrameRejection:
    @pytest.mark.timeout(60)
    async def test_partial_frames_have_content_near_an_edge(self) -> None:
        frame_paths = await extract_frames(str(SAMPLE_VIDEO))
        analyses = await asyncio.gather(
            *[analyze_frame(fp) for fp in frame_paths]
        )
        partial_frames = [a for a in analyses if not a.is_blank and not a.is_fully_visible]
        assert len(partial_frames) > 0
        for frame in partial_frames:
            near_top_edge = frame.top_margin < 0.1
            near_bottom_edge = frame.bottom_margin < 0.1
            assert near_top_edge or near_bottom_edge


@pytest.mark.skipif(
    not SAMPLE_VIDEO.exists(),
    reason="Sample video va.mp4 not found",
)
class TestIntegrationFullyVisibleFrameSelection:
    @pytest.mark.timeout(60)
    async def test_selected_frames_have_clear_margins_on_both_edges(self) -> None:
        frame_paths = await extract_frames(str(SAMPLE_VIDEO))
        analyses = await asyncio.gather(
            *[analyze_frame(fp) for fp in frame_paths]
        )
        fully_visible = [a for a in analyses if a.is_fully_visible]
        assert len(fully_visible) > 0
        for frame in fully_visible:
            assert frame.top_margin >= 0.1
            assert frame.bottom_margin >= 0.1


@pytest.mark.skipif(
    not SAMPLE_VIDEO.exists(),
    reason="Sample video va.mp4 not found",
)
class TestIntegrationFrameCountConsistency:
    @pytest.mark.timeout(60)
    async def test_distinct_frame_count_is_16(self) -> None:
        """'3169' x 4 loops = 16 distinct frames."""
        frames = await extract_distinct_frames(str(SAMPLE_VIDEO))
        assert len(frames) == 16
