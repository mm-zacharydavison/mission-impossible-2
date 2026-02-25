# The Plan: Defeating Survey Bot Detection

Two custom agent skills that demonstrate the paper's attention-video and keystroke-tracking defenses are ineffective against a sufficiently capable AI browser agent.

---

## Browser Agent Selection: Stagehand

| Criterion               | Stagehand                          | browser-use                     |
|--------------------------|------------------------------------|---------------------------------|
| **Language**             | TypeScript (Bun-compatible)        | Python (TS port lags behind)    |
| **Custom tool API**      | Zod schema + `tool()` helper       | `@tools.action()` decorator     |
| **Claude integration**   | Native via Vercel AI SDK           | Native                          |
| **Agent modes**          | DOM, CUA (vision), Hybrid          | Vision + DOM                    |
| **Underlying browser**   | Playwright                         | Raw CDP (migrated from PW)      |
| **Maintenance**          | Active (Browserbase-backed, 21k★)  | Active (78k★)                   |
| **Structured extraction**| Zod-validated schemas              | Pydantic models                 |

**Decision: Stagehand** (`@browserbasehq/stagehand`)

Reasons:
1. **TypeScript-first** — aligns with the Bun-based toolchain in this project
2. **Playwright underneath** — means we get access to the full Playwright API for low-level page interaction, which is critical for the typing skill
3. **Clean custom tool API** — Zod schema tools integrate naturally with the agent loop; the agent can decide *when* to invoke our skills based on what it sees on the page
4. **Three agent modes** — Hybrid mode (DOM + vision) is ideal: DOM mode for form interaction, CUA/vision mode for interpreting video content via screenshots
5. **`extract()` with Zod** — structured data extraction from pages (e.g. "find the video element and its src URL") is validated at the type level

Stagehand runs locally (no Browserbase account required for local dev). It wraps Playwright, so we retain full access to `page.keyboard`, `page.mouse`, CDP sessions, and network interception.

---

## Skill 1: Video Attention Check Solver

### How the attention-video works

From `attention-video/generator-v1/flicker.py`:
- A video (MP4 or GIF) shows **single digits scrolling vertically** through a fixed viewport
- Each digit is visible for ~2 seconds, with ~1.5s blank gaps between them
- The sequence (e.g. `3169`) loops multiple times
- White background, black monospace text, 1000×300 canvas at 60fps
- The participant must watch the video and type the digit sequence into an input field

### The partial-frame problem

Because digits scroll vertically through the viewport, any given non-blank frame falls into one of three categories:

1. **Entering** — the digit is partially visible at the bottom edge, clipped by the viewport
2. **Fully visible** — the digit is entirely within the viewport (the "sweet spot")
3. **Exiting** — the digit is partially visible at the top edge, being clipped as it scrolls out

Partially visible frames are dangerous: a clipped `8` can look like `0` or `3`, a clipped `6` can look like `0`, etc. We must select only fully-visible frames for digit recognition.

### Attack strategy

The video source is embedded in the survey page as a `<video>` or `<img>` element. The agent:

1. **Detects** the video/GIF element on the page using Stagehand's `extract()` or `observe()`
2. **Downloads** the media file from its `src` attribute
3. **Extracts frames** at a higher rate using ffmpeg
4. **Filters** blank frames (all/nearly-all white pixels)
5. **Classifies** remaining frames into partial vs. fully-visible using vertical content analysis
6. **Selects** only fully-visible frames, then deduplicates consecutive identical digits
7. **Sends** the selected frames to Claude's vision API to read the digit in each
8. **Assembles** the digit sequence and types it into the answer field

### Implementation

```
tom-cruise/
  skills/
    video-solver/
      index.ts          # Stagehand tool definition
      extract-frames.ts # ffmpeg frame extraction + filtering
      read-digits.ts    # Claude vision API calls
      index.test.ts     # Tests against sample videos
```

#### Step 1 — Frame extraction and selection (`extract-frames.ts`)

The core challenge is distinguishing partially-visible frames from fully-visible ones. Since digits scroll vertically through a white-background viewport, we can use **vertical content bounding box analysis**:

```ts
// Uses Bun.$ to call ffmpeg, sharp for pixel analysis
//
// 1. Download video to temp dir
//
// 2. Extract frames at higher rate:
//    ffmpeg -i input.mp4 -vf "fps=5" frame_%04d.png
//    (5 fps gives ~10 frames per digit's 2s visibility window,
//     ensuring several frames land in the fully-visible sweet spot)
//
// 3. Filter blank frames:
//    Read each PNG with sharp, check if >95% of pixels have luminance >250 → blank
//
// 4. For each non-blank frame, compute the vertical content bounding box:
//    a. Convert to grayscale, threshold to binary (dark pixels = content)
//    b. Find the topmost row containing content pixels (yMin)
//    c. Find the bottommost row containing content pixels (yMax)
//    d. Compute top margin = yMin, bottom margin = (frameHeight - yMax)
//
// 5. Classify frames as fully-visible vs. partial:
//    - If top margin < threshold (e.g. 10% of frame height) → digit is clipped at top (exiting)
//    - If bottom margin < threshold → digit is clipped at bottom (entering)
//    - If both margins are above threshold → digit is fully visible within the viewport
//    This works because the video has a white background: a fully-visible digit
//    will have clear white space above AND below it, while a partial digit will
//    have content touching or near one edge of the frame.
//
// 6. From the fully-visible frames only, deduplicate:
//    Group consecutive frames by similarity (pixel hash or average darkness).
//    Pick one representative frame per group (e.g. the one with the most centered content,
//    i.e. where abs(topMargin - bottomMargin) is minimized — the midpoint of the scroll).
//
// 7. Return array of file paths to the selected representative frames, in order.
```

Key parameters:
- Extraction rate: **5 fps** (higher than before — we need enough frames to guarantee several land in the fully-visible window for each digit)
- Blank threshold: pixel luminance > 250 across > 95% of pixels → blank
- Edge margin threshold: **10% of frame height** — content must be at least this far from both top and bottom edges to be considered fully visible
- Optimal frame selection: within each fully-visible group, prefer the frame where content is most vertically centered (maximizes distance from both edges, minimizing any clipping risk)
- Deduplication: group consecutive fully-visible frames by pixel similarity, emit one per group

#### Step 2 — Digit recognition (`read-digits.ts`)

```ts
// For each distinct frame:
// 1. Read the PNG as base64
// 2. Send to Claude vision API:
//    "This image contains a single digit (0-9) displayed in large monospace font
//     on a white background. What digit is shown? Reply with only the digit."
// 3. Collect responses into ordered sequence
// Return: string (e.g. "3169")
```

This is deliberately simple — by the time frames reach this step, they have been filtered to only fully-visible, vertically-centered digits. Large, high-contrast, unclipped single characters. Claude's vision should achieve near-100% accuracy.

As a safety net, since the sequence loops multiple times in the video, we can cross-validate: if loop N and loop N+1 produce different readings for the same position, we flag it and take the majority vote across all loops.

#### Step 3 — Stagehand tool registration (`index.ts`)

```ts
import { tool } from "ai";
import { z } from "zod";

export const videoSolverTool = tool({
  description:
    "Solves video-based attention checks. When you encounter a video or GIF " +
    "that shows a sequence of numbers/digits, use this tool. It will extract " +
    "the frames, read the digits via AI vision, and return the sequence.",
  inputSchema: z.object({
    videoUrl: z.string().url().describe("The src URL of the video or GIF element"),
  }),
  execute: async ({ videoUrl }) => {
    const frames = await extractDistinctFrames(videoUrl);
    const sequence = await readDigitsFromFrames(frames);
    return { sequence };
    // Agent then types this sequence into the answer field
  },
});
```

#### Test fixture generation (`generate-fixtures.ts`)

The repo includes a video generator at `attention-video/generator-v1/flicker.py` that produces test videos with configurable digit sequences, timing, font sizes, and loop counts. We use this to generate a diverse fixture set at test time rather than relying on a single hardcoded sample.

```ts
// Wraps flicker.py via Bun.$ to generate test videos on demand.
// Accepts overrides for the generator's config constants.
//
// Usage:
//   const video = await generateTestVideo({
//     input: "8052",
//     loops: 2,
//     showSeconds: 1.5,
//     gapSeconds: 1.0,
//     fontSize: 100,
//   });
//   // Returns: { path: "/tmp/fixtures/8052.mp4", expectedSequence: "8052" }
//
// Implementation:
// 1. Copy flicker.py to a temp dir
// 2. Patch the config constants in the copy (INPUT, LOOPS, SHOW_SECONDS, etc.)
//    using simple string replacement on the Python source
// 3. Run: Bun.$`uv run ${patchedScript}`
// 4. Return the output path and the expected sequence for assertion
```

The fixture generator enables testing across several dimensions:
- **Digit variety**: sequences containing every digit 0-9, including visually similar pairs (6/8, 3/8, 1/7, 0/8)
- **Sequence lengths**: short (2 digits) through long (9 digits, the generator's max)
- **Timing variation**: fast display (0.5s show) vs. slow (3s show), narrow gaps vs. wide gaps
- **Loop counts**: single loop (no cross-validation possible) vs. multiple loops
- **Font sizes**: small (80px) vs. large (150px), testing OCR at different scales

#### Tests (`index.test.ts`)

Using `bun test`. Tests are split into unit tests (fast, no AI calls) and integration tests (require Claude API).

**Unit tests** (frame extraction and filtering — no API calls):

1. **Blank filtering test**: Generate a video with `"5"`, extract frames, assert blank frames are filtered out and at least one content frame remains
2. **Partial frame rejection test**: Generate a video with `"7"`, extract all non-blank frames, assert that frames classified as partial have content within 10% of the top or bottom edge
3. **Fully-visible selection test**: Generate a video with `"42"`, assert the selected representative frames all have content vertically centered with clear margins on both edges
4. **Frame count consistency test**: Generate videos with sequences of length 1, 4, and 9. Assert the number of deduplicated fully-visible frame groups matches the sequence length × loop count
5. **Visually similar digit separation test**: Generate a video with `"6880"`, assert that 4 distinct frame groups are identified (the deduplicator must not merge the two `8`s with the `6` or `0` between them, since they are separated by blank gaps)

**Integration tests** (require Claude API — tagged so they can be skipped in CI):

6. **Single digit recognition**: Generate 10 videos, one for each digit `"0"` through `"9"`. Assert each is correctly identified.
7. **Visually ambiguous digits test**: Generate a video with `"13780"` (contains pairs that are visually similar when partially clipped: 1/7, 3/8, 8/0). Assert correct full-sequence recognition — validates that partial-frame filtering is doing its job.
8. **Short timing test**: Generate a video with `showSeconds=0.5` (digits flash quickly), assert correct recognition. Stresses the frame extraction rate — fewer fully-visible frames available.
9. **End-to-end against repo sample**: Run the full pipeline against `attention-video/generator-v1/va.mp4`, assert it returns `"3169"`.
10. **Randomized fuzz test**: Generate 5 videos with random 4-digit sequences, run the pipeline, assert each returns the correct sequence. Provides broad coverage without hand-picking cases.

---

## Skill 2: Human-Like Typing Simulator

### How keystroke tracking works

From `trackers/keylog/keylog.js`:
- Every `keydown` event is logged with `{ key, time }` timestamps
- Large input jumps (>10 chars at once) are flagged as `INPUT_JUMP` (paste detection)
- The R analysis script (`Cleaning_Tracker.R`) computes:
  - Inter-keystroke intervals (IKI)
  - Words per minute
  - Character count
  - Levenshtein distance between intermediate and final text (measuring corrections)
- Bot signals: uniform IKI, no corrections, no pauses, text appearing in large chunks

### Attack strategy

Instead of using Playwright's `page.type("text", { delay: 100 })` (which produces suspiciously uniform timing), we simulate realistic human typing:

1. **Variable base speed** — WPM drawn from a normal distribution matching human ranges (40-80 WPM)
2. **Inter-keystroke jitter** — each keystroke delay is sampled from a log-normal distribution (humans show right-skewed IKI distributions)
3. **Deliberate typos** — randomly introduce wrong characters at a configurable rate (~3-5%)
4. **Backspace corrections** — after a typo, pause briefly (thinking), then press Backspace and retype the correct character
5. **Word-boundary pauses** — longer pauses after spaces and punctuation (simulating word-level planning)
6. **Sentence-boundary pauses** — even longer pauses after periods/question marks (simulating thought between sentences)
7. **Occasional long pauses** — rare "thinking breaks" of 1-3 seconds mid-sentence

This produces keystroke logs that are statistically indistinguishable from human typing: variable IKI, non-zero Levenshtein distance, no input jumps, realistic WPM.

### Implementation

```
tom-cruise/
  skills/
    human-typer/
      index.ts          # Stagehand tool definition
      typer.ts          # Core typing engine
      distributions.ts  # Statistical distributions for timing
      index.test.ts     # Tests for timing distributions and typo behavior
```

#### Existing packages to leverage

| Package                  | What it does                                          | Use in our skill                |
|--------------------------|-------------------------------------------------------|---------------------------------|
| `playwright-humanize`    | `typeInto()` with typo chance, backspace corrections  | Reference implementation / fork |
| `ghost-cursor-playwright`| Bezier-curve mouse movement to elements               | Move to input field naturally   |

**`playwright-humanize`** is the closest existing package. It provides:
- Configurable typo probability
- Automatic backspace correction after typos
- Variable per-keystroke delay

However, we need to extend it because:
- It may not model **word/sentence boundary pauses** (we need these to defeat IKI analysis)
- It may not produce a **log-normal IKI distribution** (critical for statistical tests)
- We need **configurable WPM ranges** matching the specific persona

So: **use `playwright-humanize` as a dependency/reference, extend with our own distribution-aware timing layer.**

#### Core typing engine (`typer.ts`)

```ts
interface HumanTyperConfig {
  /** @default 60 */
  averageWPM: number;
  /** @default 15 */
  wpmStdDev: number;
  /** @default 0.04 */
  typoRate: number;
  /** @default 0.95 */
  typoCorrectionRate: number;
  /** @default 300 */
  wordBoundaryPauseMs: number;
  /** @default 800 */
  sentenceBoundaryPauseMs: number;
  /** @default 0.05 */
  longPauseChance: number;
  /** @default 2000 */
  longPauseMaxMs: number;
}

async function humanType(page: Page, selector: string, text: string, config?: Partial<HumanTyperConfig>): Promise<void> {
  // 1. Focus the element (optionally via ghost-cursor for natural mouse movement)
  // 2. For each character in text:
  //    a. Sample delay from log-normal distribution based on averageWPM
  //    b. If character is space/punctuation, add word/sentence boundary pause
  //    c. Roll for typo — if triggered:
  //       i.  Type a nearby key (based on QWERTY adjacency map)
  //       ii. Pause briefly (200-500ms, "noticing the error")
  //       iii. Press Backspace
  //       iv. Pause briefly again (100-200ms)
  //       v.  Type the correct character
  //    d. Roll for long pause — if triggered, wait 1-3 seconds
  //    e. Press the key via page.keyboard.press()
  // 3. Final review pause before moving on
}
```

Key design choices:
- **Log-normal IKI distribution**: `delay = lognormal(μ, σ)` where μ and σ are derived from the target WPM. This matches empirical human IKI distributions better than normal or uniform.
- **QWERTY adjacency typos**: When a typo is triggered, we pick a key adjacent to the intended key on a QWERTY layout (e.g., typing 'r' instead of 't'). This is more realistic than random character substitution.
- **No INPUT_JUMP events**: All text is entered character-by-character through keyboard events, never via clipboard paste or `element.fill()`.

#### Statistical distributions (`distributions.ts`)

```ts
// Log-normal random variate (Box-Muller transform)
function logNormal(mu: number, sigma: number): number

// Convert target WPM to log-normal parameters for IKI
function wpmToLogNormalParams(wpm: number, stdDev: number): { mu: number; sigma: number }

// QWERTY keyboard adjacency map
const QWERTY_NEIGHBORS: Record<string, string[]>
// e.g., 't' -> ['r', 'y', 'f', 'g', '5', '6']

// Pick a random neighbor for a typo
function nearbyKey(key: string): string
```

#### Stagehand tool registration (`index.ts`)

```ts
export const humanTyperTool = tool({
  description:
    "Types text into a form field with human-like behavior: variable speed, " +
    "occasional typos with corrections, natural pauses between words and sentences. " +
    "Use this instead of directly typing into text fields.",
  inputSchema: z.object({
    selector: z.string().describe("CSS selector for the input/textarea element"),
    text: z.string().describe("The text to type"),
    wpm: z.number().optional().describe("Target words per minute (default: 60)"),
  }),
  execute: async ({ selector, text, wpm }) => {
    await humanType(page, selector, text, { averageWPM: wpm });
    return { success: true, charactersTyped: text.length };
  },
});
```

#### Tests (`index.test.ts`)

1. **IKI distribution test**: Run the typer 1000 times on a single character, collect delays, assert they follow a log-normal distribution (Shapiro-Wilk or similar)
2. **Typo rate test**: Type a 500-character string, count typos introduced, assert rate is within ±1% of configured rate
3. **No INPUT_JUMP test**: Hook a mock keydown listener, type a string, assert no jump >10 characters ever occurs
4. **Word boundary pause test**: Type "hello world", assert the delay before 'w' is significantly longer than intra-word delays
5. **WPM range test**: Type a paragraph at 60 WPM target, measure actual elapsed time, assert it falls within ±20% of expected duration
6. **Backspace correction test**: Configure 100% typo rate, type "abc", assert the keydown log contains Backspace events

---

## Mock Survey for End-to-End Testing

A multi-page survey served locally via `Bun.serve()` that exercises both skills and requires the agent to generate its own responses. The survey mimics a real Qualtrics-style research study — the agent receives only a persona and the survey URL, and must navigate, comprehend, and respond to everything autonomously.

### Survey structure

The mock survey is a sequence of HTML pages, each served as a route. Navigation is handled via "Next" buttons that POST form data and redirect to the next page. The server collects all submissions into an in-memory log for post-run inspection.

| Page | Route              | Content                                                                                                  | Skills exercised                |
|------|--------------------|----------------------------------------------------------------------------------------------------------|---------------------------------|
| 1    | `/`                | **Consent page.** IRB-style informed consent text. Single checkbox "I agree" + Next button.              | Agent comprehension, clicking   |
| 2    | `/demographics`    | **Demographics.** Radio buttons for age range, gender, education level. Dropdown for U.S. state.         | Agent reasoning (persona-based) |
| 3    | `/attention-video` | **Video attention check.** Embedded GIF (generated via `flicker.py` at server startup with a random sequence). Text input: "Enter the numbers shown in the video above." | **Video solver skill**          |
| 4    | `/opinion`         | **Likert scale questions.** 5 statements about a political topic (e.g. climate policy), each with a 7-point Likert scale (Strongly Disagree → Strongly Agree). | Agent reasoning (persona-based) |
| 5    | `/open-ended`      | **Open-ended response.** Textarea: "In your own words, describe your views on [topic] and why you hold them. Please write at least 3 sentences." | **Human typer skill** + agent content generation |
| 6    | `/dictator`        | **Behavioral economics task.** Dictator game: "You have been given $10. How much would you like to give to another participant?" Slider input 0–10. | Agent reasoning                 |
| 7    | `/debrief`         | **Debrief page.** Thank you message, completion code displayed.                                          | Agent reads completion code     |

### Design principles

1. **The agent generates all response content.** The survey questions are not pre-scripted with answers. The agent must read each question, consider its persona, and produce appropriate responses. For open-ended questions, the agent composes the text itself — the human-typer skill only handles *how* it's typed, not *what* is typed.

2. **Realistic tracking scripts embedded.** Each page includes JavaScript adapted from the repo's tracker scripts (`generalTracker.htm`, `keylog.js`), simplified to work outside Qualtrics:
   - Keystroke logging with timestamps on all pages
   - Mouse movement counting
   - Paste/copy detection
   - Tab visibility change detection
   - Time-on-page measurement
   - The tracking data is collected server-side alongside form submissions, so we can analyze it post-run to verify the skills are defeating detection.

3. **Random attention-video per run.** The server generates a fresh video with a random digit sequence at startup, so the agent can't memorize the answer. The expected sequence is stored server-side for validation.

4. **Validation on submission.** The server checks:
   - Consent checkbox was checked
   - All required fields are filled
   - Attention-video answer matches the expected sequence
   - Open-ended response meets minimum length
   - Returns error messages for missing/invalid fields (the agent must handle these)

### Implementation

```
tom-cruise/
  mock-survey/
    server.ts              # Bun.serve() with routes for each page
    pages/
      consent.html         # Page 1
      demographics.html    # Page 2
      attention-video.html # Page 3 (GIF src points to /assets/attention.gif)
      opinion.html         # Page 4
      open-ended.html      # Page 5
      dictator.html        # Page 6
      debrief.html         # Page 7
    tracker.js             # Client-side tracking script (embedded in all pages)
    styles.css             # Minimal survey styling
```

#### Server (`server.ts`)

```ts
// 1. On startup:
//    - Generate a random 4-digit sequence (e.g. "7203")
//    - Run flicker.py to produce the attention GIF
//    - Store expected sequence in memory
//
// 2. Routes:
//    GET  /                  → serve consent.html
//    POST /demographics      → validate consent, serve demographics.html
//    POST /attention-video   → validate demographics, serve attention-video.html
//    POST /opinion           → validate attention video answer, serve opinion.html
//    POST /open-ended        → validate likert responses, serve open-ended.html
//    POST /dictator          → validate open-ended length, serve dictator.html
//    POST /debrief           → validate dictator, serve debrief.html with completion code
//    GET  /assets/attention.gif → serve the generated GIF
//    GET  /tracker.js        → serve the tracking script
//    GET  /styles.css        → serve the stylesheet
//
// 3. All POST handlers:
//    - Parse form data from the request body
//    - Collect tracking JSON sent alongside (via hidden field or beacon)
//    - Append to an in-memory submissions log
//    - Return validation errors or the next page
//
// 4. GET /results → JSON dump of all submissions + tracking data (for test assertions)
```

#### Tracking script (`tracker.js`)

Adapted from `trackers/general/generalTracker.htm` and `trackers/keylog/keylog.js`, combined into a single script that works outside Qualtrics:

```ts
// On page load:
// - Start tracking: mouse moves, clicks, keystrokes (with timestamps),
//   paste/copy events, tab visibility, scroll events, time-on-page
// - On keydown in textareas: log { key, time } to an array
// - Detect INPUT_JUMP (>10 char increments)
//
// Before form submission:
// - Serialize tracking data into a hidden <input name="__tracking"> field
// - Server parses this alongside the form data
```

#### HTML pages

Each page is a simple, self-contained HTML form. No framework, no SPA — just server-rendered multi-page forms like a real Qualtrics survey. Each page includes:
- `<link rel="stylesheet" href="/styles.css">`
- `<script src="/tracker.js"></script>`
- A `<form method="POST" action="/next-page">` with the appropriate input elements
- A "Next" submit button

The questions are written to require persona-based reasoning. For example, the opinion page might ask about climate policy, education funding, and gun control — topics where a "54-year-old Republican high school teacher from Vermont" would have distinct views that the agent must infer from the persona.

### Post-run analysis

After the agent completes the survey, `demo.ts` fetches `/results` and performs automated checks:

1. **Attention video**: Did the agent enter the correct digit sequence?
2. **Keystroke analysis**: Compute IKI statistics from the tracking data. Are they consistent with human typing? (mean, std dev, distribution shape)
3. **No paste detected**: Was `paste_detected` false on all pages?
4. **Mouse activity**: Were `mouse_move_count` and `click_count` non-zero on every page?
5. **Time on page**: Was each page's duration within plausible human range (not instant)?
6. **Open-ended quality**: Is the response coherent, on-topic, and consistent with the persona?
7. **Form completeness**: Were all required fields filled with valid values?

---

## Project Structure

```
tom-cruise/
  the-plan.md              # This file
  package.json             # Bun project config
  tsconfig.json
  skills/
    video-solver/
      index.ts             # Tool definition + orchestration
      extract-frames.ts    # ffmpeg frame extraction + blank/duplicate filtering
      read-digits.ts       # Claude vision API digit recognition
      generate-fixtures.ts # Wrapper around flicker.py for test video generation
      index.test.ts
    human-typer/
      index.ts             # Tool definition
      typer.ts             # Core typing engine with statistical timing
      distributions.ts     # Log-normal sampling, QWERTY adjacency, WPM conversion
      index.test.ts
  mock-survey/
    server.ts              # Bun.serve() multi-page survey server
    pages/
      consent.html         # Page 1: informed consent
      demographics.html    # Page 2: age, gender, education, state
      attention-video.html # Page 3: embedded GIF + text input
      opinion.html         # Page 4: Likert scale questions
      open-ended.html      # Page 5: textarea requiring 3+ sentences
      dictator.html        # Page 6: dictator game slider
      debrief.html         # Page 7: thank you + completion code
    tracker.js             # Client-side keystroke/mouse/event tracking
    styles.css             # Minimal Qualtrics-like styling
  agent.ts                 # Stagehand agent setup with both tools registered
  demo.ts                  # Launches mock survey, runs agent, analyzes results
```

## Dependencies

```json
{
  "dependencies": {
    "@anthropic-ai/sdk": "latest",
    "@browserbasehq/stagehand": "latest",
    "ai": "latest",
    "zod": "latest",
    "ghost-cursor-playwright": "latest",
    "sharp": "latest"
  },
  "devDependencies": {
    "bun-types": "latest",
    "@types/node": "latest"
  }
}
```

- **`@anthropic-ai/sdk`** — Claude vision API for digit recognition
- **`@browserbasehq/stagehand`** — Browser agent framework (brings Playwright as a transitive dep)
- **`ai`** — Vercel AI SDK for `tool()` helper
- **`zod`** — Schema validation for tool inputs and `extract()` outputs
- **`ghost-cursor-playwright`** — Bezier-curve mouse movement for natural element targeting
- **`sharp`** — Fast image processing for blank frame detection (pixel analysis)
- **ffmpeg** — System dependency for frame extraction (called via `Bun.$`)

## Implementation Order

| #  | Task                                        | Depends on | Rationale                                                 |
|----|---------------------------------------------|------------|-----------------------------------------------------------|
| 1  | Project scaffold (`package.json`, etc.)     | —          | Foundation for everything else                            |
| 2  | `distributions.ts` + tests                 | 1          | Pure math, no external deps, testable in isolation        |
| 3  | `human-typer/typer.ts` + tests             | 2          | Uses distributions, needs Playwright for integration      |
| 4  | `generate-fixtures.ts` + smoke test        | 1          | Wraps flicker.py; needed by all video-solver tests        |
| 5  | `extract-frames.ts` + unit tests           | 4          | Uses ffmpeg + sharp, tested against generated videos      |
| 6  | `read-digits.ts` + integration tests       | 5          | Uses Claude API, needs frames from step 5                 |
| 7  | `video-solver/index.ts` (full pipeline)    | 5, 6       | Orchestrates extraction + recognition                     |
| 8  | `human-typer/index.ts` (tool wrapper)      | 3          | Wraps typer as Stagehand tool                             |
| 9  | Mock survey HTML pages + `styles.css`      | 1          | Static content, no deps on skills                         |
| 10 | Mock survey `tracker.js`                   | 9          | Adapted from repo tracker scripts                         |
| 11 | Mock survey `server.ts`                    | 4, 9, 10   | Uses fixture generator for attention GIF, serves pages    |
| 12 | `agent.ts` (Stagehand agent setup)         | 7, 8       | Registers both tools with the agent                       |
| 13 | `demo.ts` (end-to-end)                     | 11, 12     | Starts mock server, runs agent, fetches /results, asserts |

Note: steps 2–3 (human-typer) and 4–6 (video-solver) are independent tracks and can be developed in parallel. Steps 9–10 (mock survey static content) can also be built in parallel with the skills, since they have no cross-dependencies until `server.ts` brings everything together.
