import unittest

from src.upwork.session import UpworkSession


class UpworkSessionTests(unittest.TestCase):
    def test_refresh_is_due_at_configured_interval(self):
        now = [100.0]
        session = UpworkSession(refresh_after_seconds=60, clock=lambda: now[0])
        session.cookies = {"UniversalSearchNuxt_vt": "visitor-token"}
        session.user_agent = "test-agent"
        session.last_refresh = 50.0

        self.assertFalse(session.needs_refresh())
        now[0] = 110.0
        self.assertTrue(session.needs_refresh())

    def test_headers_use_the_dynamic_search_token(self):
        session = UpworkSession(refresh_after_seconds=60)
        session.cookies = {"UniversalSearchNuxt_vt": "fresh-token"}
        session.user_agent = "test-agent"
        session.last_refresh = session._clock()

        headers, cookies = session.get_headers()

        self.assertEqual(headers["Authorization"], "Bearer fresh-token")
        self.assertEqual(headers["User-Agent"], "test-agent")
        self.assertEqual(cookies["UniversalSearchNuxt_vt"], "fresh-token")


if __name__ == "__main__":
    unittest.main()
