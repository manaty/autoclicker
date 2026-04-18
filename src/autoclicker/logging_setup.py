import logging
import re
from logging.handlers import RotatingFileHandler

from .paths import log_dir


_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat in _SECRET_PATTERNS:
            msg = pat.sub("[REDACTED]", msg)
        record.msg = msg
        record.args = ()
        return True


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("autoclicker")
    if getattr(logger, "_configured", False):
        return logger

    logger.setLevel(level.upper())
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_dir() / "autoclicker.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(RedactingFilter())
    logger.addHandler(file_handler)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.addFilter(RedactingFilter())
    logger.addHandler(stream)

    logger._configured = True
    return logger
