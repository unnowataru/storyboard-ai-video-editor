"""録画のピンと領域を一覧にする。OBS で打ったピン (録画ファイルのチャプター) と、marker.html で保存した
<録画の名前>.marks.json のピンと領域をまとめて、時刻の順に出す。

使い方: python scripts/pins.py 録画.mp4 [marks.json] [--json]
  marks.json を省くと、録画と同じフォルダー → 作業フォルダーの <録画の名前>\\ → ダウンロードのフォルダー (config の
  paths.downloads_dir、環境変数 RESOLVE_DOWNLOADS) の順に <録画の名前>.marks.json を探す。
  --json  表ではなく JSON で出す
"""
import argparse
import json
import os

import common as C


def chapters_to_rows(chapters):
    """ffprobe -show_chapters の chapters を行にする。全部のチャプターを出す (録画ツールが自動で付けたものも除かない)。"""
    return [{"t": float(ch["start_time"]), "kind": "ピン (OBS)", "note": ch.get("tags", {}).get("title", "")}
            for ch in chapters]


def marks_to_rows(marks):
    """marker.html の marks.json のピンと領域を行にする。"""
    rows = [{"t": float(p["t"]), "kind": "ピン", "note": p.get("name", "")} for p in marks.get("pins", [])]
    rows += [{"t": float(g["t"]), "kind": "丸" if g.get("shape") == "ellipse" else "四角", "index": n,
              "note": f'x={g["x"]} y={g["y"]} w={g["w"]} h={g["h"]}'} for n, g in enumerate(marks.get("regions", []))]
    return rows


def ordered(rows):
    """時刻の順に並べる。同じ時刻なら OBS のピン → ピン → 領域の順 (元の並びを保つ)。"""
    rank = {"ピン (OBS)": 0, "ピン": 1}
    return sorted(rows, key=lambda r: (r["t"], rank.get(r["kind"], 2)))


def marks_candidates(video):
    cfg = C.config()
    stem = os.path.splitext(os.path.basename(video))[0]
    name = stem + ".marks.json"
    dirs = [os.path.dirname(os.path.abspath(video)), os.path.join(cfg.path("work_dir"), stem)]
    dl = cfg.path("downloads_dir")
    if dl:
        dirs.append(dl)
    return [os.path.join(d, name) for d in dirs]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("marks", nargs="?")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    length = C.probe_video(a.video)["duration"]
    r = C.run_tool("ffprobe", ["-show_chapters", "-of", "json", a.video])
    rows = chapters_to_rows(json.loads(r.stdout).get("chapters", []))
    cands = [a.marks] if a.marks else marks_candidates(a.video)
    found = next((p for p in cands if os.path.isfile(p)), None)
    if found:
        with open(found, encoding="utf-8") as f:
            rows += marks_to_rows(json.load(f))
    rows = ordered(rows)
    if a.json:
        print(json.dumps({"video": os.path.abspath(a.video), "duration": length, "marks": found, "rows": rows},
                         ensure_ascii=False, indent=1))
        return
    print(f"{a.video}  (長さ {C.sec_to_mss(length)}、marks.json の記録: {found or '無し'})")
    for x in rows:
        print(f"  {C.sec_to_mss(x['t']):>8}  {x['kind']:<8}  {x['note']}")
    if not rows:
        print("  ピンも領域も無い")


if __name__ == "__main__":
    main()
