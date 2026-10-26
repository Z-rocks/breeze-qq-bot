#!/usr/bin/env python
"""botctl —— 机器人的控制面。

    python botctl.py status          看状态（是否暂停 / OneBot 在线 / 队列忙不忙）
    python botctl.py pause [分钟]     急停；给分钟数就到点自动恢复
    python botctl.py resume          恢复
    python botctl.py reload          校验人设配置（改完不用重启，这里只是体检）
    python botctl.py logs [行数]      看 NapCat 日志尾部

设计上刻意和「数据面」分开：这些命令只改状态文件，不碰消息流。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "qq_mcp"))

import guard   # noqa: E402
import lock    # noqa: E402

ONEBOT = "http://127.0.0.1:3000"
NAP_LOG = ROOT / "outputs" / "napcat_run.log"
PERSONA = ROOT / "qq_mcp" / "persona.json"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(action: str, timeout: float = 2.0):
    req = urllib.request.Request(f"{ONEBOT}/{action}", data=b"{}",
                                 headers={"Content-Type": "application/json"}, method="POST")
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def line(k: str, v: str, ok: bool | None = None) -> None:
    mark = "" if ok is None else ("  ✓" if ok else "  ✗")
    print(f"  {k:<10}{v}{mark}")


# ---------------------------------------------------------------- status
def cmd_status(_args) -> int:
    print("=" * 50)
    print("  Breeze QQ Bot · 状态")
    print("=" * 50)

    paused, why = guard.is_paused()
    line("运行状态", why if paused else "正常", not paused)

    online, detail = False, "连不上"
    try:
        info = api("get_login_info").get("data") or {}
        if info.get("user_id"):
            online = True
            detail = f"{info.get('nickname') or ''} ({info['user_id']})"
    except Exception as exc:  # noqa: BLE001
        detail = f"连不上 OneBot（{type(exc).__name__}）"
    line("QQ 登录", detail, online)

    line("合成队列", "忙（有任务在跑）" if lock.is_busy() else "空闲", not lock.is_busy())

    s = guard.stats()
    line("近一小时", f"语音 {s['voice_last_hour']} 条 · 文字 {s['text_last_hour']} 条")

    try:
        import memory  # noqa: PLC0415
        m = memory.stats()
        line("记忆", f"{m['episodes']} 条对话 · {m['facts']} 条事实 · {m['sessions']} 个会话")
    except Exception:
        pass

    pol = guard.load_policy()
    allow = pol.get("allow") or {}
    line("白名单", f"私聊 {len(allow.get('private') or [])} 个 · 群 {len(allow.get('groups') or [])} 个")
    if pol.get("allow_all_when_empty"):
        print("  ! 注意：allow_all_when_empty=true，白名单为空时全部放行")

    print("=" * 50)
    return 0


# ---------------------------------------------------------------- pause / resume
def cmd_pause(args) -> int:
    print(guard.pause(args.minutes, args.reason or ""))
    if not args.minutes:
        print("  用 botctl.py resume 手动恢复")
    return 0


def cmd_resume(_args) -> int:
    print(guard.resume())
    return 0


# ---------------------------------------------------------------- reload
REQUIRED = ["identity", "style", "boundaries", "examples", "voice"]


def cmd_reload(_args) -> int:
    """校验人设。server.py 每次调用都重读文件，所以这里只做体检。"""
    print("=" * 50)
    print("  人设体检")
    print("=" * 50)
    problems: list[str] = []
    try:
        raw = PERSONA.read_text(encoding="utf-8")
        p = json.loads(raw)
    except FileNotFoundError:
        print(f"  ✗ 找不到 {PERSONA}")
        return 1
    except json.JSONDecodeError as exc:
        print(f"  ✗ JSON 语法错误：第 {exc.lineno} 行第 {exc.colno} 列 —— {exc.msg}")
        return 1

    for k in REQUIRED:
        if k not in p:
            problems.append(f"缺少字段 {k}")
        else:
            print(f"  ✓ {k}")

    voice = p.get("voice") or {}
    if voice and not voice.get("profile"):
        problems.append("voice.profile 为空 —— 没有克隆音色，声音每次都会变")
    if voice.get("enabled") is None:
        problems.append("voice.enabled 未设置")

    pol = p.get("policy") or {}
    if pol.get("allow_all_when_empty"):
        problems.append("policy.allow_all_when_empty=true —— 陌生人也能触发发送，建议关掉")
    if not (pol.get("allow") or {}).get("private") and not (pol.get("allow") or {}).get("groups"):
        problems.append("policy.allow 全空 —— 现在谁都发不出去")

    print("-" * 50)
    if problems:
        for p_ in problems:
            print(f"  ! {p_}")
        print(f"\n  发现 {len(problems)} 个问题（配置已生效，但建议修）")
    else:
        print("  配置正常。改完即生效，无需重启。")
    print("=" * 50)
    return 0


# ---------------------------------------------------------------- logs
def cmd_logs(args) -> int:
    n = args.lines or 20
    if not NAP_LOG.exists():
        print(f"没有日志：{NAP_LOG}")
        return 1
    lines = NAP_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    for ln in lines[-n:]:
        print(ln[:160])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="botctl", description="Breeze QQ Bot 控制面")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status", help="看状态").set_defaults(fn=cmd_status)

    p_pause = sub.add_parser("pause", help="急停")
    p_pause.add_argument("minutes", nargs="?", type=float, help="暂停多少分钟，省略则不自动恢复")
    p_pause.add_argument("--reason", help="暂停原因，会显示在状态里")
    p_pause.set_defaults(fn=cmd_pause)

    sub.add_parser("resume", help="恢复").set_defaults(fn=cmd_resume)
    sub.add_parser("reload", help="校验人设配置").set_defaults(fn=cmd_reload)

    p_logs = sub.add_parser("logs", help="看日志")
    p_logs.add_argument("lines", nargs="?", type=int, help="行数，默认 20")
    p_logs.set_defaults(fn=cmd_logs)

    args = ap.parse_args()
    if not getattr(args, "fn", None):
        ap.print_help()
        return 1
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
