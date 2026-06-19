"""HomeBase — a private, self-hosted browser start page.

See SPEC-HomeBase-v2.md. The whole point: the page runs on the owner's machine,
fetches directly from free keyless sources, phones home to nobody, and is not
readable by anything else on the machine or LAN.

Runtime is stdlib-only (urllib + ssl + http.server) so the Windows build is a
single self-contained .exe with a minimal supply chain.
"""

__version__ = "1.0.0"
APP_NAME = "HomeBase"
