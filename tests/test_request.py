"""Offline smoke test for the public search request payload."""

import unittest
from unittest.mock import patch

from src.upwork import scraper


class SearchRequestTests(unittest.TestCase):
    @patch("src.upwork.scraper._post_graphql")
    def test_search_uses_the_visitor_search_shape(self, post_graphql):
        scraper.search_jobs("python developer", offset=5, count=20)

        alias, payload = post_graphql.call_args.args
        request = payload["variables"]["requestVariables"]
        self.assertEqual(alias, "visitorJobSearch")
        self.assertEqual(request["userQuery"], "python developer")
        self.assertEqual(request["paging"], {"offset": 5, "count": 20})
