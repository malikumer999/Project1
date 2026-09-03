import requests

from src.upwork.graphql import SEARCH_QUERY
from src.upwork.session import UpworkSession


GRAPHQL_URL = "https://www.upwork.com/api/graphql/v1"

session_manager = UpworkSession()


def search_jobs(query, offset=0, count=10):

    for attempt in range(2):

        # Get current browser session
        cookies, user_agent = session_manager.get_session()

        headers = {
            "User-Agent": user_agent,
            "Accept": "*/*",
            "Content-Type": "application/json",
        }

        params = {
            "alias": "visitorJobSearch",
        }

        payload = {
            "query": SEARCH_QUERY,
            "variables": {
                "requestVariables": {
                    "searchTerm": query,
                    "paging": {
                        "offset": offset,
                        "count": count
                    }
                }
            }
        }

        response = requests.post(
            GRAPHQL_URL,
            params=params,
            headers=headers,
            cookies=cookies,
            json=payload,
            timeout=30,
        )

        print("STATUS:", response.status_code)

        # Session expired
        if response.status_code == 401:
            print("Session expired. Refreshing session...")

            session_manager.refresh()
            continue

        response.raise_for_status()

        return response.json()

    raise RuntimeError("Unable to establish a valid session")

if __name__ == "__main__":
    result = search_jobs("python developer", count=10)

    print("\n===== RESULT =====")
    print(result)