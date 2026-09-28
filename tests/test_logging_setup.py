from __future__ import annotations

import logging
import os
from pathlib import Path

from app.core import paths
from app.core.logging_setup import setup_logging


def test_setup_logging_creates_app_log_and_file_handler():
    # 用 FLT_HOME 隔离，避免污染真实用户目录
    home = Path(__file__).resolve().parent / "_logtest_home"
    os.environ["FLT_HOME"] = str(home)
    try:
        log_path = setup_logging()
        assert log_path.name == "app.log", "日志文件名不为 app.log"
        assert log_path.exists(), "app.log 未创建"

        root = logging.getLogger()
        assert any(
            isinstance(h, logging.FileHandler)
            and getattr(h, "baseFilename", "") == str(log_path)
            for h in root.handlers
        ), "root logger 未挂载指向 app.log 的 FileHandler"

        # 写一条并确认真正落盘
        logging.getLogger("test_logging").info("logging setup probe")
        assert "logging setup probe" in log_path.read_text(encoding="utf-8")
    finally:
        os.environ.pop("FLT_HOME", None)
