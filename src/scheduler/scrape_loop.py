import time
import os
import logging
import signal
from datetime import datetime

from src.upwork.scraper import get_job_details, search_jobs
from src.storage.database import cleanup_old_jobs, init_db, job_exists, save_job
from src.logger import check_memory_usage

# Check dedicated-channel queries first; overlapping jobs route to their
# specialized channel before the general Python channel.
DEFAULT_SEARCH_QUERIES = (
    "full stack developer",
    ".net developer",
    "sqa engineer",
    "python developer",
)
PAGE_SIZE = 10
MAX_PAGES_PER_QUERY = int(os.getenv("UPWORK_MAX_PAGES_PER_QUERY", "3"))
CLEANUP_INTERVAL_SECONDS = int(os.getenv("UPWORK_CLEANUP_INTERVAL_SECONDS", "3600"))
logger = logging.getLogger("scraper")


def get_search_queries():
    """Read comma-separated search phrases, or use the project's defaults."""
    configured = os.getenv("UPWORK_SEARCH_QUERIES")
    if not configured:
        return DEFAULT_SEARCH_QUERIES
    return tuple(query.strip() for query in configured.split(",") if query.strip())

def clean_highlight_markers(text):
    """Upwork wraps matched search terms in H^...^H markers for their own UI highlighting.
    Strip them out so titles/descriptions read normally."""
    if not text:
        return text
    return text.replace("H^", "").replace("^H", "")

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


def format_budget(job_data):
    job_type = job_data.get("jobType", "")
    if job_type == "HOURLY":
        rate_min = job_data.get("hourlyBudgetMin")
        rate_max = job_data.get("hourlyBudgetMax")
        if rate_min and rate_max:
            return f"${rate_min}-${rate_max}/hr"
        elif rate_min:
            return f"${rate_min}+/hr"
        else:
            return "Rate not specified by client"
    else:
        amount = job_data.get("fixedPriceAmount") or {}
        return f"${amount.get('amount')}" if amount.get("amount") else "Budget not specified"


def description_preview(description, limit=280):
    """Flatten the full description into a Discord-friendly short preview."""
    text = " ".join((description or "").split())
    return f"{text[:limit].rstrip()}..." if len(text) > limit else text


def extract_detail_fields(response):
    """Extract the requested display fields from the job-details response."""
    response = response or {}
    details = (response.get("data") or {}).get("jobPubDetails") or {}
    opening = details.get("opening") or {}
    buyer = details.get("buyer") or {}
    buyer_extra = details.get("buyerExtra") or {}
    location = buyer.get("location") or {}
    stats = buyer.get("stats") or {}
    total_charges = stats.get("totalCharges") or {}
    client_location = ", ".join(filter(None, (location.get("city"), location.get("country"))))

    return {
        "posted_at": opening.get("postedOn") or opening.get("publishTime"),
        "detected_time": datetime.now().strftime("%H:%M"),
        "proposal_count": (opening.get("clientActivity") or {}).get("totalApplicants"),
        "payment_verified": buyer_extra.get("isPaymentMethodVerified"),
        "client_location": client_location or "Location not specified",
        "client_total_spent": total_charges.get("amount"),
        "description_preview": description_preview(opening.get("description")),
    }


def run_scrape_cycle(query="python developer", max_pages=MAX_PAGES_PER_QUERY):
    """Fetch up to ``max_pages`` result pages and save each job only once."""
    new_count = 0
    checked_count = 0
    for page_number in range(max_pages):
        offset = page_number * PAGE_SIZE
        try:
            results = search_jobs(query, offset=offset, count=PAGE_SIZE)
            jobs = extract_jobs_from_response(results)
        except Exception as exc:
            logger.exception("Error fetching %r page %d", query, page_number + 1)
            break

        if not jobs:
            break

        checked_count += len(jobs)
        for job in jobs:
            job_data = job.get("jobTile", {}).get("job", {})
            job_id = job_data.get("id")

            if not job_id or job_exists(job_id):
                continue

            title = clean_highlight_markers(job.get("title", "Untitled job"))
            ciphertext = job_data.get("ciphertext", "")
            job_type = job_data.get("jobType", "")
            budget_text = format_budget(job_data)
            level = job_data.get("contractorTier", "Not specified")
            job_url = f"https://www.upwork.com/jobs/{ciphertext}" if ciphertext else ""
            details = {"detected_time": datetime.now().strftime("%H:%M")}

            if ciphertext:
                try:
                    details = extract_detail_fields(get_job_details(ciphertext))
                except Exception as exc:
                    # A details failure should not discard a newly found job.
                    print(f"[scraper] Could not enrich job {job_id}: {exc}")

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


if __name__ == "__main__":
    init_db()
    def request_shutdown(signum, _frame):
        logger.info("Shutdown signal received (%s)", signal.Signals(signum).name)
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)
    try:
        last_cleanup = 0
        logger.info("Starting scrape loop")
        while True:
            for search_query in get_search_queries():
                run_scrape_cycle(search_query)
            now = time.monotonic()
            if now - last_cleanup >= CLEANUP_INTERVAL_SECONDS:
                try:
                    deleted = cleanup_old_jobs()
                    logger.info("Database cleanup removed %d old jobs", deleted)
                    check_memory_usage(logger)
                except Exception:
                    logger.exception("Maintenance task failed")
                last_cleanup = now
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Scraper stopped cleanly")
