"""PyInstaller entry point. Kept as a top-level script so the bundle has a clean main."""
from homebase.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
