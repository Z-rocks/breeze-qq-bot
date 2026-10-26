"""发送闸门：白名单、急停、频率限制。

三件事放在一起，因为它们共用同一份运行时状态，而且都是「发送前最后一道检查」。

状态文件：`.workbuddy-ai/state/bot.json`（不入库）
白名单配置：`persona.json` 的 `policy` 段

设计原则：**默认拒绝**。白名单为空且没开 `allow_all_when_empty` 时，谁都不能发。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / ".workbuddy-ai" / "state" / "bot.json"
PERSONA_FILE = Path(__file__).resolve().parent / "persona.json"


# ---------------------------------------------------------------- 状态
def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"paused": False, "paused_until": None, "reason": "", "sent": {}}


def _save_state(s: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def load_policy() -> dict:
    try:
        p = json.loads(PERSONA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return p.get("policy") or {}


# ---------------------------------------------------------------- 急停
def is_paused() -> tuple[bool, str]:
    s = _load_state()
    if not s.get("paused"):
        return False, ""
    until = s.get("paused_until")
    if until and time.time() > until:
        # 到点自动恢复
        s["paused"] = False
        s["paused_until"] = None
        _save_state(s)
        return False, ""
    left = ""
    if until:
        left = f"，还剩 {int((until - time.time()) / 60)} 分钟"
    return True, f"机器人已暂停{left}（{s.get('reason') or '无原因'}）"


def pause(minutes: float | None = None, reason: str = "") -> str:
    s = _load_state()
    s["paused"] = True
    s["paused_until"] = (time.time() + minutes * 60) if minutes else None
    s["reason"] = reason
    s["paused_at"] = int(time.time())
    _save_state(s)
    if minutes:
        return f"已暂停 {minutes:g} 分钟"
    return "已暂停（不会自动恢复）"


def resume() -> str:
    s = _load_state()
    s["paused"] = False
    s["paused_until"] = None
    s["reason"] = ""
    _save_state(s)
    return "已恢复"


# ---------------------------------------------------------------- 白名单
def can_send(target_type: str, target_id: str | int) -> tuple[bool, str]:
    """发送前调用。返回 (是否允许, 原因)。"""
    pol = load_policy()
    t = "group" if str(target_type).lower().startswith("g") else "private"
    tid = str(target_id).strip()

    deny = pol.get("deny") or {}
    if tid in [str(x) for x in (deny.get(t) or [])]:
        return False, f"{tid} 在拒绝名单里"

    allow = pol.get("allow") or {}
    listed = [str(x) for x in (allow.get(t) or [])]
    if tid in listed:
        return True, ""

    if pol.get("allow_all_when_empty") and not listed and not (deny.get(t) or []):
        return True, ""

    return False, (
        f"{tid} 不在{('群' if t == 'group' else '私聊')}白名单里，已拒绝发送。"
        f"要放行请把它加进 persona.json 的 policy.allow.{t}"
    )


# ---------------------------------------------------------------- 频率
def check_rate(kind: str, *, per_hour: int | None = None,
               cooldown_ms: int = 0) -> tuple[bool, str]:
    """kind = voice / text。返回 (是否允许, 原因)。"""
    s = _load_state()
    now = time.time()
    hist = [x for x in (s.get("sent", {}).get(kind) or []) if now - x < 3600]

    if cooldown_ms and hist and (now - hist[-1]) * 1000 < cooldown_ms:
        wait = int((cooldown_ms / 1000) - (now - hist[-1]))
        return False, f"{kind} 发送太频繁，还要等 {wait} 秒"
    if per_hour is not None and len(hist) >= per_hour:
        return False, f"{kind} 本小时已达上限（{per_hour} 条）"
    return True, ""


def record_send(kind: str) -> None:
    s = _load_state()
    now = time.time()
    hist = [x for x in (s.setdefault("sent", {}).get(kind) or []) if now - x < 3600]
    hist.append(now)
    s["sent"][kind] = hist
    s["last_send"] = int(now)
    _save_state(s)


def stats() -> dict:
    s = _load_state()
    now = time.time()
    return {
        "paused": bool(s.get("paused")),
        "paused_until": s.get("paused_until"),
        "reason": s.get("reason", ""),
        "voice_last_hour": len([x for x in (s.get("sent", {}).get("voice") or []) if now - x < 3600]),
        "text_last_hour": len([x for x in (s.get("sent", {}).get("text") or []) if now - x < 3600]),
        "last_send": s.get("last_send"),
        "state_file": str(STATE_FILE),
    }
