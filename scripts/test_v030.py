#!/usr/bin/env python
"""v0.3.0 回归：事件去重 + 控制台接口。

    python scripts/test_v030.py

控制台在测试进程内起一个线程，不占用真实端口（用 3199）。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qq_mcp"))

import console   # noqa: E402
import guard     # noqa: E402
import memory    # noqa: E402

PASS, FAIL = 0, 0
PORT = 3199
BASE = f"http://127.0.0.1:{PORT}"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f"   {detail}" if detail else ""))


def get(path: str):
    with OPENER.open(BASE + path, timeout=5) as r:
        return r.status, r.read().decode()


def post(path: str, payload: dict):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with OPENER.open(req, timeout=5) as r:
        return json.loads(r.read().decode())


def main() -> int:
    print("=" * 52)
    print("  v0.3.0 回归测试")
    print("=" * 52)

    # ---------------------------------------------------------- 去重
    print("\n事件去重")
    key = f"test:dedupe:{int(time.time() * 1000)}"
    check("首次是新事件", memory.mark_seen(key) is True)
    check("重复被识别", memory.mark_seen(key) is False)
    check("空 key 放行", memory.mark_seen("") is True)
    check("计数可查", memory.seen_stats()["seen"] >= 1, str(memory.seen_stats()))

    # ---------------------------------------------------------- 控制台
    print("\n控制台")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), console.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.6)

    try:
        code, html = get("/")
        check("首页 200", code == 200)
        check("页面含标题", "控制台" in html)
        check("页面含状态位", "st-run" in html and "st-qq" in html)

        code, body = get("/api/status")
        st = json.loads(body)
        check("状态接口可用", code == 200 and "paused" in st)
        check("含白名单字段", "allow_private" in st)

        code, body = get("/api/recent?limit=5")
        check("最近消息是数组", isinstance(json.loads(body), list))

        guard.resume()
        r = post("/api/pause", {"minutes": 1, "reason": "测试"})
        check("暂停接口返回 ok", r.get("ok") is True)
        st = json.loads(get("/api/status")[1])
        check("状态已变暂停", st["paused"] is True)
        check("暂停原因可见", "测试" in st["paused_reason"], st["paused_reason"])

        r = post("/api/resume", {})
        check("恢复接口返回 ok", r.get("ok") is True)
        st = json.loads(get("/api/status")[1])
        check("状态已恢复", st["paused"] is False)

        try:
            get("/api/nope")
            check("未知路径 404", False)
        except urllib.error.HTTPError as e:
            check("未知路径 404", e.code == 404)
    finally:
        srv.shutdown()

    print("\n" + "=" * 52)
    total = PASS + FAIL
    print(f"  {PASS}/{total} 通过" + ("" if FAIL == 0 else f"，{FAIL} 失败"))
    print("=" * 52)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
