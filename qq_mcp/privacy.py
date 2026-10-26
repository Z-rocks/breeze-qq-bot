"""隐私：入站脱敏 + 出站审查。

两件事方向相反，但必须成对出现：

  入站 redact  用户消息进模型前，把手机号/身份证/银行卡换成占位符 → 模型看不到真值
  出站 audit   模型输出发出去前，扫路径/密钥/代码块 → 防止把本机信息说漏

**默认不还原。** 模型回 `<PHONE_1>`，发出去时换成「你的手机号」，真值全程不出本机。
"""

from __future__ import annotations

import re

# 顺序有意义：先长后短，否则银行卡会被手机号规则切碎
INBOUND = [
    ("IDCARD", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("PHONE", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("BANK", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")),
    ("QQ", re.compile(r"(?<!\d)[1-9]\d{4,10}(?!\d)")),
]

# 出站：命中就拦（这些形态不该出现在聊天里）
OUTBOUND_BLOCK = [
    ("本地路径", re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/][^\s，。；、）)\]]{2,}")),
    ("Unix 路径", re.compile(r"/(?:home|Users|root|etc|var)/[\w./-]+")),
    ("代码块", re.compile(r"```")),
    ("密钥", re.compile(r"\b(?:sk-[\w-]{8,}|ghp_[\w]{20,}|gho_[\w]{20,}|AKIA[0-9A-Z]{16})")),
    ("URL", re.compile(r"https?://[^\s，。；）)]+")),
]

# 出站：命中就替换（改成占位，不拦整条）
OUTBOUND_MASK = [
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("身份证", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
]

FRIENDLY = {"PHONE": "你的手机号", "IDCARD": "你的身份证号",
            "BANK": "你的银行卡号", "EMAIL": "你的邮箱", "QQ": "你的 QQ 号"}


def redact(text: str, keep: set[str] | None = None) -> tuple[str, dict[str, str]]:
    """把敏感串换成占位符。返回 (脱敏后文本, 占位符→真值映射)。

    keep：不该被脱敏的值（自己的号、群号），按原样保留。
    """
    if not text:
        return text, {}
    keep = {str(k) for k in (keep or set())}
    mapping: dict[str, str] = {}
    out = text
    counter: dict[str, int] = {}

    for name, pat in INBOUND:
        def sub(m: re.Match, name=name) -> str:
            val = m.group(0)
            if val in keep:
                return val
            # 同一真值复用同一个占位符，避免 <PHONE_1> <PHONE_2> 指同一号码
            for ph, orig in mapping.items():
                if orig == val:
                    return ph
            counter[name] = counter.get(name, 0) + 1
            ph = f"<{name}_{counter[name]}>"
            mapping[ph] = val
            return ph

        out = pat.sub(sub, out)
    return out, mapping


def restore(text: str, mapping: dict[str, str], friendly: bool = True) -> str:
    """把占位符还原。friendly=True 时换成「你的手机号」而不是真值。"""
    if not text or not mapping:
        return text
    out = text
    for ph, orig in mapping.items():
        if friendly:
            kind = ph.strip("<>").rsplit("_", 1)[0]
            out = out.replace(ph, FRIENDLY.get(kind, "该信息"))
        else:
            out = out.replace(ph, orig)
    return out


def audit(text: str) -> list[str]:
    """出站审查。返回问题列表，空列表表示干净。"""
    issues: list[str] = []
    for name, pat in OUTBOUND_BLOCK:
        if pat.search(text or ""):
            issues.append(f"含{name}")
    return issues


def sanitize_outbound(text: str) -> tuple[str, list[str]]:
    """出站清洗：先遮手机号/身份证，再返回剩余无法自动修的问题。"""
    out = text or ""
    for _, pat in OUTBOUND_MASK:
        out = pat.sub("[已隐藏]", out)
    return out, audit(out)
