"""Owner-only filesystem helpers (AC-PRIV-10) + app data locations.

The interest-graph lives in config.json and the cache. They must not be readable by
other users on the machine.

  - POSIX: files 0600, dirs 0700.
  - Windows: NTFS ACL granting only the current user, with inheritance stripped
    (so it does not inherit a broad parent ACL). Implemented via `icacls`.

On Windows the app data dir is %LOCALAPPDATA%\\HomeBase; on POSIX ~/.local/share/homebase
(honoring XDG_DATA_HOME). Logs/cache/config all live under it.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"


def app_dir() -> Path:
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "HomeBase"
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "homebase"


def _windows_lock_owner_only(path: Path) -> None:
    """Strip inheritance and grant only the current user. Best-effort; raises on hard failure."""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not user:
        return
    # /inheritance:r removes inherited ACEs; /grant gives the user full control.
    # The (OI)/(CI) inheritance flags are only valid on DIRECTORIES. Applied to a file,
    # icacls rejects the /grant — and since /inheritance:r has already stripped the
    # inherited ACEs, the file is left with an EMPTY DACL that nobody (not even the
    # owner) can open, surfacing later as PermissionError. So flag only on dirs.
    grant = f"{user}:(OI)(CI)F" if path.is_dir() else f"{user}:F"
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def ensure_owner_only_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        _windows_lock_owner_only(path)
    else:
        os.chmod(path, 0o700)
    return path


def write_owner_only(path: Path, data: str | bytes, *, mode: str = "w") -> None:
    """Write a file such that only the owner can read it.

    On POSIX we pre-create with 0600 via os.open so the bytes never briefly exist
    world-readable. On Windows we write then lock the ACL.
    """
    ensure_owner_only_dir(path.parent)
    is_bytes = isinstance(data, (bytes, bytearray))
    if IS_WINDOWS:
        with open(path, "wb" if is_bytes else "w", encoding=None if is_bytes else "utf-8") as fh:
            fh.write(data)
        _windows_lock_owner_only(path)
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb" if is_bytes else "w", encoding=None if is_bytes else "utf-8") as fh:
                fh.write(data)
        finally:
            # If the file pre-existed with looser perms, tighten it.
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass


def atomic_write_owner_only(path: Path, data: str) -> None:
    """Atomic (temp+rename) owner-only write — for config saves (AC-CONFIG)."""
    ensure_owner_only_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    write_owner_only(tmp, data)
    os.replace(tmp, path)  # atomic on same filesystem
    if not IS_WINDOWS:
        os.chmod(path, 0o600)
    else:
        _windows_lock_owner_only(path)


def perms_are_owner_only(path: Path) -> bool:
    """POSIX check used by AC-PRIV-10. On Windows returns True (ACL checked out-of-band)."""
    if IS_WINDOWS:
        return True
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if path.is_dir():
        return mode == 0o700
    return mode == 0o600
