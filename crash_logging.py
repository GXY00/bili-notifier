"""Persist uncaught application exceptions for local troubleshooting."""

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
from typing import Optional


_LOGGER = logging.getLogger("bilibili_notifier.crash")


def install_crash_logging(path: Optional[Path] = None) -> Path:
    """Write uncaught main-thread and background-thread exceptions to crash.log."""
    if path is None:
        app_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        path = app_dir / "crash.log"

    if not _LOGGER.handlers:
        paths = [path]
        if path.parent == (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent):
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                paths.append(Path(local_app_data) / "BilibiliNotifier" / "crash.log")
        handler = None
        for candidate in paths:
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(
                    candidate,
                    maxBytes=1024 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
                path = candidate
                break
            except OSError:
                continue
        if handler is None:
            return path
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        _LOGGER.addHandler(handler)
        _LOGGER.setLevel(logging.ERROR)
        _LOGGER.propagate = False

    previous_hook = sys.excepthook

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            previous_hook(exc_type, exc_value, exc_traceback)
            return
        _LOGGER.critical(
            "Uncaught exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    sys.excepthook = handle_exception

    if hasattr(threading, "excepthook"):
        def handle_thread_exception(args):
            _LOGGER.error(
                "Uncaught exception in background thread %s",
                args.thread.name if args.thread else "unknown",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = handle_thread_exception

    return path
