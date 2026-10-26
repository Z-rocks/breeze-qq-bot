#!/usr/bin/env python
"""Breeze QQ Bot —— 一键启动。

做四件事：
  1. 预检（Python / 模型 / 推理程序 / NapCat / OneBot 配置 / MCP 注册）
  2. 拉起 NapCat（QQ 协议端），走快速登录
  3. 等 OneBot 就绪，打印状态面板
  4. 挂住进程；Ctrl+C 退出时一并关掉 NapCat

用法：
    python start.py            # 预检 + 启动 + 常驻
    python start.py --check    # 只预检，不启动
    python start.py --uin 123  # 指定 QQ 号（默认自动识别）
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAP_DIR = ROOT / "bin" / "napcat" / "NapCat.52230.Shell"
NAP_EXE = NAP_DIR / "NapCatWinBootMain.exe"
NAP_LOG = ROOT / "outputs" / "napcat_run.log"
CFG_DIR = NAP_DIR / "versions" / "9.9.33-52230" / "resources" / "app" / "napcat" / "config"
MCP_JSON = Path(os.path.expanduser("~")) / ".workbuddy-ai" / "mcp.json"
ONEBOT = "http://127.0.0.1:3000"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

OK, BAD, WARN = "  ✓", "  ✗", "  !"


# ---------------------------------------------------------------- 预检
def check_python() -> bool:
    ok = sys.version_info >= (3, 10)
    print(f"{OK if ok else BAD} Python {sys.version.split()[0]}" + ("" if ok else "（需要 3.10+）"))
    return ok


def check_file(path: Path, label: str, hint: str = "") -> bool:
    ok = path.exists()
    print(f"{OK if ok else BAD} {label}" + ("" if ok else f"  缺少：{path}" + (f"\n      {hint}" if hint else "")))
    return ok


def check_model() -> bool:
    models = list((ROOT / "models").glob("*.gguf"))
    if not models:
        print(f"{BAD} 模型权重   models/ 下没有 .gguf")
        print("      下载：python scripts/dl.py <hf-mirror 链接> models/xxx.gguf 8")
        return False
    for m in models:
        print(f"{OK} 模型权重      {m.name}  ({m.stat().st_size / 1073741824:.1f} GB)")
    return True


def check_onebot_config() -> tuple[bool, str]:
    files = sorted(CFG_DIR.glob("onebot11_*.json")) if CFG_DIR.exists() else []
    if not files:
        print(f"{BAD} OneBot 配置   没找到 onebot11_*.json")
        print("      先运行一次 NapCat 并登录，配置会自动生成")
        return False, ""
    f = files[-1]
    uin = f.stem.split("_", 1)[1]
    try:
        cfg = json.loads(f.read_text(encoding="utf-8"))
        servers = (cfg.get("network") or {}).get("httpServers") or []
        hit = [s for s in servers if int(s.get("port") or 0) == 3000 and s.get("enable")]
        if hit:
            print(f"{OK} OneBot HTTP   127.0.0.1:3000 已配置（账号 {uin}）")
            return True, uin
        print(f"{BAD} OneBot HTTP   配置里没有启用 3000 端口")
        print(f"      改 {f.name} 的 network.httpServers，或进 WebUI 添加")
        return False, uin
    except Exception as exc:  # noqa: BLE001
        print(f"{BAD} OneBot 配置   解析失败：{exc}")
        return False, uin


def check_mcp() -> bool:
    try:
        cfg = json.loads(MCP_JSON.read_text(encoding="utf-8"))
        ok = "qq" in (cfg.get("mcpServers") or {})
        print(f"{OK if ok else WARN} MCP 注册      " + ("qq 已在 mcp.json" if ok else "mcp.json 里没有 qq 条目"))
        if ok:
            print("      首次使用要在连接器管理页 →「自定义连接器」→ qq → 信任")
        return ok
    except Exception:
        print(f"{WARN} MCP 注册      读不到 {MCP_JSON}")
        return False


def preflight() -> tuple[bool, str]:
    print("\n预检")
    print("-" * 52)
    results = [
        check_python(),
        check_file(ROOT / "tts.py", "合成脚本      tts.py"),
        check_file(ROOT / "bin" / "breeze-cli.exe", "推理程序      bin/breeze-cli.exe"),
        check_model(),
        check_file(NAP_EXE, "NapCat        NapCatWinBootMain.exe"),
    ]
    cfg_ok, uin = check_onebot_config()
    results.append(cfg_ok)
    check_mcp()
    print("-" * 52)
    return all(results), uin


# ---------------------------------------------------------------- OneBot
def api(action: str, payload: dict | None = None, timeout: int = 5) -> dict:
    data = json.dumps(payload or {}, ensure_ascii=False).encode()
    req = urllib.request.Request(f"{ONEBOT}/{action}", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def port_open(port: int = 3000) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_ready(timeout: int = 150) -> dict | None:
    started = time.time()
    while time.time() - started < timeout:
        try:
            info = api("get_login_info")
            if info.get("status") == "ok" and (info.get("data") or {}).get("user_id"):
                return info["data"]
        except Exception:
            pass
        print(f"\r      等待 OneBot 就绪… {int(time.time() - started)}s", end="", flush=True)
        time.sleep(2)
    print()
    return None


# ---------------------------------------------------------------- 启动
def launch_napcat(uin: str) -> subprocess.Popen:
    NAP_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(NAP_LOG, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [str(NAP_EXE), uin],
        cwd=str(NAP_DIR),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    print(f"      NapCat PID {proc.pid}，日志 outputs/napcat_run.log")
    return proc


def kill_tree(proc: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                   capture_output=True)
    subprocess.run(["taskkill", "/F", "/IM", "NapCatWinBootMain.exe"], capture_output=True)


def panel(login: dict) -> None:
    uid = login.get("user_id")
    nick = login.get("nickname") or ""
    groups = friends = 0
    try:
        groups = len(api("get_group_list").get("data") or [])
        friends = len(api("get_friend_list").get("data") or [])
    except Exception:
        pass
    voices = len(list((ROOT / "voices").glob("*.breeze"))) if (ROOT / "voices").exists() else 0
    try:
        sys.path.insert(0, str(ROOT / "qq_mcp"))
        import memory  # noqa: PLC0415
        mem = memory.stats()
    except Exception:
        mem = {"episodes": 0, "facts": 0}

    print("\n" + "=" * 52)
    print(f"  已就绪    {nick} ({uid})")
    print("=" * 52)
    print(f"  OneBot    {ONEBOT}")
    print(f"  群 / 好友  {groups} / {friends}")
    print(f"  已存音色   {voices} 个")
    print(f"  记忆       {mem['episodes']} 条对话 · {mem['facts']} 条事实")
    print(f"  WebUI     http://127.0.0.1:6099/webui")
    print("=" * 52)
    print("  现在可以在 WorkBuddy 里直接说：")
    print("    「看看 QQ 登录了没」")
    print("    「用少女音跟 XX 说句晚安」")
    print("\n  Ctrl+C 停止\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Breeze QQ Bot 一键启动")
    ap.add_argument("--check", action="store_true", help="只预检，不启动")
    ap.add_argument("--uin", help="QQ 号，默认自动识别")
    args = ap.parse_args()

    print("=" * 52)
    print("  Breeze QQ Bot")
    print("=" * 52)

    ok, uin = preflight()
    uin = args.uin or uin

    if args.check:
        print("\n预检" + ("通过" if ok else "未通过，按上面的提示补齐") + "\n")
        return 0 if ok else 1
    if not ok:
        print("\n预检未通过，先修好上面的问题。\n")
        return 1

    if port_open(3000):
        print("\n" + "=" * 52)
        print("  3000 端口已在监听 —— NapCat 可能已经跑着了")
        print("=" * 52)
        try:
            panel(api("get_login_info")["data"])
        except Exception:
            print("  但 OneBot 没响应，建议先关掉旧的 NapCat 再启动")
        return 0

    print(f"\n启动 NapCat（账号 {uin}）")
    proc = launch_napcat(uin)
    login = wait_ready()

    if not login:
        print(f"{BAD} 启动超时。日志尾部：\n")
        try:
            print("\n".join(NAP_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]))
        except Exception:
            pass
        print("\n  常见原因：需要扫码登录 / QQ 号不对 / 端口被占用")
        kill_tree(proc)
        return 1

    panel(login)

    try:
        while proc.poll() is None:
            time.sleep(1)
        print("\nNapCat 已退出，检查日志：outputs/napcat_run.log")
        return 1
    except KeyboardInterrupt:
        print("\n正在停止…")
        kill_tree(proc)
        print("已停止。\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
