"""今開いているタイムラインのマーカーを読み、構成表のどの行に当たるかを出す。Resolve は読むだけで書き換えない。

使い方 (Resolve を使うので resolve_lock.py run で包む):
  python scripts/resolve_lock.py run --owner <名前> -- python scripts/read_markers.py [--storyboard 構成表.json]

- 読むのはタイムラインのマーカー (人が打ったもの)。クリップのマーカーは行の目印として使う
- 行は、まず sb2resolve.py の記録 (出力先の sb2resolve\\*.json のうち、プロジェクトとタイムラインの名前が今のものと同じで
  いちばん新しいもの。--log で指定もできる) の行ごとの位置で決める。記録のクリップの位置が今のタイムラインの V1 と全部
  合い、目印のあるクリップの行が記録と食い違わないときだけ使う。止める (hold) のクリップは、その行の止めている所にする
- 記録が無いか合わなければ、sb2resolve.py が各行のクリップの頭に付けたマーカーの customData (row<番号>) で決める。目印の
  無いクリップは直前の行に含める。タイムラインを複製したり手で切ったりしても、目印はクリップと一緒に残る
- 素材にマーカー (OBS のピンが入ったチャプター) があると、Resolve はその素材のクリップのマーカーを素材と共有するので、
  sb2resolve.py は目印を付けられない (docs/scripting-api.md)。そのときは記録で決める
- --storyboard を渡すと、行のテロップ・肩テロップ・素材の秒 (start〜end) を一緒に出す
- 時刻はタイムラインの頭からの秒 (01:00:00:00 を 0 とする)

結果: 出力先 (config の out_dir) の read_markers\\<タイムラインの名前>.json と、画面に common.report の 1 行とマーカーごとの 1 行。
"""
import argparse
import glob
import json
import os
import re
import sys

import common as C

ROW_TAG = re.compile(r"^row(\d+)$")


def row_of_clip(clip_markers):
    """クリップのマーカー ({フレーム: {customData, ...}}) から行の番号。無ければ None。"""
    for _, m in sorted(clip_markers.items()):
        hit = ROW_TAG.match(str(m.get("customData") or ""))
        if hit:
            return int(hit.group(1))
    return None


def assign_rows(clips):
    """clips: [{start, end, row}] (タイムラインの頭からのフレーム、row は目印の番号か None)。目印の無いクリップに直前の行を入れる。"""
    out, cur = [], None
    for c in sorted(clips, key=lambda c: c["start"]):
        cur = c["row"] if c["row"] is not None else cur
        out.append(dict(c, row=cur, tagged=c["row"] is not None))
    return out


def record_clips(log):
    """sb2resolve の記録から [{start, end, row, tagged}] (タイムラインの頭からのフレーム)。止めたクリップは tagged が False。"""
    out = []
    for r in (log.get("timeline") or {}).get("rows") or []:
        res = r.get("result") or {}
        if "start" in res and "end" in res:
            out.append({"start": res["start"], "end": res["end"], "row": r["row"], "tagged": True})
        h = (r.get("hold") or {}).get("result") or {}
        if "start" in h and "frames" in h:
            out.append({"start": h["start"], "end": h["start"] + h["frames"], "row": r["row"], "tagged": False})
    return sorted(out, key=lambda c: c["start"])


def find_record(folder, project, timeline):
    """folder の sb2resolve の記録のうち、プロジェクトとタイムラインの名前が同じでいちばん新しいもののパス。無ければ None。"""
    hits = []
    for path in glob.glob(os.path.join(folder, "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("project") == project and d.get("timeline_name") == timeline:
            hits.append((str(d.get("started") or ""), path))
    return max(hits)[1] if hits else None


def same_positions(a, b):
    """クリップの位置 (start, end) の並びが同じか。"""
    return sorted((c["start"], c["end"]) for c in a) == sorted((c["start"], c["end"]) for c in b)


def tag_conflicts(rec_clips, on_tl):
    """目印のあるクリップで、記録の行と目印の行が食い違う位置 (start, end) の list。目印は行の頭のクリップにだけ付くので、
    記録で止めている所 (tagged が False) に目印があるのも食い違い。同じ長さのクリップを入れ替えたタイムラインを見つける"""
    rec = {(c["start"], c["end"]): c for c in rec_clips}
    out = []
    for c in on_tl:
        r = rec.get((c["start"], c["end"]))
        if c["row"] is not None and r is not None and (r["row"] != c["row"] or not r["tagged"]):
            out.append((c["start"], c["end"]))
    return out


def locate(markers, clips, fps):
    """markers: {フレーム: {name, note, color}}。各マーカーの秒・行・クリップの中の位置 (秒) を返す。"""
    out = []
    for f, m in sorted(markers.items()):
        f = int(f)
        c = next((c for c in clips if c["start"] <= f < c["end"]), None)
        out.append({"frame": f, "seconds": round(f / fps, 2), "mss": C.sec_to_mss(f / fps),
                    "name": m.get("name", ""), "note": m.get("note", ""), "color": m.get("color", ""),
                    "row": c["row"] if c else None,
                    "in_hold": bool(c and not c["tagged"]),
                    "into_clip": round((f - c["start"]) / fps, 2) if c else None})
    return out


def row_info(sb, n):
    if not sb or not n or not 1 <= n <= len(sb["rows"]):
        return {}
    r = sb["rows"][n - 1]
    return {k: r[k] for k in ("caption", "badge", "start", "end", "seconds") if k in r}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--storyboard", help="行のテロップなどを一緒に出す構成表")
    ap.add_argument("--log", help="行の位置を読む sb2resolve.py の記録 (既定は、出力先の sb2resolve の下で、"
                                  "プロジェクトとタイムラインの名前が同じいちばん新しいもの)")
    a = ap.parse_args()
    sb = None
    if a.storyboard:
        with open(a.storyboard, encoding="utf-8") as f:
            sb = json.load(f)

    r = C.connect_or_exit()
    p = r.GetProjectManager().GetCurrentProject()
    tl = p.GetCurrentTimeline() if p else None
    if not tl:
        return C.report(False, "read_markers: 開いているタイムラインが無い")
    fps = float(tl.GetSetting("timelineFrameRate"))
    t0 = tl.GetStartFrame()
    on_tl = [{"start": it.GetStart() - t0, "end": it.GetEnd() - t0, "row": row_of_clip(it.GetMarkers() or {})}
             for it in (tl.GetItemListInTrack("video", 1) or [])]
    rec_path = a.log or find_record(C.config().out_dir("sb2resolve"), p.GetName(), tl.GetName())
    rec_clips, note = None, None
    if rec_path:
        with open(rec_path, encoding="utf-8") as f:
            rec_clips = record_clips(json.load(f))
        if not same_positions(rec_clips, on_tl):
            note = f"sb2resolve の記録のクリップの位置が今のタイムラインと合わないので、目印で決めた ({rec_path})"
            rec_clips = None
        elif tag_conflicts(rec_clips, on_tl):
            note = (f"sb2resolve の記録の行がクリップの目印と食い違う ({len(tag_conflicts(rec_clips, on_tl))} か所) ので、"
                    f"目印で決めた ({rec_path})")
            rec_clips = None
    clips = rec_clips if rec_clips is not None else assign_rows(on_tl)
    found = locate(tl.GetMarkers() or {}, clips, fps)
    for m in found:
        m.update(row_info(sb, m["row"]))

    name = tl.GetName()
    rows_from = "record" if rec_clips is not None else "tags"
    log = {"project": p.GetName(), "timeline": name, "fps": fps, "storyboard": a.storyboard,
           "rows_from": rows_from, "record": rec_path, "playhead": tl.GetCurrentTimecode(), "clips": clips,
           "markers": found}
    path = os.path.join(C.config().out_dir("read_markers"), re.sub(r'[\\/:*?"<>|]', "_", name) + ".json")
    C.write_json(path, log)
    untagged = rows_from == "tags" and not any(c["tagged"] for c in clips)
    lines = [f"{m['name'] or '(名前なし)'} {m['mss']} 行 {m['row']}" + (" (止めている所)" if m["in_hold"] else "")
             + (f" テロップ「{m['caption'].replace('|', ' ')}」" if m.get("caption") else "")
             + (f" メモ「{m['note']}」" if m["note"] else "") for m in found]
    head = []
    if note:
        head.append(note)
    if untagged:
        head.append("クリップに行の目印 (row<番号>) が無く、位置の合う sb2resolve.py の記録も無い。sb2resolve.py で組んだ"
                    "タイムラインでないか、人がクリップを動かした (記録は --log で指定できる)")
    elif rows_from == "tags" and sb:
        tagged = {c["row"] for c in clips if c["tagged"]}
        if len(tagged) < len(sb["rows"]):
            head.append(f"目印のある行は {len(tagged)} / {len(sb['rows'])}。目印の無い行のクリップは直前の行に数えている"
                        "ので、行がずれているかもしれない (素材にマーカーがあると目印を付けられない)")
    lines = head + lines
    how = "行は sb2resolve の記録から" if rows_from == "record" else "行はクリップの目印から"
    return C.report(not untagged, f"read_markers: {p.GetName()} / {name}、マーカー {len(found)} 個 ({how})", path, lines,
                    max_lines=50)


if __name__ == "__main__":
    sys.exit(main())
