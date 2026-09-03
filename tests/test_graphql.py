"""GraphQL query regression checks; no live HTTP calls or credentials."""

import unittest

from src.upwork.graphql import DETAILS_QUERY, SEARCH_QUERY


class GraphQLQueryTests(unittest.TestCase):
    def test_search_query_defines_visitor_search(self):
        self.assertIn("query VisitorJobSearch", SEARCH_QUERY)

    def test_details_query_defines_public_details(self):
        self.assertIn("query JobPubDetailsQuery", DETAILS_QUERY)
