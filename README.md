# HomeBase

A private, self-hosted browser start page that aggregates what you follow — **NY Rangers**,
**NY Mets**, the **markets** (Dow / S&P 500 / Nasdaq + any symbols you add), and the
**latest AP + Reuters headlines** — without any aggregator profiling you or selling your
interests for ads.

It runs **on your own machine**, fetches from free sources directly, and **phones home to
nobody**. No account, no tracker, no analytics. Nothing else on your computer or network
can read it.

---

## For the user (Windows) — install in two minutes

1. Download the HomeBase folder (it contains `HomeBase.exe` and `install-windows.ps1`).
2. Right-click **`install-windows.ps1`** → **Run with PowerShell**.
   *(If Windows blocks it: open PowerShell and run
   `powershell -ExecutionPolicy Bypass -File install-windows.ps1`.)*
3. The installer prints **your homepage address**, e.g. `http://127.0.0.1:8777/`.
   Write it down — it stays the same forever.

### Set it as your Brave homepage
1. Open Brave → **Settings** (`brave://settings/`).
2. **Get started** → **On startup** → **Open a specific page or set of pages** → **Add a new page** → paste your address (e.g. `http://127.0.0.1:8777/`).
3. **Appearance** → turn on **Show home button** → set it to the same address.

That's it. HomeBase starts automatically every time you log in, and Brave opens it on launch.

### Using it
- **Refresh** button updates everything now (it's polite to your sources — it won't let you hammer them).
- **Settings** lets you add stock symbols, swap a market source (Stooq ↔ Yahoo), and toggle
  any news source on/off — all without touching code.
- Every card shows **when its data is from**; if a source is down you'll see *"showing last
  known data"*, never a wrong number dressed up as live.

### Remove it
Run **`uninstall-windows.ps1`**. It stops HomeBase, removes the auto-start, and deletes all
its files (including your saved interests). No trace left.

---

## Your privacy (read this — it's the whole point)

- **It runs only on your machine** and binds the **loopback interface** (`127.0.0.1`). Other
  devices on your Wi-Fi, and websites you visit, **cannot reach or read it**.
- **No aggregator profile.** The combination of what you follow (your teams + tickers +
  topics) lives **only in a file on your PC**. No company ever sees the *combination*, and
  no one sells it.
- **No accounts, no trackers, no analytics, no telemetry.** The app uploads **nothing**.
- **News honesty (important).** Free, keyless feeds for **AP, Reuters, and Rangers news no
  longer exist**. The only keyless way to get them is a **Google News** search, so those
  items are fetched through Google and **badged "via Google."** That means Google sees those
  specific searches. If you don't want that, open **Settings** and toggle those sources
  **off** — they then send **nothing** to Google. **Mets news** comes **directly** from
  MLB and never touches Google.
- **What a source still sees:** because the fetch comes from your PC, each provider (NHL,
  MLB, Stooq, Google) sees its *own* request from your IP — the same as if you visited their
  site. What changes is that **no one gets the aggregate, and no one sells it.**

---

## Markets note
The free market sources (Stooq, Yahoo) **block data-center IPs** but work fine from a normal
home internet connection. On the friend's home Windows machine the markets card works; if it
ever can't reach a source, that card shows a clear explanation instead of fake numbers.
(See `SPEC` §12 — the markets card is gated on a one-time check on the real machine.)

---

## For developers

```bash
# run from source (Linux/macOS/Windows, Python 3.11+)
python -m homebase            # serves http://127.0.0.1:8777/
python -m homebase --open     # ...and opens it in your browser

# tests
pip install -r requirements-dev.txt
pytest                        # the full acceptance suite (privacy / correctness / freshness / ...)
```

- Stdlib-only runtime (urllib + ssl + http.server). The one packaged dependency is `tzdata`
  (Windows only), for DST-correct times.
- Architecture, the privacy/security model, and the acceptance criteria live in
  `SPEC-HomeBase-v2.md` (the hardened spec this was built from).
- Build the Windows `.exe`: see `packaging/BUILD.md`.

## Layout
```
homebase/            the app (stdlib-only runtime)
  fetcher.py         single instrumented egress chokepoint (TLS-on, no cookies, SSRF guard)
  ssrf.py            per-card-type SSRF guard
  server.py          loopback server: strict CSP + anti-rebind/CSRF, no CORS
  config.py cache.py refresher.py   bind-enum + consent model / validate-before-cache / ban-avoidance
  sources/           NHL, MLB, Mets-RSS, Google-News, Stooq, Yahoo adapters
  markets/           bundled NYSE calendar + symbol map + market-state
  static/            CSP-clean front-end (no CDN, no inline, XSS-inert)
packaging/           PyInstaller spec + Windows install/uninstall + build notes
tests/               the acceptance criteria as runnable, bypass-resistant tests
```
