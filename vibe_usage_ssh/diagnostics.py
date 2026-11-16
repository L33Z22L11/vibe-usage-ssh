"""Send sanitized UI failures to the SSH service's log, outside the terminal UI."""

import logging
import os
import traceback
from pathlib import Path
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())
logger.propagate = False


def configure_logging():
    descriptor = os.environ.get("VIBE_SSH_LOG_FD")
    if descriptor is None:
        return None
    stream = os.fdopen(int(descriptor), "w", buffering=1)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    return handler


def log_failure(operation, error):
    details = [type(error).__name__]
    if isinstance(error, HTTPError):
        details.append(f"HTTP {error.code}")
    cause = error.reason if isinstance(error, URLError) else error
    if isinstance(cause, BaseException) and cause is not error:
        details.append(type(cause).__name__)
    if isinstance(cause, OSError) and cause.errno is not None:
        details.append(f"errno={cause.errno}")
    frames = traceback.extract_tb(error.__traceback__)
    locations = " > ".join(
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames
    )
    # Exception messages, URLs, headers, source lines and locals can contain credentials.
    logger.warning(
        "%s failed: %s%s", operation, ", ".join(details), f"; at {locations}" if locations else ""
    )
