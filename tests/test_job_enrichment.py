import unittest
from unittest.mock import patch

from src.scheduler.scrape_loop import DEFAULT_SEARCH_QUERIES, description_preview, extract_detail_fields, get_search_queries


class JobEnrichmentTests(unittest.TestCase):
    def test_extract_detail_fields(self):
        response = {
            "data": {
                "jobPubDetails": {
                    "opening": {
                        "postedOn": "2026-09-03T10:00:00Z",
                        "description": "A " * 200,
                        "clientActivity": {"totalApplicants": 2},
                    },
                    "buyer": {
                        "location": {"city": "New York", "country": "United States"},
                        "stats": {"totalCharges": {"amount": 15234}},
                    },
                    "buyerExtra": {"isPaymentMethodVerified": True},
                }
            }
        }

        details = extract_detail_fields(response)

        self.assertEqual(details["proposal_count"], 2)
        self.assertTrue(details["payment_verified"])
        self.assertEqual(details["client_location"], "New York, United States")
        self.assertEqual(details["client_total_spent"], 15234)
        self.assertTrue(details["description_preview"].endswith("..."))

    def test_short_description_is_not_truncated(self):
        self.assertEqual(description_preview("Short description"), "Short description")

    def test_null_details_response_uses_safe_defaults(self):
        details = extract_detail_fields({"data": None})

        self.assertEqual(details["client_location"], "Location not specified")
        self.assertIsNone(details["proposal_count"])

    @patch.dict("os.environ", {"UPWORK_SEARCH_QUERIES": "python developer, full stack developer"})
    def test_configured_search_queries(self):
        self.assertEqual(get_search_queries(), ("python developer", "full stack developer"))

    def test_default_search_queries_include_specialized_channels(self):
        self.assertEqual(
            DEFAULT_SEARCH_QUERIES,
            ("full stack developer", ".net developer", "sqa engineer", "python developer"),
        )
