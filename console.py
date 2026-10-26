#!/usr/bin/env python
"""console —— 本地控制台（L2 控制面）。

单页，只绑 127.0.0.1，不对外。

    python console.py              # http://127.0.0.1:3100
    python console.py --port 3200

四个区块：状态灯、暂停开关、最近消息、错误。数据每 3 秒拉一次。
展示的消息会遮掉手机号/身份证——控制台可能被截图，别让它成为新的泄露源。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "qq_mcp"))

import guard    # noqa: E402
import lock     # noqa: E402
import memory   # noqa: E402
import privacy  # noqa: E402

ONEBOT = "http://127.0.0.1:3000"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
NAP_LOG = ROOT / "outputs" / "napcat_run.log"
STARTED = time.time()


def onebot(action: str, timeout: float = 2.0):
    req = urllib.request.Request(f"{ONEBOT}/{action}", data=b"{}",
                                 headers={"Content-Type": "application/json"}, method="POST")
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def mask(text: str) -> str:
    out = text or ""
    for _, pat in privacy.OUTBOUND_MASK:
        out = pat.sub("[已隐藏]", out)
    return out


def status_payload() -> dict:
    paused, why = guard.is_paused()
    online, who = False, ""
    try:
        info = onebot("get_login_info").get("data") or {}
        online = bool(info.get("user_id"))
        who = f"{info.get('nickname') or ''} ({info.get('user_id')})" if online else ""
    except Exception:
        pass

    pol = guard.load_policy()
    allow = pol.get("allow") or {}
    try:
        mem = memory.stats()
    except Exception:
        mem = {"episodes": 0, "facts": 0, "sessions": 0}

    errors: list[str] = []
    if NAP_LOG.exists():
        tail = NAP_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]
        errors = [ln[:140] for ln in tail if "[error]" in ln or "错误" in ln][-5:]

    return {
        "paused": paused,
        "paused_reason": why,
        "online": online,
        "account": who,
        "queue_busy": lock.is_busy(),
        "memory": mem,
        "allow_private": len(allow.get("private") or []),
        "allow_groups": len(allow.get("groups") or []),
        "allow_all": bool(pol.get("allow_all_when_empty")),
        "sent": guard.stats(),
        "uptime": int(time.time() - STARTED),
        "errors": errors,
    }


def recent_payload(limit: int = 20) -> list[dict]:
    with memory._conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            "SELECT session, speaker, role, text, ts FROM episodes ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "session": r["session"],
            "speaker": r["speaker"] or "",
            "role": r["role"],
            "text": mask(r["text"]),
            "time": time.strftime("%H:%M", time.localtime(r["ts"])),
        })
    return out


PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Breeze QQ Bot · 控制台</title>
<style>
:root{--bg:#F7F7F5;--card:#fff;--line:#E4E2DC;--text:#2C2C2A;--mute:#888780;
--ok:#0F6E56;--okbg:#E1F5EE;--bad:#A32D2D;--badbg:#FCEBEB;--warn:#854F0B;--warnbg:#FAEEDA}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:13px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:24px 20px 60px}
h1{font-size:15px;font-weight:500;margin:0 0 4px}
.sub{color:var(--mute);font-size:12px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.card .k{color:var(--mute);font-size:12px;margin-bottom:6px}
.card .v{font-size:15px;font-weight:500}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}
.ok{color:var(--ok)}.ok .dot{background:var(--ok)}
.bad{color:var(--bad)}.bad .dot{background:var(--bad)}
.warn{color:var(--warn)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:14px;margin-bottom:16px}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
button{border:1px solid var(--line);background:#fff;color:var(--text);
border-radius:8px;padding:7px 14px;font-size:13px;cursor:pointer}
button:hover{background:#F1EFE8}
button.primary{background:var(--badbg);border-color:#F7C1C1;color:var(--bad)}
button.resume{background:var(--okbg);border-color:#9FE1CB;color:var(--ok)}
select{border:1px solid var(--line);border-radius:8px;padding:7px;font-size:13px;background:#fff}
.msg{padding:8px 0;border-bottom:1px solid var(--line);display:flex;gap:10px}
.msg:last-child{border-bottom:0}
.msg .t{color:var(--mute);font-size:12px;min-width:42px}
.msg .s{min-width:70px;color:var(--mute);font-size:12px;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.msg .x{flex:1;word-break:break-all}
.bot{color:var(--ok)}
.err{background:var(--badbg);color:var(--bad);border-radius:8px;padding:8px 10px;
font-family:ui-monospace,Consolas,monospace;font-size:12px;margin-top:6px;word-break:break-all}
.empty{color:var(--mute);padding:10px 0}
</style></head><body><div class="wrap">
<h1>Breeze QQ Bot · 控制台</h1>
<div class="sub" id="sub">连接中…</div>

<div class="grid">
  <div class="card"><div class="k">运行状态</div><div class="v" id="st-run">—</div></div>
  <div class="card"><div class="k">QQ 登录</div><div class="v" id="st-qq">—</div></div>
  <div class="card"><div class="k">合成队列</div><div class="v" id="st-q">—</div></div>
  <div class="card"><div class="k">记忆</div><div class="v" id="st-mem">—</div></div>
</div>

<div class="panel">
  <div class="row">
    <span id="pause-hint" style="flex:1"></span>
    <select id="mins">
      <option value="0">不自动恢复</option>
      <option value="15">15 分钟</option>
      <option value="30" selected>30 分钟</option>
      <option value="60">1 小时</option>
    </select>
    <button class="primary" onclick="act('pause')">暂停</button>
    <button class="resume" onclick="act('resume')">恢复</button>
  </div>
</div>

<div class="panel">
  <div class="k" style="color:var(--mute);font-size:12px;margin-bottom:8px">
    最近消息（手机号/身份证已遮挡）</div>
  <div id="msgs"><div class="empty">加载中…</div></div>
</div>

<div class="panel" id="errbox" style="display:none">
  <div class="k" style="color:var(--mute);font-size:12px">最近错误</div>
  <div id="errs"></div>
</div>

<script>
const $ = id => document.getElementById(id);
function fmt(sec){const m=Math.floor(sec/60),h=Math.floor(m/60);
  return h? h+" 小时 "+(m%60)+" 分" : m? m+" 分" : sec+" 秒";}
function setel(el,txt,cls){el.className="v "+(cls||"");el.innerHTML=
  (cls?"<span class='dot'></span>":"")+txt;}

async function refresh(){
  try{
    const s = await (await fetch('/api/status')).json();
    $('sub').textContent = '已运行 ' + fmt(s.uptime) + ' · 每 3 秒刷新';
    setel($('st-run'), s.paused ? '已暂停' : '正常', s.paused ? 'bad' : 'ok');
    $('pause-hint').textContent = s.paused ? s.paused_reason
      : '白名单：私聊 ' + s.allow_private + ' · 群 ' + s.allow_groups
        + (s.allow_all ? ' · ⚠ 全放行' : '');
    setel($('st-qq'), s.online ? s.account : '未连接', s.online ? 'ok' : 'bad');
    setel($('st-q'), s.queue_busy ? '忙' : '空闲', s.queue_busy ? '' : 'ok');
    setel($('st-mem'), s.memory.episodes + ' 条 / ' + s.memory.facts + ' 事实', '');
    if (s.errors && s.errors.length){
      $('errbox').style.display='block';
      $('errs').innerHTML = s.errors.map(e=>'<div class="err">'+e+'</div>').join('');
    } else { $('errbox').style.display='none'; }
  }catch(e){ $('sub').textContent='控制台读不到状态：'+e; }
  try{
    const r = await (await fetch('/api/recent?limit=20')).json();
    $('msgs').innerHTML = r.length ? r.map(m=>
      '<div class="msg"><span class="t">'+m.time+'</span>'
      +'<span class="s">'+(m.speaker||m.session)+'</span>'
      +'<span class="x '+(m.role==='bot'?'bot':'')+'">'+m.text+'</span></div>'
    ).join('') : '<div class="empty">还没有消息</div>';
  }catch(e){}
}

async function act(what){
  const body = what==='pause'
    ? {minutes: Number($('mins').value) || 0, reason: '控制台手动暂停'}
    : {};
  await fetch('/api/'+what, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)});
  refresh();
}
refresh(); setInterval(refresh, 3000);
</script></div></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/api/status":
            return self._json(status_payload())
        if u.path == "/api/recent":
            q = urllib.parse.parse_qs(u.query)
            return self._json(recent_payload(int((q.get("limit") or ["20"])[0])))
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
        except json.JSONDecodeError:
            body = {}
        if u.path == "/api/pause":
            mins = float(body.get("minutes") or 0)
            msg = guard.pause(mins or None, str(body.get("reason") or "控制台暂停"))
            return self._json({"ok": True, "message": msg})
        if u.path == "/api/resume":
            return self._json({"ok": True, "message": guard.resume()})
        self._json({"error": "not found"}, 404)

    def log_message(self, *_a) -> None:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Breeze QQ Bot 控制台")
    ap.add_argument("--port", type=int, default=3100)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"控制台已启动： http://{args.host}:{args.port}")
    print("只监听本机，Ctrl+C 退出（不影响机器人）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
