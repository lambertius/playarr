"""
Safe deletion utilities — send files to the recycle bin instead of permanent deletion.

For network/UNC paths where the recycle bin is unavailable, raises NetworkDeleteError
so the caller can request explicit user confirmation before falling back to permanent deletion.
"""
import os
import logging
import shutil
import time

logger = logging.getLogger(__name__)


class NetworkDeleteError(Exception):
    """Raised when a delete targets a network path where the recycle bin is unavailable."""

    def __init__(self, paths: list[str]):
        self.paths = paths
        super().__init__(
            f"Cannot send to recycle bin on network path(s): {', '.join(paths[:3])}"
        )


def _is_network_path(path: str) -> bool:
    """Check if a path is on a network/UNC location (no recycle bin support)."""
    norm = os.path.normpath(path)
    # UNC paths: \\server\share or //server/share
    if norm.startswith("\\\\") or norm.startswith("//"):
        return True
    # Mapped network drives on Windows
    if os.name == "nt" and len(norm) >= 2 and norm[1] == ":":
        drive = norm[0].upper() + ":\\"
        try:
            import ctypes
            DRIVE_REMOTE = 4
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive)
            return drive_type == DRIVE_REMOTE
        except Exception:
            pass
    return False


def safe_delete(path: str, force_permanent: bool = False) -> None:
    """
    Delete a file or folder, preferring recycle bin over permanent deletion.

    Args:
        path: File or directory to delete.
        force_permanent: If True, skip recycle bin (use for confirmed network deletes).

    Raises:
        NetworkDeleteError: If path is on a network location and force_permanent is False.
    """
    if not path or not os.path.exists(path):
        return

    if not force_permanent and _is_network_path(path):
        raise NetworkDeleteError([path])

    if not force_permanent:
        try:
            from send2trash import send2trash as _send2trash
            _send2trash(path)
            return
        except Exception as e:
            # If send2trash fails (e.g. network path, permission), log and check
            logger.warning(f"Recycle bin failed for {path}: {e}")
            if _is_network_path(path):
                raise NetworkDeleteError([path])
            # For local paths where recycle bin fails, fall through to permanent delete
            logger.warning(f"Falling back to permanent delete for {path}")

    # Permanent deletion fallback
    if os.path.isdir(path):
        _robust_rmtree(path)
    elif os.path.isfile(path):
        os.remove(path)


def safe_delete_batch(paths: list[str], force_permanent: bool = False) -> None:
    """
    Delete multiple files/folders, preferring recycle bin.

    Raises NetworkDeleteError if any path is on a network location and force_permanent is False.
    """
    if not force_permanent:
        network_paths = [p for p in paths if os.path.exists(p) and _is_network_path(p)]
        if network_paths:
            raise NetworkDeleteError(network_paths)

    for path in paths:
        safe_delete(path, force_permanent=force_permanent)


def _robust_rmtree(folder_path: str):
    """
    Remove a directory tree, handling OneDrive-backed paths.

    Strategy:
    1. shutil.rmtree — works for normal paths and removes file contents
    2. os.rmdir with retries — picks up empty dirs after OneDrive releases locks
    3. ``rd /s /q`` subprocess — handles OneDrive stubs
    """
    if not folder_path or not os.path.isdir(folder_path):
        return

    for attempt in range(3):
        try:
            shutil.rmtree(folder_path)
        except Exception:
            time.sleep(0.5 * (attempt + 1))
        if not os.path.isdir(folder_path):
            return

    # Clear remaining children manually
    if os.path.isdir(folder_path):
        try:
            for entry in os.scandir(folder_path):
                if entry.is_dir():
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    try:
                        os.remove(entry.path)
                    except Exception:
                        pass
        except Exception:
            pass

    # Try os.rmdir
    for delay in (0.5, 1, 2):
        if not os.path.isdir(folder_path):
            return
        try:
            os.rmdir(folder_path)
            return
        except OSError:
            time.sleep(delay)

    # Windows rd /s /q
    if os.path.isdir(folder_path) and os.name == "nt":
        try:
            import subprocess
            subprocess.run(
                ["cmd", "/c", "rd", "/s", "/q", folder_path],
                capture_output=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            logger.debug(f"rd /s /q failed for {folder_path}: {e}")
