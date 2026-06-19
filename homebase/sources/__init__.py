"""Source adapter registry. Importing this package registers every adapter.

Vetted alternate list (for the settings-UI source-swap, AC §10):
  - team: nhl, mlb
  - markets: stooq (delayed) <-> yahoo (live)
  - news/headlines: google-news (aggregator), direct RSS via card config
"""
from .base import FetchSpec, SourceAdapter, all_sources, get_adapter, register  # noqa: F401

# Import for side-effect registration.
from . import nhl, mlb, headlines, stooq, yahoo  # noqa: F401,E402

# Which alternate sources a card type may be swapped to (non-dev recovery).
VETTED_ALTERNATES = {
    "markets": ["stooq", "yahoo"],
    "team": ["nhl", "mlb"],
    "headlines": ["google-news"],
}
