import asyncio
import math
from unittest.mock import AsyncMock

import pytest

from skills.human_typer.typer import (
    CDPDispatcher,
    HumanTyperConfig,
    KeystrokeEvent,
    simulate_typing,
    human_type_cdp,
)


def compute_ikis(log: list[KeystrokeEvent]) -> list[float]:
    ikis: list[float] = []
    for i in range(1, len(log)):
        ikis.append(log[i].time - log[i - 1].time)
    return ikis


# ---------------------------------------------------------------------------
# IKI distribution
# ---------------------------------------------------------------------------
class TestIKIDistribution:
    def test_iki_values_are_all_positive(self):
        log = simulate_typing(
            "The quick brown fox jumps over the lazy dog.",
            HumanTyperConfig(typo_rate=0),
        )
        ikis = compute_ikis(log)
        for iki in ikis:
            assert iki > 0

    def test_iki_distribution_is_right_skewed(self):
        text = "abcdefghijklmnopqrstuvwxyz" * 10
        log = simulate_typing(text, HumanTyperConfig(typo_rate=0))
        ikis = compute_ikis(log)

        mean = sum(ikis) / len(ikis)
        sorted_ikis = sorted(ikis)
        median = sorted_ikis[len(sorted_ikis) // 2]

        # For a right-skewed distribution, mean > median
        assert mean > median

    def test_iki_has_non_trivial_variance(self):
        text = "abcdefghijklmnopqrstuvwxyz" * 5
        log = simulate_typing(text, HumanTyperConfig(typo_rate=0))
        ikis = compute_ikis(log)

        mean = sum(ikis) / len(ikis)
        variance = sum((x - mean) ** 2 for x in ikis) / len(ikis)
        cv = math.sqrt(variance) / mean

        # Human typing typically has CV > 0.1
        assert cv > 0.1


# ---------------------------------------------------------------------------
# Typo behavior
# ---------------------------------------------------------------------------
class TestTypoBehavior:
    def test_typos_introduced_at_approximately_configured_rate(self):
        text = "a" * 500
        log = simulate_typing(
            text,
            HumanTyperConfig(typo_rate=0.05, typo_correction_rate=1.0),
        )

        typo_count = sum(1 for e in log if e.is_typo)
        rate = typo_count / len(text)

        # Allow +/- 2% tolerance
        assert 0.02 < rate < 0.10

    def test_backspace_corrections_appear_after_typos(self):
        log = simulate_typing(
            "abc" * 50,
            HumanTyperConfig(typo_rate=1.0, typo_correction_rate=1.0),
        )

        backspaces = [e for e in log if e.key == "Backspace"]
        assert len(backspaces) > 0

        # Every typo should be followed by a backspace
        for i in range(len(log)):
            if log[i].is_typo and i + 1 < len(log):
                assert log[i + 1].key == "Backspace"
                assert log[i + 1].is_correction is True

    def test_no_input_jump_events(self):
        log = simulate_typing(
            "Hello world, this is a test.",
            HumanTyperConfig(typo_rate=0.04),
        )

        # Every keystroke should be a single character or Backspace
        for event in log:
            assert len(event.key) <= len("Backspace")

        # Check no jumps > 1 character between consecutive events
        total_chars = 0
        last_total_chars = 0
        for event in log:
            if event.key == "Backspace":
                total_chars = max(0, total_chars - 1)
            elif len(event.key) == 1:
                total_chars += 1
            jump = total_chars - last_total_chars
            assert jump <= 1
            last_total_chars = total_chars


# ---------------------------------------------------------------------------
# Word boundary pauses
# ---------------------------------------------------------------------------
class TestWordBoundaryPauses:
    def test_delay_before_space_is_longer_than_average_intra_word_delay(self):
        words = "hello world again today test words more text keep going please"
        log = simulate_typing(words, HumanTyperConfig(typo_rate=0))

        intra_word_ikis: list[float] = []
        space_ikis: list[float] = []

        for i in range(1, len(log)):
            iki = log[i].time - log[i - 1].time
            if log[i].key == " ":
                space_ikis.append(iki)
            elif log[i - 1].key != " ":
                intra_word_ikis.append(iki)

        mean_intra_word = sum(intra_word_ikis) / len(intra_word_ikis)
        mean_space = sum(space_ikis) / len(space_ikis)

        assert mean_space > mean_intra_word


# ---------------------------------------------------------------------------
# WPM range
# ---------------------------------------------------------------------------
class TestWPMRange:
    def test_elapsed_time_within_expected_range_for_60_wpm(self):
        text = "The quick brown fox jumps over the lazy dog"
        char_count = len(text)
        # 60 WPM = 300 chars/min = 200ms/char
        expected_ms = char_count * 200

        log = simulate_typing(
            text,
            HumanTyperConfig(
                average_wpm=60,
                wpm_std_dev=5,
                typo_rate=0,
                long_pause_chance=0,
            ),
        )

        elapsed = log[-1].time - log[0].time

        # Allow wide tolerance due to word/sentence boundary pauses
        assert elapsed > expected_ms * 0.5
        assert elapsed < expected_ms * 2.0


# ---------------------------------------------------------------------------
# human_type_cdp (async, uses a mock CDP dispatcher)
# ---------------------------------------------------------------------------
class TestHumanTypeCDP:
    def _make_dispatcher(self) -> CDPDispatcher:
        mock_client = AsyncMock()
        return CDPDispatcher(mock_client, session_id="test-session")

    @pytest.mark.asyncio
    async def test_dispatches_key_events_for_each_character(self):
        dispatcher = self._make_dispatcher()
        await human_type_cdp(dispatcher, "hi", average_wpm=200, typo_rate=0)

        # Should have keyDown+keyUp for 'h' and keyDown+keyUp for 'i' = 4 calls
        assert dispatcher._client.send_raw.call_count == 4

    @pytest.mark.asyncio
    async def test_typo_produces_extra_key_events(self):
        dispatcher = self._make_dispatcher()
        await human_type_cdp(dispatcher, "a" * 20, average_wpm=200, typo_rate=1.0)

        # With 100% typo rate, each char produces:
        #   wrong char (keyDown+keyUp) + Backspace (keyDown+keyUp) + correct char (keyDown+keyUp) = 6
        # 20 chars * 6 = 120 calls
        assert dispatcher._client.send_raw.call_count == 120

    @pytest.mark.asyncio
    async def test_dispatches_correct_cdp_event_structure(self):
        dispatcher = self._make_dispatcher()
        await human_type_cdp(dispatcher, "a", average_wpm=200, typo_rate=0)

        calls = dispatcher._client.send_raw.call_args_list
        # keyDown for 'a'
        assert calls[0].args == ("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "text": "a"})
        assert calls[0].kwargs == {"session_id": "test-session"}
        # keyUp for 'a'
        assert calls[1].args == ("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a"})
        assert calls[1].kwargs == {"session_id": "test-session"}
