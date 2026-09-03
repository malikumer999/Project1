import sqlite3
import os

DB_PATH = os.path.join("data", "jobs.db")


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
            posted_to_discord INTEGER DEFAULT 0
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
    }.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
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
    conn.execute("""
        INSERT OR IGNORE INTO jobs (
            job_id, title, ciphertext, job_type, budget_text, level, job_url, source_query,
            posted_at, detected_time, proposal_count, payment_verified,
            client_location, client_total_spent, description_preview
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id, title, ciphertext, job_type, budget_text, level, job_url, source_query,
        details.get("posted_at"), details.get("detected_time"), details.get("proposal_count"),
        details.get("payment_verified"), details.get("client_location"),
        details.get("client_total_spent"), details.get("description_preview"),
    ))
    conn.commit()
    conn.close()


def get_unposted_jobs():
    """Return all jobs that haven't been posted to Discord yet."""
    conn = get_connection()
    cursor = conn.execute("""
        SELECT job_id, title, ciphertext, job_type, budget_text, level, job_url, source_query,
               posted_at, detected_time, proposal_count, payment_verified,
               client_location, client_total_spent, description_preview
        FROM jobs WHERE posted_to_discord = 0
        ORDER BY first_seen ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    columns = [
        "job_id", "title", "ciphertext", "job_type", "budget_text", "level", "job_url", "source_query",
        "posted_at", "detected_time", "proposal_count", "payment_verified",
        "client_location", "client_total_spent", "description_preview",
    ]
    return [dict(zip(columns, row)) for row in rows]


def mark_as_posted(job_id):
    """Mark a job as posted so it doesn't get sent to Discord again."""
    conn = get_connection()
    conn.execute("UPDATE jobs SET posted_to_discord = 1 WHERE job_id = ?", (job_id,))
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
