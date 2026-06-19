"""AC-OPS — non-developer Windows operability. Structural + behavioral where gradable here;
the clean-Windows .exe run is deferred (no Windows in CI) with an explicit reason."""
from __future__ import annotations

from pathlib import Path

import pytest

from homebase.config import default_config
from homebase.server import serve

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
INSTALL = (PKG / "install-windows.ps1").read_text()
SPEC = (PKG / "homebase.spec").read_text()
README = (ROOT / "README.md").read_text()
APP_JS = (ROOT / "homebase" / "static" / "app.js").read_text()
INDEX = (ROOT / "homebase" / "static" / "index.html").read_text()
MAIN = (ROOT / "homebase" / "__main__.py").read_text()
REQS = (ROOT / "requirements.txt").read_text()


def test_ac_ops_packaging_artifacts_exist():
    for f in ["homebase.spec", "install-windows.ps1", "uninstall-windows.ps1", "entry.py", "BUILD.md"]:
        assert (PKG / f).is_file(), f"missing packaging artifact {f}"
    assert (ROOT / "README.md").is_file()


def test_ac_ops_single_exe_no_separate_runtime():
    # one self-contained .exe; runtime deps are stdlib-only (just tzdata on Windows)
    assert "HomeBase" in SPEC and "EXE(" in SPEC
    # no heavy framework / no "install Python/Node first"
    lowered = REQS.lower()
    for banned in ["flask", "django", "fastapi", "node", "requests", "aiohttp"]:
        assert banned not in lowered
    assert "tzdata" in lowered


def test_ac_ops_autostart_registered_by_installer():
    assert "schtasks" in INSTALL and "ONLOGON" in INSTALL
    assert "--install" in INSTALL and "--port" in INSTALL  # config + port pinned at install


def test_ac_ops_port_pinned_and_url_fixed():
    # the installer pins a chosen port; the runtime supports it
    assert '"--install"' in MAIN or "--install" in MAIN
    assert "--port" in MAIN
    # the server binds exactly the configured port (no runtime port change)
    cfg = default_config()
    cfg.port = 8791
    from homebase.app import App
    from homebase.fetcher import ReplayFetcher
    from homebase.clock import FrozenClock
    import datetime as dt
    app = App(config=cfg, fetcher=ReplayFetcher(routes=lambda u: None),
              clock=FrozenClock(dt.datetime(2026, 6, 17, tzinfo=dt.timezone.utc)))
    srv = serve(app)
    try:
        assert srv.socket.getsockname()[1] == 8791
    finally:
        srv.server_close()


def test_ac_ops_readme_brave_steps_verbatim():
    for needle in ["On startup", "Show home button", "127.0.0.1", "Settings"]:
        assert needle in README, f"README missing Brave step: {needle}"


def test_ac_ops_boot_race_handled():
    # the page shows a "starting…" state and the poller retries rather than dying on connect
    assert "starting" in INDEX.lower()
    assert "catch" in APP_JS and "poll" in APP_JS
    # the server entry prints a clear message if the port is busy (not a silent crash)
    assert "in use" in MAIN.lower()


@pytest.mark.skip(reason="AC-OPS clean-Windows run (one .exe, Task Scheduler, reboot, Brave) "
                         "must be verified on a real Windows machine — no Windows host here.")
def test_ac_ops_clean_windows_install():  # pragma: no cover
    raise AssertionError("manual: install on a clean Windows box, reboot, open Brave")
