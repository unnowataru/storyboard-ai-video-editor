"""録画の後に、録画ファイルから出来事の候補を時刻つきで出す。ピンは打たず、元の録画は書き換えない。

候補の種類:
  change      画面の大きな切り替わり (画面が落ち着いたところで 1 件。スクロールの途中では出さない)
  still-start 長い静止の始まり (scene_events.still_sec 秒以上、画面がほぼ動かなかった区間。待ちとは限らない)
  still-end   その静止の終わり
  error-word  エラーの語 (scene_events.error_words) が、前に読んだときには無く、新しく映った (--ocr のときだけ)

使い方:
  python scripts/scene_events.py 録画.mp4 [--ocr] [--json] [--out 候補.json]
  --out の既定は 作業フォルダー\\<録画の名前>\\<録画の名前>.events.json

しくみ:
  scene_events.step_sec 秒ごとに 1 フレームを、幅 scene_events.width に縮めた灰色で読み、前のフレームとの差と、
  最後に「切り替わった」とした画面との差を、明るさが level を超えて変わった画素の割合で比べる。
  --ocr では、何かが現れて落ち着いたフレームだけを原寸で取り出し、Windows 標準の文字認識 (ocr_windows.ps1) で読む。
  文字認識は手元の Windows で動き、画面の中身を外に送らない。JSON と画面には、読んだ文字そのものは書かず、当たった語 (設定の語) だけを書く。
  AI のチャットの画面など、説明の文が映る録画では、説明の中の「エラー」「失敗」も拾う。候補は人が見て選ぶ。
数の値と語は config/defaults.json の scene_events。使い方の説明は docs/recording.md。
"""
import argparse
import json
import os
import shutil
import subprocess

import numpy as np

import common as C

KIND_ORDER = {"change": 0, "still-start": 1, "still-end": 2, "error-word": 3}


def settings():
    return C.config().section("scene_events")


def ratio(p, q, level):
    """明るさが level を超えて変わった画素の割合。uint8 の引き算のあふれを避けるため、符号つきで差を取る。"""
    return float((np.abs(p.astype(np.int16) - q.astype(np.int16)) > level).mean())


def detect(samples, s):
    """(秒, 灰色の画) の列から候補を見つける。(候補 [{"t", "kind", "note"}], 文字認識に回す秒の一覧) を返す。"""
    events, ocr_times = [], []
    base = prev = prev_t = None
    pending = still_from = None
    still_on = moved = False
    for t, g in samples:
        if prev is None:
            base, prev, prev_t = g, g, t
            continue
        r_prev, r_base = ratio(g, prev, s["level"]), ratio(g, base, s["level"])
        # 長い静止: 前のフレームからほとんど変わらない状態が still_sec 続いたら、その始まりを候補にする
        if r_prev < s["still_ratio"]:
            if still_from is None:
                still_from = prev_t
            if not still_on and t - still_from >= s["still_sec"]:
                events.append({"t": still_from, "kind": "still-start", "note": f"{round(t - still_from)} 秒以上ほぼ動かない"})
                still_on = True
        else:
            if still_on:
                events.append({"t": t, "kind": "still-end", "note": ""})
            still_on, still_from = False, None
        # 大きな切り替わり: 基準の画面から大きく変わったら、落ち着くのを待って 1 件にする (時刻は変わり始め)
        if pending is None and r_base >= s["change_ratio"]:
            pending = t
        settled = r_prev < s["settle_ratio"]
        if pending is not None and settled:
            events.append({"t": pending, "kind": "change", "note": f"画面の {round(r_base * 100)}% が変わった"})
            base, pending = g, None
        # 文字認識: 何かが現れて (小さな変化でも)、落ち着いたフレームだけを読む
        if r_prev >= s["ocr_ratio"]:
            moved = True
        elif moved and settled:
            ocr_times.append(t)
            moved = False
        prev, prev_t = g, t
    if pending is not None:
        events.append({"t": pending, "kind": "change", "note": "録画の終わりまで動き続けた"})
    return events, ocr_times


def word_events(found, words):
    """found = [(秒, 文字認識の単語の一覧)] (時刻の順)。前に読んだときには無かった語が現れた時刻を候補にする。"""
    events, before = [], set()
    for t, texts in found:
        joined = " ".join(texts)
        now = {w for w in words if w in joined}
        for w in sorted(now - before):
            events.append({"t": t, "kind": "error-word", "note": f"語: {w}"})
        before = now
    return events


def ordered(events):
    return sorted(events, key=lambda e: (e["t"], KIND_ORDER[e["kind"]]))


def small_frames(video, info, s):
    """step_sec ごとに、幅 width の灰色のフレームを (秒, 画) で返す。"""
    w = int(s["width"])
    h = max(2, int(round(info["height"] * w / info["width"] / 2)) * 2)
    p = subprocess.Popen([C.config().tool("ffmpeg"), "-v", "error", "-i", video, "-vf",
                          f"fps=1/{s['step_sec']},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE, creationflags=C.LOW_PRIORITY)
    n = 0
    while True:
        buf = p.stdout.read(w * h)
        if len(buf) < w * h:
            break
        yield n * s["step_sec"], np.frombuffer(buf, np.uint8).reshape(h, w)
        n += 1
    if p.wait() != 0 or n == 0:
        raise RuntimeError(f"動画を読めなかった: {video} (FFmpeg の終了コード {p.returncode}、{n} フレーム)")


def read_words(video, info, times, tmp, s):
    """times の秒のフレームを原寸の ocr_scale 倍で取り出して文字認識にかけ、[(秒, 単語の一覧)] を返す。"""
    import track
    shots = []
    for i, t in enumerate(times):
        png = os.path.join(tmp, f"f{i:06d}.png")
        C.run_tool("ffmpeg", ["-ss", str(t), "-i", video, "-frames:v", "1", "-vf",
                              f"scale={info['width'] * s['ocr_scale']}:{info['height'] * s['ocr_scale']}", png])
        shots.append({"png": png})
    res = track.ocr(shots, tmp) if shots else {}
    return [(t, [x["text"] for x in res.get(sh["png"], [])]) for t, sh in zip(times, shots)]


def default_out(video):
    stem = os.path.splitext(os.path.basename(video))[0]
    return os.path.join(C.config().work_dir(stem), stem + ".events.json")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--ocr", action="store_true", help="エラーの語も探す (文字認識を使うので時間がかかる)")
    ap.add_argument("--json", action="store_true", help="画面にも JSON で出す")
    ap.add_argument("--out", help="候補の JSON の保存先")
    a = ap.parse_args(argv)
    s = settings()
    info = C.probe_video(a.video)
    events, ocr_times = detect(small_frames(a.video, info, s), s)
    if a.ocr:
        stem = os.path.splitext(os.path.basename(a.video))[0]
        tmp = C.config().work_dir(stem, "scene-events-frames")   # 機密を含む画面のことがあるので、クラウドで同期しているフォルダーに置かない。終わったら消す
        try:
            events += word_events(read_words(a.video, info, ocr_times, tmp, s), s["error_words"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    events = ordered(events)
    out = a.out or default_out(a.video)
    doc = {"video": os.path.abspath(a.video), "duration": info["duration"], "ocr": a.ocr, "events": events}
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    if a.json:
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        return
    print(f"{a.video}  (長さ {C.sec_to_mss(info['duration'])}、候補 {len(events)} 件、保存: {out})")
    for e in events:
        print(f"  {C.sec_to_mss(e['t']):>8}  {e['kind']:<11}  {e['note']}")


if __name__ == "__main__":
    main()
