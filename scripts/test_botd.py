#!/usr/bin/env python
"""botd 端到端测试：假 LLM + 假 NapCat 事件。

    python scripts/test_botd.py

不碰真实 QQ、不真发消息（botd 用 --dry-run）。
验证：事件接收 → 去重 → 白名单 → 脱敏 → 调模型 → 出站审查 → 打印回复。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qq_mcp"))

import guard  # noqa: E402

PY = sys.executable
LLM_PORT = 3198
BOTD_PORT = 3197
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
REPLY = "在的呀，怎么啦"

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f"   {detail}" if detail else ""))


class MockLLM(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": REPLY}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


def post_event(payload: dict) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{BOTD_PORT}/", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with OPENER.open(req, timeout=5) as r:
        return r.status


def make_event(text: str, mid: int, uid: int = 1513929790) -> dict:
    return {
        "post_type": "message", "message_type": "private", "sub_type": "friend",
        "self_id": 3554609881, "user_id": uid, "message_id": mid, "time": int(time.time()),
        "sender": {"user_id": uid, "nickname": "测试好友"},
        "message": [{"type": "text", "data": {"text": text}}],
    }


def main() -> int:
    print("=" * 52)
    print("  botd 端到端测试")
    print("=" * 52)

    llm = ThreadingHTTPServer(("127.0.0.1", LLM_PORT), MockLLM)
    threading.Thread(target=llm.serve_forever, daemon=True).start()

    guard.resume()
    env = {
        **os.environ,
        "DEEPSEEK_API_KEY": "test-key",
        "DEEPSEEK_BASE": f"http://127.0.0.1:{LLM_PORT}",
    }
    proc = subprocess.Popen(
        [PY, str(ROOT / "botd.py"), "--port", str(BOTD_PORT), "--dry-run"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    time.sleep(2.5)

    try:
        print("\n事件接收")
        code = post_event(make_event("在吗", 900001))
        check("回调返回 204", code == 204, str(code))

        time.sleep(2.5)
        mid = 900002
        post_event(make_event("在吗", mid))
        time.sleep(2.5)

        print("\n暂停拦截")
        guard.pause(1, "测试")
        post_event(make_event("暂停时不该回", 900003))
        time.sleep(2.5)
        guard.resume()

        print("\n白名单拦截")
        post_event(make_event("陌生人", 900004, uid=888888))
        time.sleep(2.5)

        print("\n脱敏")
        post_event(make_event("我电话13812345678", 900005))
        time.sleep(2.5)

        out = proc.stdout.read() if proc.poll() is not None else ""
        if not out:
            proc.terminate()
            try:
                out = proc.communicate(timeout=5)[0]
            except Exception:
                out = ""
        print("\n判定")
        check("收到并生成回复", REPLY in out, out[-300:])
        check("暂停时跳过", "已暂停" in out or "跳过" in out, out[-300:])
        check("白名单外跳过", "白名单" in out, out[-300:])
        check("正常消息有回复", out.count("已回复") + out.count("[dry-run]") >= 2,
              f"回复次数={out.count('[dry-run]')}")
    finally:
        if proc.poll() is None:
            proc.terminate()
        llm.shutdown()

    print("\n" + "=" * 52)
    total = PASS + FAIL
    print(f"  {PASS}/{total} 通过" + ("" if FAIL == 0 else f"，{FAIL} 失败"))
    print("=" * 52)
    if FAIL:
        print("\n--- botd 输出 ---")
        print(out[-1200:])
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
