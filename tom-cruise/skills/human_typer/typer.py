import asyncio
import random
import time
from dataclasses import dataclass
from typing import Optional

from skills.human_typer.distributions import nearby_key, sample_iki


@dataclass
class HumanTyperConfig:
    average_wpm: float = 70
    wpm_std_dev: float = 17.5
    typo_rate: float = 0.01
    typo_correction_rate: float = 0.96
    word_boundary_pause_ms: float = 300
    sentence_boundary_pause_ms: float = 800
    long_pause_chance: float = 0.05
    long_pause_max_ms: float = 2000


@dataclass
class KeystrokeEvent:
    key: str
    time: float
    is_typo: bool = False
    is_correction: bool = False


SENTENCE_ENDINGS = frozenset((".", "!", "?"))
WORD_BOUNDARIES = frozenset((" ", "\t", "\n"))


def compute_delay(
    char: str,
    prev_char: Optional[str],
    config: HumanTyperConfig,
) -> float:
    delay = sample_iki(config.average_wpm, config.wpm_std_dev)

    # Sentence boundary pause (previous char was sentence-ending punctuation, current is space)
    if prev_char and prev_char in SENTENCE_ENDINGS and char in WORD_BOUNDARIES:
        delay += config.sentence_boundary_pause_ms * (0.5 + random.random())
    # Word boundary pause
    elif char in WORD_BOUNDARIES:
        delay += config.word_boundary_pause_ms * (0.3 + random.random() * 0.7)

    # Random long pause
    if random.random() < config.long_pause_chance:
        delay += random.random() * config.long_pause_max_ms

    return max(delay, 10)  # Minimum 10ms


def simulate_typing(
    text: str,
    config: Optional[HumanTyperConfig] = None,
) -> list[KeystrokeEvent]:
    cfg = config if config is not None else HumanTyperConfig()
    log: list[KeystrokeEvent] = []
    prev_char: Optional[str] = None
    current_time = time.time() * 1000  # ms

    for char in text:
        delay = compute_delay(char, prev_char, cfg)
        current_time += delay

        if random.random() < cfg.typo_rate:
            wrong_key = nearby_key(char)
            log.append(KeystrokeEvent(key=wrong_key, time=current_time, is_typo=True))

            if random.random() < cfg.typo_correction_rate:
                current_time += 200 + random.random() * 300
                log.append(KeystrokeEvent(key="Backspace", time=current_time, is_correction=True))
                current_time += 100 + random.random() * 100
                log.append(KeystrokeEvent(key=char, time=current_time))
        else:
            log.append(KeystrokeEvent(key=char, time=current_time))

        prev_char = char

    return log


class CDPDispatcher:
    """Wraps browser-use's CDP client for key event dispatch."""

    def __init__(self, cdp_client: object, session_id: str) -> None:
        self._client = cdp_client
        self._session_id = session_id

    async def send(self, method: str, params: dict[str, object]) -> None:
        await self._client.send_raw(method, params, session_id=self._session_id)  # type: ignore[attr-defined]


async def human_type_cdp(
    dispatcher: CDPDispatcher,
    text: str,
    *,
    average_wpm: float = 60,
    typo_rate: float = 0.01,
) -> None:
    wpm_std_dev = average_wpm * 0.25

    for i, char in enumerate(text):
        delay = sample_iki(average_wpm, wpm_std_dev)

        # Add word/sentence boundary pauses
        extra_delay = 0.0
        if char == " ":
            extra_delay += 100 + random.random() * 200
        if i > 0 and text[i - 1] in ".!?" and char == " ":
            extra_delay += 300 + random.random() * 500

        await asyncio.sleep((delay + extra_delay) / 1000)

        # Roll for typo
        if random.random() < typo_rate:
            wrong_char = nearby_key(char)
            await _dispatch_char(dispatcher, wrong_char)
            await asyncio.sleep((200 + random.random() * 300) / 1000)
            await _dispatch_key(dispatcher, "Backspace")
            await asyncio.sleep((100 + random.random() * 100) / 1000)

        await _dispatch_char(dispatcher, char)


async def _dispatch_char(dispatcher: CDPDispatcher, char: str) -> None:
    await dispatcher.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": char, "text": char})
    await dispatcher.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": char})


async def _dispatch_key(dispatcher: CDPDispatcher, key: str) -> None:
    await dispatcher.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": key})
    await dispatcher.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": key})
