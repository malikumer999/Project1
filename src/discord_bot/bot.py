import os
import logging
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from src.storage.database import get_unposted_jobs, init_db, mark_as_posted
from src.logger import check_memory_usage


load_dotenv()
logger = logging.getLogger("discord")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
FULL_STACK_CHANNEL_ID = os.getenv("FULL_STACK_DISCORD_CHANNEL_ID")
FULL_STACK_CHANNEL_ID = int(FULL_STACK_CHANNEL_ID) if FULL_STACK_CHANNEL_ID else None
DOTNET_CHANNEL_ID = os.getenv("DOTNET_DISCORD_CHANNEL_ID")
DOTNET_CHANNEL_ID = int(DOTNET_CHANNEL_ID) if DOTNET_CHANNEL_ID else None
SQA_CHANNEL_ID = os.getenv("SQA_DISCORD_CHANNEL_ID")
SQA_CHANNEL_ID = int(SQA_CHANNEL_ID) if SQA_CHANNEL_ID else None
MEMORY_CHECK_INTERVAL_SECONDS = int(os.getenv("MEMORY_CHECK_INTERVAL_SECONDS", "60"))
last_memory_check = 0.0

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def format_job_message(job):
    payment_verified = "■ Payment verified" if job.get("payment_verified") else "□ Payment not verified"
    proposals = job.get("proposal_count")
    proposals = proposals if proposals is not None else "Not specified"
    description = job.get("description_preview") or "No description preview available."
    return (
        "■ **New Job Posted!**\n"
        f"Title: {job['title']}\n"
        f"Posted: {format_time_ago(job.get('posted_at'))}\n"
        f"Budget/Rate: {job['budget_text']}\n"
        f"Level: {job['level']}\n"
        f"Time: {job.get('detected_time') or 'Not specified'}\n"
        f"Proposals: {proposals}\n"
        f"Client Info: {payment_verified}, {job.get('client_location') or 'Location not specified'}, "
        f"{format_spend(job.get('client_total_spent'))} spent\n"
        f"{description}\n"
        f"[Apply Here]({job['job_url']})"
    )


def format_spend(amount):
    if amount is None:
        return "Spend not specified"
    try:
        number = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    return f"${number:,.0f}" if number.is_integer() else f"${number:,.2f}"


def format_time_ago(value):
    """Render an Upwork ISO timestamp or Unix timestamp as a short age."""
    if value in (None, ""):
        return "Not specified"
    try:
        if isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            posted = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        else:
            posted = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OSError):
        return str(value)

    seconds = max(0, int((datetime.now(timezone.utc) - posted).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86_400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86_400
    return f"{days} day{'s' if days != 1 else ''} ago"


def get_channel_id_for_job(job):
    """Route specialized search results to their configured Discord channels."""
    specialized_channels = {
        "full stack developer": FULL_STACK_CHANNEL_ID,
        ".net developer": DOTNET_CHANNEL_ID,
        "sqa engineer": SQA_CHANNEL_ID,
    }
    channel_id = specialized_channels.get(job.get("source_query", "").casefold())
    if channel_id:
        return channel_id
    return CHANNEL_ID


@bot.event
async def on_ready():
    logger.info("Bot logged in as %s", bot.user)
    init_db()
    if not post_new_jobs_loop.is_running():
        post_new_jobs_loop.start()


@tasks.loop(seconds=10)
async def post_new_jobs_loop():
    global last_memory_check
    now = time.monotonic()
    if now - last_memory_check >= MEMORY_CHECK_INTERVAL_SECONDS:
        check_memory_usage(logger)
        last_memory_check = now
    try:
        jobs = get_unposted_jobs()
    except Exception:
        logger.exception("Could not read unposted jobs")
        return

    for job in jobs:
        channel_id = get_channel_id_for_job(job)
        channel = bot.get_channel(channel_id)
        if channel is None:
            logger.error("Could not find Discord channel ID: %s", channel_id)
            continue
        try:
            await channel.send(format_job_message(job))
            mark_as_posted(job["job_id"])
            logger.info("Posted job %s", job["job_id"])
        except Exception:
            logger.exception("Could not post job %s", job.get("job_id"))


@post_new_jobs_loop.error
async def post_new_jobs_loop_error(error):
    logger.error(
        "Job posting loop failed",
        exc_info=(type(error), error, error.__traceback__),
    )


bot.run(DISCORD_TOKEN)
