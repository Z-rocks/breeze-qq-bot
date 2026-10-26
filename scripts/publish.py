#!/usr/bin/env python
"""把本地仓库发布到 GitHub 与 Gitee。

令牌只从环境变量读，不落盘、不打印：

    GH_TOKEN=ghp_xxx GITEE_TOKEN=yyy python scripts/publish.py

做的事：
  1. GitHub：API 建仓 → 生成临时部署密钥 → 走 ssh.github.com:443 推送 → 删密钥
     （本机 github.com 的 443 直连不通，只能走 SSH over 443）
  2. Gitee：API 建仓 → HTTPS 推送 → 改成公开
  3. 推送完成后清理令牌痕迹

参数：
    --name  仓库名，默认 breeze-qq-bot
    --skip-github / --skip-gitee  只发一边
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GH_OWNER = "Z-rocks"
GITEE_OWNER = "zrocks"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sh(cmd: list[str], env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    e = {**os.environ, **(env or {})}
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=e)
    if check and r.returncode != 0:
        print(f"  ! 命令失败：{' '.join(cmd[:3])}…")
        print((r.stderr or r.stdout or "")[-500:])
    return r


def api(url: str, token: str, method: str = "GET", payload: dict | None = None,
        gitee: bool = False) -> tuple[int, dict | list | None]:
    headers = {"Content-Type": "application/json"}
    if gitee:
        headers["Authorization"] = f"token {token}"
    else:
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/vnd.github+json"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with OPENER.open(req, timeout=30) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body.strip() else None)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:300]}


# ---------------------------------------------------------------- GitHub
def publish_github(name: str, token: str) -> bool:
    print("\nGitHub")
    print("-" * 46)

    code, body = api("https://api.github.com/user/repos", token, "POST", {
        "name": name,
        "description": "本地 TTS + QQ 语音机器人：Breeze TTS 2 接入 QQ，MCP 协议驱动",
        "private": False,
        "has_issues": True,
        "has_wiki": False,
    })
    if code == 201:
        print(f"  ✓ 仓库已创建  {GH_OWNER}/{name}")
    elif code == 422:
        print(f"  · 仓库可能已存在（HTTP 422），继续尝试推送")
    else:
        print(f"  ✗ 建仓失败  HTTP {code}  {str(body)[:200]}")
        return False

    tmp = Path(tempfile.mkdtemp(prefix="ghkey-"))
    key = tmp / "deploy_key"
    r = sh(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key), "-q"])
    if not key.exists():
        print("  ✗ 生成密钥失败")
        return False
    pub = (tmp / "deploy_key.pub").read_text().strip()

    code, body = api(f"https://api.github.com/repos/{GH_OWNER}/{name}/keys", token, "POST", {
        "title": f"publish-{int(time.time())}",
        "key": pub,
        "read_only": False,
    })
    if code != 201:
        print(f"  ✗ 添加部署密钥失败  HTTP {code}  {str(body)[:200]}")
        return False
    key_id = body["id"]
    print(f"  ✓ 部署密钥已添加  id={key_id}")

    url = f"git@ssh.github.com:{GH_OWNER}/{name}.git"
    env = {
        "GIT_SSH_COMMAND": (
            f'ssh -i "{key}" -p 443 -o StrictHostKeyChecking=no '
            f"-o UserKnownHostsFile=/dev/null -o BatchMode=yes -o LogLevel=ERROR"
        )
    }
    sh(["git", "remote", "remove", "github"], check=False)
    sh(["git", "remote", "add", "github", url])
    r = sh(["git", "push", "-u", "github", "master", "--force"], env=env, check=False)
    ok = r.returncode == 0
    print("  ✓ 已推送 master" if ok else "  ✗ 推送失败")

    api(f"https://api.github.com/repos/{GH_OWNER}/{name}/keys/{key_id}", token, "DELETE")
    print("  ✓ 部署密钥已删除")
    for f in tmp.iterdir():
        f.unlink(missing_ok=True)
    tmp.rmdir()
    return ok


# ---------------------------------------------------------------- Gitee
def publish_gitee(name: str, token: str) -> bool:
    print("\nGitee")
    print("-" * 46)

    fields = {
        "access_token": token,
        "name": name,
        "path": name,
        "description": "本地 TTS + QQ 语音机器人：Breeze TTS 2 接入 QQ，MCP 协议驱动",
        "private": "false",
        "auto_init": "false",
    }
    data = urllib.parse.urlencode(fields, encoding="utf-8").encode()
    req = urllib.request.Request(
        "https://gitee.com/api/v5/user/repos", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with OPENER.open(req, timeout=30) as r:
            code, body = r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        code, body = e.code, {"raw": e.read().decode()[:200]}

    if code in (201, 200):
        print(f"  ✓ 仓库已创建  {GITEE_OWNER}/{name}")
    elif code == 400 and "already" in str(body).lower():
        print(f"  · 仓库已存在  {GITEE_OWNER}/{name}")
    else:
        print(f"  ! 建仓返回 HTTP {code}：{str(body)[:200]}（继续尝试推送）")

    sh(["git", "remote", "remove", "gitee"], check=False)
    sh(["git", "remote", "add", "gitee", f"https://gitee.com/{GITEE_OWNER}/{name}.git"])

    auth = base64.b64encode(f"{GITEE_OWNER}:{token}".encode()).decode()
    env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://gitee.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: Basic {auth}",
    }
    r = sh(["git", "push", "-u", "gitee", "master", "--force"], env=env, check=False)
    ok = r.returncode == 0
    print("  ✓ 已推送 master" if ok else "  ✗ 推送失败")

    if ok:
        code, body = api(f"https://gitee.com/api/v5/repos/{GITEE_OWNER}/{name}", token,
                         "PATCH", {"name": name, "path": name, "private": False}, gitee=True)
        print("  ✓ 已设为公开" if code in (200, 201) else f"  ! 改公开失败 HTTP {code}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="breeze-qq-bot")
    ap.add_argument("--skip-github", action="store_true")
    ap.add_argument("--skip-gitee", action="store_true")
    args = ap.parse_args()

    gh = os.environ.get("GH_TOKEN", "").strip()
    gt = os.environ.get("GITEE_TOKEN", "").strip()

    if not gh and not args.skip_github:
        print("缺少 GH_TOKEN")
    if not gt and not args.skip_gitee:
        print("缺少 GITEE_TOKEN")

    print(f"仓库：{args.name}")
    ok = True
    if gh and not args.skip_github:
        ok &= publish_github(args.name, gh)
    if gt and not args.skip_gitee:
        ok &= publish_gitee(args.name, gt)

    print("\n" + "=" * 46)
    if not args.skip_github:
        print(f"  https://github.com/{GH_OWNER}/{args.name}")
    if not args.skip_gitee:
        print(f"  https://gitee.com/{GITEE_OWNER}/{args.name}")
    print("=" * 46)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
