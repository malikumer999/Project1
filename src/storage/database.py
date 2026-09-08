import sqlite3
import os
from datetime import datetime, timezone

DB_PATH = os.path.join("data", "jobs.db")

QUERY_PRIORITY = {
    "full stack developer": 0,
    ".net developer": 1,
    "sqa engineer": 2,
    "python developer": 3,
}


def _query_priority(source_query):
    return QUERY_PRIORITY.get((source_query or "").casefold(), 100)


def get_connection():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    return conn


def init_db():
    """Create the jobs table if it doesn't exist yet. Safe to call every startup."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            title TEXT,
            ciphertext TEXT,
            job_type TEXT,
            budget_text TEXT,
            level TEXT,
            job_url TEXT,
            source_query TEXT,
            posted_at TEXT,
            detected_time TEXT,
            proposal_count INTEGER,
            payment_verified INTEGER,
            client_location TEXT,
            client_total_spent REAL,
            description_preview TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            posted_to_discord INTEGER DEFAULT 0,
            is_private INTEGER DEFAULT 0,
            delivery_status TEXT DEFAULT 'pending',
            discord_channel_id INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discord_channels (
            source_query TEXT PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            channel_name TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            active INTEGER DEFAULT 1
        )
    """)
    # Existing databases need these columns too; SQLite supports adding them
    # without losing previously collected jobs.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for column, definition in {
        "posted_at": "TEXT",
        "source_query": "TEXT",
        "detected_time": "TEXT",
        "proposal_count": "INTEGER",
        "payment_verified": "INTEGER",
        "client_location": "TEXT",
        "client_total_spent": "REAL",
        "description_preview": "TEXT",
        "is_private": "INTEGER DEFAULT 0",
        "delivery_status": "TEXT DEFAULT 'pending'",
        "discord_channel_id": "INTEGER",
        "full_description": "TEXT",
        "project_duration": "TEXT",
        "category_name": "TEXT",
        "skills": "TEXT",
        "workload": "TEXT",
        "client_member_since": "TEXT",
        "client_jobs_posted": "INTEGER",
        "client_jobs_open": "INTEGER",
        "client_total_assignments": "INTEGER",
        "client_hours_count": "INTEGER",
        "client_feedback_count": "INTEGER",
        "client_score": "REAL",
        "client_country_timezone": "TEXT",
        "total_hired": "INTEGER",
        "interviewing": "INTEGER",
        "invites_sent": "INTEGER",
        "unanswered_invites": "INTEGER",
        "job_type_label": "TEXT",
    }.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
    conn.execute(
        """UPDATE jobs SET posted_to_discord = 0, delivery_status = 'pending'
           WHERE is_private = 1 AND delivery_status = 'private'"""
    )
    channel_columns = {row[1] for row in conn.execute("PRAGMA table_info(discord_channels)")}
    if "active" not in channel_columns:
        conn.execute("ALTER TABLE discord_channels ADD COLUMN active INTEGER DEFAULT 1")
    conn.commit()
    conn.close()


def job_exists(job_id):
    """Check if we've already stored this job_id."""
    conn = get_connection()
    cursor = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None


def save_job(job_id, title, ciphertext, job_type, budget_text, level, job_url, source_query=None, details=None):
    """Insert a newly-found job. Does nothing if it already exists (avoids duplicate errors)."""
    source_query = resolve_canonical_source_query(source_query) if source_query else source_query
    conn = get_connection()
    details = details or {}
    is_private = bool(details.get("is_private")) or not bool(ciphertext)
    existing = conn.execute(
        "SELECT source_query, posted_to_discord FROM jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    should_update_route = (
        existing
        and source_query
        and existing[0] != source_query
        and (
            not existing[1]
            or _query_priority(source_query) < _query_priority(existing[0])
        )
    )
    if should_update_route:
        conn.execute(
            """UPDATE jobs
               SET source_query = ?, posted_to_discord = 0,
                   delivery_status = 'pending', discord_channel_id = NULL
               WHERE job_id = ?""",
            (source_query, job_id),
        )
    conn.execute("""
        INSERT OR IGNORE INTO jobs (
            job_id, title, ciphertext, job_type, budget_text, level, job_url, source_query,
            posted_at, detected_time, proposal_count, payment_verified,
            client_location, client_total_spent, description_preview,
            is_private, delivery_status, posted_to_discord,
            full_description, project_duration, category_name,
            skills, workload, client_member_since,
            client_jobs_posted, client_jobs_open, client_total_assignments,
            client_hours_count, client_feedback_count, client_score,
            client_country_timezone, total_hired, interviewing,
            invites_sent, unanswered_invites, job_type_label
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id, title, ciphertext, job_type, budget_text, level, job_url, source_query,
        details.get("posted_at"), details.get("detected_time"), details.get("proposal_count"),
        details.get("payment_verified"), details.get("client_location"),
        details.get("client_total_spent"), details.get("description_preview"),
        int(is_private), "pending", 0,
        details.get("full_description"), details.get("project_duration"), details.get("category_name"),
        ",".join(details.get("skills") or []),
        details.get("workload"), details.get("client_member_since"),
        details.get("client_jobs_posted"), details.get("client_jobs_open"),
        details.get("client_total_assignments"), details.get("client_hours_count"),
        details.get("client_feedback_count"), details.get("client_score"),
        details.get("client_country_timezone"),
        details.get("total_hired"), details.get("interviewing"),
        details.get("invites_sent"), details.get("unanswered_invites"),
        details.get("job_type_label"),
    ))
    conn.commit()
    conn.close()


def get_unposted_jobs():
    """Return all jobs that haven't been posted to Discord yet, oldest first."""
    conn = get_connection()
    cursor = conn.execute("""
        SELECT job_id, title, ciphertext, job_type, budget_text, level, job_url, source_query,
               posted_at, detected_time, proposal_count, payment_verified,
             client_location, client_total_spent, description_preview,
             is_private, delivery_status, discord_channel_id,
             full_description, project_duration, category_name,
             skills, workload, client_member_since,
             client_jobs_posted, client_jobs_open, client_total_assignments,
             client_hours_count, client_feedback_count, client_score,
             client_country_timezone, total_hired, interviewing,
             invites_sent, unanswered_invites, job_type_label
         FROM jobs WHERE posted_to_discord = 0
        ORDER BY posted_at ASC, first_seen ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    columns = [
        "job_id", "title", "ciphertext", "job_type", "budget_text", "level", "job_url", "source_query",
        "posted_at", "detected_time", "proposal_count", "payment_verified",
        "client_location", "client_total_spent", "description_preview",
        "is_private", "delivery_status", "discord_channel_id",
        "full_description", "project_duration", "category_name",
        "skills", "workload", "client_member_since",
        "client_jobs_posted", "client_jobs_open", "client_total_assignments",
            "client_hours_count", "client_feedback_count", "client_score",
            "client_country_timezone", "total_hired", "interviewing",
            "invites_sent", "unanswered_invites", "job_type_label",
        ]
    jobs = [dict(zip(columns, row)) for row in rows]
    cutoff = datetime.now(timezone.utc).timestamp() - (30 * 86400)
    filtered = []
    for job in jobs:
        posted_at = job.get("posted_at")
        posted_ts = None
        if posted_at is not None:
            try:
                if isinstance(posted_at, (int, float)):
                    posted_ts = float(posted_at)
                else:
                    text = str(posted_at)
                    if text.replace(".", "", 1).isdigit():
                        posted_ts = float(text)
                    else:
                        posted_ts = datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OSError):
                posted_ts = None
        if posted_ts is not None and posted_ts < cutoff:
            continue
        filtered.append(job)
    return filtered


def update_job_details(job_id, details):
    """Backfill fields on a job row (used to enrich jobs saved before the
    full_description column existed)."""
    if not details:
        return
    conn = get_connection()
    try:
        assignments = []
        values = []
        for key in (
            "full_description", "project_duration", "category_name",
            "description_preview", "client_location", "client_total_spent",
            "proposal_count", "payment_verified", "client_jobs_posted",
            "client_jobs_open", "client_total_assignments", "client_hours_count",
            "client_feedback_count", "client_score", "client_country_timezone",
            "client_member_since", "workload",
            "total_hired", "interviewing", "invites_sent",
            "unanswered_invites", "job_type_label",
        ):
            if key in details:
                assignments.append(f"{key} = ?")
                values.append(details[key])
        if details.get("skills"):
            assignments.append("skills = ?")
            values.append(",".join(details["skills"]))
        if not assignments:
            return
        values.append(job_id)
        conn.execute(
            f"UPDATE jobs SET {', '.join(assignments)} WHERE job_id = ?",
            values,
        )
        conn.commit()
    finally:
        conn.close()


def get_unposted_job_count():
    """Return the number of unposted jobs (for periodic logging)."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM jobs WHERE posted_to_discord = 0").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def mark_as_posted(job_id):
    """Mark a job as posted so it doesn't get sent to Discord again."""
    conn = get_connection()
    conn.execute(
        "UPDATE jobs SET posted_to_discord = 1, delivery_status = 'posted' WHERE job_id = ?",
        (job_id,),
    )
    conn.commit()
    conn.close()


def cleanup_old_jobs(days=30):
    """Delete jobs older than ``days`` days by scrape date or Upwork post date."""
    if days < 1:
        raise ValueError("days must be at least 1")
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM jobs WHERE first_seen < datetime('now', ?)",
            (f"-{days} days",),
        )
        deleted_by_first_seen = cursor.rowcount
        cursor = conn.execute(
            "SELECT job_id, posted_at FROM jobs WHERE posted_at IS NOT NULL"
        )
        deleted_by_posted_at = 0
        for job_id, posted_at in cursor.fetchall():
            posted_ts = None
            if posted_at:
                try:
                    if str(posted_at).replace(".", "", 1).isdigit():
                        posted_ts = float(posted_at)
                        if posted_ts > 10_000_000_000:
                            posted_ts /= 1000
                    else:
                        posted_dt = datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))
                        posted_ts = posted_dt.timestamp()
                except (TypeError, ValueError, OSError):
                    continue
            if posted_ts is not None and posted_ts < cutoff:
                conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
                deleted_by_posted_at += 1
        conn.commit()
        return deleted_by_first_seen + deleted_by_posted_at
    finally:
        conn.close()


def purge_unpostable_jobs():
    """One-shot cleanup: remove all unposted jobs older than 30 days by posted_at.

    Used to clear a backlog where rows accumulated before the scrape-time filter
    was added. Safe to call repeatedly.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - (30 * 86400)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT job_id, posted_at FROM jobs WHERE posted_to_discord = 0 AND posted_at IS NOT NULL"
        ).fetchall()
        deleted = 0
        for job_id, posted_at in rows:
            posted_ts = None
            try:
                if isinstance(posted_at, (int, float)):
                    posted_ts = float(posted_at)
                else:
                    text = str(posted_at)
                    if text.replace(".", "", 1).isdigit():
                        posted_ts = float(text)
                    else:
                        posted_ts = datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OSError):
                continue
            if posted_ts is not None and posted_ts < cutoff:
                conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
                deleted += 1
        conn.commit()
        return deleted
    finally:
        conn.close()


def save_channel_mapping(source_query, channel_id, channel_name):
    """Insert or update a channel mapping. If a different source_query is
    already mapped to the same channel_id, deactivate it and reassign so the
    dashboard does not show the channel twice with conflicting status."""
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT source_query, active FROM discord_channels WHERE channel_id = ?",
            (channel_id,),
        ).fetchall()
        for existing_query, existing_active in existing:
            if existing_query != source_query and existing_active:
                conn.execute(
                    "UPDATE discord_channels SET active = 0, updated_at = CURRENT_TIMESTAMP "
                    "WHERE channel_id = ? AND source_query = ?",
                    (channel_id, existing_query),
                )
        conn.execute(
            """INSERT INTO discord_channels (source_query, channel_id, channel_name, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(source_query) DO UPDATE SET
               channel_id = excluded.channel_id,
               channel_name = excluded.channel_name,
                    updated_at = CURRENT_TIMESTAMP,
                    active = 1""",
            (source_query, channel_id, channel_name),
        )
        conn.commit()
    finally:
        conn.close()


def get_channel_mapping(source_query):
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT channel_id, channel_name FROM discord_channels "
            "WHERE source_query = ? AND active = 1",
            (source_query,),
        ).fetchone()
    finally:
        conn.close()


def get_channel_mappings():
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT source_query, channel_id, channel_name, active FROM discord_channels"
        ).fetchall()
    finally:
        conn.close()


def mark_channel_deleted(source_query):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE discord_channels SET active = 0, updated_at = CURRENT_TIMESTAMP "
            "WHERE source_query = ? AND active = 1",
            (source_query,),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_jobs_for_query(source_query):
    """Remove all jobs found by a query whose channel was deleted."""
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM jobs WHERE source_query = ?", (source_query,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def channel_mapping_exists(channel_id):
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT 1 FROM discord_channels WHERE channel_id = ? LIMIT 1",
            (channel_id,),
        ).fetchone() is not None
    finally:
        conn.close()


def get_channel_mapping_by_id(channel_id):
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT source_query, channel_name FROM discord_channels WHERE channel_id = ? LIMIT 1",
            (channel_id,),
        ).fetchone()
    finally:
        conn.close()


def update_channel_name(channel_id, channel_name):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE discord_channels SET channel_name = ?, updated_at = CURRENT_TIMESTAMP WHERE channel_id = ?",
            (channel_name, channel_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_unposted_jobs_for_query(source_query):
    """Remove still-queued jobs for a source_query whose channel was deleted.
    Jobs already posted to Discord are kept, so they are never re-sent."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM jobs WHERE source_query = ? AND posted_to_discord = 0",
            (source_query,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def has_channel_mapping_for_query(source_query):
    """Return True if any mapping (active or inactive) exists for this query."""
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT 1 FROM discord_channels WHERE source_query = ? LIMIT 1",
            (source_query,),
        ).fetchone() is not None
    finally:
        conn.close()


def is_channel_deleted_for_query(source_query):
    """Return True when this query has a channel mapping that has been
    deactivated (its Discord channel was deleted)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT active FROM discord_channels WHERE source_query = ? LIMIT 1",
            (source_query,),
        ).fetchone()
        return row is not None and row[0] == 0
    finally:
        conn.close()


def resolve_canonical_source_query(query):
    """If a different but related query has an active channel mapping, return
    that query instead. Otherwise return the input unchanged.

    Example: "mern stack developer" → "mern stack" (if the channel is mapped
    to "mern stack"). This keeps jobs and the routing table in sync.
    """
    if not query:
        return query
    target = query.casefold().strip()
    target_words = set(target.replace(".", " ").split())
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT source_query FROM discord_channels WHERE active = 1"
        ).fetchall()
    finally:
        conn.close()
    for (mapped_query,) in rows:
        mapped = (mapped_query or "").casefold().strip()
        if not mapped or mapped == target:
            continue
        mapped_words = set(mapped.replace(".", " ").split())
        if target_words == mapped_words or target_words < mapped_words or mapped_words < target_words:
            return mapped_query
    return query
