"""utils/logger.py - Centralized structured logging for arXiv pipeline."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


class PipelineFormatter(logging.Formatter):
    """Format log có timestamp và mức độ rõ ràng, chuyên nghiệp."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        prefix = f"[{timestamp}] [{record.levelname:5s}]"
        return f"{prefix} {record.getMessage()}"


def setup_logger(
    name: str = "arxiv",
    log_file: Optional[Path] = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Tạo hoặc lấy logger chuẩn hóa cho pipeline."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(PipelineFormatter())
        logger.addHandler(console)

    if log_file:
        log_file = Path(log_file).expanduser().resolve()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        has_file = any(
            isinstance(h, logging.FileHandler) and Path(h.baseFilename) == log_file
            for h in logger.handlers
        )
        if not has_file:
            file_h = logging.FileHandler(log_file, encoding="utf-8")
            file_h.setFormatter(PipelineFormatter())
            logger.addHandler(file_h)

    return logger


def get_logger(name: str = "arxiv") -> logging.Logger:
    """Lấy logger đã cấu hình."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger
