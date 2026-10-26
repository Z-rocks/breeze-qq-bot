#!/usr/bin/env python
"""botd —— 实时自动回复进程。

链路：

    QQ ──► NapCat ──(HTTP POST 事件)──► botd :3002
                                          │
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                    去重 / 急停 / 白名单   人设 + 记忆     DeepSeek API
                          └───────────────┼───────────────┘
                                          ▼
                                    脱敏审查 → 发送（文字或语音）

为什么用 HTTP 回调而不是 WebSocket：NapCat 支持把事件 POST 到指定 URL，
标准库就能接，不用为了一个 WebSocket 客户端引第三方包。

用法：
    set DEEPSEEK_API_KEY=sk-xxx
    python botd.py                    # 监听 3002，等 NapCat 推事件
    python botd.py --dry-run          # 不真发，只打印打算发什么
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "qq_mcp"))

import guard     # noqa: E402
import memory    # noqa: E402
import privacy   # noqa: E402

ONEBOT = os.environ.get("ONEBOT_HTTP", "http://127.0.0.1:3000")
LLM_BASE = os.environ.get("DEEPSEEK_BASE", "https://api.deepseek.com")
LLM_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
LLM_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
PERSONA = Path(__file__).resolve().parent / "qq_mcp" / "persona.json"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

DRY = False
SESSION_COOLDOWN: dict[str, float] = {}
COOLDOWN_LOCK = threading.Lock()


# ---------------------------------------------------------------- 基础
def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {msg}", flush=True)


def persona() -> dict:
    try:
        return json.loads(PERSONA.read_text(encoding="utf-8"))
    except Exception:
        return {}


def onebot(action: str, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(f"{ONEBOT}/{action}", data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------- 提示词
def build_messages(p: dict, session: str, incoming: str, related: list[dict]) -> list[dict]:
    ident = p.get("identity") or {}
    style = p.get("style") or {}
    bounds = p.get("boundaries") or []
    examples = p.get("examples") or []

    sys_parts = [
        f"你叫{ident.get('name', '小鲸鱼')}，{ident.get('age', 20)} 岁，{ident.get('role', '群里的朋友')}。",
        "你在 QQ 上跟人聊天，不是在当助手。回复要短、口语化、像真人发消息。",
        f"单条不超过 {style.get('max_chars', 40)} 字；一次最多 {style.get('split', [1, 3])[-1]} 条，用换行分隔。",
        f"可以用的语气词：{'、'.join(style.get('particles') or [])}。",
    ]
    if style.get("avoid"):
        sys_parts.append(f"绝对不要说：{'、'.join(style['avoid'])}。")
    if bounds:
        sys_parts.append("必须遵守的边界：\n" + "\n".join(f"- {b}" for b in bounds))
    if examples:
        sys_parts.append("说话风格参考（不要照抄）：\n" + "\n".join(
            f"  对方：{e.get('user')}\n  你：{e.get('bot')}" for e in examples[:3]))

    ctx_lines = []
    for m in memory.recent(session, 8)[:-1]:
        who = "你" if m["role"] == "bot" else (m.get("speaker") or "对方")
        ctx_lines.append(f"{who}：{m['text']}")
    if related:
        ctx_lines.append("（更早的相关片段：" + "；".join(r["text"][:40] for r in related[:3]) + "）")

    user = ("最近聊天：\n" + "\n".join(ctx_lines) + "\n\n" if ctx_lines else "") + f"新消息：{incoming}\n\n直接给出你要发的消息，不要解释。"

    return [
        {"role": "system", "content": "\n".join(sys_parts)},
        {"role": "user", "content": user},
    ]


def ask_llm(messages: list[dict], timeout: int = 45) -> str:
    if not LLM_KEY:
        raise RuntimeError("没有 DEEPSEEK_API_KEY，无法生成回复")
    payload = {"model": LLM_MODEL, "messages": messages, "temperature": 1.1,
               "max_tokens": 200, "stream": False}
    req = urllib.request.Request(
        f"{LLM_BASE}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_KEY}"},
        method="POST",
    )
    with OPENER.open(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


# ---------------------------------------------------------------- 发送
def send_text(session: str, text: str) -> None:
    kind, tid = session.split(":", 1)
    key = "group_id" if kind == "group" else "user_id"
    action = "send_group_msg" if kind == "group" else "send_private_msg"
    onebot(action, {key: int(tid), "message": [{"type": "text", "data": {"text": text}}]})
    memory.log_message(session, msg_id=None, speaker_id="", speaker="小鲸鱼",
                       role="bot", text=text)


def send_voice(session: str, text: str) -> bool:
    """调 tts.py 合成再发。失败就退回文字，不让语音拖死整条链路。"""
    import subprocess
    out = ROOT / "outputs" / f"botd_{int(time.time())}.wav"
    p = persona()
    voice = p.get("voice") or {}
    cmd = [sys.executable, str(ROOT / "tts.py"), text, "--out", str(out),
           "--cfg-scale", str(voice.get("cfg", 1.8))]
    if voice.get("profile"):
        cmd += ["--voice", str(voice["profile"])]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    if r.returncode != 0 or not out.exists():
        log(f"合成失败，退回文字：{(r.stderr or '')[-120:]}")
        return False
    kind, tid = session.split(":", 1)
    key = "group_id" if kind == "group" else "user_id"
    action = "send_group_msg" if kind == "group" else "send_private_msg"
    onebot(action, {key: int(tid), "message": [{"type": "record", "data": {"file": out.as_uri()}}]},
           timeout=180)
    memory.log_message(session, msg_id=None, speaker_id="", speaker="小鲸鱼",
                       role="bot", text=f"[语音] {text}")
    return True


# ---------------------------------------------------------------- 处理一条消息
def handle_message(ev: dict) -> None:
    mtype = ev.get("message_type")
    if mtype not in ("private", "group"):
        return
    if ev.get("user_id") and str(ev.get("user_id")) == str(ev.get("self_id")):
        return

    session = f"{mtype}:{ev.get('group_id') if mtype == 'group' else ev.get('user_id')}"
    text = "".join(s.get("data", {}).get("text", "")
                   for s in (ev.get("message") or []) if s.get("type") == "text").strip()
    if not text:
        return

    msg_id = str(ev.get("message_id") or "")
    if msg_id and not memory.mark_seen(f"{session}:{msg_id}"):
        return

    sender = ev.get("sender") or {}
    memory.log_message(session, msg_id=msg_id, speaker_id=str(ev.get("user_id") or ""),
                       speaker=sender.get("card") or sender.get("nickname") or "",
                       role="user", text=text, ts=int(ev.get("time") or 0) or None)

    paused, why = guard.is_paused()
    if paused:
        log(f"跳过（{why}）")
        return
    kind, tid = session.split(":", 1)
    ok, why = guard.can_send(kind, tid)
    if not ok:
        log(f"跳过（{why}）")
        return

    # 同会话冷却，避免一次连发被刷屏
    with COOLDOWN_LOCK:
        last = SESSION_COOLDOWN.get(session, 0)
        if time.time() - last < 3:
            log("跳过（同会话冷却）")
            return
        SESSION_COOLDOWN[session] = time.time()

    p = persona()
    safe_text, mapping = privacy.redact(text, keep={str(ev.get("user_id") or "")})
    related = memory.search(safe_text, limit=3)

    try:
        reply = ask_llm(build_messages(p, session, safe_text, related))
    except Exception as exc:  # noqa: BLE001
        log(f"生成失败：{exc}")
        return
    if not reply:
        return

    reply = privacy.restore(reply, mapping)  # 占位符换回「你的手机号」这种说法
    cleaned, issues = privacy.sanitize_outbound(reply)
    if issues:
        log(f"出站拦截（{'、'.join(issues)}），原文：{reply[:60]}")
        return

    # 拟人延迟
    time.sleep(random.uniform(0.8, 2.5))

    voice_cfg = p.get("voice") or {}
    use_voice = voice_cfg.get("enabled") and random.random() < float(voice_cfg.get("probability", 0.15))

    if DRY:
        log(f"[dry-run] {session} ← {cleaned!r}" + ("（语音）" if use_voice else ""))
        return

    sent = False
    if use_voice and guard.check_rate("voice", per_hour=(voice_cfg.get("limits") or {}).get("max_per_hour"))[0]:
        sent = send_voice(session, cleaned)
        if sent:
            guard.record_send("voice")
    if not sent:
        send_text(session, cleaned)
        guard.record_send("text")
    log(f"已回复 {session}：{cleaned[:40]}")


# ---------------------------------------------------------------- HTTP 回调
class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", errors="replace") if n else "{}"
        self.send_response(204)
        self.end_headers()
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            return
        if ev.get("post_type") != "message":
            return
        threading.Thread(target=handle_message, args=(ev,), daemon=True).start()

    def log_message(self, *_a) -> None:
        pass


def main() -> int:
    global DRY
    ap = argparse.ArgumentParser(description="实时自动回复进程")
    ap.add_argument("--port", type=int, default=3002)
    ap.add_argument("--dry-run", action="store_true", help="不真发，只打印")
    args = ap.parse_args()
    DRY = args.dry_run

    if not LLM_KEY:
        log("⚠ 没有 DEEPSEEK_API_KEY，收到消息会生成失败")
    print("=" * 52)
    print("  botd · 实时自动回复")
    print("=" * 52)
    print(f"  回调地址   http://127.0.0.1:{args.port}")
    print(f"  模型       {LLM_MODEL}")
    print(f"  模式       {'演练（不发送）' if DRY else '真实发送'}")
    print("=" * 52)
    print("  NapCat 侧需要把事件推到这个地址：")
    print(f"    网络配置 → HTTP客户端 → url = http://127.0.0.1:{args.port}")
    print("  Ctrl+C 退出\n")

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
