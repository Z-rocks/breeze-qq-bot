"""Multi-connection ranged downloader with resume + retry.

Usage:
    python dl.py URL OUT_PATH [CONNECTIONS]

Splits the remote file into N contiguous ranges, downloads them in parallel
to OUT_PATH.partNNN, then merges. Re-running resumes existing parts.
"""
import os
import sys
import threading
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def head_size(url):
    req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        cr = r.headers.get("Content-Range")
        if cr and "/" in cr:
            return int(cr.rsplit("/", 1)[1])
        cl = r.headers.get("Content-Length")
        if cl:
            return int(cl)
    raise RuntimeError("cannot determine remote size")


def fetch_range(url, start, end, path, retries=8):
    want = end - start + 1
    for attempt in range(retries):
        have = os.path.getsize(path) if os.path.exists(path) else 0
        if have >= want:
            return
        s = start + have
        req = urllib.request.Request(
            url, headers={**UA, "Range": f"bytes={s}-{end}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(path, "ab") as f:
                while True:
                    block = r.read(262144)
                    if not block:
                        break
                    f.write(block)
        except Exception as exc:  # noqa: BLE001
            time.sleep(min(2 * (attempt + 1), 15))
            print(f"  retry {attempt + 1} {os.path.basename(path)}: {exc}", flush=True)
    if os.path.getsize(path) < want:
        raise RuntimeError(f"incomplete part {path}")


def main():
    url, out = sys.argv[1], sys.argv[2]
    conns = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    total = head_size(url)
    print(f"remote size: {total / 1048576:.1f} MB, connections: {conns}", flush=True)

    step = total // conns
    jobs = []
    for i in range(conns):
        start = i * step
        end = total - 1 if i == conns - 1 else (start + step - 1)
        jobs.append((start, end, f"{out}.part{i:03d}"))

    threads = [
        threading.Thread(target=fetch_range, args=(url, s, e, p), daemon=True)
        for s, e, p in jobs
    ]
    for t in threads:
        t.start()

    while any(t.is_alive() for t in threads):
        done = sum(os.path.getsize(p) for _, _, p in jobs if os.path.exists(p))
        pct = done * 100 / total
        rate = done / max(time.time() - START, 1) / 1024
        print(f"\r  {pct:5.1f}%  {done / 1048576:7.1f}/{total / 1048576:.1f} MB  {rate:6.0f} KB/s", end="", flush=True)
        time.sleep(3)

    for t in threads:
        t.join()
    print("\nmerging...", flush=True)
    with open(out, "wb") as dst:
        for _, _, p in jobs:
            with open(p, "rb") as src:
                while True:
                    block = src.read(4194304)
                    if not block:
                        break
                    dst.write(block)
            os.remove(p)
    size = os.path.getsize(out)
    print(f"done: {out} ({size / 1048576:.1f} MB)", flush=True)
    if size != total:
        print("WARNING: size mismatch", flush=True)
        sys.exit(2)


if __name__ == "__main__":
    START = time.time()
    main()
