# Building the Windows `.exe`

Produces a single self-contained `dist/HomeBase.exe` — no separately-installed Python/Node.

## On a Windows build host
```powershell
# 1. Python 3.11+ installed
python -m venv build-venv
build-venv\Scripts\activate
pip install -r ..\requirements-dev.txt   # pulls pyinstaller + tzdata

# 2. build (run from the packaging/ folder so entry.py / pathex resolve)
cd packaging
pyinstaller homebase.spec

# 3. ship these two files together:
#    dist\HomeBase.exe
#    install-windows.ps1   (and uninstall-windows.ps1)
```

## Why these bundle choices
- **`tzdata` is bundled** (`collect_all("tzdata")` in the spec). Windows has no system IANA
  tz database; `zoneinfo` needs the package or `America/New_York` conversion fails — which
  would break AC-CORR-1 (DST-correct game times).
- **`static/*` and `markets/*.json` are collected** so the `__file__`-relative reads resolve
  inside the onefile bundle.
- **console = True** so the small window shows the URL and "Press Ctrl+C to stop". The
  auto-start task launches it **hidden** (`-WindowStyle Hidden`) so logins stay clean.

## Smoke test the build
```powershell
dist\HomeBase.exe --print-url      # prints the homepage URL, exits
dist\HomeBase.exe                  # serves; open the URL in a browser
```

## Verifying on the real machine (the one remaining gate)
The markets card must be confirmed once on the friend's **residential** Windows connection
(Stooq/Yahoo block data-center IPs). If a source is unreachable there, the card shows a
labeled "unavailable" state — by design — rather than fake numbers.
