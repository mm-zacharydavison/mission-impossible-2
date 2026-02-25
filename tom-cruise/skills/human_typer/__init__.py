"""Human typer skill — browser-use custom action registration."""

from browser_use import ActionResult, BrowserSession

from skills.human_typer.typer import CDPDispatcher, HumanTyperConfig, human_type_cdp


async def human_type_tool(
    text: str,
    wpm: int = 70,
    typo_rate: float = 0.01,
    browser_session: BrowserSession = None,  # type: ignore[assignment]
) -> ActionResult:
    """Type text with human-like behavior into the currently focused element.

    Uses realistic inter-keystroke intervals (log-normal distribution),
    occasional typos with corrections, and natural pauses at word/sentence
    boundaries. Dispatches individual CDP key events to avoid paste detection.
    """
    cdp_session = await browser_session.get_or_create_cdp_session()
    dispatcher = CDPDispatcher(cdp_session.cdp_client, cdp_session.session_id)
    await human_type_cdp(
        dispatcher,
        text,
        average_wpm=wpm,
        typo_rate=typo_rate,
    )
    return ActionResult(extracted_content=f"Typed {len(text)} characters with human-like timing")
