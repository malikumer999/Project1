"""Legacy compatibility API; browser-generated tokens are never hard-coded."""

from src.upwork.session import UpworkSession

_session = UpworkSession()


def get_current_session():
    return _session.get_headers()
