"""文件锁：让 TTS 串行执行。

8GB 显存只能跑一个实例，两个合成请求同时进来必然 OOM。
锁文件放在 `.workbuddy-ai/state/`，跨进程有效（MCP 会话可能不止一个）。

超时 steal：持锁进程崩了会留下锁文件，超过 stale 秒就认为失效。
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / ".workbuddy-ai" / "state"


@contextmanager
def exclusive(name: str = "tts", timeout: float = 300, stale: float = 600,
              on_wait=None):
    """拿独占锁。拿不到就排队等，超时抛 TimeoutError。

    on_wait: 可选的等待回调，参数是已等待秒数，用来打印「前面还有任务」。
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = STATE_DIR / f"{name}.lock"
    started = time.time()
    waited = False

    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {int(started)}".encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > stale:
                try:
                    lock.unlink()
                except OSError:
                    pass
                continue
            elapsed = time.time() - started
            if elapsed > timeout:
                raise TimeoutError(
                    f"等 {name} 锁超过 {timeout:.0f} 秒，可能有别的合成任务卡住了"
                    f"（锁文件：{lock}）")
            if not waited:
                waited = True
            if on_wait:
                on_wait(elapsed)
            time.sleep(0.5)

    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def is_busy(name: str = "tts") -> bool:
    lock = STATE_DIR / f"{name}.lock"
    try:
        return lock.exists() and (time.time() - lock.stat().st_mtime) < 600
    except OSError:
        return False
