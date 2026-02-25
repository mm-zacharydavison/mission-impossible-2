import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skills.video_solver.read_digits import (
    read_digit_from_frame,
    read_digits_from_frames,
    read_digits_with_validation,
)

SAMPLE_VIDEO = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "attention-video"
    / "generator-v1"
    / "va.mp4"
)


# ---------------------------------------------------------------------------
# Unit tests (mocked Anthropic API)
# ---------------------------------------------------------------------------


def _mock_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


class TestReadDigitFromFrame:
    @pytest.fixture()
    def tmp_png(self, tmp_path: Path) -> Path:
        p = tmp_path / "digit.png"
        # Minimal 1x1 white PNG (valid enough for base64 encoding)
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        return p

    @pytest.fixture()
    def tmp_jpg(self, tmp_path: Path) -> Path:
        p = tmp_path / "digit.jpg"
        p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
        return p

    @pytest.fixture()
    def tmp_jpeg(self, tmp_path: Path) -> Path:
        p = tmp_path / "digit.jpeg"
        p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
        return p

    async def test_returns_single_digit(self, tmp_png: Path) -> None:
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(
                return_value=_mock_response("7")
            )
            result = await read_digit_from_frame(str(tmp_png))
            assert result == "7"

    async def test_extracts_digit_from_noisy_response(self, tmp_png: Path) -> None:
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(
                return_value=_mock_response("The digit is 3.")
            )
            result = await read_digit_from_frame(str(tmp_png))
            assert result == "3"

    async def test_returns_raw_text_when_no_digit_found(self, tmp_png: Path) -> None:
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(
                return_value=_mock_response("unknown")
            )
            result = await read_digit_from_frame(str(tmp_png))
            assert result == "unknown"

    async def test_handles_non_text_content_block(self, tmp_png: Path) -> None:
        block = MagicMock()
        block.type = "image"
        resp = MagicMock()
        resp.content = [block]
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(return_value=resp)
            result = await read_digit_from_frame(str(tmp_png))
            assert result == ""

    async def test_png_media_type(self, tmp_png: Path) -> None:
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(
                return_value=_mock_response("5")
            )
            await read_digit_from_frame(str(tmp_png))
            call_kwargs = mock_client.messages.create.call_args.kwargs
            image_block = call_kwargs["messages"][0]["content"][0]
            assert image_block["source"]["media_type"] == "image/png"

    async def test_jpg_media_type(self, tmp_jpg: Path) -> None:
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(
                return_value=_mock_response("5")
            )
            await read_digit_from_frame(str(tmp_jpg))
            call_kwargs = mock_client.messages.create.call_args.kwargs
            image_block = call_kwargs["messages"][0]["content"][0]
            assert image_block["source"]["media_type"] == "image/jpeg"

    async def test_jpeg_media_type(self, tmp_jpeg: Path) -> None:
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(
                return_value=_mock_response("5")
            )
            await read_digit_from_frame(str(tmp_jpeg))
            call_kwargs = mock_client.messages.create.call_args.kwargs
            image_block = call_kwargs["messages"][0]["content"][0]
            assert image_block["source"]["media_type"] == "image/jpeg"


class TestReadDigitsFromFrames:
    async def test_empty_input_returns_empty_string(self) -> None:
        result = await read_digits_from_frames([])
        assert result == ""

    async def test_assembles_digits_in_order(self, tmp_path: Path) -> None:
        frames = []
        for i, digit in enumerate("3169"):
            p = tmp_path / f"frame_{i}.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
            frames.append(str(p))

        responses = [_mock_response(d) for d in "3169"]
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(side_effect=responses)
            result = await read_digits_from_frames(frames)
            assert result == "3169"

    async def test_single_frame(self, tmp_path: Path) -> None:
        p = tmp_path / "frame.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(
                return_value=_mock_response("0")
            )
            result = await read_digits_from_frames([str(p)])
            assert result == "0"


class TestReadDigitsWithValidation:
    async def test_short_sequence_returned_as_is(self, tmp_path: Path) -> None:
        frames = []
        for i, digit in enumerate("31"):
            p = tmp_path / f"frame_{i}.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
            frames.append(str(p))

        responses = [_mock_response(d) for d in "31"]
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(side_effect=responses)
            result = await read_digits_with_validation(frames, 4)
            assert result == "31"

    async def test_exact_length_returned_as_is(self, tmp_path: Path) -> None:
        frames = []
        for i, digit in enumerate("3169"):
            p = tmp_path / f"frame_{i}.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
            frames.append(str(p))

        responses = [_mock_response(d) for d in "3169"]
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(side_effect=responses)
            result = await read_digits_with_validation(frames, 4)
            assert result == "3169"

    async def test_majority_vote_across_loops(self, tmp_path: Path) -> None:
        # Simulate 3 loops of a 4-digit sequence with one error in loop 2
        digits = "3169" "3169" "3169"
        frames = []
        for i in range(len(digits)):
            p = tmp_path / f"frame_{i}.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
            frames.append(str(p))

        responses = [_mock_response(d) for d in digits]
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(side_effect=responses)
            result = await read_digits_with_validation(frames, 4)
            assert result == "3169"

    async def test_majority_vote_corrects_errors(self, tmp_path: Path) -> None:
        # Loop 1: 3169, Loop 2: 3189 (error at pos 2), Loop 3: 3169
        digits = "3169" "3189" "3169"
        frames = []
        for i in range(len(digits)):
            p = tmp_path / f"frame_{i}.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
            frames.append(str(p))

        responses = [_mock_response(d) for d in digits]
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(side_effect=responses)
            result = await read_digits_with_validation(frames, 4)
            assert result == "3169"

    async def test_incomplete_last_loop(self, tmp_path: Path) -> None:
        # 2 full loops + 2 digits of a third loop
        digits = "3169" "3169" "31"
        frames = []
        for i in range(len(digits)):
            p = tmp_path / f"frame_{i}.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
            frames.append(str(p))

        responses = [_mock_response(d) for d in digits]
        with patch(
            "skills.video_solver.read_digits.client"
        ) as mock_client:
            mock_client.messages.create = AsyncMock(side_effect=responses)
            result = await read_digits_with_validation(frames, 4)
            assert result == "3169"


# ---------------------------------------------------------------------------
# Integration tests (require ANTHROPIC_API_KEY and va.mp4)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not SAMPLE_VIDEO.exists(),
    reason="Sample video va.mp4 not found",
)
class TestIntegrationDigitRecognition:
    @pytest.mark.timeout(120)
    async def test_recognizes_va_mp4_as_3169_repeated_4_times(self) -> None:
        from skills.video_solver.extract_frames import extract_distinct_frames

        frames = await extract_distinct_frames(str(SAMPLE_VIDEO))
        sequence = await read_digits_from_frames(frames)
        assert sequence == "3169316931693169"

    @pytest.mark.timeout(120)
    async def test_cross_validation_extracts_3169_from_looped_sequence(self) -> None:
        from skills.video_solver.extract_frames import extract_distinct_frames

        frames = await extract_distinct_frames(str(SAMPLE_VIDEO))
        sequence = await read_digits_with_validation(frames, 4)
        assert sequence == "3169"
