import os
import logging
import time
from datetime import datetime, timezone

import discord
from dotenv import dotenv_values
from discord.ext import commands, tasks
from dotenv import load_dotenv

from src.storage.database import (
    channel_mapping_exists,
    get_channel_mappings,
    get_channel_mapping_by_id,
    get_channel_mapping,
    get_unposted_jobs,
    init_db,
    mark_as_posted,
    save_channel_mapping,
    mark_channel_deleted,
    update_channel_name,
    delete_jobs_for_query,
    delete_unposted_jobs_for_query,
    has_channel_mapping_for_query,
)
from src.logger import check_memory_usage, format_uptime


load_dotenv()
logger = logging.getLogger("discord")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("Discord_Token")


def read_channel_id(*names):
    for name in names:
        value = os.getenv(name)
        if value:
            try:
                return int(value)
            except ValueError:
                logger.error("Invalid Discord channel ID in %s", name)
    return None


CHANNEL_ID = read_channel_id("DISCORD_CHANNEL_ID", "Discord_Channel_ID")
FULL_STACK_CHANNEL_ID = os.getenv("FULL_STACK_DISCORD_CHANNEL_ID")
FULL_STACK_CHANNEL_ID = read_channel_id("FULL_STACK_DISCORD_CHANNEL_ID")
DOTNET_CHANNEL_ID = os.getenv("DOTNET_DISCORD_CHANNEL_ID")
DOTNET_CHANNEL_ID = read_channel_id("DOTNET_DISCORD_CHANNEL_ID")
SQA_CHANNEL_ID = os.getenv("SQA_DISCORD_CHANNEL_ID")
SQA_CHANNEL_ID = read_channel_id("SQA_DISCORD_CHANNEL_ID")
PYTHON_CHANNEL_ID = read_channel_id(
    "PYTHON_DISCORD_CHANNEL_ID", "python-developer_Channel_ID"
)
PRIVATE_UNAVAILABLE_CHANNEL_ID = read_channel_id("PRIVATE_UNAVAILABLE_DISCORD_CHANNEL_ID")
MEMORY_CHECK_INTERVAL_SECONDS = int(os.getenv("MEMORY_CHECK_INTERVAL_SECONDS", "60"))
UPTIME_REPORT_INTERVAL_SECONDS = int(os.getenv("UPTIME_REPORT_INTERVAL_SECONDS", "3600"))
CHANNEL_DISCOVERY_INTERVAL_SECONDS = int(os.getenv("CHANNEL_DISCOVERY_INTERVAL_SECONDS", "15"))
last_memory_check = 0.0
last_channel_discovery = 0.0
last_uptime_report = 0.0
bot_started_at = time.monotonic()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

QUERY_CHANNEL_NAMES = {
    "full stack developer": "full-stack-developer",
    ".net developer": "net-developer",
    "sqa engineer": "sqa-engineer",
    "python developer": "python-developer",
    "private-unavailable": "private-unavailable-jobs",
}

QUERY_CHANNEL_KEYWORDS = {
    "full stack developer": (("full", "stack"),),
    ".net developer": ((".net",), ("net",)),
    "sqa engineer": ("sqa",),
    "python developer": ("python",),
    "private-unavailable": (("private",), ("unavailable",)),
}

QUERY_CHANNEL_ENV_KEYS = {
    "full stack developer": "FULL_STACK_DISCORD_CHANNEL_ID",
    ".net developer": "DOTNET_DISCORD_CHANNEL_ID",
    "sqa engineer": "SQA_DISCORD_CHANNEL_ID",
    "python developer": "PYTHON_DISCORD_CHANNEL_ID",
    "private-unavailable": "PRIVATE_UNAVAILABLE_DISCORD_CHANNEL_ID",
}


def configured_search_queries():
    configured = os.getenv("UPWORK_SEARCH_QUERIES", "")
    if not configured:
        # The scraper may have appended new queries to .env after this process
        # started; pick them up instead of seeing an empty list.
        configured = dotenv_values(os.path.join(os.getcwd(), ".env")).get("UPWORK_SEARCH_QUERIES", "")
    return tuple(query.strip().casefold() for query in configured.split(",") if query.strip())


def query_channel_matches(query, channel_name):
    normalized_query = query.casefold().replace(".", " ")
    query_words = {word for word in normalized_query.replace("_", "-").split() if word}
    normalized_channel = channel_name.casefold().replace("_", "-")
    return bool(query_words) and all(word in normalized_channel for word in query_words)


def query_env_key(query):
    normalized = "".join(
        character if character.isalnum() else "_"
        for character in query.upper()
    ).strip("_") or "UNKNOWN"
    return f"{normalized}_DISCORD_CHANNEL_ID"


def persist_channel_id(env_key, channel_id):
    """Write a discovered channel ID to the local .env without touching secrets."""
    env_path = os.path.join(os.getcwd(), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        lines = []

    replacement = f"{env_key}={channel_id}\n"
    active_lines = [line for line in lines if not line.lstrip().startswith("#")]
    if any(
        line.split("=", 1)[1].strip() == str(channel_id)
        for line in active_lines
        if "=" in line
    ):
        # This channel ID is already recorded under another key; never add a
        # duplicate entry for the same channel under a second name.
        return
    for index, line in enumerate(lines):
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        aliases = {
            "PYTHON_DISCORD_CHANNEL_ID": "python-developer_Channel_ID",
        }
        if key == env_key or key == aliases.get(env_key):
            lines[index] = replacement
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(replacement)

    with open(env_path, "w", encoding="utf-8") as file:
        file.writelines(lines)


def persist_search_query(query):
    """Add a newly discovered channel name to the shared Upwork query list."""
    env_path = os.path.join(os.getcwd(), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        lines = []

    queries = []
    for line in lines:
        if line.startswith("UPWORK_SEARCH_QUERIES="):
            queries = [item.strip() for item in line.split("=", 1)[1].split(",") if item.strip()]
            break
    if query.casefold() in {item.casefold() for item in queries}:
        return
    # New query goes FIRST so the scraper picks it up in the very next cycle
    # instead of waiting behind all existing queries.
    queries.insert(0, query)
    replacement = f"UPWORK_SEARCH_QUERIES={','.join(queries)}\n"
    for index, line in enumerate(lines):
        if line.startswith("UPWORK_SEARCH_QUERIES="):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)
    with open(env_path, "w", encoding="utf-8") as file:
        file.writelines(lines)
    logger.info("Added new Upwork search query from channel name: '%s'", query)


def remove_search_query(query):
    """Drop a query from UPWORK_SEARCH_QUERIES in .env after its channel is deleted."""
    env_path = os.path.join(os.getcwd(), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        return
    target = query.casefold()
    removed = False
    for index, line in enumerate(lines):
        if line.startswith("UPWORK_SEARCH_QUERIES="):
            queries = [item.strip() for item in line.split("=", 1)[1].split(",") if item.strip()]
            remaining = [item for item in queries if item.casefold() != target]
            if len(remaining) != len(queries):
                lines[index] = f"UPWORK_SEARCH_QUERIES={','.join(remaining)}\n"
                removed = True
            break
    if removed:
        with open(env_path, "w", encoding="utf-8") as file:
            file.writelines(lines)
        load_dotenv(override=True)
        logger.info("Removed search query after channel deletion: '%s'", query)


def persist_deleted_channel_comment(channel_name, channel_id):
    env_path = os.path.join(os.getcwd(), ".env")
    comment = f"# {channel_name} => is deleted\n"
    try:
        with open(env_path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        lines = []
    updated_lines = []
    id_commented = False
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and value.strip() == str(channel_id) and not key.lstrip().startswith("#"):
            updated_lines.append(f"# {line.rstrip()} => is deleted\n")
            id_commented = True
        else:
            updated_lines.append(line)
    if comment not in updated_lines:
        if updated_lines and not updated_lines[-1].endswith("\n"):
            updated_lines[-1] += "\n"
        updated_lines.append(comment)
    if id_commented or updated_lines != lines:
        with open(env_path, "w", encoding="utf-8") as file:
            file.writelines(updated_lines)


def persist_renamed_channel(old_name, new_name, channel_id):
    """Comment the old generic ID key and append the renamed channel key."""
    env_path = os.path.join(os.getcwd(), ".env")
    old_key = channel_name_env_key(old_name)
    new_key = channel_name_env_key(new_name)
    try:
        with open(env_path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        lines = []

    update_comment = f"# {old_name} => name is updated to {new_name}\n"
    updated_lines = []
    old_key_commented = False
    new_key_present = False
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key == new_key:
            new_key_present = True
        if key == old_key and old_key != new_key:
            if not old_key_commented:
                updated_lines.append(update_comment)
                old_key_commented = True
            updated_lines.append(f"# {line.rstrip()} => name is updated\n")
        else:
            updated_lines.append(line)

    if not old_key_commented and update_comment not in updated_lines:
        updated_lines.append(update_comment)
    if not new_key_present:
        updated_lines.append(f"{new_key}={channel_id}\n")

    with open(env_path, "w", encoding="utf-8") as file:
        file.writelines(updated_lines)


def persist_new_channel(channel):
    """Persist any manually created visible text channel by its own name."""
    normalized_name = "".join(
        character if character.isalnum() else "_"
        for character in channel.name.upper()
    ).strip("_")
    if not normalized_name:
        normalized_name = str(channel.id)
    env_key = f"DISCORD_CHANNEL_{normalized_name}_ID"
    query = channel.name.replace("-", " ").replace("_", " ").strip()
    if query:
        persist_search_query(query)
        # Route jobs scraped for this query back to this channel.
        if query_channel_matches(query, channel.name):
            save_channel_mapping(query.casefold(), channel.id, channel.name)
            os.environ[query_env_key(query)] = str(channel.id)
    persist_channel_id(env_key, channel.id)
    logger.info("New Discord channel discovered: %s (ID %s)", channel.name, channel.id)


def channel_name_env_key(channel_name):
    normalized_name = "".join(
        character if character.isalnum() else "_"
        for character in channel_name.upper()
    ).strip("_") or "UNKNOWN"
    return f"DISCORD_CHANNEL_{normalized_name}_ID"


def configured_channel_ids():
    """Return channel IDs already configured in environment variables."""
    channel_ids = set()
    for key, value in os.environ.items():
        if "CHANNEL_ID" not in key.upper() or not value:
            continue
        try:
            channel_ids.add(int(value))
        except ValueError:
            continue
    return channel_ids


def format_job_message(job):
    payment_verified = "■ Payment verified" if job.get("payment_verified") else "□ Payment not verified"
    proposals = job.get("proposal_count")
    proposals = proposals if proposals is not None else "Not specified"
    description = job.get("description_preview") or "No description preview available."
    apply_line = (
        f"[Apply Here]({job['job_url']})"
        if job.get("job_url")
        else "Apply link unavailable: this job may be private or deleted."
    )
    private_notice = "\n⚠️ **Private or unavailable job**\n" if job.get("is_private") else ""
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
        f"{description}{private_notice}"
        f"{apply_line}"
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
    if job.get("is_private"):
        mapping = get_channel_mapping("private-unavailable")
        return mapping[0] if mapping else PRIVATE_UNAVAILABLE_CHANNEL_ID

    source_query = job.get("source_query", "").casefold()
    mapping = get_channel_mapping(source_query)
    if mapping:
        return mapping[0]
    # This query used to have a dedicated channel that has since been deleted.
    # Keep the job queued instead of dumping it into the generic default channel.
    if has_channel_mapping_for_query(source_query):
        return None
    specialized_channels = {
        "full stack developer": FULL_STACK_CHANNEL_ID,
        ".net developer": DOTNET_CHANNEL_ID,
        "sqa engineer": SQA_CHANNEL_ID,
        "python developer": PYTHON_CHANNEL_ID,
        "private-unavailable": PRIVATE_UNAVAILABLE_CHANNEL_ID,
    }
    if source_query == "private-unavailable" and not (
        PRIVATE_UNAVAILABLE_CHANNEL_ID or get_channel_mapping(source_query)
    ):
        return None
    channel_id = specialized_channels.get(source_query)
    if channel_id:
        return channel_id
    # Channels created manually after startup: their IDs live only in .env
    # (e.g. DISCORD_CHANNEL_LARAVEL_DEVELOPER_ID) until the next restart.
    env_values = dotenv_values(os.path.join(os.getcwd(), ".env"))
    raw = os.getenv(query_env_key(source_query)) or env_values.get(query_env_key(source_query))
    try:
        channel_id = int(raw) if raw else None
    except ValueError:
        channel_id = None
    if channel_id:
        return channel_id
    return CHANNEL_ID


def discover_query_channels():
    """Store IDs for channels whose names match configured query channels."""
    configured_ids = configured_channel_ids()
    visible_channel_ids = {
        channel.id
        for channel in bot.get_all_channels()
        if isinstance(channel, discord.TextChannel)
    }
    for source_query, channel_id, channel_name, active in get_channel_mappings():
        if channel_id not in visible_channel_ids:
            if active and mark_channel_deleted(source_query):
                logger.error("Discord channel '%s' => is deleted", channel_name)
                persist_deleted_channel_comment(channel_name, channel_id)
                # Stop scraping this keyword and wipe its jobs so a future
                # re-created channel starts fetching from zero.
                remove_search_query(source_query)
                removed = delete_jobs_for_query(source_query)
                if removed:
                    logger.info(
                        "Deleted %d stored job(s) for deleted channel '%s'",
                        removed,
                        channel_name,
                    )
            purged = delete_unposted_jobs_for_query(source_query)
            if purged:
                logger.info(
                    "Purged %d queued job(s) for missing channel '%s'",
                    purged,
                    channel_name,
                )
    for channel in bot.get_all_channels():
        if not isinstance(channel, discord.TextChannel):
            continue
        channel_name = channel.name.casefold().replace("_", "-")
        known_queries = dict(QUERY_CHANNEL_NAMES)
        for query in configured_search_queries():
            known_queries.setdefault(query, query.replace(" ", "-"))
        matches_configured_query = any(
            channel_name == expected_name.casefold()
            or (
                query == "private-unavailable"
                and ("private" in channel_name or "unavailable" in channel_name)
            )
            or query_channel_matches(query, channel_name)
            for query, expected_name in known_queries.items()
        )
        existing_channel = get_channel_mapping_by_id(channel.id)
        if existing_channel and existing_channel[1] != channel.name:
            update_channel_name(channel.id, channel.name)
            persist_renamed_channel(existing_channel[1], channel.name, channel.id)
            logger.info(
                "Discord channel renamed: '%s' -> '%s'",
                existing_channel[1],
                channel.name,
            )
        if (
            not matches_configured_query
            and channel.id not in configured_ids
            and not channel_mapping_exists(channel.id)
        ):
            persist_new_channel(channel)
        for query, expected_name in known_queries.items():
            exact_name = channel_name == expected_name.casefold()
            keyword_sets = QUERY_CHANNEL_KEYWORDS.get(query)
            if keyword_sets:
                if query in {"full stack developer", ".net developer"}:
                    keyword_match = any(
                        all(keyword in channel_name for keyword in keyword_set)
                        for keyword_set in keyword_sets
                    )
                elif query == "private-unavailable":
                    keyword_match = any(
                        all(keyword in channel_name for keyword in keyword_set)
                        for keyword_set in keyword_sets
                    )
                else:
                    keyword_match = all(keyword in channel_name for keyword in keyword_sets)
            else:
                keyword_match = query_channel_matches(query, channel_name)
            if exact_name or keyword_match:
                existing = get_channel_mapping(query)
                mapping_changed = existing != (channel.id, channel.name)
                if mapping_changed:
                    save_channel_mapping(query, channel.id, channel.name)
                    env_key = QUERY_CHANNEL_ENV_KEYS.get(query, query_env_key(query))
                    if channel.id not in configured_ids:
                        persist_channel_id(env_key, channel.id)
                    logger.info("Channel discovered or updated: %s", channel.name)


@bot.event
async def on_ready():
    logger.info("Bot logged in as %s", bot.user)
    init_db()
    discover_query_channels()
    if not post_new_jobs_loop.is_running():
        post_new_jobs_loop.start()


@tasks.loop(seconds=10)
async def post_new_jobs_loop():
    global last_memory_check, last_channel_discovery, last_uptime_report
    now = time.monotonic()
    if now - last_memory_check >= MEMORY_CHECK_INTERVAL_SECONDS:
        check_memory_usage(logger)
        last_memory_check = now
    if now - last_channel_discovery >= CHANNEL_DISCOVERY_INTERVAL_SECONDS:
        discover_query_channels()
        last_channel_discovery = now
    if now - last_uptime_report >= UPTIME_REPORT_INTERVAL_SECONDS:
        logger.info("System running time: %s", format_uptime(now - bot_started_at))
        last_uptime_report = now
    try:
        jobs = get_unposted_jobs()
    except Exception:
        logger.exception("Could not read unposted jobs")
        return

    for job in jobs:
        channel_id = get_channel_id_for_job(job)
        if channel_id is None:
            if job.get("is_private"):
                logger.error(
                    "Private/unavailable channel is missing for job %s; job remains queued and will not be rerouted.",
                    job.get("job_id"),
                )
            else:
                logger.error(
                    "No Discord channel is configured or available for job %s; job remains queued.",
                    job.get("job_id"),
                )
            continue
        channel = bot.get_channel(channel_id)
        if channel is None:
            if job.get("is_private"):
                logger.error(
                    "Private/unavailable channel is deleted or unavailable (ID %s) for job %s; "
                    "job remains queued and will not be rerouted.",
                    channel_id,
                    job.get("job_id"),
                )
            else:
                logger.error(
                    "Discord channel is missing or deleted (ID %s) for job %s; job remains queued.",
                    channel_id,
                    job.get("job_id"),
                )
            continue
        try:
            message = await channel.send(format_job_message(job))
            mark_as_posted(job["job_id"])
            logger.info(
                "Posted job '%s' to channel '%s'",
                job.get("title", "Untitled job"),
                channel.name,
            )
        except discord.HTTPException as exc:
            if exc.status == 429:
                retry_after = getattr(exc, "retry_after", None)
                logger.warning(
                    "Discord rate limit for job %s; retry_after=%s. Job remains queued.",
                    job.get("job_id"),
                    retry_after,
                )
            else:
                logger.exception("Discord rejected job %s; job remains queued", job.get("job_id"))
        except Exception:
            logger.exception("Could not post job %s; job remains queued", job.get("job_id"))


@post_new_jobs_loop.error
async def post_new_jobs_loop_error(error):
    logger.error(
        "Job posting loop failed",
        exc_info=(type(error), error, error.__traceback__),
    )


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN, log_handler=None)
