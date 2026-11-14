"""track.py の位置の JSON を、構成表の行ごとのマスク (masks) に直し、マスクを追加した新しい構成表を書く。

使い方:
  python scripts/track2masks.py 構成表.json 録画.track.json [--type mosaic] [--shape rect]

元の構成表は書き換えない。書き出し先は構成表と同じフォルダーの <構成表の名前>-masks.json
(構成表の中の相対パスが同じ意味のまま使える)。
- 座標: 構成表のルールどおり素材の画素のまま書く (タイムラインの画素には、構成表を Resolve に組む sb2resolve.py が直す)。
  四角は track.pad (素材の画素) だけ広げ、外側へ整数に丸める
- 時間: 行の素材の範囲 (start〜end) に重なる segment の部分を、行の始まりからの書き出しの秒 (倍速も含む) に直す。
  変わり目は、変わった後の素材が映る最初の書き出しのフレームに切り上げる
- 動き: 続いた segment で同じ label の四角を近いものどうしつなぎ、1 つのマスクにする。位置か大きさが
  track.key_step (素材の画素) 以上変わった所 (四辺のどれか) だけ keys にする (1 フレーム前に前の位置の key を置いて段差にする)。
  変わらないものは keys なしの固定のマスク。マスクは行の全体で出る (出ていない間もその位置を隠す)
- 録画が違う行 (行の source が位置の JSON の video と違う) にはマスクを追加しない
- 種類と形の既定は track.mask_type / track.mask_shape。label (ip-1 など) は書かない
書式は docs/track.md と docs/storyboard.md。
"""
import argparse
import json
import math
import os
import sys

import common as C

FORMAT_VERSION = 1   # 読める位置の JSON の version


def padded(b, pad):
    """素材の画素の四角 (余白なし) に余白を足し、外側へ整数に丸めた四角 (素材の画素のまま)。"""
    x0, y0 = math.floor(b["x"] - pad), math.floor(b["y"] - pad)
    x1, y1 = math.ceil(b["x"] + b["w"] + pad), math.ceil(b["y"] + b["h"] + pad)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def _edges(b):
    return b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]


def _center(b):
    return b["x"] + b["w"] / 2, b["y"] + b["h"] / 2


def row_tracks(segments, fps_src, start, end, rate, fps_out, pad, base=None):
    """行 1 つ分の、つながった四角の列の一覧。列は [(行の始まりからの書き出しの秒, 余白を足した素材の画素の四角), ...]。

    start / end は行の素材の範囲 (秒)、rate は書き出しの秒 ÷ 素材の秒。重ならない segment は使わない。
    base は行の頭に映る素材の時刻 (秒)。sb2resolve.py は素材の開始をフレームに丸めて置く (round(start × fps)) ので、
    書き出しの時刻はその丸めた開始から数える (省略すると start)。
    続いた segment (前の end と次の start が同じフレーム) の同じ label の四角を、中心の近い順に 1 対 1 でつなぐ。
    """
    tracks, prev, prev_end = [], [], None   # prev: 前の segment の四角とその列
    for g in segments:
        a, b = max(g["start"] / fps_src, start), min(g["end"] / fps_src, end)
        if b <= a:
            prev, prev_end = [], None
            continue
        base_t = start if base is None else base
        t = max(0, math.ceil((a - base_t) * rate * fps_out - 1e-9)) / fps_out   # 変わった後の素材が映る最初のフレーム
        cur, free = [], list(prev) if prev_end == g["start"] else []
        for box in g["boxes"]:
            cands = [p for p in free if p[0]["label"] == box["label"]]
            if cands:
                cx, cy = _center(box)
                p = min(cands, key=lambda q: (_center(q[0])[0] - cx) ** 2 + (_center(q[0])[1] - cy) ** 2)
                free.remove(p)
                tr = p[1]
            else:
                tr = []
                tracks.append(tr)
            tr.append((t, padded(box, pad)))
            cur.append((box, tr))
        prev, prev_end = cur, g["end"]
    return tracks


def to_mask(track, step, fps_out, kind, shape):
    """列を構成表のマスク 1 件にする。四辺のどれかが step 以上動いた所だけ keys にし、無ければ固定のマスク。"""
    first = track[0][1]
    keys, last = [], first
    for t, box in track[1:]:
        if max(abs(e - f) for e, f in zip(_edges(box), _edges(last))) < step:
            continue
        if not keys:
            keys.append({"t": 0.0, **first})
        hold = round(t - 1 / fps_out, 6)
        if hold > keys[-1]["t"]:
            keys.append({"t": hold, **last})
        if t <= keys[-1]["t"]:
            keys.pop()   # 同じフレームに落ちた変わり目は後の位置を使う
        keys.append({"t": round(t, 6), **box})
        last = box
    m = {"type": kind, "shape": shape, **first}
    if keys:
        m.update({k: keys[0][k] for k in "xywh"})   # 最初の位置 (同じフレームの変わり目で入れ替わることがある)
        m["keys"] = keys
    return m


def convert(sb_path, track_path, kind=None, shape=None):
    """マスクを追加した構成表 (元の JSON に masks を追加したもの) と、結果の要約を返す。"""
    cfg = C.config()
    s = cfg.section("track")
    kind, shape = kind or s["mask_type"], shape or s["mask_shape"]
    with open(track_path, encoding="utf-8") as f:
        tj = json.load(f)
    if tj.get("version") != FORMAT_VERSION:
        sys.exit(f"位置の JSON の version が {tj.get('version')}。読めるのは {FORMAT_VERSION}")
    sb = cfg.load_storyboard(sb_path)                  # 素材の場所と fps を決めるため
    with open(sb_path, encoding="utf-8") as f:
        raw = json.load(f)                              # 書き出すのは元の JSON (設定の値を写し込まない)
    fps_out = float(sb["fps"])
    used = set()
    added = moving = 0
    skipped = []
    spans = C.row_spans(sb)   # 行の長さは組む側 (sb2resolve.plan) と同じく、フレームに丸めた長さで数える
    for i, (r, out_r) in enumerate(zip(sb["rows"], raw["rows"])):
        if not C.same_path(r.get("source") or sb["source"], tj["video"]):
            skipped.append(i + 1)
            continue
        start, end = C.tc_to_sec(r["start"]), C.tc_to_sec(r["end"])
        if end <= start:
            sys.exit(f"{i + 1} 行目の end が start より前か同じ")
        rate = spans[i][1] / fps_out / (end - start)
        for n, g in enumerate(tj["segments"]):
            if g["end"] / tj["fps"] > start and g["start"] / tj["fps"] < end:
                used.add(n)
        masks = []
        base = round(start * tj["fps"]) / tj["fps"]   # 組む側と同じく、素材の開始フレームを丸める
        for tr in row_tracks(tj["segments"], tj["fps"], start, end, rate, fps_out, s["pad"], base):
            m = to_mask(tr, s["key_step"], fps_out, kind, shape)
            if m not in masks:
                masks.append(m)
        if masks:
            out_r["masks"] = out_r.get("masks", []) + masks
            added += len(masks)
            moving += sum(1 for m in masks if "keys" in m)
    return raw, {"masks": added, "moving": moving, "rows": len(sb["rows"]), "skipped_rows": skipped,
                 "segments": len(tj["segments"]), "unused_segments": len(tj["segments"]) - len(used)}


def default_out(sb_path):
    stem, _ = os.path.splitext(os.path.abspath(sb_path))
    return stem + "-masks.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("storyboard")
    ap.add_argument("track_json")
    ap.add_argument("--type", choices=["mosaic", "blur", "black"], help="隠し方 (既定は config の track.mask_type)")
    ap.add_argument("--shape", choices=["rect", "ellipse"], help="形 (既定は config の track.mask_shape)")
    a = ap.parse_args()
    out = default_out(a.storyboard)
    doc, r = convert(a.storyboard, a.track_json, a.type, a.shape)
    lines = []
    if r["skipped_rows"]:
        lines.append(f"録画が位置の JSON と違うので追加しなかった行: {', '.join(map(str, r['skipped_rows']))}")
    ok = len(r["skipped_rows"]) < r["rows"]
    if not ok:
        lines.append("どの行も位置の JSON の録画 (video) と同じ録画を使っていない。何も書かなかった")
    else:
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
    C.report(ok, f"track2masks: マスク {r['masks']} (動く {r['moving']})、segment {r['segments']} のうち "
                 f"どの行にも当たらない {r['unused_segments']}", out if ok else None, lines)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
