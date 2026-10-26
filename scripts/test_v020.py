#!/usr/bin/env python
"""v0.2.0 回归测试：白名单 / 脱敏 / 出站审查 / 急停 / 串行锁。

    python scripts/test_v020.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qq_mcp"))

import guard    # noqa: E402
import lock     # noqa: E402
import privacy  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f"   {detail}" if detail else ""))


def main() -> int:
    print("=" * 52)
    print("  v0.2.0 回归测试")
    print("=" * 52)

    # ---------------------------------------------------------- 脱敏
    print("\n入站脱敏")
    raw = "我手机13812345678，身份证110101199001011234，卡号6222021234567890123"
    masked, mapping = privacy.redact(raw)
    check("手机号被替换", "13812345678" not in masked, masked)
    check("身份证被替换", "110101199001011234" not in masked, masked)
    check("银行卡被替换", "6222021234567890123" not in masked, masked)
    check("占位符已生成", len(mapping) >= 3, str(mapping))
    check("原文已不含数字串", "1381" not in masked)

    kept, _ = privacy.redact("我的号是1513929790", keep={"1513929790"})
    check("白名单值不脱敏", "1513929790" in kept, kept)

    same, m2 = privacy.redact("打13812345678，再打一次13812345678")
    check("同一号码复用占位符", same.count("<PHONE_1>") == 2 and len(m2) == 1, same)

    # ---------------------------------------------------------- 出站
    print("\n出站审查")
    win_path = r"改一下 E:\project\breeze\tts.py"
    check("拦 Windows 路径（反斜杠）", "含本地路径" in privacy.audit(win_path), str(privacy.audit(win_path)))
    check("拦 Windows 路径（正斜杠）", "含本地路径" in privacy.audit("改一下 E:/project/tts.py"))
    check("拦 Unix 路径", "含Unix 路径" in privacy.audit("/home/adm/.ssh/id_rsa"),
          str(privacy.audit("/home/adm/.ssh/id_rsa")))
    check("拦代码块", "含代码块" in str(privacy.audit("这样写\n```python\nprint(1)\n```")))
    check("拦密钥", "含密钥" in str(privacy.audit("token 是 ghp_abcdefghijklmnopqrstuvwx")))
    check("干净文本放行", privacy.audit("今天天气不错") == [])

    cleaned, issues = privacy.sanitize_outbound("我电话13900001111")
    check("出站遮手机号", "13900001111" not in cleaned, cleaned)
    check("出站遮完无残留问题", issues == [], str(issues))

    # ---------------------------------------------------------- 白名单
    print("\n白名单")
    ok, why = guard.can_send("private", "1513929790")
    check("名单内放行", ok, why)
    ok, why = guard.can_send("private", "999999")
    check("名单外拒绝", not ok, why)
    check("拒绝原因可读", "白名单" in why, why)
    ok, _ = guard.can_send("group", "123456")
    check("空群白名单拒绝", not ok)

    # ---------------------------------------------------------- 急停
    print("\n急停")
    guard.resume()
    check("初始未暂停", not guard.is_paused()[0])
    guard.pause(0.02, "测试")
    paused, why = guard.is_paused()
    check("暂停生效", paused, why)
    check("原因可见", "测试" in why, why)
    time.sleep(1.3)
    guard.resume()
    check("恢复生效", not guard.is_paused()[0])

    # ---------------------------------------------------------- 串行锁
    print("\n串行锁")
    with lock.exclusive("test", timeout=2):
        check("持锁时 is_busy", lock.is_busy("test"))
        t0 = time.time()
        try:
            with lock.exclusive("test", timeout=1):
                pass
            check("重入应超时", False)
        except TimeoutError:
            check("重入超时", True, f"{time.time() - t0:.1f}s")
    check("释放后不忙", not lock.is_busy("test"))

    # ---------------------------------------------------------- 收尾
    print("\n" + "=" * 52)
    total = PASS + FAIL
    print(f"  {PASS}/{total} 通过" + ("" if FAIL == 0 else f"，{FAIL} 失败"))
    print("=" * 52)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
