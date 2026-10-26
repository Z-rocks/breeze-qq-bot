"""多角色配音拼接：逐句合成 → 拼成一条 wav。

Breeze TTS 2 一次只出一个音色，所以对白要逐句生成、再用静音拼起来。
所有输出都是 24kHz / 单声道 / 16bit，直接字节拼接即可。

用法：
    python scripts/dub_dialog.py out.wav lines.json

lines.json 格式：
    [
      {"speaker": "萧炎", "text": "...", "instruction": "...", "seed": 7, "cfg": 2.2},
      {"speaker": "纳兰嫣然", "text": "...", "instruction": "...", "seed": 11, "cfg": 2.2}
    ]
"""

from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
TMP = ROOT / "outputs" / "_dub"
SILENCE_MS = 400


def synth(line: dict, idx: int) -> Path:
    out = TMP / f"{idx:02d}_{line.get('speaker', 'x')}.wav"
    cmd = [
        PY, str(ROOT / "tts.py"), line["text"],
        "--out", str(out),
        "--instruction", line["instruction"],
        "--cfg-scale", str(line.get("cfg", 2.0)),
        "--seed", str(line.get("seed", 42)),
    ]
    # 用已保存的音色克隆：音色被参考锁死，instruction 只负责情绪 → 同一人也能有起伏
    if line.get("voice"):
        cmd += ["--voice", str(line["voice"])]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        print(f"[fail] 第 {idx} 句合成失败")
        print(r.stdout[-600:], r.stderr[-600:])
        raise SystemExit(1)
    print(f"  [{idx}] {line.get('speaker')}: {out.name} "
          f"({out.stat().st_size // 1024} KB)", flush=True)
    return out


def concat(files: list[Path], dest: Path, gap_ms: int = SILENCE_MS):
    params = None
    chunks = []
    for f in files:
        with wave.open(str(f)) as w:
            if params is None:
                params = w.getparams()
            frames = w.readframes(w.getnframes())
        chunks.append(frames)
    nch, sw, fr = params.nchannels, params.sampwidth, params.framerate
    silence = b"\x00" * (int(fr * gap_ms / 1000) * nch * sw)
    with wave.open(str(dest), "w") as out:
        out.setnchannels(nch)
        out.setsampwidth(sw)
        out.setframerate(fr)
        for i, c in enumerate(chunks):
            out.writeframes(c)
            if i != len(chunks) - 1:
                out.writeframes(silence)
    print(f"[ok] 拼接完成：{dest} ({dest.stat().st_size // 1024} KB)")


def main():
    dest = Path(sys.argv[1])
    spec = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    TMP.mkdir(parents=True, exist_ok=True)
    print(f"[1/2] 逐句合成（{len(spec)} 句）…", flush=True)
    files = [synth(line, i) for i, line in enumerate(spec)]
    print("[2/2] 拼接…", flush=True)
    concat(files, dest)


if __name__ == "__main__":
    main()
