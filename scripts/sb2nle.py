"""構成表 (JSON、書式は docs/storyboard.md) を、編集ソフト向けの FCPXML 1.10 / OTIO / EDL に変換する試作。

使い方: python sb2nle.py <構成表.json> [出力フォルダー] [--source 素材]
出力フォルダーの既定は 出力先 (config の out_dir) の nle。
テロップは FCPXML のタイトル (Basic Title) にする。OTIO と EDL はテロップを持てないのでマーカーに名前で残す。
マスクと周りを暗くする範囲は、どの形式にも載せられないので、マーカーのメモに座標 (素材の画素) を書いて残す (GUI で手で付ける前提)。
書体・字の大きさ・色・テロップの背景・位置は config/defaults.json の caption (構成表で上書きできる)。
素材の場所と ffprobe の場所は scripts/common.py が決める。
"""
import argparse
import json
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import common as C


def probe(path):
    """素材の (幅, 高さ, 長さの秒)。測るのは common.probe_video (組む側と同じ)。convert の probe_fn (テストの差し替え) も同じ形。"""
    v = C.probe_video(path)
    return v["width"], v["height"], v["duration"]


def load(sb_path, source=None):
    """構成表を読む。fps と seconds_per_row は 構成表 → config の順。素材の場所は common.Config.load_storyboard が決める。
    素材・使う範囲・タイムラインでの位置とフレーム数は common.row_clips (Resolve に組む側と同じ読み方)。
    素材の頭と長さ (in_f・src_len) と速さは、形式に書くフレームの整数からここで決める。
    形式の時間の単位は 1/fps 秒なので、fps は整数だけを扱う。整数でなければ ValueError (row_clips とずれないように)。"""
    sb = C.config().load_storyboard(sb_path, source)
    if sb.get("layout") is not None:   # 画面を切って置き、余白をぼかした背景にする形は FCPXML に書けない。全体のまま出さずに止める
        sys.exit("layout (画面を縮めて寄せ、余白をぼかした背景にする配置) は FCPXML / OTIO / EDL に書けない。"
                 "Resolve に組むときは sb2resolve.py を使う")
    if float(sb["fps"]) != int(float(sb["fps"])):
        raise ValueError(f"fps は整数だけを扱う: {sb['fps']}")
    fps = int(sb["fps"])
    rows = []
    rec = 0  # タイムラインの終わり (フレーム)
    for r, c in zip(sb["rows"], C.row_clips(sb)):
        s, e, out_f = c["start"], c["end"], c["frames"]
        in_f, src_len = round(s * fps), round((e - s) * fps)
        rows.append(dict(i=c["row"] - 1, src=c["source"], in_f=in_f, src_len=src_len, out_f=out_f, rec=c["rec"],
                         speed=src_len / out_f, caption=r.get("caption", "").replace("|", "\n"),
                         badge=r.get("badge"), zoom=r.get("zoom"), focus=r.get("focus"), hold=r.get("hold"), raw=r,
                         masks=sb.get("masks", []) + r.get("masks", [])))
        rec = c["rec"] + out_f + c["hold_frames"]   # 止める区間 (hold) は形式に載せず、空けておく (Resolve と位置をそろえる)
    return sb, fps, rows, rec


def note_of(r):
    parts = []
    if r["badge"]:
        parts.append(f"肩テロップ: {r['badge']}")
    if r["speed"] > 1.001:
        parts.append(f"速さ x{r['speed']:.2f}")
    for m in r["masks"]:
        parts.append("マスク {type}/{shape} x={x} y={y} w={w} h={h}".format(
            type=m.get("type", "blur"), shape=m.get("shape", "rect"), **{k: m[k] for k in "xywh"}))
    if r["hold"]:
        parts.append(f"止める {r['hold']['seconds']}秒 ({r['hold'].get('at', 'end')})")
    if r["focus"]:
        f = r["focus"]
        parts.append("周りを暗くする x={x} y={y} w={w} h={h}".format(**{k: f[k] for k in "xywh"})
                     + (f" dim={f['dim']}" if "dim" in f else ""))
    if r["zoom"]:
        z = r["zoom"]
        parts.append(f"ズーム x={z['x']} y={z['y']} w={z['w']} h={z['h']} {z.get('seconds', 0)}秒"
                     + (" (ズーム範囲から全体へ引く)" if z.get("direction") == "out" else ""))
    return " / ".join(parts)


def warnings_of(sb, rows):
    """構成表の知らない項目 (sb2resolve.py と同じ文) と、セーフエリア (config の safe_area) に収まらないテロップとズームの警告。
    自動では直さない。"""
    font_file = C.caption_font(sb)
    out = C.unknown_key_warnings(sb)
    for r in rows:
        if r["caption"]:
            out += [f"row{r['i'] + 1}: {w}" for w in C.fit_caption(sb, r["caption"], font_file)["warnings"]]
        w = C.zoom_safe_area_warning(sb, r.get("zoom_tl", r["zoom"]))   # 判定はタイムラインの画素に直した後の値
        if w:
            out.append(f"row{r['i'] + 1}: {w}")
        if r["hold"]:
            out.append(f"row{r['i'] + 1}: 止める区間 (hold) は FCPXML / OTIO / EDL に載せていない (その分を空けてある)")
    return out


# ---------- FCPXML 1.10 ----------
def fcpxml(sb, fps, rows, total, srcinfo):
    """srcinfo: 素材ごとの (幅, 高さ, 長さの秒)。sequence (タイムライン) の解像度は構成表の timeline から取り、
    素材の解像度は素材の format に使う。ズーム範囲は素材の画素で書き、convert がタイムラインの画素に直した値 (zoom_tl) を使う
    (docs/storyboard.md)。"""
    W, H = sb["timeline"]["width"], sb["timeline"]["height"]
    cap = sb["caption"]
    font_file = C.caption_font(sb)
    color = " ".join(f"{v / 255:g}" for v in cap["text_rgb"]) + " 1"
    t = lambda f: f"{f}/{fps}s" if f else "0s"  # noqa: E731

    def fmt(fid, w, h):
        return f'<format id="{fid}" name="FFVideoFormat{h}p{fps}" frameDuration="1/{fps}s" width="{w}" height="{h}"/>'

    assets, aid = {}, 3
    for r in rows:
        if r["src"] not in assets:
            assets[r["src"]] = f"r{aid}"
            aid += 1
    res = [fmt("r1", W, H),
           '<effect id="r2" name="Basic Title" uid=".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"/>']
    formats = {(W, H): "r1"}   # 素材の解像度がタイムラインと違うときだけ、素材用の format を追加する
    for src in assets:
        wh = tuple(srcinfo[src][:2])
        if wh not in formats:
            formats[wh] = f"r{aid}"
            aid += 1
            res.append(fmt(formats[wh], *wh))
    asset_fmt = {src: formats[tuple(srcinfo[src][:2])] for src in assets}
    for src, a in assets.items():
        uri = Path(src).as_uri()
        res.append(f'<asset id="{a}" name={quoteattr(Path(src).stem)} start="0s" duration="{t(round(srcinfo[src][2] * fps))}" '
                   f'hasVideo="1" hasAudio="1" format="{asset_fmt[src]}" audioSources="1" audioChannels="2">'
                   f'<media-rep kind="original-media" src={quoteattr(uri)}/></asset>')
    clips = []
    for r in rows:
        inner = []
        # 子要素の offset は親クリップの素材時間で書く (FCPXML のルール)
        if r["caption"]:
            fit = C.fit_caption(sb, r["caption"], font_file)
            # テロップの背景の中心の高さ (下が 0、上が 1) を FCPXML の位置 (画面の高さを 100、中央が 0、上が正) にする。
            # Resolve がタイトルの位置を読み込むかは未確認
            py = (fit["bar_center"] - 0.5) * 100
            text = "\n".join(fit["lines"])
            inner.append(
                f'<title ref="r2" lane="1" offset="{t(r["in_f"])}" duration="{t(r["out_f"])}" name={quoteattr(r["caption"][:30])}>'
                f'<adjust-transform position="0 {py:.4f}"/>'
                f'<text><text-style ref="ts{r["i"]}">{escape(text)}</text-style></text>'
                f'<text-style-def id="ts{r["i"]}"><text-style font={quoteattr(cap["font_name"])} fontSize="{fit["size"]}" '
                f'fontColor="{color}" alignment="center"/></text-style-def></title>')
        if r["zoom"]:
            z = r["zoom_tl"]
            g = C.zoom_geometry(z, W, H)   # 倍率と範囲の中心は Resolve に組む側 (common.scene_values) と同じ決め方
            s, (cx, cy) = g["scale"], g["center"]
            # FCPXML の position は画面の高さを 100 とした値。y は上が正
            px, py = (W / 2 - cx) * s / H * 100, (cy - H / 2) * s / H * 100
            zf = round(float(z.get("seconds", 0)) * fps)
            # 構成表の zoom.direction が "out" なら、ズーム範囲から全体へ引く (始めと終わりを入れ替える)
            sc0, sc1 = C.zoom_ends(z, "1 1", f"{s:.4f} {s:.4f}")
            po0, po1 = C.zoom_ends(z, "0 0", f"{px:.4f} {py:.4f}")
            if zf == 0:   # 最初から寄った (引いた) 状態。同じ時刻に 2 つの値を書かず、終わりの値だけを置く
                inner.insert(0, f'<adjust-transform position="{po1}" scale="{sc1}"/>')
            else:
                inner.insert(0,
                    f'<adjust-transform><param name="scale"><keyframeAnimation>'
                    f'<keyframe time="{t(r["in_f"])}" value="{sc0}"/><keyframe time="{t(r["in_f"] + zf)}" value="{sc1}"/>'
                    f'</keyframeAnimation></param><param name="position"><keyframeAnimation>'
                    f'<keyframe time="{t(r["in_f"])}" value="{po0}"/><keyframe time="{t(r["in_f"] + zf)}" value="{po1}"/>'
                    f'</keyframeAnimation></param></adjust-transform>')
        note = note_of(r)
        inner.append(f'<marker start="{t(r["in_f"])}" duration="1/{fps}s" value={quoteattr(r["caption"].splitlines()[0] if r["caption"] else f"row {r["i"] + 1}")}'
                     + (f' note={quoteattr(note)}' if note else "") + '/>')
        if abs(r["speed"] - 1) > 1e-3:
            inner.insert(0, f'<timeMap><timept time="{t(r["in_f"])}" value="{t(r["in_f"])}" interp="linear"/>'
                            f'<timept time="{t(r["in_f"] + r["out_f"])}" value="{t(r["in_f"] + r["src_len"])}" interp="linear"/></timeMap>')
        clips.append(f'<asset-clip ref="{assets[r["src"]]}" offset="{t(r["rec"])}" start="{t(r["in_f"])}" '
                     f'duration="{t(r["out_f"])}" name={quoteattr(f"row{r["i"] + 1}")} format="{asset_fmt[r["src"]]}" tcFormat="NDF">'
                     + "".join(inner) + "</asset-clip>")
    name = Path(sb["output"]).stem
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.10">\n<resources>\n'
            + "\n".join(res) + f'\n</resources>\n<library><event name={quoteattr(name)}><project name={quoteattr(name)}>'
            f'<sequence format="r1" duration="{t(total)}" tcStart="0s" tcFormat="NDF"><spine>\n'
            + "\n".join(clips) + "\n</spine></sequence></project></event></library>\n</fcpxml>\n")


# ---------- OTIO ----------
def otio(sb, fps, rows):
    rt = lambda v: {"OTIO_SCHEMA": "RationalTime.1", "rate": float(fps), "value": float(v)}  # noqa: E731
    tr = lambda s, d: {"OTIO_SCHEMA": "TimeRange.1", "start_time": rt(s), "duration": rt(d)}  # noqa: E731
    color = sb["marker"]["row_color"].upper()
    children = []
    for r in rows:
        eff = []
        if abs(r["speed"] - 1) > 1e-3:
            eff.append({"OTIO_SCHEMA": "LinearTimeWarp.1", "name": "", "effect_name": "LinearTimeWarp",
                        "time_scalar": r["speed"], "metadata": {}})
        note = note_of(r)
        children.append({
            "OTIO_SCHEMA": "Clip.2", "name": f"row{r['i'] + 1}", "metadata": {},
            # OTIO では source_range の長さがタイムライン上の長さ。速さは time_scalar で表す
            "source_range": tr(r["in_f"], r["out_f"]),
            "effects": eff,
            "markers": [{"OTIO_SCHEMA": "Marker.2", "name": r["caption"].splitlines()[0] if r["caption"] else "",
                         "color": color, "marked_range": tr(r["in_f"], 1), "comment": note,
                         "metadata": {"Resolve_OTIO": {"Note": note}}}],
            "enabled": True,
            "media_references": {"DEFAULT_MEDIA": {
                "OTIO_SCHEMA": "ExternalReference.1", "name": Path(r["src"]).name, "metadata": {},
                # Resolve は file:// の %20 を解釈せずオフラインになる。素のパスで書く
                "target_url": r["src"].replace("\\", "/"), "available_range": None}},
            "active_media_reference_key": "DEFAULT_MEDIA"})
        if r["hold"]:   # 止める区間は載せず、その長さの空き (Gap) を置いて後ろの行の位置を Resolve とそろえる
            children.append({"OTIO_SCHEMA": "Gap.1", "name": f"row{r['i'] + 1}-hold", "metadata": {},
                             "source_range": tr(0, C.hold_frames(sb, r["raw"])), "effects": [], "markers": [],
                             "enabled": True})
    return {"OTIO_SCHEMA": "Timeline.1", "name": Path(sb["output"]).stem, "metadata": {},
            "global_start_time": rt(0),
            "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "metadata": {}, "effects": [], "markers": [],
                       "enabled": True, "source_range": None,
                       "children": [{"OTIO_SCHEMA": "Track.1", "name": "V1", "kind": "Video", "metadata": {},
                                     "effects": [], "markers": [], "enabled": True, "source_range": None,
                                     "children": children}]}}


# ---------- EDL (CMX3600) ----------
def edl(sb, fps, rows):
    def tc(f):
        return C.frame_to_timecode(f, fps)
    color = sb["marker"]["row_color"]
    lines = [f"TITLE: {Path(sb['output']).stem}", "FCM: NON-DROP FRAME", ""]
    for n, r in enumerate(rows, 1):
        src_out = r["in_f"] + r["out_f"]  # 速さを変えるときも、素材側はタイムラインと同じ長さで書き、M2 行で速さを渡す
        rec_out = r["rec"] + r["out_f"]
        lines.append(f"{n:03d}  AX       V     C        {tc(r['in_f'])} {tc(src_out)} {tc(r['rec'])} {tc(rec_out)}")
        lines.append(f"* FROM CLIP NAME: {Path(r['src']).name}")
        if abs(r["speed"] - 1) > 1e-3:
            lines.append(f"M2   AX       {r['speed'] * fps:05.1f}                {tc(r['in_f'])}")
        if r["caption"]:
            lines.append(f" |C:ResolveColor{color} |M:{r['caption'].splitlines()[0]} |D:1")
        lines.append("")
    return "\n".join(lines) + "\n"


def convert(sb_path, out_dir, source=None, probe_fn=None):
    """変換して 3 つのファイルを書く。probe_fn はテストで ffprobe の代わりに渡す。書いたパスの dict を返す。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sb, fps, rows, total = load(sb_path, source)
    srcinfo = {}
    for r in rows:
        if r["src"] not in srcinfo:
            srcinfo[r["src"]] = (probe_fn or probe)(r["src"])
        # ズーム範囲は素材の画素。素材をタイムラインに全体を収めて置いたときの画素に直す (同じ解像度なら変わらない)
        r["zoom_tl"] = C.source_to_timeline(r["zoom"], *srcinfo[r["src"]][:2], sb)
    stem = Path(sb["output"]).stem
    paths = {k: out_dir / f"{stem}.{k}" for k in ("fcpxml", "otio", "edl")}
    paths["fcpxml"].write_text(fcpxml(sb, fps, rows, total, srcinfo), encoding="utf-8", newline="\n")
    paths["otio"].write_text(json.dumps(otio(sb, fps, rows), ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    paths["edl"].write_text(edl(sb, fps, rows), encoding="utf-8", newline="\r\n")
    return sb, rows, total, paths


def main():
    ap = argparse.ArgumentParser(description="構成表を FCPXML / OTIO / EDL に変換する")
    ap.add_argument("storyboard")
    ap.add_argument("out_dir", nargs="?", help="出力フォルダー (既定は 出力先 (config の out_dir) の nle)")
    ap.add_argument("--source", help="素材の場所 (構成表の source より優先)")
    a = ap.parse_args()
    a.out_dir = a.out_dir or C.config().out_dir("nle")
    os.makedirs(a.out_dir, exist_ok=True)
    # 記録の JSON: 出力フォルダーの <構成表の名前>.sb2nle.json (書いたファイル・行ごとの範囲と速さ・警告。失敗したときは例外の全文)
    path = os.path.join(a.out_dir, Path(a.storyboard).stem + ".sb2nle.json")
    log = {"storyboard": os.path.abspath(a.storyboard)}
    try:
        sb, rows, total, paths = convert(a.storyboard, a.out_dir, a.source)
        warns = warnings_of(sb, rows)
        log.update(ok=True, total_frames=total, files={k: str(v) for k, v in paths.items()}, warnings=warns,
                   rows=[{k: r[k] for k in ("i", "src", "in_f", "src_len", "out_f", "rec", "speed")} for r in rows])
    except (Exception, SystemExit) as e:   # 変換中の例外も 1 行と記録の JSON にする
        import traceback
        log.update(ok=False, error=traceback.format_exc())
        C.write_json(path, log)
        last = (str(e) or type(e).__name__).strip().splitlines()
        return C.report(False, "sb2nle: 変換できない", path, [last[-1] if last else type(e).__name__])
    C.write_json(path, log)
    # 画面には件数と警告だけを出す (書いたファイルと行ごとの使う範囲は記録の JSON にある)
    return C.report(True, f"sb2nle: rows {len(rows)}, total {total} frames", path, ["警告 " + w for w in warns])


if __name__ == "__main__":
    sys.exit(main())
