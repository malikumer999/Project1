"""Compatibility wrapper for callers that only need fresh browser cookies."""

from src.upwork.session import UpworkSession


def refresh_session():
    return UpworkSession().get_session(force_refresh=True)
