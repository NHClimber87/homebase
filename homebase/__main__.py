"""Entry point: `python -m homebase` (and the PyInstaller .exe target).

Loads config, hydrates the cache (page renders immediately, never cold-blank), starts the
background refresher, and serves the loopback page. Prints the fixed Brave-homepage URL.
"""
from __future__ import annotations

import argparse
import sys
import webbrowser

from . import APP_NAME, __version__
from .app import App
from .config import load_config
from .server import bind_host, serve


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="homebase", description=f"{APP_NAME} — private start page")
    parser.add_argument("--open", action="store_true", help="open the page in the default browser")
    parser.add_argument("--no-refresh", action="store_true", help="serve cache only; no background fetch")
    parser.add_argument("--install", action="store_true",
                        help="write/repair config, pin the port, print the homepage URL, then exit")
    parser.add_argument("--port", type=int, default=None, help="pin this port at install time")
    parser.add_argument("--print-url", action="store_true", help="print the fixed homepage URL and exit")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.port is not None and 1 <= args.port <= 65535:
        cfg.port = args.port

    def _url() -> str:
        return f"http://127.0.0.1:{cfg.port}/" if bind_host(cfg.bind) != "::1" else f"http://[::1]:{cfg.port}/"

    # Install-time port pin: the port is fixed for the life of the install so the Brave
    # homepage URL never changes (§10). The installer calls this, then registers autostart.
    if args.install:
        from .config import save_config
        save_config(cfg)
        sys.stdout.write(_url() + "\n")
        return 0
    if args.print_url:
        sys.stdout.write(_url() + "\n")
        return 0

    app = App(config=cfg)
    url = _url()

    try:
        server = serve(app)
    except OSError as exc:
        sys.stderr.write(
            f"{APP_NAME}: could not bind {bind_host(cfg.bind)}:{cfg.port} ({exc}).\n"
            f"The port may be in use. Re-run the installer to pick a free port.\n"
        )
        return 2

    for w in cfg.warnings:
        sys.stderr.write(f"{APP_NAME} notice: {w}\n")

    if not args.no_refresh:
        app.start_background()

    sys.stdout.write(f"{APP_NAME} {__version__} serving {url}\n")
    sys.stdout.write("Set this as your Brave homepage. Press Ctrl+C to stop.\n")
    sys.stdout.flush()
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stdout.write("\nShutting down…\n")
    finally:
        app.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
