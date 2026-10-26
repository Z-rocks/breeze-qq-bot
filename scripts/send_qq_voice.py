"""一条命令跑完：拉起 NapCat → 等 OneBot 就绪 → 合成语音 → 发 QQ 语音消息。

为什么要写成一个脚本：NapCat 的 QQ 进程必须活到「消息发出去」那一刻，
而分开调用工具时后台进程会被回收。所以把整条链放进同一个进程里。

用法：
    python scripts/send_qq_voice.py <目标QQ> [文字] [--instruction ...]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAP_DIR = ROOT / "bin" / "napcat" / "NapCat.52230.Shell"
NAP_LOG = ROOT / "outputs" / "napcat_run.log"
TTS = ROOT / "tts.py"
PY = sys.executable
ONEBOT = "http://127.0.0.1:3000"
BOT_UIN = "3554609881"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(action: str, payload: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload or {}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        f"{ONEBOT}/{action}", data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def kill_old():
    for name in ("NapCatWinBootMain.exe", "QQ.exe"):
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)


def boot(timeout=150) -> bool:
    NAP_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(NAP_LOG, "w", encoding="utf-8", errors="replace")
    subprocess.Popen(
        [str(NAP_DIR / "NapCatWinBootMain.exe"), BOT_UIN],
        cwd=str(NAP_DIR),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    started = time.time()
    while time.time() - started < timeout:
        time.sleep(3)
        try:
            info = api("get_login_info", {}, timeout=4)
            if info.get("status") == "ok" and info.get("data", {}).get("user_id"):
                print(f"[ok] 已登录：{info['data']['user_id']} ({info['data'].get('nickname')}) "
                      f"耗时 {time.time() - started:.0f}s", flush=True)
                return True
        except Exception:
            pass
        print(f"  等待 OneBot… {time.time() - started:.0f}s", flush=True)
    return False


def synth(text: str, out: Path, instruction: str, cfg: float):
    cmd = [PY, str(TTS), text, "--out", str(out), "--instruction", instruction,
           "--cfg-scale", str(cfg)]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        print(r.stdout[-800:], r.stderr[-800:])
        raise SystemExit("合成失败")
    print(f"[ok] 合成完成：{out.name} ({out.stat().st_size // 1024} KB)", flush=True)


def send_voice(target: int, wav: Path) -> dict:
    msg = [{"type": "record", "data": {"file": wav.as_uri()}}]
    res = api("send_private_msg", {"user_id": int(target), "message": msg}, timeout=180)
    if res.get("status") != "ok" or res.get("retcode") != 0:
        # 退回 base64
        import base64
        b64 = "base64://" + base64.b64encode(wav.read_bytes()).decode()
        res = api("send_private_msg",
                  {"user_id": int(target), "message": [{"type": "record", "data": {"file": b64}}]},
                  timeout=180)
    return res


def main():
    target = int(sys.argv[1])
    text = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else (
        "[笑] 嗨！我是小鲸鱼，特地跑来跟你打个招呼～今天也要元气满满哦！"
    )
    instruction = "活泼开朗的少女音，语调轻快上扬，语气俏皮，带着笑意"
    cfg = 2.0
    wav_arg = None
    for i, a in enumerate(sys.argv):
        if a == "--instruction" and i + 1 < len(sys.argv):
            instruction = sys.argv[i + 1]
        elif a == "--cfg" and i + 1 < len(sys.argv):
            cfg = float(sys.argv[i + 1])
        elif a == "--wav" and i + 1 < len(sys.argv):
            wav_arg = sys.argv[i + 1]

    print("[1/5] 清理旧进程…", flush=True)
    kill_old()
    time.sleep(2)

    print("[2/5] 启动 NapCat…", flush=True)
    if not boot():
        print("[fail] OneBot 没起来，日志尾部：")
        print(NAP_LOG.read_text(encoding="utf-8", errors="replace")[-1500:])
        raise SystemExit(1)

    print("[3/5] 检查好友列表…", flush=True)
    friends = api("get_friend_list").get("data") or []
    hit = [f for f in friends if int(f.get("user_id", 0)) == target]
    if hit:
        print(f"[ok] {target} 在好友列表里：{hit[0].get('nickname') or hit[0].get('remark')}", flush=True)
    else:
        print(f"[warn] {target} 不在好友列表（好友共 {len(friends)} 个），仍按私聊发送", flush=True)

    if wav_arg:
        wav = Path(wav_arg)
        if not wav.is_absolute():
            wav = ROOT / wav
        if not wav.exists():
            raise SystemExit(f"找不到音频：{wav}")
        print(f"[4/5] 使用现成音频：{wav.name}", flush=True)
    else:
        wav = ROOT / "outputs" / "greeting_girl.wav"
        if not wav.exists():
            print("[4/5] 合成语音…", flush=True)
            synth(text, wav, instruction, cfg)
        else:
            print(f"[4/5] 复用已合成音频：{wav.name}", flush=True)

    print("[5/5] 发送语音…", flush=True)
    res = send_voice(target, wav)
    print("[result] " + json.dumps(res, ensure_ascii=False)[:600], flush=True)


if __name__ == "__main__":
    main()
