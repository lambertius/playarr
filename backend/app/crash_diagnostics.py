"""
Crash diagnostics — capture *why* the process dies.

Frozen windowed builds send stdout/stderr to ``os.devnull``, so Python
tracebacks from unhandled exceptions and native faults (access violations)
normally vanish, leaving the app log ending mid-request with no cause.  This
module routes all of those to a dedicated ``crash.log`` that is flushed on every
write, so the next hard death leaves a readable record.

Captured:
  * native fatal faults (segfault/access violation) via ``faulthandler``;
  * unhandled exceptions on the main thread (``sys.excepthook``);
  * unhandled exceptions on worker threads (``threading.excepthook``);
  * unraised-but-fatal asyncio exceptions (loop exception handler);
  * process exit (``atexit``) — so a clean stop is distinguishable from a crash.
"""
import atexit
import faulthandler
import logging
import os
import sys
import threading
import time
import traceback

logger = logging.getLogger("playarr.crash")

_crash_file = None
_armed = False

# Rotate crash.log at startup once it grows past this; bounds disk use across
# restarts. Within a single run, growth is bounded by the dedup in record().
_MAX_CRASH_LOG_BYTES = 2 * 1024 * 1024

_record_lock = threading.Lock()
_last_msg: str | None = None
_last_repeat = 0


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def record(text: str) -> None:
    """Append a timestamped line to crash.log (best-effort).

    Identical consecutive messages are collapsed into a single line with a
    repeat count, so a route that fails on *every* request (the exact scenario
    crash diagnostics exist to catch) can't flood crash.log or hammer the disk
    with a flush per occurrence.
    """
    global _last_msg, _last_repeat
    try:
        if _crash_file is None:
            return
        with _record_lock:
            if text == _last_msg:
                _last_repeat += 1
                # Log the first few, then only every 100th, to keep a trace of
                # an ongoing flood without writing a line each time.
                if _last_repeat <= 3 or _last_repeat % 100 == 0:
                    _crash_file.write(f"[{_stamp()}] {text} (repeat #{_last_repeat})\n")
                    _crash_file.flush()
                return
            _last_msg = text
            _last_repeat = 0
            _crash_file.write(f"[{_stamp()}] {text}\n")
            _crash_file.flush()
    except Exception:
        pass


def install_crash_handlers(log_dir: str) -> None:
    """Arm crash diagnostics, writing to ``<log_dir>/crash.log``."""
    global _crash_file, _armed
    if _armed:
        return
    try:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "crash.log")
        # Rotate a single generation if the previous log got large. Done before
        # opening (and only at startup) because faulthandler holds this fd for
        # the process lifetime — rotating mid-run would invalidate it.
        try:
            if os.path.exists(path) and os.path.getsize(path) > _MAX_CRASH_LOG_BYTES:
                bak = path + ".1"
                if os.path.exists(bak):
                    os.remove(bak)
                os.replace(path, bak)
        except OSError:
            pass
        # line-buffered so the last line survives a hard kill
        _crash_file = open(path, "a", buffering=1, encoding="utf-8")
        record(f"=== crash diagnostics armed (pid {os.getpid()}, {sys.platform}) ===")

        # Native fatal faults → dump every thread's stack to crash.log.
        faulthandler.enable(file=_crash_file, all_threads=True)

        _prev_excepthook = sys.excepthook

        def _excepthook(exc_type, exc, tb):
            msg = "".join(traceback.format_exception(exc_type, exc, tb))
            record(f"UNHANDLED EXCEPTION (main thread):\n{msg}")
            logger.critical("Unhandled exception (main thread):\n%s", msg)
            try:
                _prev_excepthook(exc_type, exc, tb)
            except Exception:
                pass

        sys.excepthook = _excepthook

        def _threadhook(args):
            if args.exc_type is SystemExit:
                return
            msg = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            tname = getattr(args.thread, "name", "?")
            record(f"UNHANDLED EXCEPTION (thread {tname}):\n{msg}")
            logger.critical("Unhandled exception (thread %s):\n%s", tname, msg)

        threading.excepthook = _threadhook

        def _on_exit():
            record(f"process exiting normally (pid {os.getpid()})")

        atexit.register(_on_exit)

        _armed = True
        logger.info("Crash diagnostics armed -> %s", path)
    except Exception:
        logger.exception("Failed to arm crash diagnostics")


def install_asyncio_handler(loop) -> None:
    """Attach an exception handler to the running event loop so otherwise-silent
    asyncio errors land in crash.log."""
    try:
        def _handler(loop, context):
            exc = context.get("exception")
            if exc is not None:
                msg = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            else:
                msg = context.get("message", str(context))
            record(f"ASYNCIO LOOP EXCEPTION:\n{msg}")
            logger.error("Asyncio loop exception: %s", context.get("message"))
        loop.set_exception_handler(_handler)
    except Exception:
        logger.exception("Failed to install asyncio exception handler")
