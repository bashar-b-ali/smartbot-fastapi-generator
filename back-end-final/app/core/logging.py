import logging
import re
import sys

import structlog

from app.core.config import settings


class _QuietSuccessfulApiAccessFilter(logging.Filter):
    _pattern = re.compile(
        r'"(?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD) /api/[^" ]* HTTP/[^" ]+" 2\d{2}'
    )

    def filter(self, record: logging.LogRecord) -> bool:
        return not self._pattern.search(record.getMessage())


def configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _QuietSuccessfulApiAccessFilter) for item in access_logger.filters):
        access_logger.addFilter(_QuietSuccessfulApiAccessFilter())

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.is_production:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


logger = structlog.get_logger()
