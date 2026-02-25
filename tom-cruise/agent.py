"""browser-use agent that navigates the mock survey using custom skills."""

from __future__ import annotations

from dataclasses import dataclass

from browser_use import Agent, BrowserProfile, BrowserSession, ChatAnthropic, Tools

from skills.human_typer import human_type_tool
from skills.video_solver import solve_attention_video_tool


@dataclass
class AgentConfig:
    survey_url: str
    persona: str
    headless: bool = False


def build_task_prompt(survey_url: str, persona: str) -> str:
    return f"""\
Complete the survey at {survey_url} as this persona: {persona}

Navigate page by page. On each page, interact with the form elements and
click the Next button to advance.

Page 1 (Consent): Check the "I agree to participate" checkbox, then click Next.

Page 2 (Demographics): Fill out age range, gender, education level, and state
based on the persona. Select radio buttons and the state dropdown, then click Next.

Page 3 (Attention Video): There is an <img id="attention-video"> element whose
src points to an animated GIF. Use the solve_attention_video tool with the
FULL absolute URL (including http://localhost:...) of that image src.
Type the returned digit sequence into the text input, then click Next.

Page 4 (Opinions): Answer all 5 Likert scale questions about climate policy by
selecting radio buttons consistent with the persona. Then click Next.

Page 5 (Open-ended): Generate a thoughtful 3-5 sentence response about climate
policy views based on the persona. Use the human_type_tool to type this
response into the textarea (do NOT paste or use the built-in type action).
Then click Next.

Page 6 (Dictator Game): Move the slider to an amount ($0-$10) that fits the
persona's generosity, then click Next.

Page 7 (Debrief): Read the completion code displayed on the page and report it."""


def create_tools() -> Tools:
    tools = Tools()
    tools.action(
        description=(
            "Type text with human-like behavior into the currently focused element. "
            "Uses realistic inter-keystroke intervals, occasional typos with corrections, "
            "and natural pauses. Use this for the open-ended textarea response."
        ),
    )(human_type_tool)
    tools.action(
        description=(
            "Solve a video/GIF attention check. Pass the full src URL of the "
            "attention video image element. Returns the digit sequence shown."
        ),
    )(solve_attention_video_tool)
    return tools


async def run_survey(config: AgentConfig) -> str | None:
    """Run the survey agent and return the completion code (if extracted)."""
    llm = ChatAnthropic(model="claude-sonnet-4-6", temperature=0.0)

    browser_profile = BrowserProfile(
        headless=config.headless,
        disable_security=True,
    )
    browser_session = BrowserSession(browser_profile=browser_profile)

    agent = Agent(
        task=build_task_prompt(config.survey_url, config.persona),
        llm=llm,
        browser_session=browser_session,
        tools=create_tools(),
        use_vision=True,
        max_actions_per_step=4,
    )

    result = await agent.run()

    await browser_session.stop()

    # Extract completion code from agent history
    if result and result.history:
        for entry in reversed(result.history):
            if entry.result:
                for action_result in entry.result:
                    content = action_result.extracted_content or ""
                    if "SURVEY-" in content:
                        # Extract the code
                        for word in content.split():
                            if word.startswith("SURVEY-"):
                                return word.rstrip(".,;:!?")
    return None
