"""Logging filter that masks PII before anything reaches a log sink."""
import logging

from .pii import mask


class PiiScrubber(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # keep the args' shape: uvicorn's access formatter unpacks them, and %d needs an int
        def scrub(v):
            return v if isinstance(v, (int, float)) else mask(str(v))[0]
        record.msg = scrub(record.msg)
        if isinstance(record.args, dict):
            record.args = {k: scrub(v) for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(scrub(v) for v in record.args)
        return True


def install() -> None:
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "finsight"):
        logging.getLogger(name).addFilter(PiiScrubber())
