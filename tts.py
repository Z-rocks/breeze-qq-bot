#!/usr/bin/env python
"""Breeze TTS 2 本地语音合成小工具（封装 breeze-cli.exe）。

常用示例：
    python tts.py "你好，这是本地合成的语音。"
    python tts.py "Welcome aboard." --out outputs/en.wav
    python tts.py --file script.txt --instruction "温柔的年轻女声，语速偏慢"
    python tts.py "今晚的事，我们得谈谈。" --voice narrator --cfg-scale 2
    python tts.py "It is good to hear your voice." --ref-audio ref.wav --ref-text "参考音频的逐字稿"
    python tts.py --save-voice narrator --ref-audio ref.wav --ref-text "参考音频的逐字稿"

文本里的括号标签可以触发气声/笑声等：中文用方括号 [笑] [叹气] [咳嗽] [清嗓子]，
英文用圆括号 (laugh) (sigh) (cough)。这类标签通常需要 --cfg-scale 2~3 才会生效。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLI = ROOT / "bin" / "breeze-cli.exe"
DEFAULT_MODEL = ROOT / "models" / "breeze-tts-2-q8_0.gguf"
OUT_DIR = ROOT / "outputs"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Breeze TTS 2 本地语音合成（文本 → 24kHz WAV）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("text", nargs="?", help="要朗读的文本；也可以用 --file 从文件读")
    p.add_argument("--file", help="从 UTF-8 文本文件读取正文（长文会自动按句子切分）")
    p.add_argument("--out", help="输出 wav 路径，默认 outputs/tts_<时间戳>.wav")
    p.add_argument("--model", default=str(DEFAULT_MODEL), help="GGUF 模型路径")
    p.add_argument("--instruction", help="音色描述或表演指令（音色设计用）")
    p.add_argument("--ref-audio", help="参考音频 wav（音色克隆用，5~15 秒干净人声）")
    p.add_argument("--ref-text", help="参考音频的逐字稿，必须完全一致")
    p.add_argument("--voice", help="使用已保存的音色名")
    p.add_argument("--save-voice", help="把 --ref-audio 编码成可复用的音色并退出")
    p.add_argument("--description", help="配合 --save-voice 保存的音色说明")
    p.add_argument("--cfg-scale", type=float, default=1.0, help="指令强度，1.0 关闭；标签类建议 2~3")
    p.add_argument("--seed", type=int, default=42, help="随机种子，换种子＝换一条演绎")
    p.add_argument("--max-new", type=int, help="帧数上限，1 帧 80ms，默认 750（60 秒）")
    p.add_argument("--split-chars", type=int, help="长文切分阈值，默认 600，0 表示不切")
    p.add_argument("--cpu", action="store_true", help="强制走 CPU")
    p.add_argument("--timings", action="store_true", help="打印各阶段耗时")
    p.add_argument("--list-voices", action="store_true", help="列出已保存的音色")
    return p


def main() -> int:
    args = build_parser().parse_args()

    if not CLI.exists():
        print(f"找不到 {CLI}", file=sys.stderr)
        return 1
    if not args.list_voices and not Path(args.model).exists():
        print(f"找不到模型 {args.model}", file=sys.stderr)
        return 1

    cmd = [str(CLI), str(args.model)]

    if args.list_voices:
        cmd.append("--list-voices")
        return subprocess.call(cmd)

    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8").strip()
    if not text:
        print("缺少文本：位置参数或 --file 二选一", file=sys.stderr)
        return 1

    cmd += ["--text", text]

    if args.save_voice:
        cmd += ["--save-voice", args.save_voice]
        if not args.ref_audio:
            print("--save-voice 需要配合 --ref-audio 与 --ref-text", file=sys.stderr)
            return 1
    if args.instruction:
        cmd += ["--instruction", args.instruction]
    if args.ref_audio:
        cmd += ["--ref-audio", args.ref_audio]
    if args.ref_text:
        cmd += ["--ref-text", args.ref_text]
    if args.voice:
        cmd += ["--voice", args.voice]
    if args.description:
        cmd += ["--description", args.description]
    if args.cfg_scale != 1.0:
        cmd += ["--cfg-scale", str(args.cfg_scale)]
    if args.seed != 42:
        cmd += ["--seed", str(args.seed)]
    if args.max_new:
        cmd += ["--max-new", str(args.max_new)]
    if args.split_chars is not None:
        cmd += ["--split-chars", str(args.split_chars)]
    if args.cpu:
        cmd.append("--cpu")
    if args.timings:
        cmd.append("--timings")

    out = args.out
    if not args.save_voice:
        if not out:
            OUT_DIR.mkdir(exist_ok=True)
            out = str(OUT_DIR / time.strftime("tts_%Y%m%d_%H%M%S.wav"))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--output", out]

    print("运行：", " ".join(f'"{c}"' if " " in c else c for c in cmd[:2]), "...", flush=True)
    started = time.time()
    code = subprocess.call(cmd)
    if code != 0:
        print(f"生成失败，退出码 {code}", file=sys.stderr)
        return code

    if args.save_voice:
        print(f"音色已保存：{args.save_voice}")
        return 0

    size = os.path.getsize(out)
    print(f"完成：{out}  ({size / 1024:.0f} KB, 用时 {time.time() - started:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
