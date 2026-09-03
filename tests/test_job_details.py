"""Offline smoke test for the public job-details request payload."""

import unittest
from unittest.mock import patch

from src.upwork import scraper


class JobDetailsRequestTests(unittest.TestCase):
    @patch("src.upwork.scraper._post_graphql")
    def test_details_request_is_anonymous(self, post_graphql):
        scraper.get_job_details("~012345")

        alias, payload = post_graphql.call_args.args
        self.assertEqual(alias, "gql-query-get-visitor-job-details")
        self.assertEqual(payload["variables"], {"id": "~012345", "isLoggedIn": False})
