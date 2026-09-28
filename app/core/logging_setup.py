from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from app.core import paths


def _fallback_excepthook(exc_type, exc, tb) -> None:
    """进程级未捕获异常的兜底：写文件而不是只丢给被丢弃的 stderr。

    冻结版（console=False）下 stderr 不可见，必须落到磁盘才能排障。
    """
    msg = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        logging.getLogger("crash").critical("未捕获异常导致进程退出:\n%s", msg)
    except Exception:
        pass
    # 即便 data_root 不可写，也落到 exe 同级，保证可见
    try:
        target = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
                  else paths.data_root()) / "crash.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(msg, encoding="utf-8")
    except Exception:
        pass


def setup_logging(level: int = logging.DEBUG) -> Path:
    """在数据目录创建 app.log 并挂载 FileHandler，同时接管 sys.excepthook。

    必须在任何可能失败的 import（PySide6 等）之前调用，否则启动期崩溃无痕迹。
    """
    log_path = paths.data_root() / "app.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
    root = logging.getLogger()
    have = any(
        isinstance(h, logging.FileHandler)
        and getattr(h, "baseFilename", "") == str(log_path)
        for h in root.handlers
    )
    if not have:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter(fmt))
        root.addHandler(fh)
        root.setLevel(level)
    sys.excepthook = _fallback_excepthook
    return log_path
