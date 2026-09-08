import time
import os
import logging
import signal
from datetime import datetime

from dotenv import dotenv_values, load_dotenv

from src.upwork.scraper import NetworkUnavailableError, get_job_details, search_jobs
from src.storage.database import cleanup_old_jobs, get_channel_mappings, init_db, is_channel_deleted_for_query, job_exists, save_job
from src.logger import check_memory_usage, format_uptime

UPTIME_REPORT_INTERVAL_SECONDS = 3600

load_dotenv()
_initial_search_queries = os.getenv("UPWORK_SEARCH_QUERIES")

# Check dedicated-channel queries first; overlapping jobs route to their
# specialized channel before the general Python channel.
DEFAULT_SEARCH_QUERIES = (
    "full stack developer",
    ".net developer",
    "sqa engineer",
    "python developer",
)
PAGE_SIZE = 10
MAX_PAGES_PER_QUERY = int(os.getenv("UPWORK_MAX_PAGES_PER_QUERY", "5"))
# Jobs older than this are never saved or posted (default: 30 days).
MAX_JOB_AGE_SECONDS = int(os.getenv("UPWORK_MAX_JOB_AGE_DAYS", "30")) * 86_400
CLEANUP_INTERVAL_SECONDS = int(os.getenv("UPWORK_CLEANUP_INTERVAL_SECONDS", "3600"))
logger = logging.getLogger("scraper")


def get_search_queries():
    """Read comma-separated search phrases, or use the project's defaults."""
    load_dotenv(override=False)
    configured = os.getenv("UPWORK_SEARCH_QUERIES")
    file_configured = dotenv_values().get("UPWORK_SEARCH_QUERIES")
    if configured == _initial_search_queries and file_configured is not None:
        configured = file_configured
    if not configured:
        return DEFAULT_SEARCH_QUERIES
    queries = tuple(query.strip() for query in configured.split(",") if query.strip())
    # When one query is a word-superset of another (e.g. "mern stack" and
    # "mern stack developer"), keep only ONE so we don't scrape the same jobs
    # twice. Prefer the one that has an active channel mapping; if neither does,
    # keep the longer (more specific) one.
    word_sets = [set(q.casefold().replace(".", " ").split()) for q in queries]
    channel_by_query = {
        (source_query or "").casefold(): True
        for source_query, _cid, _name, active in get_channel_mappings()
        if active
    }
    drop = set()
    for i in range(len(queries)):
        if i in drop:
            continue
        for j in range(len(queries)):
            if i == j or j in drop:
                continue
            if word_sets[i] < word_sets[j] or word_sets[j] < word_sets[i]:
                i_mapped = channel_by_query.get(queries[i].casefold())
                j_mapped = channel_by_query.get(queries[j].casefold())
                if i_mapped and not j_mapped:
                    drop.add(j)
                elif j_mapped and not i_mapped:
                    drop.add(i)
                    break
                elif word_sets[i] < word_sets[j]:
                    drop.add(i)
                    break
                else:
                    drop.add(j)
    queries = tuple(q for i, q in enumerate(queries) if i not in drop)
    # Dedupe by casefold so trailing duplicates collapse.
    seen = set()
    unique = []
    for q in queries:
        key = q.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(q)
    queries = tuple(unique)
    # Two different queries can still point at the same Discord channel (e.g.
    # "full stack developer" and "fullstackjobs"); scrape each channel once.
    seen_channel_ids = set()
    unique_queries = []
    channel_by_query = {
        (source_query or "").casefold(): channel_id
        for source_query, channel_id, _name, active in get_channel_mappings()
        if active
    }
    for q in queries:
        channel_id = channel_by_query.get(q.casefold())
        if channel_id and channel_id in seen_channel_ids:
            continue
        if channel_id:
            seen_channel_ids.add(channel_id)
        unique_queries.append(q)
    return tuple(unique_queries)

def clean_highlight_markers(text):
    """Upwork wraps matched search terms in H^...^H markers for their own UI highlighting.
    Strip them out so titles/descriptions read normally."""
    if not text:
        return text
    return text.replace("H^", "").replace("^H", "")


def job_matches_query(job, query):
    """Require every meaningful query word to appear in the search result."""
    #used to filter out irrelevant jobs that Upwork returns for a query, e.g. "python developer" returning "python developer and seo expert"
    query_words = {
        word for word in query.casefold().replace(".", " ").split() if word
    }
    skill_text = " ".join(
        skill.get("prefLabel", "")
        for skill in job.get("ontologySkills", [])
        if isinstance(skill, dict)
    )
    searchable_text = " ".join(
        (
            job.get("title", ""),
            job.get("description", ""),
            skill_text,
        )
    ).casefold().replace(".", " ")
    return bool(query_words) and all(word in searchable_text for word in query_words)

def extract_jobs_from_response(results):
    """Pull the flat list of job dicts out of the raw GraphQL response."""
    return (
        results.get("data", {})
        .get("search", {})
        .get("universalSearchNuxt", {})
        .get("visitorJobSearchV1", {})
        .get("results", [])
    )


def extract_paging_from_response(results):
    """Return Upwork's search paging metadata, if provided."""
    return (
        results.get("data", {})
        .get("search", {})
        .get("universalSearchNuxt", {})
        .get("visitorJobSearchV1", {})
        .get("paging", {})
    )


def _format_money(value):
    """Render a numeric value as a clean dollar amount (no trailing .0 noise)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value) if value is not None else "Not specified"
    if number.is_integer():
        return f"${int(number):,}"
    return f"${number:,.2f}"


def format_budget(job_data):
    job_type = job_data.get("jobType", "")
    if job_type == "HOURLY":
        rate_min = job_data.get("hourlyBudgetMin")
        rate_max = job_data.get("hourlyBudgetMax")
        if rate_min and rate_max:
            return f"{_format_money(rate_min)}-{_format_money(rate_max)}/hr"
        elif rate_min:
            return f"{_format_money(rate_min)}+/hr"
        else:
            return "Rate not specified by client"
    else:
        amount = job_data.get("fixedPriceAmount") or {}
        return _format_money(amount.get("amount")) if amount.get("amount") else "Budget not specified"


def description_preview(description, limit=280):
    """Flatten the full description into a Discord-friendly short preview."""
    text = " ".join((description or "").split())
    return f"{text[:limit].rstrip()}..." if len(text) > limit else text


def _normalize_posted_at(value):
    """Convert Upwork posted_at to a Unix timestamp in seconds (float)."""
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            return timestamp
        text = str(value)
        if text.replace(".", "", 1).isdigit():
            timestamp = float(text)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            return timestamp
        posted = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return posted.timestamp()
    except (TypeError, ValueError, OSError):
        return None


def extract_detail_fields(response):
    """Extract the requested display fields from the job-details response."""
    response = response or {}
    details = (response.get("data") or {}).get("jobPubDetails") or {}
    opening = details.get("opening") or {}
    unavailable = bool(response.get("errors")) or not opening
    buyer = details.get("buyer") or {}
    buyer_extra = details.get("buyerExtra") or {}
    location = buyer.get("location") or {}
    stats = buyer.get("stats") or {}
    total_charges = stats.get("totalCharges") or {}
    buyer_jobs = buyer.get("jobs") or {}
    company = buyer.get("company") or {}
    client_location = ", ".join(filter(None, (location.get("city"), location.get("country"))))
    full_description = clean_highlight_markers(opening.get("description") or "")
    engagement = opening.get("engagementDuration") or {}
    category = opening.get("category") or {}
    budget = opening.get("budget") or {}
    segmentation = opening.get("segmentationData") or []
    skills = []
    seen_skills = set()
    for item in segmentation:
        if not isinstance(item, dict):
            continue
        label = item.get("label") or item.get("name")
        if not label or label in seen_skills:
            continue
        if item.get("type") in ("skill", "SKILL"):
            skills.append(label)
            seen_skills.add(label)
    sands_skills = (opening.get("sandsData") or {}).get("ontologySkills") or []
    for item in sands_skills:
        if not isinstance(item, dict):
            continue
        label = item.get("prefLabel")
        if label and label not in seen_skills:
            skills.append(label)
            seen_skills.add(label)
    client_activity = opening.get("clientActivity") or {}
    workload = opening.get("workload") or ""
    extended_budget = opening.get("extendedBudgetInfo") or {}

    return {
        "posted_at": _normalize_posted_at(opening.get("postedOn") or opening.get("publishTime")),
        "detected_time": datetime.now().strftime("%H:%M"),
        "proposal_count": client_activity.get("totalApplicants"),
        "total_hired": client_activity.get("totalHired"),
        "interviewing": client_activity.get("unansweredInvites"),
        "invites_sent": client_activity.get("invitationsSent"),
        "payment_verified": buyer_extra.get("isPaymentMethodVerified"),
        "client_location": client_location or "Location not specified",
        "client_total_spent": total_charges.get("amount"),
        "client_total_assignments": stats.get("totalAssignments"),
        "client_hours_count": stats.get("hoursCount"),
        "client_feedback_count": stats.get("feedbackCount"),
        "client_score": stats.get("score"),
        "client_jobs_posted": buyer_jobs.get("postedCount"),
        "client_jobs_open": buyer_jobs.get("openCount"),
        "client_member_since": _normalize_posted_at(company.get("contractDate")),
        "client_country_timezone": location.get("countryTimezone"),
        "client_company_name": company.get("name"),
        "description_preview": description_preview(opening.get("description")),
        "full_description": full_description,
        "project_duration": engagement.get("label"),
        "experience_level": (opening.get("info") or {}).get("contractorTier"),
        "job_type_label": (opening.get("info") or {}).get("type") or budget.get("currencyCode"),
        "workload": workload,
        "contract_to_hire": bool(opening.get("contractToHire")),
        "category_name": category.get("name"),
        "skills": skills,
        "is_private": unavailable,
    }


def _search_posted_too_old(job, max_age_seconds=MAX_JOB_AGE_SECONDS):
    """Return True when the search result's own timestamp is older than max_age."""
    job_data = job.get("jobTile", {}).get("job", {}) or {}
    for key in ("publishTime", "createTime", "sourcingTimestamp"):
        value = job_data.get(key)
        if value is None:
            continue
        try:
            ts = float(value)
            if ts > 10_000_000_000:
                ts /= 1000
            from time import time as _now
            if (_now() - ts) > max_age_seconds:
                return True
            return False
        except (TypeError, ValueError):
            continue
    return False


def _is_posted_too_old(details, max_age_seconds=MAX_JOB_AGE_SECONDS):
    """Return True when the parsed posted_at is older than max_age_seconds."""
    posted_at = (details or {}).get("posted_at")
    if posted_at is None:
        return False
    try:
        if isinstance(posted_at, (int, float)):
            ts = float(posted_at)
        else:
            text = str(posted_at)
            if text.replace(".", "", 1).isdigit():
                ts = float(text)
            else:
                ts = datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        from time import time as _now
        return (_now() - ts) > max_age_seconds
    except (TypeError, ValueError, OSError):
        return False


def run_scrape_cycle(query="python developer", max_pages=MAX_PAGES_PER_QUERY):
    """Fetch up to ``max_pages`` result pages and save each job only once."""
    new_count = 0
    checked_count = 0
    for page_number in range(max_pages):
        offset = page_number * PAGE_SIZE
        try:
            results = search_jobs(query, offset=offset, count=PAGE_SIZE)
            jobs = extract_jobs_from_response(results)
        except NetworkUnavailableError as exc:
            logger.warning("Network is unstable while checking %r: %s", query, exc)
            return False
        except Exception as exc:
            logger.exception("Error fetching %r page %d", query, page_number + 1)
            break

        if not jobs:
            break

        checked_count += len(jobs)
        for job in jobs:
            job_data = job.get("jobTile", {}).get("job", {})
            job_id = job_data.get("id")

            if not job_id:
                continue

            if job_exists(job_id):
                logger.debug("DEDUP: job %s already exists in DB, skipping", job_id)
                continue

            search_ts = None
            job_data_raw = job.get("jobTile", {}).get("job", {}) or {}
            for key in ("publishTime", "createTime", "sourcingTimestamp"):
                val = job_data_raw.get(key)
                if val is not None:
                    try:
                        search_ts = float(val)
                        if search_ts > 10_000_000_000:
                            search_ts /= 1000
                    except (TypeError, ValueError):
                        pass
                    break
            search_age_days = ((time.time() if hasattr(time, 'time') else __import__('time').time()) - search_ts) / 86400 if search_ts else None
            too_old_search = _search_posted_too_old(job)
            if too_old_search:
                age_str = f"{search_age_days:.1f} days" if search_age_days is not None else "unknown"
                logger.debug(
                    "AGE FILTER (search): skipping job %s — search timestamp=%.3f, age=%s, limit=30 days",
                    job_id, search_ts or -1, age_str,
                )
                continue

            ciphertext = job_data.get("ciphertext", "")
            if ciphertext and not job_matches_query(job, query):
                logger.debug(
                    "QUERY MISMATCH: job %s does not match query '%s' (title=%r)",
                    job_id, query, job.get("title", "")[:60],
                )
                continue

            title = clean_highlight_markers(job.get("title", "Untitled job"))
            if not ciphertext:
                logger.warning(
                    "Job %s appears private or unavailable; recording it without posting a broken link.",
                    job_id,
                )
            job_type = job_data.get("jobType", "")
            budget_text = format_budget(job_data)
            level = job_data.get("contractorTier", "Not specified")
            job_url = f"https://www.upwork.com/jobs/{ciphertext}" if ciphertext else ""
            details = {
                "posted_at": None,
                "detected_time": datetime.now().strftime("%H:%M"),
                "is_private": not bool(ciphertext),
            }

            if ciphertext:
                try:
                    details = extract_detail_fields(get_job_details(ciphertext))
                    if details.get("is_private"):
                        # Transient failures (network, Selenium hiccup) look
                        # identical to a genuinely private job. Retry once
                        # before accepting the private/unavailable verdict.
                        time.sleep(2)
                        details = extract_detail_fields(get_job_details(ciphertext))
                except Exception as exc:
                    logger.warning("Could not enrich job %s: %s", job_id, exc)

            # A job with a ciphertext has a public page: never route it to the
            # private/unavailable channel just because detail enrichment failed.
            # The bot backfills missing details before posting.
            if ciphertext and details.get("is_private"):
                details["is_private"] = 0

            if _is_posted_too_old(details):
                posted_at = details.get("posted_at")
                age_days = None
                try:
                    if isinstance(posted_at, (int, float)):
                        ts = float(posted_at)
                    else:
                        text = str(posted_at)
                        if text.replace(".", "", 1).isdigit():
                            ts = float(text)
                        else:
                            ts = datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
                    age_days = (time.time() - ts) / 86400
                except Exception:
                    pass
                age_str = f"{age_days:.1f} days" if age_days is not None else "unknown"
                logger.debug(
                    "AGE FILTER (details): skipping job %s — posted_at=%s, age=%s, limit=30 days",
                    job_id, posted_at, age_str,
                )
                continue

            try:
                save_job(job_id, title, ciphertext, job_type, budget_text, level, job_url, query, details)
                new_count += 1
            except Exception:
                logger.exception("Could not save job %s", job_id)

        paging = extract_paging_from_response(results)
        total = paging.get("total")
        if len(jobs) < PAGE_SIZE or (isinstance(total, int) and offset + len(jobs) >= total):
            break

    logger.info("%s: checked %d jobs, saved %d new", query, checked_count, new_count)
    return True


def run_scrape_loop(install_signal_handlers=True):
    """Run the scrape cycle forever. When ``install_signal_handlers`` is False
    (e.g. launched from a background thread) the caller handles signals."""
    init_db()
    if install_signal_handlers:
        def request_shutdown(signum, _frame):
            logger.info("Shutdown signal received (%s)", signal.Signals(signum).name)
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, request_shutdown)
        signal.signal(signal.SIGTERM, request_shutdown)
    try:
        last_cleanup = 0
        started_at = time.monotonic()
        last_uptime_report = started_at
        logger.info("Starting scrape loop")
        warned_deleted_queries = set()
        while True:
            network_available = True
            for search_query in get_search_queries():
                if is_channel_deleted_for_query(search_query):
                    if search_query not in warned_deleted_queries:
                        logger.info(
                            "Skipping '%s': its Discord channel was deleted",
                            search_query,
                        )
                        warned_deleted_queries.add(search_query)
                    continue
                if search_query in warned_deleted_queries:
                    warned_deleted_queries.discard(search_query)
                if not run_scrape_cycle(search_query):
                    network_available = False
                    break
            if not network_available:
                logger.warning("Network is unstable; holding scraper and retrying in 30 seconds.")
                time.sleep(30)
                continue
            now = time.monotonic()
            if now - last_uptime_report >= UPTIME_REPORT_INTERVAL_SECONDS:
                logger.info(
                    "System running time: %s",
                    format_uptime(now - started_at),
                )
                last_uptime_report = now
            if now - last_cleanup >= CLEANUP_INTERVAL_SECONDS:
                try:
                    cleanup_old_jobs()
                    check_memory_usage(logger)
                except Exception:
                    logger.exception("Maintenance task failed")
                last_cleanup = now
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Scraper stopped cleanly")


if __name__ == "__main__":
    import os
    import signal
    import time

    from src.dashboard import request_shutdown, start_dashboard
    from src.storage.database import get_connection

    start_dashboard(open_browser=True)
    print("[scraper] Dashboard opened in your default browser.")

    started_at = time.monotonic()

    def _handle_shutdown(signum, _frame):
        elapsed = time.monotonic() - started_at
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        print(f"\n[scraper] Stop signal received (signal {signum}). Uptime: {h:02d}:{m:02d}:{s:02d}")
        request_shutdown()
        conn = get_connection()
        try:
            total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            posted_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE posted_to_discord = 1").fetchone()[0]
            channel_count = conn.execute("SELECT COUNT(*) FROM discord_channels WHERE active = 1").fetchone()[0]
        finally:
            conn.close()
        print("=" * 60)
        print("  SCRAPER STOPPED")
        print("=" * 60)
        print(f"  Discord Channels (active): {channel_count}")
        print(f"  Total Jobs:                {total_jobs}")
        print(f"  Posted to Discord:         {posted_jobs}")
        print("=" * 60)
        os._exit(0)

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)
    run_scrape_loop()
