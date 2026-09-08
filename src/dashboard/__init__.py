"""Flask dashboard: live stats for scraper, jobs, Discord channels, and start/stop control."""

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from src.storage.database import get_connection

logging.getLogger("werkzeug").setLevel(logging.ERROR)

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5000"))
STARTED_AT = time.monotonic()
STARTED_AT_WALL = datetime.now(timezone.utc)
CHILD_PID = os.getpid()


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    @app.route("/")
    def index():
        return render_template("dashboard.html", started_at_wall=STARTED_AT_WALL.isoformat())

    @app.route("/api/stats")
    def stats():
        conn = get_connection()
        try:
            total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            posted_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE posted_to_discord = 1").fetchone()[0]
            unposted_jobs = total_jobs - posted_jobs
            private_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_private = 1").fetchone()[0]
            channel_rows = conn.execute("""
                SELECT channel_id, channel_name, source_query, active, updated_at
                  FROM discord_channels
              ORDER BY active DESC, channel_name, source_query
            """).fetchall()
            deduped = {}
            for cid, cname, sq, active, updated_at in channel_rows:
                key = cid if cid else sq
                entry = deduped.setdefault(key, {
                    "source_query": sq,
                    "channel_id": cid,
                    "channel_name": cname,
                    "active": bool(active),
                    "updated_at": updated_at,
                })
                if bool(active) and not entry["active"]:
                    entry.update({
                        "source_query": sq,
                        "channel_id": cid,
                        "channel_name": cname,
                        "active": True,
                        "updated_at": updated_at,
                    })
            channel_count = sum(1 for e in deduped.values() if e["active"])
            per_channel = []
            for entry in deduped.values():
                if not entry["active"]:
                    continue
                row = conn.execute("""
                    SELECT COUNT(*),
                           SUM(CASE WHEN posted_to_discord = 1 THEN 1 ELSE 0 END)
                      FROM jobs WHERE source_query = ?
                """, (entry["source_query"],)).fetchone()
                per_channel.append({
                    "label": entry["channel_name"] or entry["source_query"] or "default",
                    "channel_id": entry["channel_id"],
                    "total": row[0] or 0,
                    "posted": row[1] or 0,
                })
            per_channel.sort(key=lambda r: r["total"], reverse=True)
            recent = conn.execute("""
                SELECT job_id, title, source_query, posted_at, first_seen
                  FROM jobs
                 WHERE posted_to_discord = 1
              ORDER BY first_seen DESC
                 LIMIT 10
            """).fetchall()
            recent_jobs = [
                {
                    "job_id": row[0],
                    "title": row[1] or "Untitled",
                    "source_query": row[2] or "",
                    "posted_at": row[3],
                    "first_seen": row[4],
                }
                for row in recent
            ]
        finally:
            conn.close()
        return jsonify(
            {
                "uptime_seconds": time.monotonic() - STARTED_AT,
                "started_at": STARTED_AT_WALL.isoformat(),
                "total_jobs": total_jobs,
                "posted_jobs": posted_jobs,
                "unposted_jobs": unposted_jobs,
                "private_jobs": private_jobs,
                "channel_count": channel_count,
                "per_channel": per_channel,
                "recent_jobs": recent_jobs,
                "server_time": datetime.now(timezone.utc).isoformat(),
            }
        )

    @app.route("/api/channels")
    def channels():
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT source_query, channel_id, channel_name, active, updated_at
                  FROM discord_channels
              ORDER BY active DESC, channel_name, source_query
            """).fetchall()
        finally:
            conn.close()
        deduped = {}
        for sq, cid, cname, active, updated_at in rows:
            key = cid if cid else sq
            entry = deduped.setdefault(key, {
                "source_query": sq,
                "channel_id": cid,
                "channel_name": cname,
                "active": bool(active),
                "updated_at": updated_at,
            })
            if bool(active) and not entry["active"]:
                entry.update({
                    "source_query": sq,
                    "channel_id": cid,
                    "channel_name": cname,
                    "active": True,
                    "updated_at": updated_at,
                })
        channels_list = sorted(
            deduped.values(),
            key=lambda e: (not e["active"], e["channel_name"] or "", e["source_query"] or ""),
        )
        return jsonify(
            {
                "channels": channels_list,
                "server_time": datetime.now(timezone.utc).isoformat(),
            }
        )

    @app.route("/api/stop")
    def stop():
        if not _stop_state["stopped"]:
            return ("", 204)
        return jsonify(_stop_state["final"] or {})

    @app.route("/api/control", methods=["POST", "GET"])
    def control():
        global _child_process
        if request.method == "GET":
            return jsonify({
                "running": _child_process is not None and _child_process.poll() is None,
                "child_pid": _child_process.pid if _child_process else None,
                "stopped": _stop_state["stopped"],
                "final": _stop_state["final"],
            })
        action = request.json.get("action") if request.is_json else request.form.get("action")
        if action == "stop":
            return _do_stop()
        if action == "start":
            return _do_start()
        return jsonify({"error": "unknown action"}), 400

    return app


_server = None
_server_thread = None
_stop_state = {"stopped": False, "final": None}
_child_process = None
_child_lock = threading.Lock()


def request_shutdown():
    """Mark the dashboard as stopped and capture final stats for the stop overlay."""
    conn = get_connection()
    try:
        total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        posted_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE posted_to_discord = 1").fetchone()[0]
        channel_count = conn.execute("SELECT COUNT(*) FROM discord_channels WHERE active = 1").fetchone()[0]
    finally:
        conn.close()
    _stop_state["stopped"] = True
    _stop_state["final"] = {
        "uptime": _format_duration(time.monotonic() - STARTED_AT),
        "total_jobs": total_jobs,
        "posted_jobs": posted_jobs,
        "channel_count": channel_count,
    }


def is_stopped():
    return _stop_state["stopped"]


def _format_duration(seconds):
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _do_stop():
    global _child_process
    with _child_lock:
        if _child_process is None or _child_process.poll() is not None:
            request_shutdown()
            return jsonify({"status": "already stopped"})
        try:
            if os.name == "nt":
                _child_process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                _child_process.terminate()
            try:
                _child_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _child_process.kill()
                _child_process.wait()
        except Exception:
            pass
        _child_process = None
    request_shutdown()
    return jsonify({"status": "stopped", "final": _stop_state["final"]})


def _do_start():
    global _child_process
    with _child_lock:
        if _child_process is not None and _child_process.poll() is None:
            return jsonify({"status": "already running", "pid": _child_process.pid})
        _stop_state["stopped"] = False
        _stop_state["final"] = None
        env = os.environ.copy()
        env["DISABLE_DASHBOARD"] = "1"
        _child_process = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=os.getcwd(),
            env=env,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
        )
    return jsonify({"status": "started", "pid": _child_process.pid})


def start_dashboard(open_browser=True):
    """Start the dashboard server in a background thread."""
    global _server, _server_thread
    app = create_app()
    _server = app
    _server_thread = threading.Thread(
        target=lambda: app.run(
            host=DASHBOARD_HOST,
            port=DASHBOARD_PORT,
            debug=False,
            use_reloader=False,
        ),
        name="dashboard",
        daemon=True,
    )
    _server_thread.start()
    url = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"
    print(f"[dashboard] Live at {url}")
    if open_browser:
        try:
            import webbrowser
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    return url
