"""Configures a dual-sink logger (console INFO+, rotating file DEBUG+) shared by
every bot module, and a redaction filter that masks API secrets and signatures so
keys/signatures never hit disk or the console in cleartext (SRS FR-LOG-01..05,
FR-CLIENT-09).
"""

import logging
import os
from logging.handlers import RotatingFileHandler

# Keys whose values must never be written to a log sink in cleartext.
_SECRET_KEYS = {"signature", "secret", "apikey", "api_secret", "x-mbx-apikey"}

# Precompiled patterns: match `key=value`, `key: value`, `'key': 'value'`, etc.,
# and replace only the value part with ***. Matched case-insensitively.
import re

_REDACTION_PATTERNS = [
    re.compile(rf"({re.escape(key)}['\"]?\s*[:=]\s*['\"]?)([^,'\"\s}}]+)", re.IGNORECASE)
    for key in _SECRET_KEYS
]


class RedactingFilter(logging.Filter):
    """Masks secret-like values in the rendered message before it reaches any handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern in _REDACTION_PATTERNS:
            msg = pattern.sub(r"\1***", msg)
        record.msg = msg
        record.args = ()
        return True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger. Idempotent: repeated calls reuse existing handlers.

    Console handler emits at LOG_LEVEL (default INFO); the rotating file handler at
    ``logs/trading_bot.log`` always captures DEBUG and above (5 MB x 3 backups).
    """
    logger = logging.getLogger(name)
    if logger.handlers:  # idempotent: avoid duplicate handlers on repeated imports/calls
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    console.setFormatter(fmt)
    console.addFilter(RedactingFilter())

    os.makedirs("logs", exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join("logs", "trading_bot.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    file_handler.addFilter(RedactingFilter())

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger
