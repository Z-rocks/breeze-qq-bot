#!/usr/bin/env python
"""QQ × Breeze TTS 2 —— 一个极简 MCP 服务器（stdio，纯标准库）。

它做两件事：
  1. 通过 OneBot 11 HTTP 接口（NapCat / LLOneBot 暴露）收发 QQ 消息；
  2. 需要发语音时，直接调用本项目的 tts.py 现场合成 24kHz WAV，
     再以 QQ 语音消息（record）发出——NapCat 会自动转成 SILK。

环境变量（都有默认值）：
  ONEBOT_HTTP    OneBot 11 HTTP 地址，默认 http://127.0.0.1:3000
  ONEBOT_TOKEN   access_token，没设就留空
  BREEZE_ROOT    项目根目录，默认本文件的上上级
  BREEZE_PYTHON  跑 tts.py 的 Python，默认当前解释器
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import memory  # 同目录的 SQLite 记忆库
import guard   # 白名单 / 急停 / 频率
import privacy  # 入站脱敏 / 出站审查
import lock    # TTS 串行锁

ROOT = Path(os.environ.get("BREEZE_ROOT", Path(__file__).resolve().parent.parent))
PY = os.environ.get("BREEZE_PYTHON", sys.executable)
ONEBOT = os.environ.get("ONEBOT_HTTP", "http://127.0.0.1:3000").rstrip("/")
TOKEN = os.environ.get("ONEBOT_TOKEN", "")

PROTOCOL = "2024-11-05"

# 本机常挂着 http_proxy，访问 127.0.0.1 必须绕过，否则请求会被代理吞掉
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------- OneBot 调用
def onebot(action: str, payload: dict | None = None, timeout: int = 60) -> dict:
    url = f"{ONEBOT}/{action}"
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"连不上 OneBot（{ONEBOT}）。NapCat 起了吗？HTTP 服务端口对吗？原始错误：{exc}"
        ) from exc


def ok(result: dict) -> dict:
    if result.get("status") != "ok" and result.get("retcode") not in (0, None):
        raise RuntimeError(f"OneBot 返回失败：{json.dumps(result, ensure_ascii=False)[:400]}")
    return result


def target_of(target_type: str, target_id: str | int) -> tuple[str, int]:
    t = "group" if str(target_type).lower().startswith("g") else "private"
    return t, int(target_id)


def send_segments(target_type: str, target_id: str | int, segments: list) -> dict:
    t, tid = target_of(target_type, target_id)
    action = "send_group_msg" if t == "group" else "send_private_msg"
    key = "group_id" if t == "group" else "user_id"
    return ok(onebot(action, {key: tid, "message": segments}, timeout=120))


# ---------------------------------------------------------------- TTS 合成
def synth(text: str, instruction: str | None, voice: str | None, cfg: float | None) -> Path:
    out = ROOT / "outputs" / time.strftime("qq_%Y%m%d_%H%M%S.wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [PY, str(ROOT / "tts.py"), text, "--out", str(out)]
    if instruction:
        cmd += ["--instruction", instruction]
    if voice:
        cmd += ["--voice", voice]
    if cfg:
        cmd += ["--cfg-scale", str(cfg)]

    proc = subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0 or not out.exists():
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise RuntimeError("合成失败：\n" + "\n".join(tail))
    return out


def send_record(target_type: str, target_id: str | int, wav: Path) -> dict:
    """先试本地绝对路径，失败再退回 base64。"""
    t, tid = target_of(target_type, target_id)
    action = "send_group_msg" if t == "group" else "send_private_msg"
    key = "group_id" if t == "group" else "user_id"

    for data in ({"file": wav.as_uri()}, {"file": "base64://" + base64.b64encode(wav.read_bytes()).decode()}):
        res = onebot(action, {key: tid, "message": [{"type": "record", "data": data}]}, timeout=180)
        if res.get("status") == "ok" or res.get("retcode") == 0:
            return res
    raise RuntimeError(f"语音发送失败：{json.dumps(res, ensure_ascii=False)[:400]}")


# ---------------------------------------------------------------- 人设 + 记忆
PERSONA_FILE = Path(__file__).resolve().parent / "persona.json"

_BOT_UIN = {"value": ""}


def persona() -> dict:
    """读人设。改文件即生效，不用重启（每次调用都重新读）。"""
    try:
        return json.loads(PERSONA_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"人设文件读不了：{exc}"}


def bot_uin() -> str:
    if not _BOT_UIN["value"]:
        try:
            info = onebot("get_login_info").get("data") or {}
            _BOT_UIN["value"] = str(info.get("user_id") or "")
        except Exception:
            pass
    return _BOT_UIN["value"]


def sync_history(target_type: str, target_id: str | int, count: int = 20) -> tuple[str, int]:
    """把最近的聊天记录拉进记忆库（按 message_id 幂等去重）。返回 (session, 新增条数)。"""
    t, tid = target_of(target_type, target_id)
    session = f"{t}:{tid}"
    action = "get_group_msg_history" if t == "group" else "get_friend_msg_history"
    key = "group_id" if t == "group" else "user_id"
    data = onebot(action, {key: tid, "count": count}).get("data") or {}
    me = bot_uin()
    added = 0
    for m in data.get("messages") or []:
        mid = str(m.get("message_id") or "")
        # 事件级去重：QQ 会重推，处理过的直接跳过
        if mid and not memory.mark_seen(f"{session}:{mid}"):
            continue
        sender = m.get("sender") or {}
        uid = str(sender.get("user_id") or "")
        name = sender.get("card") or sender.get("nickname") or uid
        text = "".join(
            seg.get("data", {}).get("text", "")
            for seg in (m.get("message") or [])
            if seg.get("type") == "text"
        )
        ok = memory.log_message(
            session,
            msg_id=mid or None,
            speaker_id=uid,
            speaker=name,
            role="bot" if uid == me else "user",
            text=text,
            ts=int(m.get("time") or 0) or None,
        )
        added += 1 if ok else 0
    return session, added


# ---------------------------------------------------------------- 工具实现
def t_status(_: dict) -> str:
    info = onebot("get_login_info")
    groups = onebot("get_group_list")
    friends = onebot("get_friend_list")
    return json.dumps(
        {
            "onebot": ONEBOT,
            "login": info.get("data"),
            "groups": len(groups.get("data") or []),
            "friends": len(friends.get("data") or []),
        },
        ensure_ascii=False,
        indent=2,
    )


def t_list_groups(_: dict) -> str:
    data = onebot("get_group_list").get("data") or []
    rows = [{"group_id": g.get("group_id"), "name": g.get("group_name")} for g in data]
    return json.dumps(rows, ensure_ascii=False, indent=2)


def t_list_friends(_: dict) -> str:
    data = onebot("get_friend_list").get("data") or []
    rows = [{"user_id": f.get("user_id"), "name": f.get("nickname") or f.get("remark")} for f in data]
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _gate(kind: str, t: str, tid: int) -> None:
    """发送前的三道闸：急停 → 白名单 → 频率。任一不过就抛错。"""
    paused, why = guard.is_paused()
    if paused:
        raise RuntimeError(why)
    ok, why = guard.can_send(t, tid)
    if not ok:
        raise RuntimeError(why)
    limits = guard.load_policy().get(kind) or {}
    ok, why = guard.check_rate(
        kind,
        per_hour=limits.get("max_per_hour"),
        cooldown_ms=int(limits.get("cooldown_ms") or 0),
    )
    if not ok:
        raise RuntimeError(why)


def t_send_text(args: dict) -> str:
    t, tid = target_of(args["target_type"], args["target_id"])
    _gate("text", t, tid)

    text, issues = privacy.sanitize_outbound(args["text"])
    if issues:
        raise RuntimeError(f"出站审查拦截：{'、'.join(issues)}。内容里疑似含本机信息，改一改再发。")

    res = send_segments(args["target_type"], args["target_id"],
                        [{"type": "text", "data": {"text": text}}])
    guard.record_send("text")
    memory.log_message(
        f"{t}:{tid}",
        msg_id=str(res.get("data", {}).get("message_id") or "") or None,
        speaker_id=bot_uin(), speaker="小鲸鱼", role="bot", text=text,
    )
    return json.dumps({"message_id": res.get("data", {}).get("message_id")}, ensure_ascii=False)


def t_send_voice(args: dict) -> str:
    t, tid = target_of(args["target_type"], args["target_id"])
    _gate("voice", t, tid)

    text, issues = privacy.sanitize_outbound(args["text"])
    if issues:
        raise RuntimeError(f"出站审查拦截：{'、'.join(issues)}。语音会把内容念出来，改一改再发。")

    # 8GB 显存只能跑一个实例，合成必须串行
    with lock.exclusive("tts", timeout=300):
        wav = synth(text, args.get("instruction"), args.get("voice"), args.get("cfg_scale"))

    res = send_record(args["target_type"], args["target_id"], wav)
    guard.record_send("voice")
    memory.log_message(
        f"{t}:{tid}",
        msg_id=str(res.get("data", {}).get("message_id") or "") or None,
        speaker_id=bot_uin(), speaker="小鲸鱼", role="bot",
        text=f"[语音] {text}",
    )
    return json.dumps(
        {
            "message_id": res.get("data", {}).get("message_id"),
            "wav": str(wav),
            "size_kb": round(wav.stat().st_size / 1024),
        },
        ensure_ascii=False,
    )


def t_recent(args: dict) -> str:
    t, tid = target_of(args["target_type"], args["target_id"])
    action = "get_group_msg_history" if t == "group" else "get_friend_msg_history"
    key = "group_id" if t == "group" else "user_id"
    data = onebot(action, {key: tid, "count": int(args.get("count") or 20)}).get("data") or {}
    msgs = data.get("messages") or []
    rows = []
    for m in msgs[-int(args.get("count") or 20):]:
        sender = (m.get("sender") or {}).get("nickname") or (m.get("sender") or {}).get("user_id")
        parts = []
        for seg in m.get("message") or []:
            if seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
            else:
                parts.append(f"[{seg.get('type')}]")
        rows.append({"from": sender, "text": "".join(parts)})
    return json.dumps(rows, ensure_ascii=False, indent=2)


def t_persona(_: dict) -> str:
    return json.dumps(persona(), ensure_ascii=False, indent=2)


def t_context(args: dict) -> str:
    """回复前调用一次：同步最新消息 → 返回人设 + 最近对话 + 相关回忆 + 事实。"""
    t, tid = target_of(args["target_type"], args["target_id"])
    session, added = sync_history(args["target_type"], args["target_id"], int(args.get("sync") or 20))
    p = persona()
    scene = "group" if t == "group" else "private"
    recent = memory.recent(session, int(args.get("recent") or 15))

    query = str(args.get("query") or "").strip()
    related = memory.search(query, limit=6) if query else []
    seen = {(r["ts"], r["text"]) for r in recent}
    related = [r for r in related if (r["ts"], r["text"]) not in seen]

    # 入站脱敏：模型只看得到占位符，真值不出本机
    keep = {bot_uin(), str(tid), str(args["target_id"])}
    for rows in (recent, related):
        for r in rows:
            r["text"], _ = privacy.redact(r["text"], keep=keep)

    facts = memory.get_facts("global", 20) + memory.get_facts(f"session:{session}", 10)
    return json.dumps(
        {
            "session": session,
            "synced_new": added,
            "persona": {k: p.get(k) for k in ("identity", "traits", "style", "boundaries", "examples")},
            "voice": {**(p.get("voice") or {}), "scene": scene},
            "recent": recent,
            "related": related,
            "facts": facts,
            "hint": "按 persona.style 控制长度和口癖；related 是过去的相关片段，理解即可，不要逐字复述；"
                    "要发语音就调 qq_send_voice 并把文字放进 text。",
        },
        ensure_ascii=False,
        indent=2,
    )


def t_remember(args: dict) -> str:
    kind = str(args.get("kind") or "episode")
    if kind == "fact":
        scope = "global"
        if args.get("target_type") and args.get("target_id"):
            t, tid = target_of(args["target_type"], args["target_id"])
            scope = f"session:{t}:{tid}"
        memory.set_fact(str(args["key"]), str(args["value"]), scope,
                        float(args.get("confidence") or 1.0))
        return json.dumps({"ok": True, "scope": scope, "key": args["key"]}, ensure_ascii=False)

    if not args.get("target_type") or not args.get("target_id"):
        raise RuntimeError("kind=episode 需要 target_type + target_id")
    t, tid = target_of(args["target_type"], args["target_id"])
    ok = memory.log_message(f"{t}:{tid}", msg_id=None, speaker_id=bot_uin(),
                            speaker="小鲸鱼", role="system",
                            text=str(args["text"]), kind="event")
    return json.dumps({"ok": ok, "session": f"{t}:{tid}"}, ensure_ascii=False)


TARGET_SCHEMA = {
    "target_type": {"type": "string", "enum": ["group", "private"], "description": "group=群聊，private=私聊"},
    "target_id": {"type": "string", "description": "群号或 QQ 号"},
}

TOOLS = [
    {
        "name": "qq_context",
        "description": (
            "【回复前先调这个】一次拿到回话所需的全部上下文：人设、声音配置、最近的对话、"
            "和 query 相关的过往回忆、以及记住的事实。会顺带把最新聊天记录同步进记忆库。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **TARGET_SCHEMA,
                "query": {"type": "string", "description": "可选：这次要聊的话题关键词，用来检索过往相关片段"},
                "recent": {"type": "integer", "description": "最近多少条对话，默认 15"},
                "sync": {"type": "integer", "description": "从 OneBot 拉多少条历史入库，默认 20"},
            },
            "required": ["target_type", "target_id"],
        },
        "handler": t_context,
    },
    {
        "name": "qq_persona",
        "description": "读取当前人设配置（身份、性格、说话风格、边界、示例、声音参数）。",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": t_persona,
    },
    {
        "name": "qq_remember",
        "description": (
            "把值得记住的东西写进记忆库。kind=fact 存稳定事实（如「她在找工作」，用 key/value）；"
            "kind=episode 存事件片段（如「今天聊到衡水的一家公司」，用 text，需带 target）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["fact", "episode"], "description": "默认 episode"},
                "key": {"type": "string", "description": "kind=fact 时必填，如 job_status"},
                "value": {"type": "string", "description": "kind=fact 时必填，如「在看衡水的公司」"},
                "text": {"type": "string", "description": "kind=episode 时必填，事件描述"},
                "confidence": {"type": "number", "description": "可选：这条事实的可信度 0~1"},
                "target_type": {"type": "string", "enum": ["group", "private"]},
                "target_id": {"type": "string"},
            },
            "required": ["kind"],
        },
        "handler": t_remember,
    },
    {
        "name": "qq_status",
        "description": "检查 QQ 连接状态：OneBot 是否在线、当前登录账号、群和好友数量。",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": t_status,
    },
    {
        "name": "qq_list_groups",
        "description": "列出机器人所在的所有群（群号 + 群名）。",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": t_list_groups,
    },
    {
        "name": "qq_list_friends",
        "description": "列出机器人的好友（QQ 号 + 昵称）。",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": t_list_friends,
    },
    {
        "name": "qq_send_text",
        "description": "给指定群或好友发送一条文字消息。",
        "inputSchema": {
            "type": "object",
            "properties": {**TARGET_SCHEMA, "text": {"type": "string", "description": "消息正文"}},
            "required": ["target_type", "target_id", "text"],
        },
        "handler": t_send_text,
    },
    {
        "name": "qq_send_voice",
        "description": (
            "用本地 Breeze TTS 2 把文字合成语音，再以 QQ 语音消息发出去。"
            "适合需要'说话'而不是打字的场景；合成约 1~2 秒，返回 wav 路径。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **TARGET_SCHEMA,
                "text": {"type": "string", "description": "要朗读的文字；可用 [笑] [叹气] 等语气标签"},
                "instruction": {"type": "string", "description": "音色/表演描述，如'温柔的年轻女声，语速偏慢'"},
                "voice": {"type": "string", "description": "已保存的音色名（可选，需先用 tts.py --save-voice 存过）"},
                "cfg_scale": {"type": "number", "description": "指令强度 1.0~3.0，语气标签建议 2~2.5"},
            },
            "required": ["target_type", "target_id", "text"],
        },
        "handler": t_send_voice,
    },
    {
        "name": "qq_recent",
        "description": "读取某个群或好友最近的聊天记录，用于了解上下文再回复。",
        "inputSchema": {
            "type": "object",
            "properties": {
                **TARGET_SCHEMA,
                "count": {"type": "integer", "description": "条数，默认 20，最多 50"},
            },
            "required": ["target_type", "target_id"],
        },
        "handler": t_recent,
    },
]

HANDLERS = {t["name"]: t["handler"] for t in TOOLS}
PUBLIC_TOOLS = [{k: v for k, v in t.items() if k != "handler"} for t in TOOLS]


# ---------------------------------------------------------------- MCP 协议
def reply(msg_id, result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> None:
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        want = (msg.get("params") or {}).get("protocolVersion")
        reply(
            msg_id,
            {
                "protocolVersion": want or PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "breeze-qq", "version": "0.1.0"},
            },
        )
        return

    if method in ("notifications/initialized", "initialized"):
        return

    if method == "ping":
        reply(msg_id, {})
        return

    if method == "tools/list":
        reply(msg_id, {"tools": PUBLIC_TOOLS})
        return

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if not fn:
            reply(msg_id, {"content": [{"type": "text", "text": f"未知工具：{name}"}], "isError": True})
            return
        try:
            text = fn(args)
            reply(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as exc:  # noqa: BLE001
            reply(msg_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        return

    if msg_id is not None:
        reply(msg_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, list):
            for m in msg:
                handle(m)
        else:
            handle(msg)


if __name__ == "__main__":
    main()
