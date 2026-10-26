#!/usr/bin/env python
"""watchdog —— NapCat 守护进程。

NapCat 挂掉整个链路就断了。这个进程独立于它，盯着 3000 端口：

    端口在 + OneBot 有响应  → 正常，重置退避
    连续 N 次探测失败        → 杀掉残留进程，重新拉起
    拉起也失败               → 退避时间翻倍（15s → 30 → 60 … 封顶 5 分钟）

用法：
    python watchdog.py                # 常驻守护
    python watchdog.py --once         # 只检查一次
    python watchdog.py --interval 10  # 探测间隔
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from start import NAP_EXE, ROOT, api, launch_napcat, port_open, wait_ready  # noqa: E402

LOG = ROOT / "outputs" / "watchdog.log"


def log(msg: str) -> None:
    line = f"{datetime.now():%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def healthy() -> bool:
    """端口开着 且 OneBot 有响应，才算健康。只看端口会被半死状态骗过去。"""
    if not port_open(3000):
        return False
    try:
        info = api("get_login_info", timeout=3)
        return bool((info.get("data") or {}).get("user_id"))
    except Exception:
        return False


def resolve_uin() -> str:
    from start import CFG_DIR  # noqa: PLC0415
    files = sorted(CFG_DIR.glob("onebot11_*.json")) if CFG_DIR.exists() else []
    if not files:
        return ""
    return files[-1].stem.split("_", 1)[1]


def main() -> int:
    ap = argparse.ArgumentParser(description="NapCat 守护进程")
    ap.add_argument("--interval", type=float, default=15, help="探测间隔秒，默认 15")
    ap.add_argument("--threshold", type=int, default=2, help="连续失败几次才重启，默认 2")
    ap.add_argument("--once", action="store_true", help="只检查一次")
    ap.add_argument("--uin", help="QQ 号，默认从配置推断")
    args = ap.parse_args()

    uin = args.uin or resolve_uin()
    if not uin:
        log("找不到 QQ 号（没有 onebot11_*.json），先跑一次 start.py")
        return 1

    if not NAP_EXE.exists():
        log(f"找不到 NapCat：{NAP_EXE}")
        return 1

    if args.once:
        ok = healthy()
        log("健康" if ok else "异常（端口未开或 OneBot 无响应）")
        return 0 if ok else 1

    log(f"守护启动  账号 {uin}  探测间隔 {args.interval:g}s  失败阈值 {args.threshold}")
    fails = 0
    backoff = 0.0

    try:
        while True:
            if healthy():
                if fails:
                    log("已恢复")
                fails = 0
                backoff = 0.0
                time.sleep(args.interval)
                continue

            fails += 1
            log(f"探测失败 {fails}/{args.threshold}")
            if fails < args.threshold:
                time.sleep(args.interval)
                continue

            if backoff:
                log(f"退避等待 {backoff:.0f}s 再试")
                time.sleep(backoff)

            log("重启 NapCat…")
            for img in ("NapCatWinBootMain.exe", "QQ.exe"):
                subprocess.run(["taskkill", "/F", "/IM", img], capture_output=True)
            time.sleep(2)

            proc = launch_napcat(uin)
            login = wait_ready(120)
            if login:
                log(f"重启成功  {login.get('nickname') or ''} ({login.get('user_id')})")
                fails = 0
                backoff = 0.0
            else:
                backoff = min(backoff * 2 or 30, 300)
                log(f"重启失败，下次退避 {backoff:.0f}s（看 outputs/napcat_run.log）")
                fails = 0
                try:
                    proc.terminate()
                except Exception:
                    pass
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log("守护退出（NapCat 不受影响）")
        return 0


if __name__ == "__main__":
    sys.exit(main())
