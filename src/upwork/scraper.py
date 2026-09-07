"""Upwork GraphQL requests with proactive and rejection-triggered refresh."""

import logging

import requests
import urllib3

from src.upwork.graphql import DETAILS_QUERY, SEARCH_QUERY
from src.upwork.session import UpworkSession

GRAPHQL_URL = "https://www.upwork.com/api/graphql/v1"
session_manager = UpworkSession()
logger = logging.getLogger("upwork")


class NetworkUnavailableError(RuntimeError):
    """Raised when Upwork cannot be reached or its response cannot be read."""


def _post_graphql(alias, payload):
    """Send a request; renew once if the current visitor token is rejected."""
    for attempt in range(2):
        try:
            headers, cookies = session_manager.get_headers(force_refresh=attempt == 1)
            response = requests.post(
                GRAPHQL_URL, 
                params={"alias": alias},
                headers=headers,
                cookies=cookies,
                json=payload,
                timeout=30)
        except (requests.exceptions.RequestException, urllib3.exceptions.HTTPError, TimeoutError, OSError) as exc:
            raise NetworkUnavailableError(
                "Network is unavailable or unstable; retrying later."
            ) from exc
        if response.status_code not in (401, 403):
            try:
                response.raise_for_status()
                return response.json()
            except (requests.exceptions.RequestException, ValueError) as exc:
                raise NetworkUnavailableError(
                    "Upwork response could not be read; retrying later."
                ) from exc
        logger.warning("Visitor session rejected; refreshing and retrying once.")
    raise NetworkUnavailableError("Upwork rejected the refreshed visitor session.")


def search_jobs(query, offset=0, count=10):
    return _post_graphql("visitorJobSearch", {
        "query": SEARCH_QUERY,
        "variables": {"requestVariables": {"userQuery": query, "sort": "relevance+desc", "highlight": True, "paging": {"offset": offset, "count": count}}},
    })


def get_job_details(job_id):
    return _post_graphql("gql-query-get-visitor-job-details", {
        "query": DETAILS_QUERY, "variables": {"id": job_id, "isLoggedIn": False},
    })
