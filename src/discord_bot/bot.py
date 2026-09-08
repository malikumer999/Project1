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
    purge_unpostable_jobs,
    update_job_details,
)
from src.logger import check_memory_usage, format_uptime
from src.upwork.scraper import get_job_details as fetch_job_details
from src.scheduler.scrape_loop import extract_detail_fields


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
    load_dotenv(override=True)
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
    if number.is_integer():
        return f"${int(number):,}"
    return f"${number:,.2f}"


def _thread_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _thread_member_since(value):
    """Render client_member_since (unix ts or ISO) as 'Sep 8, 2026'."""
    if value in (None, ""):
        return "Not specified"
    try:
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %Y")
    except (TypeError, ValueError, OSError):
        return str(value)


def _thread_proposals(value):
    """Map proposal_count to an Upwork-like range label."""
    count = _thread_int(value)
    if count <= 0:
        return "0"
    if count <= 4:
        return "Less than 5"
    if count <= 9:
        return "5 to 10"
    if count <= 14:
        return "10 to 15"
    if count <= 19:
        return "15 to 20"
    return "20+"


def format_thread_content(job):
    """Build the thread message: the full job details, structured like the
    Upwork job page (summary, budget, type, skills, activity, client info)."""
    description = job.get("full_description") or job.get("description_preview") or "No description available."
    title = (job.get("title") or "").strip()
    if title:
        for variant in {title, title.replace("—", "-"), title.replace("-", "—")}:
            variant = variant.strip()
            if variant and description.lower().startswith(variant.lower()):
                description = description[len(variant):].lstrip(" \n\r\t-—:.,")
                break

    lines = []
    if title:
        lines.append(f"**{title}**")
    lines.append(f"Posted {format_time_ago(job.get('posted_at'))}")
    location = (job.get("client_location") or "").strip()
    if location and location != "Location not specified":
        lines.append(location)

    lines.append("")
    lines.append("**Summary**")
    lines.append(description)

    lines.append("")
    lines.append(f"**{job.get('budget_text') or 'Budget not specified'}**")
    lines.append(job.get("job_type_label") or job.get("job_type") or "Not specified")
    lines.append("")
    lines.append(job.get("level") or "Not specified")
    lines.append("Experience Level")
    lines.append("Remote Job")
    if job.get("project_duration"):
        lines.append(job["project_duration"])
    lines.append("Project Type")

    skills = [s.strip() for s in (job.get("skills") or "").split(",") if s.strip()]
    if skills:
        lines.append("")
        lines.append("**Skills and Expertise**")
        lines.append("Mandatory skills")
        lines.append(", ".join(skills))

    lines.append("")
    lines.append("**Activity on this job**")
    lines.append(f"Proposals: {_thread_proposals(job.get('proposal_count'))}")
    lines.append(f"Hires: {_thread_int(job.get('total_hired'))}")
    lines.append(f"Interviewing: {_thread_int(job.get('interviewing'))}")
    lines.append(f"Invites sent: {_thread_int(job.get('invites_sent'))}")
    lines.append(f"Unanswered invites: {_thread_int(job.get('unanswered_invites'))}")

    lines.append("")
    lines.append("**About the client**")
    lines.append(f"Member since {_thread_member_since(job.get('client_member_since'))}")
    if location:
        lines.append(location)
    assignments = _thread_int(job.get("client_total_assignments"))
    jobs_open = _thread_int(job.get("client_jobs_open"))
    hires = _thread_int(job.get("total_hired")) or assignments
    lines.append(f"{hires} hire{'s' if hires != 1 else ''}, {jobs_open} active")

    if job.get("job_url"):
        lines.append("")
        lines.append(f"[View job on Upwork]({job['job_url']})")

    content = "\n".join(lines)
    if len(content) > 1900:
        content = content[:1897].rstrip() + "..."
    return content or "No details available."


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
        if mapping:
            return mapping[0]
        if PRIVATE_UNAVAILABLE_CHANNEL_ID:
            save_channel_mapping("private-unavailable", PRIVATE_UNAVAILABLE_CHANNEL_ID, "private-unavailable-jobs")
            return PRIVATE_UNAVAILABLE_CHANNEL_ID
        return None

    if job.get("posted_at") is None:
        mapping = get_channel_mapping("private-unavailable")
        if mapping:
            return mapping[0]
        if PRIVATE_UNAVAILABLE_CHANNEL_ID:
            save_channel_mapping("private-unavailable", PRIVATE_UNAVAILABLE_CHANNEL_ID, "private-unavailable-jobs")
            return PRIVATE_UNAVAILABLE_CHANNEL_ID
        return None

    source_query = job.get("source_query", "").casefold()
    mapping = get_channel_mapping(source_query)
    if mapping:
        return mapping[0]
    if has_channel_mapping_for_query(source_query):
        return None
    specialized_channels = {
        "full stack developer": FULL_STACK_CHANNEL_ID,
        ".net developer": DOTNET_CHANNEL_ID,
        "sqa engineer": SQA_CHANNEL_ID,
        "python developer": PYTHON_CHANNEL_ID,
    }
    channel_id = specialized_channels.get(source_query)
    if channel_id:
        save_channel_mapping(source_query, channel_id, QUERY_CHANNEL_NAMES.get(source_query, source_query))
        return channel_id
    env_values = dotenv_values(os.path.join(os.getcwd(), ".env"))
    raw = os.getenv(query_env_key(source_query)) or env_values.get(query_env_key(source_query))
    try:
        channel_id = int(raw) if raw else None
    except ValueError:
        channel_id = None
    if channel_id:
        save_channel_mapping(source_query, channel_id, source_query)
        return channel_id
    return CHANNEL_ID


def consolidate_duplicate_channels():
    """Deactivate duplicate source_query mappings that point at the same channel.

    When two queries (e.g. "full stack developer" and "fullstackjobs") both
    resolve to the same Discord channel, keep the one whose name matches the
    channel's actual name, and deactivate the other.
    """
    active = get_channel_mappings()
    by_channel = {}
    for source_query, channel_id, channel_name, active_flag in active:
        if not active_flag:
            continue
        by_channel.setdefault(channel_id, []).append((source_query, channel_name))
    consolidated = 0
    for channel_id, entries in by_channel.items():
        if len(entries) < 2:
            continue
        scored = []
        for source_query, channel_name in entries:
            sq_norm = source_query.casefold().replace(".", " ").replace("-", " ").strip()
            cn_norm = (channel_name or "").casefold().replace(".", " ").replace("-", " ").strip()
            if sq_norm and cn_norm and (sq_norm in cn_norm or cn_norm in sq_norm):
                score = 2
            elif any(w in cn_norm.split() for w in sq_norm.split() if w):
                score = 1
            else:
                score = 0
            scored.append((score, source_query))
        scored.sort(key=lambda x: (-x[0], x[1]))
        keep_query = scored[0][1]
        for _score, source_query in scored[1:]:
            if mark_channel_deleted(source_query):
                logger.info(
                    "Consolidated duplicate channel %s: keeping '%s', deactivated '%s'",
                    channel_id, keep_query, source_query,
                )
                consolidated += 1
    return consolidated


def sync_search_queries_with_channels():
    """Keep UPWORK_SEARCH_QUERIES in .env in sync with active channel mappings.

    - Every active channel's query is (re-)added, so a re-created channel
      starts scraping again even if its query was removed when it was deleted.
    - Queries whose channel mapping is inactive are dropped, so deleted
      channels stop being scraped.
    """
    mappings = get_channel_mappings()
    active_queries = {(sq or "").casefold() for sq, _cid, _name, active in mappings if active}
    inactive_queries = {(sq or "").casefold() for sq, _cid, _name, active in mappings if not active}

    env_path = os.path.join(os.getcwd(), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        return

    queries = []
    for line in lines:
        if line.startswith("UPWORK_SEARCH_QUERIES="):
            queries = [item.strip() for item in line.split("=", 1)[1].split(",") if item.strip()]
            break

    updated = False
    # Re-add queries for active channels that are missing from the list.
    existing = {q.casefold() for q in queries}
    for query in sorted(active_queries):
        if query and query not in existing:
            queries.insert(0, query)
            updated = True
            logger.info("Re-added search query for active channel: '%s'", query)
    # Drop queries for channels that are deleted/inactive.
    kept = [q for q in queries if q.casefold() not in inactive_queries]
    if len(kept) != len(queries):
        dropped = [q for q in queries if q.casefold() in inactive_queries]
        updated = True
        logger.info("Dropped search queries with no active channel: %s", dropped)
        queries = kept

    if updated:
        replacement = f"UPWORK_SEARCH_QUERIES={','.join(queries)}\n"
        for index, line in enumerate(lines):
            if line.startswith("UPWORK_SEARCH_QUERIES="):
                lines[index] = replacement
                break
        else:
            lines.append(replacement)
        with open(env_path, "w", encoding="utf-8") as file:
            file.writelines(lines)
        load_dotenv(override=True)


def discover_query_channels():
    """Store IDs for channels whose names match configured query channels."""
    configured_ids = configured_channel_ids()
    visible_channel_ids = {
        channel.id
        for channel in bot.get_all_channels()
        if isinstance(channel, discord.TextChannel)
    }
    # The guild/channel cache may not be populated yet (right after on_ready).
    # Never make delete/rename decisions from an empty or incomplete cache.
    if not bot.guilds or not visible_channel_ids:
        return
    for source_query, channel_id, channel_name, active in get_channel_mappings():
        if channel_id not in visible_channel_ids:
            if not active:
                continue
            # Cache is ready and the bot is connected: this channel really is
            # gone from the server (or the bot lost access to it).
            logger.warning(
                "Channel '%s' (ID %s) is no longer visible to the bot; "
                "marking it deleted and removing its search query.",
                channel_name,
                channel_id,
            )
            if mark_channel_deleted(source_query):
                # Delete ALL jobs for this query (posted and unposted) so that
                # if the channel is created again, scraping starts fresh and
                # every job is re-fetched from the beginning.
                removed = delete_jobs_for_query(source_query)
                remove_search_query(source_query)
                persist_deleted_channel_comment(channel_name, channel_id)
                logger.info(
                    "Deleted %d job(s) for removed channel '%s'; query '%s' "
                    "will be re-fetched from scratch if the channel is re-created",
                    removed,
                    channel_name,
                    source_query,
                )
            continue
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
            old_query = existing_channel[0]
            old_name = existing_channel[1]
            update_channel_name(channel.id, channel.name)
            persist_renamed_channel(old_name, channel.name, channel.id)
            logger.info(
                "Discord channel renamed: '%s' -> '%s'",
                old_name,
                channel.name,
            )
            if old_query and old_query.casefold() != channel.name.casefold().replace("-", " ").replace("_", " "):
                if mark_channel_deleted(old_query):
                    removed = delete_jobs_for_query(old_query)
                    remove_search_query(old_query)
                    if removed:
                        logger.info(
                            "Deleted %d job(s) for renamed channel '%s' (was '%s')",
                            removed, channel.name, old_name,
                        )
        if (
            not matches_configured_query
            and channel.id not in configured_ids
            and not channel_mapping_exists(channel.id)
        ):
            persist_new_channel(channel)
        matched_query = None
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
                matched_query = query
                break
        if matched_query:
            existing = get_channel_mapping(matched_query)
            if existing != (channel.id, channel.name):
                if existing is None and has_channel_mapping_for_query(matched_query):
                    # Re-creation: this query had a channel that was deleted.
                    # Wipe every job recorded under it so the new channel gets
                    # a completely fresh feed from zero.
                    wiped = delete_jobs_for_query(matched_query)
                    if wiped:
                        logger.info(
                            "Cleared %d old job(s) for re-created channel '%s'",
                            wiped,
                            matched_query,
                        )
                save_channel_mapping(matched_query, channel.id, channel.name)
                # Make sure the scraper is searching this keyword again — the
                # query is removed when the channel is deleted and must come
                # back when the channel is re-created.
                persist_search_query(matched_query)
                env_key = QUERY_CHANNEL_ENV_KEYS.get(matched_query, query_env_key(matched_query))
                if channel.id not in configured_ids:
                    persist_channel_id(env_key, channel.id)
                logger.info("Channel discovered or updated: %s (query: %s)", channel.name, matched_query)


@bot.event
async def on_ready():
    logger.info("Bot logged in as %s", bot.user)
    init_db()
    purged = purge_unpostable_jobs()
    if purged:
        logger.info("Purged %d unposted job(s) older than 30 days on startup", purged)
    discover_query_channels()
    sync_search_queries_with_channels()
    # Only consolidate when the channel cache is actually populated, otherwise
    # the "duplicates" logic deactivates good mappings based on an empty cache.
    if bot.guilds and any(bot.get_all_channels()):
        consolidated = consolidate_duplicate_channels()
        if consolidated:
            logger.info("Consolidated %d duplicate channel mapping(s)", consolidated)
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
        sync_search_queries_with_channels()
        last_channel_discovery = now
    if now - last_uptime_report >= UPTIME_REPORT_INTERVAL_SECONDS:
        logger.info("System running time: %s", format_uptime(now - bot_started_at))
        last_uptime_report = now
    try:
        jobs = get_unposted_jobs()
    except Exception:
        logger.exception("Could not read unposted jobs")
        return

    if not jobs:
        return

    logger.info("Posting loop found %d unposted job(s)", len(jobs))

    for job in jobs:
        if not job.get("full_description") and job.get("ciphertext"):
            try:
                details = extract_detail_fields(fetch_job_details(job["ciphertext"]))
                if details.get("is_private"):
                    # Transient failures (network hiccup, rate limit) look
                    # identical to a genuinely private job. Retry once
                    # before accepting the private/unavailable verdict.
                    time.sleep(2)
                    details = extract_detail_fields(fetch_job_details(job["ciphertext"]))
                if details.get("is_private"):
                    # The job has a valid ciphertext, so its public page
                    # exists: never reroute it to the private/unavailable
                    # channel just because detail enrichment failed.
                    details["is_private"] = 0
                update_job_details(job["job_id"], details)
                job.update({k: v for k, v in details.items() if v is not None})
            except Exception:
                logger.exception("Could not backfill details for job %s", job.get("job_id"))
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
            try:
                thread_name = f"Job: {(job.get('title') or 'Untitled')[:80]}"
                thread_content = format_thread_content(job)
                thread = await message.create_thread(
                    name=thread_name,
                    auto_archive_duration=60,
                )
                await thread.send(thread_content)
                logger.info(
                    "Created thread for job '%s' in channel '%s'",
                    job.get("title", "Untitled job"),
                    channel.name,
                )
            except Exception:
                logger.exception("Could not create thread for job %s", job.get("job_id"))
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
