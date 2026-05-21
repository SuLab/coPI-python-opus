"""LabBot Podcast — daily personalized research briefings for each PI.

Usage:
    python -m src.podcast.main            # run once for all pending recipients
    python -m src.podcast.main scheduler  # long-running daily scheduler

Scheduler behaviour
-------------------
Recipients (agents + opted-in users) are processed one at a time.

Window mode (default 00:00–03:00 UTC):
    Each recipient is processed in turn; the scheduler sleeps between each so
    that the full cohort is spread evenly across the window.  Agents are
    processed first, then users.

Catch-up mode (any time outside the window):
    If the container starts and any recipient is missing today's episode the
    scheduler processes all of them immediately with a short pause between
    each.  This covers restarts after a crash or a missed window.

Per-recipient completion is checked via the DB (PodcastEpisode.episode_date ==
today) rather than a single global flag, so a partial run or a crash is
automatically resumed on the next boot.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import typer

from src.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = typer.Typer(invoke_without_command=True)

# Minimum seconds between recipients during the stagger window.
_MIN_STAGGER_SECS = 60
# Seconds between recipients during catch-up (outside window).
_CATCHUP_PAUSE_SECS = 60
# Maximum sleep between "all done" checks (so newly opted-in users aren't
# delayed more than this many seconds into the next day).
_MAX_IDLE_SLEEP_SECS = 4 * 3600


# ---------------------------------------------------------------------------
# Per-recipient helpers
# ---------------------------------------------------------------------------

async def _get_pending_recipients(today: date) -> tuple[list, list]:
    """Return (pending_agents, pending_users) who have no episode for today.

    Agents are instances of AgentRegistry; users are instances of User (with
    .profile pre-loaded).  Both lists are sorted deterministically so the order
    is stable across restarts within the same day.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from src.database import get_session_factory
    from src.models.agent_registry import AgentRegistry
    from src.models.podcast import PodcastEpisode
    from src.models.podcast_preferences import PodcastPreferences
    from src.models.user import User

    session_factory = get_session_factory()
    async with session_factory() as db:
        # Agents already done today
        done_agents_res = await db.execute(
            select(PodcastEpisode.agent_id).where(
                PodcastEpisode.episode_date == today,
                PodcastEpisode.agent_id.is_not(None),
            )
        )
        done_agent_ids = {r[0] for r in done_agents_res}

        # Users already done today
        done_users_res = await db.execute(
            select(PodcastEpisode.user_id).where(
                PodcastEpisode.episode_date == today,
                PodcastEpisode.user_id.is_not(None),
            )
        )
        done_user_ids = {r[0] for r in done_users_res}

        # Active agents where podcast is explicitly opted in (INNER JOIN —
        # agents with no prefs row or podcast_enabled=False are excluded).
        agents_res = await db.execute(
            select(AgentRegistry)
            .join(
                PodcastPreferences,
                PodcastPreferences.agent_id == AgentRegistry.agent_id,
            )
            .where(
                AgentRegistry.status == "active",
                PodcastPreferences.podcast_enabled.is_(True),
            )
        )
        all_agents = agents_res.scalars().all()
        pending_agents = sorted(
            [a for a in all_agents if a.agent_id not in done_agent_ids],
            key=lambda a: a.agent_id,
        )

        # user_ids covered by the agent path — skip in the user loop
        agent_user_ids = {a.user_id for a in all_agents if a.user_id is not None}

        # Opted-in users not yet done
        users_res = await db.execute(
            select(User)
            .join(PodcastPreferences, PodcastPreferences.user_id == User.id)
            .options(selectinload(User.profile))
            .where(
                User.onboarding_complete.is_(True),
                PodcastPreferences.podcast_enabled.is_(True),
            )
        )
        all_opted_in = users_res.scalars().all()
        pending_users = sorted(
            [
                u for u in all_opted_in
                if u.id not in agent_user_ids
                and u.id not in done_user_ids
                and u.profile is not None
                and u.profile.research_summary
            ],
            key=lambda u: str(u.id),
        )

    return pending_agents, pending_users


async def _process_agent(agent) -> bool:
    """Run the full pipeline for one agent in its own DB session."""
    from src.database import get_session_factory
    from src.podcast.pipeline import run_pipeline_for_agent

    settings = get_settings()
    slack_tokens = settings.get_slack_tokens()
    tokens = slack_tokens.get(agent.agent_id, {})
    bot_token = agent.slack_bot_token or tokens.get("bot", "")

    session_factory = get_session_factory()
    try:
        async with session_factory() as db:
            ok = await run_pipeline_for_agent(
                agent_id=agent.agent_id,
                bot_name=agent.bot_name,
                pi_name=agent.pi_name,
                bot_token=bot_token,
                slack_user_id=agent.slack_user_id,
                db_session=db,
            )
            await db.commit()
        return ok
    except Exception as exc:
        logger.error("Pipeline failed for agent %s: %s", agent.agent_id, exc, exc_info=True)
        return False


async def _process_user(user) -> bool:
    """Run the full pipeline for one plain user in its own DB session."""
    from src.database import get_session_factory
    from src.podcast.pipeline import run_podcast_for_user

    session_factory = get_session_factory()
    try:
        async with session_factory() as db:
            ok = await run_podcast_for_user(user_id=user.id, db_session=db)
            await db.commit()
        return ok
    except Exception as exc:
        logger.error("Pipeline failed for user %s: %s", user.id, exc, exc_info=True)
        return False


def _seconds_until_window(now: datetime, window_start_hour: int) -> int:
    """Seconds until the next opening of the daily generation window."""
    target = now.replace(hour=window_start_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(60, int((target - now).total_seconds()))


# ---------------------------------------------------------------------------
# One-shot batch runner (used by the 'main' command and legacy callers)
# ---------------------------------------------------------------------------

async def run_podcast(dry_run: bool = False) -> list[str]:
    """Run the podcast pipeline for all pending recipients today.

    Returns list of identifiers (agent_ids + "user:<uuid>") that produced
    episodes.  Already-completed recipients (episode exists for today) are
    skipped automatically.
    """
    today = datetime.now(timezone.utc).date()
    pending_agents, pending_users = await _get_pending_recipients(today)
    produced: list[str] = []

    for agent in pending_agents:
        if dry_run:
            logger.info("DRY RUN — would run pipeline for agent: %s", agent.agent_id)
            continue
        ok = await _process_agent(agent)
        if ok:
            produced.append(agent.agent_id)

    for user in pending_users:
        if dry_run:
            logger.info("DRY RUN — would run pipeline for user: %s (%s)", user.id, user.name)
            continue
        ok = await _process_user(user)
        if ok:
            produced.append(f"user:{user.id}")

    logger.info("Podcast run complete: %d episodes produced", len(produced))
    return produced


# ---------------------------------------------------------------------------
# Long-running scheduler
# ---------------------------------------------------------------------------

async def _scheduler_loop(window_start: int, window_end: int) -> None:
    """Single long-lived event loop for the daily scheduler.

    Keeping a single asyncio.run() call avoids the "Future attached to a
    different loop" errors that arise if asyncio.run() is called in a tight
    while-loop (each call creates a new event loop and the SQLAlchemy asyncpg
    engine is bound to the one that created it).
    """
    logger.info(
        "Podcast scheduler started (window=%02d:00–%02d:00 UTC)", window_start, window_end
    )

    while True:
        now = datetime.now(timezone.utc)
        today = now.date()

        pending_agents, pending_users = await _get_pending_recipients(today)
        total_pending = len(pending_agents) + len(pending_users)

        if total_pending == 0:
            sleep_secs = _seconds_until_window(now, window_start)
            logger.info(
                "All episodes generated for %s. Sleeping %ds until %02d:00 UTC.",
                today, min(sleep_secs, _MAX_IDLE_SLEEP_SECS), window_start,
            )
            await asyncio.sleep(min(sleep_secs, _MAX_IDLE_SLEEP_SECS))
            continue

        in_window = window_start <= now.hour < window_end

        if in_window:
            # Spread remaining recipients evenly across the remaining window.
            window_end_dt = now.replace(hour=window_end, minute=0, second=0, microsecond=0)
            remaining_secs = max(0, int((window_end_dt - now).total_seconds()))
            stagger_delay = max(_MIN_STAGGER_SECS, remaining_secs // total_pending)

            # Process one recipient — agents first, then users.
            if pending_agents:
                agent = pending_agents[0]
                logger.info(
                    "[window] Agent %s (%d remaining, next in %ds)",
                    agent.agent_id, total_pending, stagger_delay,
                )
                await _process_agent(agent)
            else:
                user = pending_users[0]
                logger.info(
                    "[window] User %s (%d remaining, next in %ds)",
                    user.id, total_pending, stagger_delay,
                )
                await _process_user(user)

            await asyncio.sleep(stagger_delay)

        else:
            # Outside window — catch-up: process all with a short pause between.
            logger.info(
                "[catchup] %d recipients missing today's episode — running immediately",
                total_pending,
            )
            all_pending = [("agent", a) for a in pending_agents] + [("user", u) for u in pending_users]
            for i, (kind, recipient) in enumerate(all_pending):
                if kind == "agent":
                    logger.info(
                        "[catchup] Agent %s (%d/%d)", recipient.agent_id, i + 1, total_pending
                    )
                    await _process_agent(recipient)
                else:
                    logger.info(
                        "[catchup] User %s (%d/%d)", recipient.id, i + 1, total_pending
                    )
                    await _process_user(recipient)
                if i < total_pending - 1:
                    await asyncio.sleep(_CATCHUP_PAUSE_SECS)

            now = datetime.now(timezone.utc)
            sleep_secs = _seconds_until_window(now, window_start)
            logger.info(
                "[catchup] Done. Sleeping %ds until %02d:00 UTC.",
                min(sleep_secs, _MAX_IDLE_SLEEP_SECS), window_start,
            )
            await asyncio.sleep(min(sleep_secs, _MAX_IDLE_SLEEP_SECS))


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@app.command()
def main(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without posting or generating audio"),
):
    """Run the podcast pipeline once for all pending recipients today."""
    results = asyncio.run(run_podcast(dry_run=dry_run))
    if results:
        typer.echo(f"\nProduced {len(results)} episodes:")
        for aid in results:
            typer.echo(f"  {aid}")
    else:
        typer.echo("No episodes produced.")


@app.command("scheduler")
def scheduler(
    window_start: int = typer.Option(0, "--window-start", help="UTC hour to begin staggered generation (default midnight)"),
    window_end: int = typer.Option(3, "--window-end", help="UTC hour to finish staggered generation (default 3am)"),
):
    """Long-running daily scheduler.

    During the window (default 00:00–03:00 UTC) recipients are processed one at
    a time with an adaptive delay so that the full cohort is spread evenly across
    the window.

    If the container starts outside the window and any recipient is missing
    today's episode, all pending recipients are processed immediately (catch-up).
    """
    if not (0 <= window_start < window_end <= 24):
        typer.echo(f"Invalid window: {window_start}–{window_end}. window_start must be < window_end.", err=True)
        raise typer.Exit(1)
    asyncio.run(_scheduler_loop(window_start, window_end))


if __name__ == "__main__":
    app()
