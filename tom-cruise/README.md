# Tom Cruise

> NOT-RELEASED

An AI browser agent that defeats survey bot detection — demonstrating that
video attention checks and keystroke tracking are ineffective against a
sufficiently capable AI agent.

Accompanies the paper *"Mission Possible: The Collection of High-Quality Data"*.

## What It Does

The agent navigates a multi-page mock survey (consent → demographics →
attention video → opinion → open-ended → dictator game → debrief) using two
custom skills:

| Skill                | Purpose                                                              |
|----------------------|----------------------------------------------------------------------|
| **Video Solver**     | Downloads the attention-check GIF, extracts frames via ffmpeg, filters to fully-visible digits, and reads them with Claude's vision API |
| **Human Typer**      | Types open-ended responses character-by-character via CDP key events with log-normal inter-keystroke intervals, deliberate typos, and natural pauses |

Each test run generates a **fresh video** with a random digit sequence, so the
agent can never memorize the answer.

## Prerequisites

| Dependency      | Version   | Purpose                          |
|-----------------|-----------|----------------------------------|
| Python          | >= 3.11   | Runtime                          |
| [uv](https://docs.astral.sh/uv/) | latest | Python package & project manager |
| ffmpeg          | any       | Frame extraction from video      |
| Chromium        | (via Playwright) | Browser automation          |
| `ANTHROPIC_API_KEY` | —     | Claude API for vision OCR + agent LLM |

## Quick Start

### One-shot install

```bash
chmod +x setup.sh
./setup.sh
```

This will:
1. Install `uv` if missing
2. Install `ffmpeg` if missing (via apt/dnf/pacman/brew)
3. Install Python dependencies via `uv sync`
4. Install Playwright's Chromium browser
5. Pre-cache the video generator dependencies

### Set your API key

```bash
export ANTHROPIC_API_KEY='sk-ant-...'
```

### Run the demo

```bash
uv run demo.py
```

This starts the mock survey server (with a freshly generated attention video),
launches the browser agent, and prints a post-run analysis of form submissions
and keystroke tracking data.

## Project Structure

```
tom-cruise/
├── setup.sh                 # One-shot install script
├── demo.py                  # End-to-end demo runner
├── agent.py                 # browser-use agent config + tool registration
├── pyproject.toml           # Python project config (uv)
├── the-plan.md              # Detailed implementation plan
├── skills/
│   ├── human_typer/         # Human-like typing via CDP key events
│   │   ├── distributions.py # Log-normal IKI sampling, QWERTY adjacency map
│   │   ├── typer.py         # Keystroke simulation + CDP dispatch
│   │   └── test_*.py        # Unit tests
│   └── video_solver/        # Video attention check solver
│       ├── extract_frames.py # ffmpeg frame extraction + blank/partial filtering
│       ├── read_digits.py   # Claude vision API digit OCR
│       └── test_*.py        # Unit tests
├── mock_survey/
│   └── server.py            # aiohttp server (generates random video on startup)
└── mock-survey/             # Static survey assets
    ├── pages/*.html         # Survey pages (consent through debrief)
    ├── tracker.js           # Client-side keystroke/mouse/event tracking
    └── styles.css           # Survey styling
```

## Running Tests

```bash
uv run -m pytest
```

Tests are in `skills/*/test_*.py`. Integration tests that call the Claude API
are tagged and can be filtered if needed.

## How the Video Solver Works

1. The survey page embeds an `<img>` pointing to an animated GIF
2. The agent downloads the GIF and extracts frames at 5 fps via ffmpeg
3. Blank frames (> 99.9% bright pixels) are discarded
4. Remaining frames are classified by vertical content bounding box analysis —
   only frames where the digit has clear margins from both top and bottom edges
   are kept (rejecting partially-scrolled digits that could be misread)
5. Consecutive similar frames are deduplicated, keeping the most centered one
6. Each representative frame is sent to Claude Haiku for single-digit OCR
7. The repeating sequence is detected and typed into the answer field

## How the Human Typer Works

Instead of `page.type()` (uniform timing, detectable), the typer:

- Samples inter-keystroke intervals from a **log-normal distribution**
  (matching empirical human typing patterns)
- Adds longer pauses at **word and sentence boundaries**
- Introduces **deliberate typos** (QWERTY-adjacent keys) at a configurable rate,
  followed by backspace corrections
- Dispatches individual **CDP `Input.dispatchKeyEvent`** calls — no clipboard,
  no `INPUT_JUMP` events
