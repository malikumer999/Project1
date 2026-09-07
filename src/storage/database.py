import sqlite3
import os

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
            is_private, delivery_status, posted_to_discord
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id, title, ciphertext, job_type, budget_text, level, job_url, source_query,
        details.get("posted_at"), details.get("detected_time"), details.get("proposal_count"),
        details.get("payment_verified"), details.get("client_location"),
        details.get("client_total_spent"), details.get("description_preview"),
        int(is_private), "pending", 0,
    ))
    conn.commit()
    conn.close()


def get_unposted_jobs():
    """Return all jobs that haven't been posted to Discord yet."""
    conn = get_connection()
    cursor = conn.execute("""
        SELECT job_id, title, ciphertext, job_type, budget_text, level, job_url, source_query,
               posted_at, detected_time, proposal_count, payment_verified,
             client_location, client_total_spent, description_preview,
             is_private, delivery_status, discord_channel_id
         FROM jobs WHERE posted_to_discord = 0
        ORDER BY first_seen ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    columns = [
        "job_id", "title", "ciphertext", "job_type", "budget_text", "level", "job_url", "source_query",
        "posted_at", "detected_time", "proposal_count", "payment_verified",
        "client_location", "client_total_spent", "description_preview",
        "is_private", "delivery_status", "discord_channel_id",
    ]
    return [dict(zip(columns, row)) for row in rows]


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
    """Delete jobs older than ``days`` days and return the deleted count."""
    if days < 1:
        raise ValueError("days must be at least 1")
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM jobs WHERE first_seen < datetime('now', ?)",
            (f"-{days} days",),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def save_channel_mapping(source_query, channel_id, channel_name):
    conn = get_connection()
    try:
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
