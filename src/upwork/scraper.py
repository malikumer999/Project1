"""Upwork GraphQL requests with proactive and rejection-triggered refresh."""

from src.upwork.graphql import DETAILS_QUERY, SEARCH_QUERY
from src.upwork.session import UpworkSession

GRAPHQL_URL = "https://www.upwork.com/api/graphql/v1"
session_manager = UpworkSession()


def _post_graphql(alias, payload):
    """Send a request; renew once if the current visitor token is rejected."""
    import requests

    for attempt in range(2):
        headers, cookies = session_manager.get_headers(force_refresh=attempt == 1)
        response = requests.post(GRAPHQL_URL, params={"alias": alias}, headers=headers, cookies=cookies, json=payload, timeout=30)
        if response.status_code not in (401, 403):
            response.raise_for_status()
            return response.json()
        print("[upwork] Visitor session rejected; refreshing and retrying once.")
    raise RuntimeError("Upwork rejected the refreshed visitor session.")


def search_jobs(query, offset=0, count=10):
    return _post_graphql("visitorJobSearch", {
        "query": SEARCH_QUERY,
        "variables": {"requestVariables": {"userQuery": query, "sort": "relevance+desc", "highlight": True, "paging": {"offset": offset, "count": count}}},
    })


def get_job_details(job_id):
    return _post_graphql("gql-query-get-visitor-job-details", {
        "query": DETAILS_QUERY, "variables": {"id": job_id, "isLoggedIn": False},
    })
