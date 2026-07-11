import logging
import sys

from app.core.config import get_settings

settings = get_settings()


def configure_logging() -> None:
    log_level = logging.DEBUG if settings.debug else logging.INFO

    logging.basicConfig(
        level=log_level,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.sql_echo else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)