"""Tests for config.py — setup_logging and configuration constants."""

import logging
import os
import tempfile
import pytest
from config import setup_logging, LOG_LEVEL, DASHBOARD_PORT, WEBHOOK_URL


class TestSetupLogging:
    def test_default_level(self):
        setup_logging()
        root = logging.getLogger()
        # Root logger level should be DEBUG (handlers filter to configured level)
        assert root.level == logging.DEBUG

    def test_custom_level(self):
        setup_logging(level="WARNING")
        root = logging.getLogger()
        # Console handler should have WARNING level
        console_handler = root.handlers[0]
        assert console_handler.level == logging.WARNING

    def test_file_handler_created(self):
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name
        try:
            setup_logging(log_file=log_path)
            root = logging.getLogger()
            file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
            assert len(file_handlers) >= 1
            # File handler should have DEBUG level
            assert file_handlers[0].level == logging.DEBUG
        finally:
            # Clean up
            setup_logging()  # Reset handlers
            os.unlink(log_path)

    def test_no_file_handler_when_empty(self):
        setup_logging(log_file="")
        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 0

    def test_invalid_level_defaults_to_info(self):
        setup_logging(level="NONEXISTENT")
        root = logging.getLogger()
        # Should use INFO as fallback
        console_handler = root.handlers[0]
        assert console_handler.level == logging.INFO


class TestConfigConstants:
    def test_log_level_default(self):
        # When LOG_LEVEL env var not set, defaults to INFO
        assert LOG_LEVEL in ("INFO", "DEBUG", "WARNING", "ERROR")

    def test_dashboard_port_is_int(self):
        assert isinstance(DASHBOARD_PORT, int)

    def test_webhook_url_is_string(self):
        assert isinstance(WEBHOOK_URL, str)
