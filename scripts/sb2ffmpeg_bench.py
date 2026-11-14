"""簡易版 (sb2ffmpeg.py) を、その PC で一番速く動かすパラメーター (同時に書く本数とスレッドの数) を測って決める。
使い方: python scripts/sb2ffmpeg_bench.py [--make-source] [--runs 2] [--apply] [--storyboard 構成表 --source 素材]
- 候補は「同時に 1 本・2 本」×「スレッドは FFmpeg に任せる・読み込みに論理 CPU の数 ÷ 本数、フィルターと書き出しに 1」の 4 つ
- 候補を順番を入れ替えながら --runs 回ずつ流し (1 回目は前から、2 回目は後ろから)、かかった時間の中央値で比べる
- 流している間、1 秒ごとに CPU の使用率・空きメモリ・コミット済みのメモリ・FFmpeg と sb2ffmpeg の合計のメモリを記録する
- 結果は 出力先の sb2ffmpeg-bench\\bench-<時刻>.json。--apply を付けると、一番速い候補を config/local.json の
  sb2ffmpeg (jobs・threads) に書く。sb2ffmpeg.py はその値で FFmpeg を走らせる
- 既定の構成表は tests/bench-storyboard.json (見本 sample1 から等倍・20 倍速・50 倍速・ズーム・拡大の重ね表示・
  マスクの行を選んだもの。簡易版は 1269 フレーム、42.3 秒)。素材は 作業フォルダーの bench-4k.mp4 (--make-source で作る合成の 4K、245 秒、
  H.264 40 Mbps、キーフレームは 2 秒ごと)
- Windows だけで動く (CPU とメモリを Windows の API で読む)
"""
import argparse
import ctypes
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import winreg
from ctypes import wintypes

import common as C

HERE = os.path.dirname(os.path.abspath(__file__))
SB = os.path.join(HERE, "..", "tests", "bench-storyboard.json")
SOURCE_SECONDS = 245


def make_source(path):
    """合成の 4K の素材 (testsrc2、30 fps、H.264 40 Mbps、キーフレームは 2 秒ごと) を作る。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    C.run_tool("ffmpeg", ["-f", "lavfi", "-i", f"testsrc2=size=3840x2160:rate=30:duration={SOURCE_SECONDS}",
                          "-vf", C.BT709_VF, *C.BT709_TAGS, "-c:v", "libx264", "-preset", "veryfast",
                          "-b:v", "40M", "-maxrate", "40M", "-bufsize", "80M", "-g", "60", "-keyint_min", "60",
                          "-sc_threshold", "0", path])


def candidates(cpus):
    """比べる候補 [(同時に書く本数, threads の値)]。"""
    return [(j, t) for j in (1, 2) for t in ("auto", f"{max(1, cpus // j)},1,1")]


def run_order(n, runs):
    """候補の番号を流す順。奇数回目は前から、偶数回目は後ろから。"""
    return [(r, i) for r in range(runs) for i in (range(n) if r % 2 == 0 else reversed(range(n)))]


class _Mem(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def memory():
    """(物理メモリの合計, 空き, コミット済み) の MB。"""
    m = _Mem()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    mb = 1024 * 1024
    return m.ullTotalPhys // mb, m.ullAvailPhys // mb, (m.ullTotalPageFile - m.ullAvailPageFile) // mb


def cpu_times():
    """(アイドル, 全体) の時間 (100 ns 単位)。全体はカーネル (アイドルを含む) とユーザーの和。"""
    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    v = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime
    return v(idle), v(kernel) + v(user)


def process_memory_mb(pid):
    """ffmpeg.exe 全部と、pid のプロセス (sb2ffmpeg.py) の作業セットの合計 (MB)。tasklist の CSV を読む。"""
    r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, encoding="oem", errors="replace")
    total = 0
    for line in r.stdout.splitlines():
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) >= 5 and (cols[0].lower() == "ffmpeg.exe" or cols[1] == str(pid)):
            total += int("".join(ch for ch in cols[4] if ch.isdigit()) or 0)
    return total // 1024


def measure(cmd):
    """cmd を流し、終わるまで 1 秒ごとに CPU とメモリを記録する。"""
    samples, start = [], time.monotonic()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace")
    out = []
    reader = threading.Thread(target=lambda: out.extend(p.stdout), daemon=True)
    reader.start()
    last = cpu_times()
    while True:
        try:   # 終わったらすぐ時間を取る (1 秒の待ちと記録の時間を足さない)
            p.wait(timeout=1)
            secs = round(time.monotonic() - start, 1)
            break
        except subprocess.TimeoutExpired:
            pass
        now = cpu_times()
        busy = 1 - (now[0] - last[0]) / max(1, now[1] - last[1])
        last = now
        _, avail, commit = memory()
        samples.append({"cpu": round(100 * busy, 1), "avail_mb": avail, "commit_mb": commit,
                        "proc_mb": process_memory_mb(p.pid)})
    reader.join()
    pick = lambda k, f: f(s[k] for s in samples) if samples else None
    return {"seconds": secs, "rc": p.returncode, "first_line": out[0].strip() if out else "",
            "cpu_avg": round(statistics.mean(s["cpu"] for s in samples), 1) if samples else None,
            "cpu_max": pick("cpu", max), "proc_mb_max": pick("proc_mb", max),
            "avail_mb_min": pick("avail_mb", min), "commit_mb_max": pick("commit_mb", max)}


def environment():
    """測った PC の条件。"""
    def text(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, encoding="oem",   # コンソールのコードページで出る
                                  errors="replace").stdout.strip().splitlines()[0]
        except (OSError, IndexError):
            return ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            cpu = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except OSError:
        cpu = os.environ.get("PROCESSOR_IDENTIFIER", "")

    class _Power(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]
    pw = _Power()
    ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(pw))
    return {"computer": os.environ.get("COMPUTERNAME", ""), "cpu": cpu, "logical_cpus": os.cpu_count(),
            "memory_mb": memory()[0], "ac_power": {0: False, 1: True}.get(pw.ACLineStatus),
            "power_scheme": text(["powercfg", "/getactivescheme"]),
            "ffmpeg": text([C.config().tool("ffmpeg"), "-hide_banner", "-version"])}


def best(summary):
    """かかった時間の中央値が一番短い候補。失敗した回のある候補は選ばない。"""
    ok = [s for s in summary if s["failed"] == 0]
    return min(ok, key=lambda s: s["median_seconds"]) if ok else None


def apply(choice):
    """一番速い候補を config/local.json の sb2ffmpeg に書く (ほかの項目はそのまま)。"""
    path = C.config().local_path
    local = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            local = json.load(f)
    local["sb2ffmpeg"] = {**local.get("sb2ffmpeg", {}), "jobs": choice["jobs"], "threads": choice["threads"]}
    C.write_json(path, local)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--storyboard", default=SB, help="測りに使う構成表 (既定 tests/bench-storyboard.json)")
    ap.add_argument("--source", help="素材 (既定は 作業フォルダーの bench-4k.mp4)")
    ap.add_argument("--make-source", action="store_true", help="合成の 4K の素材を作ってから測る (既にあれば作らない)")
    ap.add_argument("--runs", type=int, default=2, help="候補ごとに流す回数 (既定 2。差が小さければ 3 にする)")
    ap.add_argument("--apply", action="store_true", help="一番速い候補を config/local.json の sb2ffmpeg に書く")
    a = ap.parse_args()
    # sb2ffmpeg.py は相対パスの素材を構成表のフォルダーから探すので、ここで今のフォルダーからの絶対パスにしてから渡す
    src = os.path.abspath(a.source or os.path.join(C.config().work_dir(), "bench-4k.mp4"))
    if a.make_source and not os.path.isfile(src):
        make_source(src)
    if not os.path.isfile(src):
        return C.report(False, f"sb2ffmpeg_bench: 素材が無い ({src})。--make-source で作るか --source で渡す")
    out_dir = C.config().out_dir("sb2ffmpeg-bench")
    env = environment()
    cands = candidates(env["logical_cpus"] or 1)
    runs = []
    for r, i in run_order(len(cands), max(1, a.runs)):
        jobs, threads = cands[i]
        cmd = [sys.executable, os.path.join(HERE, "sb2ffmpeg.py"), a.storyboard, "--source", src,
               "--out", os.path.join(out_dir, "bench.build.json"), "--jobs", str(jobs), "--threads", threads]
        m = measure(cmd)
        runs.append({"round": r + 1, "jobs": jobs, "threads": threads, **m})
        print(f"  {r + 1} 回目 同時に {jobs} 本・スレッド {threads}: {m['seconds']} 秒"
              + ("" if m["rc"] == 0 else f" (失敗 rc={m['rc']}: {m['first_line']})"), flush=True)
    summary = []
    for jobs, threads in cands:
        rs = [x for x in runs if x["jobs"] == jobs and x["threads"] == threads]
        good = [x["seconds"] for x in rs if x["rc"] == 0]
        summary.append({"jobs": jobs, "threads": threads, "failed": len(rs) - len(good),
                        "median_seconds": statistics.median(good) if good else None,
                        "proc_mb_max": max((x["proc_mb_max"] or 0) for x in rs),
                        "avail_mb_min": min((x["avail_mb_min"] or 0) for x in rs)})
    choice = best(summary)
    result = {"made": time.strftime("%Y-%m-%d %H:%M:%S"), "environment": env, "storyboard": os.path.abspath(a.storyboard),
              "source": src, "runs": runs, "summary": summary, "best": choice}
    path = os.path.join(out_dir, f"bench-{time.strftime('%Y%m%d-%H%M%S')}.json")
    if choice and a.apply:
        result["applied_to"] = apply(choice)
    C.write_json(path, result)
    lines = [f"同時に {s['jobs']} 本・スレッド {s['threads']}: 中央値 {s['median_seconds']} 秒、"
             f"メモリの最大 {s['proc_mb_max']} MB、空きの最小 {s['avail_mb_min']} MB"
             + (f"、失敗 {s['failed']} 回" if s["failed"] else "") for s in summary]
    if not choice:
        return C.report(False, "sb2ffmpeg_bench: どの候補も失敗した", path, lines)
    head = f"sb2ffmpeg_bench: 一番速いのは 同時に {choice['jobs']} 本・スレッド {choice['threads']}"
    head += f" (config/local.json に書いた)" if a.apply else " (--apply で config/local.json に書く)"
    return C.report(True, head, path, lines)


if __name__ == "__main__":
    sys.exit(main())
