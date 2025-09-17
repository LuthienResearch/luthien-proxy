"""Small helper to configure logging for containers.

Forces StreamHandler to stdout and respects LOG_LEVEL.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional


def configure_logging(level: Optional[str] = None) -> None:
    """Configure root logging to stdout so Docker captures .info logs.

    - Respects LOG_LEVEL env if level not provided.
    - Forces a StreamHandler(sys.stdout) with a concise format.
    - Uses force=True to override any prior basicConfig.
    """
    lvl_str = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    lvl = getattr(logging, lvl_str, logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
