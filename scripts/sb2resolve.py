"""構成表 (書式は docs/storyboard.md) から、Resolve Studio にタイムラインを組む。

使い方 (Resolve を使うので resolve_lock.py run で包む。--dry-run は接続しないので包まなくてよい):
  python scripts/resolve_lock.py run --owner <名前> -- python scripts/sb2resolve.py 構成表.json [--source 素材] [--name 新しいプロジェクトの名前 | --project 既存のプロジェクトの名前]
      [--timeline タイムラインの名前] [--output 書き出し先] [--render | --render-anyway] [--rows 行] [--overwrite] [--dry-run]
- 既定は新しいプロジェクトを作って組む。名前は --name、構成表の project、output の名前の順。同じ名前のプロジェクトがあれば
  末尾に時刻を追加する (既存のプロジェクトを上書きしない)。--name で名前を決めたときに同じ名前があれば止める
- --project: 既存のプロジェクトを開き、新しいタイムラインとして組む (再生フレームレートを GUI で直したプロジェクトを使い回す)。
  プロジェクトの設定は変えない。タイムラインのフレームレートか解像度が構成表と違えば、何も追加せずに止める。
  再生フレームレートが違えば警告する。素材はメディアプールのいちばん上のフォルダーに取り込む (同じ素材が既にあればそれを使う)
- タイムラインの名前は --timeline、無ければ新しいプロジェクトではプロジェクトの名前、既存のプロジェクトでは構成表の project か
  output の名前。--timeline の名前が既にあれば止める。決めた名前が既にあれば末尾に -2・-3 と番号を追加する (上書きしない)
- どの行にも同じ規則で、カット (source の start〜end)・倍速 (seconds)・止める (hold)・テロップ・肩テロップ・ズーム・マスク・
  周りを暗くする (focus) を置く。
  マスクとズーム範囲は素材の画素で書き、素材の解像度でタイムラインの画素に直す
- 同じ素材は 1 回だけ取り込む。AppendToTimeline の戻り値の TimelineItem をそのまま使う (トラックを読み直さない)
- 行ごとの Fusion は common.build_fusion (マスク → 周りを暗くする → ズーム → テロップ → 肩テロップ)
- focus の枠線の色 auto は素材の画素の明るさで決め (common.with_auto_border)、callout の dst の省略は自動の置き場所
  (common.callout_auto_dst) にする。決めた値は記録の auto (--dry-run でも同じ値)
- 書き出し: --render (完成版)・--render-anyway (NG があっても仮組みとして)・--rows a-b (行を指定した部分。--render とは一緒に使えない)・
  --overwrite (既にある書き出し先を置き換える)。書き出し先の決め方と守り方、一時ディレクトリを通す書き方、記録の render は
  docs/storyboard.md
結果: 出力先 (config の out_dir) の sb2resolve\\<構成表の名前>.json と、画面に common.report の 1 行。
組む前の点検 (start と end、素材の長さ、マスク・focus・callout の書式と現れ始める時刻、callout の自動の置き場所の空き、
肩テロップに書いた速さ (「9 倍速」「等倍」) と行の実際の速さ) で
誤りがあれば、Resolve に触らずに止める。
--dry-run は点検と、組むときと同じ値の計算 (セーフエリアなどの警告、素材の解像度) だけをして、Resolve に触らない。
"""
import argparse
import os
import sys
import tempfile
import time
import traceback

import common as C


# ---------- 組む前 ----------
def plan(sb):
    """行ごとの素材・使う範囲 (秒)・タイムラインでの位置とフレーム数・速さ (%)。
    行の読み方は common.row_clips。速さと止めたクリップの位置はここで決める (check_final は別に計算して照らし合わせる)。"""
    fps = float(sb["fps"])
    out = []
    for c in C.row_clips(sb):
        s, e, at, n, hn = c["start"], c["end"], c["rec"], c["frames"], c["hold_frames"]
        out.append({"row": c["row"], "source": c["source"], "start": s, "end": e, "rec": at,
                    "frames": n, "pct": (e - s) / (n / fps) * 100 if n > 0 else None,
                    "hold_rec": at + n if hn else None, "hold_frames": hn})
    return out


def problems(sb, rows, lengths):
    """組む前に見つかる構成表の誤り (文の list)。lengths は素材ごとの長さ (秒)。読めない素材は長さを見ない。"""
    fps = float(sb["fps"])
    out = []
    for p, r in zip(rows, sb["rows"]):
        n = p["row"]
        if not os.path.isfile(p["source"]):
            out.append(f"{n} 行目: 素材が無い ({p['source']})")
        if p["end"] <= p["start"]:
            out.append(f"{n} 行目: end が start より前か同じ")
        if p["frames"] <= 0:
            out.append(f"{n} 行目: 秒数が 0 以下")
        length = lengths.get(p["source"])
        if length is not None and p["frames"] > 0:
            # 遅くする行 (seconds が使う範囲より長い) は、一度タイムラインの長さだけ等速で置くので、その分も素材に要る
            need = max(p["end"], p["start"] + p["frames"] / fps)
            if need > length + 1 / fps:
                out.append(f"{n} 行目: 素材の長さ {length:.2f} 秒を越える ({need:.2f} 秒まで使う)")
        for k, m in enumerate(list(sb.get("masks") or []) + list(r.get("masks") or []), 1):
            out += [f"{n} 行目のマスク {k}: {x}" for x in C.mask_problems(m)]
        if r.get("hold") is not None:
            bad = C.hold_problems(r["hold"], length)
            out += [f"{n} 行目の hold: {x}" for x in bad]
            at = r["hold"].get("at", "end") if isinstance(r["hold"], dict) else "end"
            held = p["end"] if at == "end" else None
            if held is None and not bad:
                held = C.tc_to_sec(at)
            if held is not None and p["hold_frames"] and held < p["hold_frames"] / fps:
                # 止めたクリップは、止めたいフレームで終わる範囲を hold の長さだけ置くので、その前に素材が要る
                out.append(f"{n} 行目の hold: 止めたい時刻が素材の頭に近く、止める範囲を置けない")
        if r.get("caption_style") not in (None, "closing"):
            out.append(f"{n} 行目: caption_style は closing だけ")
        for key, check, length_key in (("callout", C.callout_problems, "in"), ("focus", C.focus_problems, "seconds")):
            if r.get(key) is None or (key == "focus" and not r[key]):   # 空の callout は書式の誤り
                continue
            bad = check(r[key])
            out += [f"{n} 行目の {key}: {x}" for x in bad]
            total = p["frames"] + p["hold_frames"]
            start, _ = C.appear_frames(r[key], sb[key], length_key, fps) if not bad else (0, 0)
            if total > 0 and start >= total:
                out.append(f"{n} 行目の {key}: start ({start / fps:g} 秒) が行の長さ ({total / fps:g} 秒。止めた区間を含む)"
                           " 以降なので現れない")
        z = r.get("zoom")
        if z and not (all(isinstance(z.get(k), (int, float)) for k in "xywh") and z["w"] > 0 and z["h"] > 0):
            out.append(f"{n} 行目のズーム範囲: x y w h は数で、w と h は 0 より大きい")
        label = C.badge_speed_label(r.get("badge"))
        if label and p["pct"] is not None:
            bad = badge_speed_problem(label, p["pct"] / 100)
            if bad:
                out.append(f"{n} 行目: {bad}")
    return out


def badge_speed_problem(label, speed):
    """肩テロップの速さの表記 (common.badge_speed_label の値) と行の実際の速さ (倍。止めた区間は入れない) が合わなければ
    文、合えば None。「N 倍速」は実際の速さを N を書いた桁で四捨五入して N と同じなら合う (差が桁の半分まで)。
    「等倍」は 1 との差が common.SAME_SPEED_TOL まで。check_final.check_badge_speed は同じ決め方を別に持つ。"""
    same = abs(speed - 1) <= C.SAME_SPEED_TOL
    if label["places"] is None:
        ok, places = same, 2
    else:
        ok, places = abs(speed - label["speed"]) <= 0.5 * 10 ** -label["places"] + 1e-9, max(1, label["places"])
    if ok:
        return None
    return f"肩テロップは {label['text']}だが、実際の速さは {speed:.{places}f} 倍" + (" (等倍)" if same else "")


def auto_dst_problems(sb, rows, sizes, font_file):
    """dst を省略した callout の、自動の置き場所の空きの点検 (文の list)。Resolve に触る前に止めるため、組む前に
    common.callout_auto_dst と同じ計算をする。sizes は素材ごとの (幅, 高さ)。解像度の分からない素材の行と、書式の誤りの
    ある callout (problems が見る) は見ない。"""
    out = []
    for p, r in zip(rows, sb["rows"]):
        c, size = r.get("callout"), sizes.get(p["source"])
        if not isinstance(c, dict) or "dst" in c or not size or not size[0] or C.callout_problems(c):
            continue
        try:
            C.callout_auto_dst(sb, c, size[0], size[1], C.caption_band(sb, r, font_file))
        except ValueError as e:
            out.append(f"{p['row']} 行目の callout: {e}")
    return out


def total_frames(rows):
    """書き出しの全フレーム数 (行のフレーム数と止めた区間の合計)。"""
    return sum(p["frames"] + p["hold_frames"] for p in rows)


def probe_sources(rows):
    """素材ごとの ffprobe の値 (幅・高さ・フレームレート・長さ)。読めないものは入れない (点検で長さを見ない)。"""
    out = {}
    for p in rows:
        if p["source"] not in out and os.path.isfile(p["source"]):
            try:
                out[p["source"]] = C.probe_video(p["source"])
            except (RuntimeError, KeyError, IndexError, ValueError, SystemExit):
                pass
    return out


def row_values(sb, rows, sizes, font_file, reader=None):
    """行ごとの Fusion の値 (common.scene_values)。sizes は素材ごとの (幅, 高さ)。
    返す値は (行ごとの値の list、警告の list、誤りの list)。座標を使う行 (マスク・ズーム・focus) で素材の解像度が
    分からなければ誤り (違う座標で黙って組まない)。
    focus の枠線の色が auto の行は、素材の画素を読んで色を決める (common.with_auto_border。--dry-run でも)。
    決めた色と測った明るさは focus.border.auto に入れる。決められなければ誤り (その行は auto.dark の色で組む。
    --render は書き出さず、--render-anyway の仮組みはその色で書き出す)、しきい値に近ければ警告。
    値を作れない行 (callout の自動の置き場所に空きが無いなど) は誤りにし、値は部品の無い値 (common.empty_values。error に理由) にする。
    reader は素材のフレームを読む関数の差し替え (テスト用)。
    構成表と config/local.json の storyboard に知らない項目 (書き間違い) があれば警告する (common.unknown_keys)。"""
    vals, errs = [], []
    warns = C.unknown_key_warnings(sb)
    for p, row in zip(rows, sb["rows"]):
        size = sizes.get(p["source"]) or (None, None)
        row, auto = C.with_auto_border(sb, row, p["source"], p["frames"], p["pct"] / 100, reader)
        if auto and auto.get("error"):
            errs.append(f"{p['row']} 行目の focus: {auto['error']}")
        elif auto and auto.get("warning"):
            warns.append(f"{p['row']} 行目: {auto['warning']}")
        try:
            V = C.scene_values(sb, row, font_file, source_size=size, speed=p["pct"] / 100)
        except ValueError as e:   # callout の自動の置き場所に空きが無いなど。その行は組まない (add_fusion が飛ばす)
            errs.append(f"{p['row']} 行目: {e}")
            V = dict(C.empty_values(), error=str(e))
            vals.append(V)
            continue
        if auto and V["focus"] and V["focus"]["border"]:
            V["focus"]["border"]["auto"] = auto
        if C.needs_coords(V) and not size[0]:
            errs.append(f"{p['row']} 行目: 素材の解像度を読めないので、マスク・ズーム・focus・callout・point・layout の座標を直せない")
        if V["callout"] and V["callout"]["warning"]:
            warns.append(f"{p['row']} 行目: {V['callout']['warning']}")
        total = p["frames"] + p["hold_frames"]
        for key in ("focus", "callout"):
            end = V[key]["ramp_frames"][-1][0] if V[key] else 0
            if end > total:
                warns.append(f"{p['row']} 行目の {key}: 現れ終わる ({end / float(sb['fps']):g} 秒) 前に行が終わる"
                             f" (行の長さ {total / float(sb['fps']):g} 秒。止めた区間を含む)")
        if V["zoom"] and V["zoom"]["warning"]:
            warns.append(f"{p['row']} 行目: {V['zoom']['warning']}")
        if p["hold_frames"]:
            # 止めた区間では画が止まっているので、範囲を動かす keys は止めた瞬間の位置で止まる (現れる動きは続く)
            late = max([k["frame"] for m in V["masks"] + ([V["focus"]] if V["focus"] else []) for k in m["keys"]] or [0])
            if late > p["frames"] - 1:
                warns.append(f"{p['row']} 行目: マスクか focus の keys が行の後ろ (止めた区間) まで続く。止めた区間では"
                             "画が止まっているので、止めた瞬間の位置のまま")
        warns += [f"{p['row']} 行目: {w}" for w in (V["caption"] or {}).get("fit", {}).get("warnings", [])]
        warns += [f"{p['row']} 行目: {w}" for w in (V["side"] or {}).get("warnings", [])]
        vals.append(V)
    return vals, warns, errs


def auto_values(rows, vals):
    """行ごとに sb2resolve.py が決めた値: focus の枠線の色 (auto。決めた色・測った明るさ・見た素材のフレーム)、
    callout の自動の置き場所 (dst を省略した行) と重ねる順 (order)。記録の auto に入れる。"""
    out = []
    for p, V in zip(rows, vals):
        d = {}
        b = (V["focus"] or {}).get("border") or {}
        if b.get("auto"):
            d["focus_border"] = b["auto"]
        co = V["callout"] or {}
        if co.get("dst_auto"):
            d["callout_dst"] = dict(co["dst_auto"], h=round(co["dst"][3], 1), size=round(co["size"], 4))
        if co.get("order"):
            d["callout_order"] = co["order"]
        if d:
            out.append({"row": p["row"], **d})
    return out


def parse_rows(text, count):
    """--rows の値 (「4」か「3-5」) を (始まりの行, 終わりの行) にする。構成表の行は count 行。
    返す値は ((a, b), None) か (None, 誤りの文)。a > b と行の外は誤り (Resolve は MarkIn > MarkOut でも止めないので、ここで止める)。"""
    parts = str(text).strip().split("-")
    if not 1 <= len(parts) <= 2 or not all(x.strip().isdigit() for x in parts):
        return None, f"--rows {text}: 「4」か「3-5」の形で書く"
    a, b = int(parts[0]), int(parts[-1])
    if a > b:
        return None, f"--rows {text}: 始まりの行 {a} が終わりの行 {b} より後"
    if a < 1 or b > count:
        return None, f"--rows {text}: 構成表の行は 1〜{count}"
    return (a, b), None


def rows_label(sel):
    """部分の書き出しの名前に入れる行 (rows3-5。1 行なら rows4)。"""
    a, b = sel
    return f"rows{a}" if a == b else f"rows{a}-{b}"


def rows_frames(rows, sel):
    """a〜b 行目の、頭を 0 としたフレームの範囲 (最初, 最後。両端を含む)。最後は b 行目の止めたクリップの終わりまで。"""
    first, last = rows[sel[0] - 1], rows[sel[1] - 1]
    return first["rec"], last["rec"] + last["frames"] + last["hold_frames"] - 1


# ---------- Resolve ----------
def base_name(sb, storyboard=None):
    """構成表から決める名前: project、無ければ output の名前、それも無ければ構成表のファイルの名前。"""
    path = sb.get("output") or storyboard or "storyboard"
    return sb.get("project") or os.path.splitext(os.path.basename(path))[0]


def project_name(pm, sb, wanted=None):
    """作るプロジェクトの名前。wanted (--name) が既にあれば None。構成表から決めた名前が既にあれば時刻を追加する。"""
    names = set(pm.GetProjectListInCurrentFolder() or [])
    if wanted:
        return None if wanted in names else wanted
    base = base_name(sb)
    return base if base not in names else f"{base}-{time.strftime('%Y%m%d-%H%M%S')}"


def timeline_name(proj, base, wanted=None):
    """組むタイムラインの名前。wanted (--timeline) が既にあれば None。base が既にあれば -2・-3 と番号を追加する。
    同じ名前のタイムラインは作れない (CreateEmptyTimeline が None を返す。21.1.1 の実機) ので、先に名前で見る。"""
    names = {proj.GetTimelineByIndex(i + 1).GetName() for i in range(int(proj.GetTimelineCount() or 0))}
    if wanted:
        return None if wanted in names else wanted
    n, name = 2, base
    while name in names:
        name, n = f"{base}-{n}", n + 1
    return name


def monitor_format(W, H, fps):
    """モニターの形式 (videoMonitorFormat) の名前。1080p と 2160p だけ決める (それ以外は None で入れない)。"""
    return {1080: f"HD 1080p {fps:g}", 2160: f"UHD 2160p {fps:g}"}.get(H) if (W, H) in ((1920, 1080), (3840, 2160)) else None


SETTING_NAMES = {"timelineFrameRate": "タイムラインのフレームレート", "timelineResolutionWidth": "タイムラインの解像度の幅",
                 "timelineResolutionHeight": "タイムラインの解像度の高さ", "timelinePlaybackFrameRate": "再生フレームレート",
                 "videoMonitorFormat": "ビデオモニタリングのフォーマット"}


def same_setting(k, got, want):
    """プロジェクトの設定の読み戻し got が want と同じか (数は 0.001 まで。モニターの形式は文字のまま)。"""
    if k == "videoMonitorFormat":
        return got == want
    try:
        return abs(float(str(got).split()[0]) - float(want)) < 1e-3
    except (ValueError, IndexError):
        return False


def playback_warning(k, got, want):
    """再生フレームレートとモニターの形式が違うときの警告 (GUI での直し方)。"""
    what = SETTING_NAMES[k]
    return (f"{what} ({k}) が {got} のまま。このプロジェクトは プロジェクト設定 > マスター設定 > {what}を {want} にする"
            " (API では変えられない。再生でフレームが落ちて見えることがある)。新しいプロジェクトをこの値で始めるには、"
            "プロジェクト設定の「…」>「現在の設定をデフォルトプリセットに設定」を 1 回行う"
            " (docs/setup.md)")


def playback_values(W, H, fps):
    """再生フレームレートとモニターの形式 (1080p と 2160p だけ) の、構成表から決まる値。"""
    out = {"timelinePlaybackFrameRate": f"{fps:g}"}
    mf = monitor_format(W, H, float(fps))
    if mf:
        out["videoMonitorFormat"] = mf
    return out


def existing_settings(proj, W, H, fps):
    """既存のプロジェクトの設定を読むだけ (変えない)。タイムラインのフレームレートと解像度が構成表と違えば errors、
    再生フレームレートとモニターの形式が違えば warnings (新しいプロジェクトのときと同じ文)。"""
    core = {"timelineFrameRate": f"{fps:g}", "timelineResolutionWidth": str(W), "timelineResolutionHeight": str(H)}
    extra = playback_values(W, H, fps)
    out = {"read": {k: proj.GetSetting(k) for k in core | extra}, "errors": [], "warnings": []}
    for k, v in core.items():
        if not same_setting(k, out["read"][k], v):
            out["errors"].append(f"プロジェクトの{SETTING_NAMES[k]} ({k}) が {out['read'][k]}。構成表は {v}"
                                 " (既存のプロジェクトの設定は変えない)")
    out["warnings"] = [playback_warning(k, out["read"][k], v) for k, v in extra.items()
                       if not same_setting(k, out["read"][k], v)]
    return out


def project_settings(proj, W, H, fps):
    """作ったばかりのプロジェクトに、取り込みとタイムラインの作成より前に解像度とフレームレートを入れ、読み戻す。

    再生のフレームレート (timelinePlaybackFrameRate) とモニターの形式 (videoMonitorFormat) は、作った直後に入れても
    False で 24 のまま残った (21.1.1 の実機)。入れてみて読み戻し、違えば warnings に GUI での直し方を書く (自動では直さない)。
    """
    want = {"timelineFrameRate": f"{fps:g}", "timelineResolutionWidth": str(W), "timelineResolutionHeight": str(H)}
    out = {"set": bool(proj.SetSettings(want))}
    extra = playback_values(W, H, fps)
    out["set_playback"] = {k: bool(proj.SetSettings({k: v})) for k, v in extra.items()}
    want |= extra
    out["read"] = {k: proj.GetSetting(k) for k in want}
    out["warnings"] = [playback_warning(k, out["read"][k], v) for k, v in want.items()
                       if not same_setting(k, out["read"][k], v)]
    return out


def import_media(mp, rows, fallback_fps):
    """素材を 1 回ずつ取り込む。{素材のパス: {"item", "fps", "size", "frames"}}。取り込めない素材は item が None。"""
    media = {}
    for p in rows:
        src = p["source"]
        if src in media:
            continue
        # .pyi は ImportClipInfo (辞書) の list と書くが、21.1.1 では辞書を渡すと None が返る。パスの文字列なら通る
        got = mp.ImportMedia([src]) or []
        mpi = got[0] if got else None
        media[src] = {"item": mpi, "fps": C.source_fps(mpi, fallback_fps) if mpi else None,
                      "size": C.clip_resolution(mpi) if mpi else (None, None),
                      "frames": int(mpi.GetClipProperty("Frames") or 0) if mpi else None}
    return media


def build_timeline(proj, mp, sb, rows, media, name):
    """空のタイムラインに全部の行を 1 回の AppendToTimeline で置き、速さを変える。
    返す値は (タイムライン, 行ごとの項目, 行ごとの止めたクリップ (無ければ None), 行ごとの結果)。

    行に hold があれば、行の後ろに止めたクリップを 1 つずつ置く (置き方と確認の仕方は place_hold の説明)。

    SetSpeed(RippleTimeline=False) はタイムライン上の長さを保ち、素材側の範囲を伸び縮みさせる (21.1.1 の実機)。
    そこで各行を、タイムラインでの長さ (行の秒数) の分だけ等速で置いてから速さを変える。後ろの行は動かない。
    startFrame / endFrame は素材のフレームなので、素材のフレームレートで秒から換算する。endFrame は含まない。
    """
    fps = float(sb["fps"])
    tl = mp.CreateEmptyTimeline(name)
    if not tl:
        return None, [], [], []
    proj.SetCurrentTimeline(tl)
    t0 = tl.GetStartFrame()
    infos = []
    for p in rows:
        m = media[p["source"]]
        st = round(p["start"] * m["fps"])
        infos.append({"mediaPoolItem": m["item"], "startFrame": st, "endFrame": st + round(p["frames"] / fps * m["fps"]),
                      "recordFrame": t0 + p["rec"]})
    got = mp.AppendToTimeline(infos) or []
    # 戻り値は映像の項目 (音声は GetLinkedItems)。行との対応は置いた位置で取る
    at = {g.GetStart() - t0: g for g in got}
    items = [at.get(p["rec"]) for p in rows]
    holds, held = [], {}
    for p, row in zip(rows, sb["rows"]):
        if not p["hold_frames"]:
            holds.append(None)
            continue
        m = media[p["source"]]
        h = C.hold_source_frame(row, m["fps"], p["frames"], p["pct"] / 100)
        hit, held[p["row"]] = place_hold(mp, tl, m, p, h, round(p["hold_frames"] / fps * m["fps"]), round(fps))
        holds.append(hit)
    results = []
    for p, row, it in zip(rows, sb["rows"], items):
        m = media[p["source"]]
        r = {"row": p["row"], "rec": p["rec"], "frames": p["frames"], "pct": p["pct"]}
        if p["hold_frames"]:
            r["hold"] = held[p["row"]]
        if it is None:
            r["error"] = "AppendToTimeline の戻り値にこの行のクリップが無い"
            results.append(r)
            continue
        if abs(p["pct"] - 100.0) > 1e-6:
            r["set_speed"] = bool(it.SetSpeed({"Percentage": p["pct"], "RippleTimeline": False}))
        want = (round(p["start"] * m["fps"]), round(p["end"] * m["fps"]))
        got_r = {"start": it.GetStart() - t0, "end": it.GetEnd() - t0, "src_start": it.GetSourceStartFrame(),
                 "src_end": it.GetSourceEndFrame(), "speed": (it.GetSpeed() or {}).get("Percentage")}
        r["result"] = got_r
        r["src_diff"] = (got_r["src_start"] - want[0], got_r["src_end"] - want[1])
        # 素材側は、割り切れない倍速で 1 フレームずれることがある。素材の最後まで届くクリップでは GetSourceEndFrame が
        # 1〜2 少なく出るので、そのときは右の余り (GetRightOffset) が 0 かで見る (docs/scripting-api.md)
        reaches_end = bool(m["frames"]) and want[1] >= m["frames"] and it.GetRightOffset() == 0
        r["match"] = (got_r["start"] == p["rec"] and got_r["end"] - got_r["start"] == p["frames"]
                      and r.get("set_speed", True) and abs(r["src_diff"][0]) <= 1
                      and abs(r["src_diff"][1]) <= (2 if reaches_end else 1))
        r["match"] = r["match"] and r.get("hold", {}).get("match", True)
        results.append(r)
    return tl, items, holds, results


HOLD_TRIES = 3   # 止めたクリップが期待のフレームを映さないときに置き直す回数の上限 (1 回目を含む)


def place_hold(mp, tl, m, p, h, hn, fps):
    """止めたクリップを 1 つ置いて Percentage 0 にし、位置・長さ・映す素材のフレームを読む。返す値は (項目, 結果)。
    h で終わる hn フレームの範囲を、ほかのクリップとまとめずに置き、止める前に再生ヘッドをクリップの直後に置く。
    止めたクリップは、止めるときに再生ヘッドがクリップの直後にあれば最後のフレーム、上か前にあれば頭のフレームを映す。
    AppendToTimeline の後に再生ヘッドが直後へ動かないことがあるので、SetCurrentTimecode で置いて GetCurrentTimecode で
    読み戻す (docs/scripting-api.md。読めなければ記録の playhead は None)。止めた後の GetSourceStartFrame が
    h と違えば、クリップ (つながった音声も) を消して置き直す (HOLD_TRIES 回まで)。fps はタイムラインのフレームレート (整数)。"""
    t0 = tl.GetStartFrame()
    after = p["hold_rec"] + p["hold_frames"]   # クリップの直後 (タイムラインの頭からのフレーム)
    want = {"start": p["hold_rec"], "frames": p["hold_frames"], "src": h, "playhead": after}
    tries = []
    hit, out = None, {"match": False, "want": want, "tries": tries}
    for n in range(1, HOLD_TRIES + 1):
        got = mp.AppendToTimeline([{"mediaPoolItem": m["item"], "startFrame": h + 1 - hn, "endFrame": h + 1,
                                    "recordFrame": t0 + p["hold_rec"]}]) or []
        hit = next((g for g in got if g.GetStart() - t0 == p["hold_rec"]), None)
        if hit is None:
            out["error"] = "AppendToTimeline の戻り値に止めたクリップが無い"
            tries.append({"try": n, "error": out["error"]})
            return None, out
        set_tc = bool(tl.SetCurrentTimecode(C.frame_to_timecode(t0 + after, fps)))
        tc = tl.GetCurrentTimecode()
        try:
            playhead = C.timecode_to_frame(tc, fps) - t0
        except (TypeError, ValueError, AttributeError):
            playhead = None   # 読めない。止める前の確認は諦め、止めた後の GetSourceStartFrame で見る
        ok = bool(hit.SetSpeed({"Percentage": 0.0, "RippleTimeline": False}))
        res = {"start": hit.GetStart() - t0, "frames": hit.GetEnd() - hit.GetStart(), "src": hit.GetSourceStartFrame(),
               "speed": (hit.GetSpeed() or {}).get("Percentage")}
        match = ok and (res["start"], res["frames"], res["src"]) == (p["hold_rec"], p["hold_frames"], h)
        # Resolve が止められない素材のフレームがある (置いた範囲をずらしても、そのフレームだけ飛ばして隣を映す。
        # 21.1.1 の実機、docs/scripting-api.md)。2 回続けて同じ隣のフレーム (±1) を映したら、置き直しても
        # 変わらないので、それで止めて記録の near に残す。再生ヘッドの不調 (範囲の頭のフレームを映す) と取り違えないよう、
        # 映したのが範囲の頭のフレームのとき・再生ヘッドが直後に無いと読めたとき・前回の止める操作が失敗したときは数えない
        head_src = h + 1 - hn
        near = (not match and ok and n >= 2 and res["src"] is not None and abs(res["src"] - h) == 1
                and res["src"] != head_src and playhead in (None, after)
                and (res["start"], res["frames"]) == (p["hold_rec"], p["hold_frames"])
                and tries[-1].get("src") == res["src"] and tries[-1].get("set_speed")
                and tries[-1].get("playhead") in (None, after))
        if near:
            match = True
            out["near"] = {"want": h, "got": res["src"]}
        tries.append({"try": n, "set_timecode": set_tc, "playhead": playhead, "set_speed": ok, "src": res["src"]})
        if playhead is None:
            tries[-1]["playhead_error"] = f"GetCurrentTimecode を読めない ({tc!r})"
        out.update({"set_speed": ok, "result": res, "playhead": playhead, "match": match})
        if match:
            out["matched_at"] = n
            break
        if n == HOLD_TRIES:
            out["error"] = f"{HOLD_TRIES} 回置いても期待のフレームを映さない"
        else:
            tries[-1]["deleted"] = bool(tl.DeleteClips([hit] + list(hit.GetLinkedItems() or []), False))
            if not tries[-1]["deleted"]:
                out["error"] = "合わなかった止めたクリップを消せない"
                break
    return hit, out


def add_fusion(items, sb, rows, media, font_file, holds=None):
    """行ごとに、書いてあるもの (マスク・周りを暗くする・ズーム・layout・callout・指すマーク・解説の欄・テロップ・肩テロップ) だけを Fusion で組む。何も無い行には合成を追加しない。
    返す値は (全部組めたか, 行ごとの結果, 警告と誤りの文の list)。素材の解像度が分からず座標を直せない行は組まずに失敗にする。"""
    vals, warns, errs = row_values(sb, rows, {k: v["size"] for k, v in media.items()}, font_file)
    ok, res = not errs, ({"errors": errs} if errs else {})
    auto = auto_values(rows, vals)
    if auto:
        res["auto"] = auto   # build_and_render が記録の auto に移す
    for it, p, V in zip(items, rows, vals):
        if it is None or V.get("error") or (C.needs_coords(V) and not media[p["source"]]["size"][0]):
            continue   # 値を作れない行と、座標を直せない行は組まない (errs に入っている)
        if not C.has_parts(V):
            continue
        r_ok, d = C.build_fusion(it, V)
        res[f"row{p['row']}"] = d
        ok = ok and r_ok
    # 止めたクリップの Fusion: 位置は止めた瞬間のまま、現れる動きは行の頭からの時刻のまま続ける (common.hold_values)
    for hit, p, V, row in zip(holds or [], rows, vals, sb["rows"]):
        if hit is None or V.get("error"):
            continue
        HV = C.hold_values(V, C.hold_time(row, media[p["source"]]["fps"], p["frames"], p["pct"] / 100), p["frames"])
        if C.needs_coords(HV) and not media[p["source"]]["size"][0]:
            continue
        if C.has_parts(HV):   # 止めたクリップに肩テロップは無い (hold_values)
            r_ok, d = C.build_fusion(hit, HV)
            res[f"row{p['row']}_hold"] = d
            ok = ok and r_ok
    return ok, res, warns + errs


def add_markers(items, sb):
    """行の頭にクリップのマーカー (名前はテロップの頭、メモは肩テロップ、customData は row<番号>)。"""
    mk = sb["marker"]
    out = {}
    for n, (it, r) in enumerate(zip(items, sb["rows"]), 1):
        if it is not None:
            name = (r.get("caption") or f"row {n}").replace("|", " ")[:40]
            out[n] = bool(it.AddMarker(0, mk["row_color"], name, r.get("badge") or "", 1, f"row{n}"))
    return out


def marker_warnings(done):
    """行の目印を付けられなかった行の警告。素材にマーカー (OBS のピンが入ったチャプター) があると、Resolve はその素材の
    クリップのマーカーを素材と共有し、頭のフレーム 0 に別のマーカーがあると付けられない (21.1.1 の実機)。
    read_markers.py は、そのときはこの記録の行の位置で行を決める"""
    bad = [n for n, ok in done.items() if not ok]
    if not bad:
        return []
    shown = "・".join(map(str, bad[:10])) + (" ほか" if len(bad) > 10 else "")
    return [f"{len(bad)} 行 ({shown} 行目) に行の目印のマーカーを付けられない。素材にマーカー (OBS のピン) があると"
            "付けられない。read_markers.py はこの記録の行の位置で行を決める"]


def render_stem(path):
    """CustomName。拡張子は書き出し形式の方が決めるので、パス側の拡張子は捨てる。"""
    return os.path.splitext(os.path.basename(path))[0]


def effective_render_path(path, fmt):
    """Resolve が TargetDir と CustomName から書くファイル。拡張子は render.format。"""
    path = os.path.abspath(path)
    return os.path.join(os.path.dirname(path), f"{render_stem(path)}.{fmt}")


def _without_extended_prefix(path):
    """Windows の拡張パス接頭辞 (\\\\?\\) を外す。UNC は \\\\server\\share に戻す。"""
    p = os.fspath(path)
    prefix = "\\\\?\\"
    if p.startswith(prefix):
        rest = p[len(prefix):]
        if rest[:4].upper() == "UNC\\":
            return "\\\\" + rest[4:]
        return rest
    return p


def same_output_file(a, b):
    """同じファイルか。相対と絶対、..、OS の大文字小文字、シンボリックリンクを揃える。

    比較の前に Windows の拡張パス接頭辞を外す。名前が違っても、両方あれば os.path.samefile でハードリンクを見る。
    """
    def key(p):
        p = _without_extended_prefix(p)
        p = _without_extended_prefix(os.path.realpath(p))
        return os.path.normcase(os.path.normpath(p))
    if key(a) == key(b):
        return True
    try:   # 別の名前のハードリンクも同じファイル (両方あるときだけ見られる)
        return os.path.samefile(a, b)
    except OSError:
        return False


def default_preview_path(cfg, final_output, storyboard, fmt, suffix="preview"):
    """仮組みの既定の書き出し先。完成版とは別の、設定の仮組みの書き出し先 (preview_dir) の下の「<完成版の名前>-<suffix>」。"""
    if final_output:
        stem = render_stem(final_output)
    else:
        stem = render_stem(storyboard or "storyboard")
    if not stem or stem in (".", ".."):
        stem = "storyboard"
    root = cfg.path("preview_dir")
    if not root:
        return None
    return os.path.join(root, f"{stem}-{suffix}.{fmt}")


def preview_output(cfg, sb, args, final_output, suffix="preview", label="仮組み"):
    """仮組み (部分の書き出しも) が書くファイルを決める。final_output は --output を当てる前の完成版。
    suffix は既定の名前の後ろ (default_preview_path)、label は拒否の文の呼び名。

    返す値は (書くファイル or None, 拒否するときの (要約, 行) or None)。
    拒否は Resolve に接続する前に行う。
    """
    fmt = str((sb.get("render") or {}).get("format") or "mp4")
    if getattr(args, "output", None):
        raw = os.path.abspath(args.output)
    else:
        raw = default_preview_path(cfg, final_output, getattr(args, "storyboard", None), fmt, suffix)
        if not raw:
            return None, (f"sb2resolve: {label}の書き出し先が決まらない (preview_dir が無い。"
                                "環境変数 RESOLVE_PREVIEW_DIR か config/local.json の paths.preview_dir で指定する)", [])
    target = effective_render_path(raw, fmt)
    if final_output:
        final_target = effective_render_path(final_output, fmt)
        if same_output_file(target, final_target):
            lines = [f"{label}: {target}", f"完成版: {final_target}"]
            alt = default_preview_path(cfg, final_output, getattr(args, "storyboard", None), fmt, suffix)
            if alt:
                alt_target = effective_render_path(alt, fmt)
                if not same_output_file(alt_target, final_target):
                    lines.append(f"別の場所の例: {alt_target}")
            return target, (f"sb2resolve: {label}の書き出し先が完成版と同じファイルになるので書き出さない", lines)
    return target, existing_problem(target, label, getattr(args, "overwrite", False))


def existing_problem(target, label, overwrite):
    """書き出し先 target が既にあり、--overwrite が無ければ拒否の (要約, 行)。無ければ None。接続する前に呼ぶ。"""
    if overwrite or not os.path.lexists(target):
        return None
    return (f"sb2resolve: {label}の書き出し先が既にあるので上書きしない",
            [target, "別名の --output を指定するか、置き換えるなら --overwrite を付ける"])


def final_target(sb, args):
    """完成版 (--render) が書くファイルを決める。返す値は preview_output と同じ (書くファイル, 拒否の (要約, 行) or None)。"""
    fmt = str((sb.get("render") or {}).get("format") or "mp4")
    target = effective_render_path(sb["output"], fmt)
    return target, existing_problem(target, "完成版", getattr(args, "overwrite", False))


def publish(src, dest, overwrite=False):
    """一時ファイル src を dest として置く。dest が既にあれば FileExistsError で、どちらも消さない。

    先に os.link を試す。宛先があると Linux でも Windows でも FileExistsError になり、置き換えない。
    同じファイルシステムが要るので、呼び出し側は dest の親に一時ディレクトリを作る。
    link できないとき (FileExistsError 以外の OSError) だけ、宛先が無ければ os.rename する。
    その確認の後に宛先ができると、POSIX の os.rename は宛先を置き換える。ここに競合がある。
    Windows の os.rename は宛先があると失敗する。
    overwrite なら os.replace で置き換える (宛先の名前を差し替える。宛先のファイルの中身には書かない)。
    """
    if overwrite:
        os.replace(src, dest)
        return dest
    try:
        os.link(src, dest)
    except FileExistsError:
        raise
    except OSError:
        if os.path.lexists(dest):
            raise FileExistsError(dest)
        os.rename(src, dest)
        return dest
    try:
        os.remove(src)
    except OSError:
        pass
    return dest


def render_via_temp(proj, sb, dest, limit, label="仮組み", overwrite=False, marks=None):
    """dest の親の一時ディレクトリへ書き、dest が無いとき (overwrite なら有っても) だけそこへ置く。完成版も仮組みもこれで書く。

    返す dict は render() と同じで、dest (本来の宛先) を追加する。置けたときは path が dest。書き出しが失敗したとき、
    または dest が既にあって置けないときは、done は false で path は一時ファイル。何も書けなかったときは
    空の一時ディレクトリを消し、path は None。既存の dest は消さない。一時ディレクトリは置けたときも消す (消せなくてもよい)。
    上限で待つのをやめても Resolve がまだ書き出している (IsRenderingInProgress が真) ときは、何も片付けずに失敗にし、
    still_rendering と temp_dir を記録に残す (後から書かれるファイルを消さない)。
    """
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="sb2render-", dir=parent)
    rr = render(proj, sb, temp_dir, render_stem(dest), limit, marks)
    rr["dest"] = dest
    if not rr.get("done") and rr.get("busy"):   # render_jobs が最後に見た IsRenderingInProgress
        rr["still_rendering"], rr["temp_dir"] = True, temp_dir
        return rr
    if not rr.get("done"):
        if not (rr.get("path") and os.path.lexists(rr["path"])):
            rr["path"] = None   # 何も書けていない。空の一時ディレクトリを残さない
            try:
                os.rmdir(temp_dir)
            except OSError:
                pass
        return rr
    try:
        publish(rr["path"], dest, overwrite)
    except FileExistsError:
        rr["done"] = False
        rr["status"] = (f"{label}の書き出し先が既にあるので置かない "
                        f"(既存のファイルは消していない。これは{label}の書き出しの成功ではない)")
        return rr
    except OSError as e:
        rr["done"] = False
        rr["status"] = f"{label}を書き出し先へ置けない ({e})。これは{label}の書き出しの成功ではない"
        return rr
    rr["path"] = dest
    try:
        os.rmdir(temp_dir)
    except OSError:
        pass
    return rr


def render(proj, sb, target_dir, name, limit, marks=None):
    """今のタイムラインを target_dir/name.<形式> に書き出して、終わるまで待つ。
    marks (MarkIn, MarkOut。タイムラインの絶対フレーム、両端を含む) を渡すと、SelectAllFrames を偽にしてその範囲だけ書く。

    21.1.1 では、同じパスにファイルがあると確認なしで消してから書く。空のファイルを作って閉じても守られない。
    呼ぶのは render_via_temp だけで、まだファイルの無い一時ディレクトリを渡す。
    """
    rd = sb["render"]
    os.makedirs(target_dir, exist_ok=True)
    # name は既に拡張子を除いてある。ここで effective_render_path を使うと、名前の中の点まで落とす
    path = os.path.join(target_dir, f"{name}.{rd['format']}")
    # 設定が入らないまま書き出すと、前の設定のまま別の場所や形式に書かれるので、そこで止める
    if not proj.SetCurrentRenderFormatAndCodec(rd["format"], rd["codec"]):
        return {"done": False, "status": f"形式 {rd['format']} / {rd['codec']} を設定できない", "path": path}
    settings = {"SelectAllFrames": marks is None, "TargetDir": target_dir, "CustomName": name}
    if marks is not None:
        settings |= {"MarkIn": marks[0], "MarkOut": marks[1]}
    if not proj.SetRenderSettings(settings):
        return {"done": False, "status": "書き出しの設定 (保存先と名前) を入れられない", "path": path}
    job = proj.AddRenderJob()
    if not job:
        return {"done": False, "status": "書き出しの作業を追加できない", "path": path}
    r = C.render_jobs(proj, [job], interval=2, limit=limit)
    r["path"] = path
    return r


# ---------- 入口 ----------
def run(args, log):
    """組む。log に記録を追加し、(成否, 要約, 画面に出す行) を返す。"""
    cfg = C.config()
    sb = cfg.load_storyboard(args.storyboard, args.source)
    # --output を当てる前に残す。当てた後のパスと比べると、仮組みの --output が完成版と同じかが分からない
    final_output = sb.get("output")
    if getattr(args, "output", None):
        sb["output"] = os.path.abspath(args.output)   # 構成表の output より優先 (相対パスは今のフォルダーから)
    log["output"] = sb.get("output")
    if getattr(args, "render", False) and getattr(args, "rows", None) is not None:
        # --rows は部分だけを仮組みの書き出し先に書き、完成版は書かない。--render を黙って無視しないよう、接続する前に止める
        return False, "sb2resolve: --render と --rows は一緒に使えない (Resolve には触っていない)", [
            "完成版は --render だけ、部分の書き出しは --rows だけで流す"]
    sel = None
    if getattr(args, "rows", None) is not None:   # 空の指定も parse_rows で止める (全編の書き出しにしない)
        # 部分の書き出しの行は、構成表の行の数だけで決まるので、ここで確認する (Resolve は MarkIn > MarkOut でも止めない)
        sel, err = parse_rows(args.rows, len(sb.get("rows") or []))
        if err:
            return False, f"sb2resolve: {err} (Resolve には触っていない)", []
        log["range"] = {"rows": list(sel)}
    args.rows_sel = sel
    wants_render = getattr(args, "render", False) or getattr(args, "render_anyway", False) or bool(sel)
    dry = getattr(args, "dry_run", False)
    anyway = getattr(args, "render_anyway", False)
    # 仮組みと部分の書き出しの出力先が悪いときは、ここで止める (この後の connect_resolve より前)。--dry-run は書き出さないので対象外
    if wants_render and (anyway or sel) and not dry:
        if sel:
            target, err = preview_output(cfg, sb, args, final_output, f"{rows_label(sel)}-preview", "部分の書き出し")
        else:
            target, err = preview_output(cfg, sb, args, final_output)
        if target:
            log["output"] = target
        if err:
            return False, err[0], err[1]
        # プロジェクト名は完成版 (または明示した --output) のまま。書くファイルだけ仮組み先にする
        args.preview_target = target
    elif wants_render and not sb.get("output") and not ((anyway or sel) and dry):
        # --render-anyway・--rows の --dry-run はファイルを書かないので、構成表の output が無くても止めない
        return False, "sb2resolve: 書き出し先が無い (構成表の output か --output)", []
    elif wants_render and not dry:
        # 完成版も、書き出し先が既にあればここで止める (--overwrite が無いとき)
        target, err = final_target(sb, args)
        if err:
            return False, err[0], err[1]
        args.final_target = target
    rows = plan(sb)
    log["rows"] = rows
    if sel:
        a, b = rows_frames(rows, sel)
        log["range"] |= {"frames": [a, b], "count": b - a + 1}
    info = probe_sources(rows)
    lengths = {k: v["duration"] for k, v in info.items()}
    log["source_lengths"] = lengths
    bad = problems(sb, rows, lengths)
    bad += auto_dst_problems(sb, rows, {k: (v["width"], v["height"]) for k, v in info.items()}, C.caption_font(sb))
    log["problems"] = bad
    if bad:
        return False, f"sb2resolve: 構成表に誤り {len(bad)} 件 (Resolve には触っていない)", bad
    if args.dry_run:
        # Resolve に触らずに、組むときと同じ値を作って警告と誤りを見る (素材の解像度は ffprobe)
        vals, warns, errs = row_values(sb, rows, {k: (v["width"], v["height"]) for k, v in info.items()},
                                       C.caption_font(sb))
        log["warnings"], log["errors"] = warns, errs
        log["auto"] = auto_values(rows, vals)   # 枠線の色 (auto) と callout の自動の置き場所。Resolve に組むときと同じ値
        total = total_frames(rows)
        part = (f"、部分の書き出しは {sel[0]}〜{sel[1]} 行目のフレーム {log['range']['frames'][0]}〜{log['range']['frames'][1]}"
                if sel else "")
        return not errs, (f"sb2resolve --dry-run: 行 {len(rows)}、{total} フレーム{part}、誤り {len(errs)}、警告 {len(warns)}"
                          " (Resolve には触っていない)"), errs + ["警告 " + w for w in warns]
    resolve = C.connect_resolve(quiet=True)
    if not resolve:
        return False, f"sb2resolve: {C.NO_RESOLVE}", [C.RESOLVE_HINT]
    pm = resolve.GetProjectManager()
    cur = pm.GetCurrentProject()
    log["previous_project"] = cur.GetName() if cur else None
    W, H, fps = sb["timeline"]["width"], sb["timeline"]["height"], sb["fps"]
    wanted_tl = getattr(args, "timeline", None)
    if getattr(args, "project", None):
        name = args.project
        log["mode"], log["project"] = "existing", name
        if name not in (pm.GetProjectListInCurrentFolder() or []):
            return False, f"sb2resolve: プロジェクト {name} が無い (プロジェクトマネージャーの今のフォルダー)", []
        proj = cur if cur and cur.GetName() == name else pm.LoadProject(name)
        if not proj:
            return False, f"sb2resolve: プロジェクト {name} を開けない", []
        log["settings"] = existing_settings(proj, W, H, fps)
        if log["settings"]["errors"]:
            return (False, f"sb2resolve: プロジェクト {name} の設定が構成表と違う (何も追加していない)",
                    log["settings"]["errors"])
        tname = timeline_name(proj, wanted_tl or base_name(sb, args.storyboard), wanted_tl)
    else:
        name = project_name(pm, sb, args.name)
        if not name:
            return False, f"sb2resolve: プロジェクト {args.name} は既にある (上書きしない)", []
        proj = pm.CreateProject(name)
        log["mode"], log["project"] = "new", name
        if not proj:
            return False, f"sb2resolve: プロジェクト {name} を作れない", []
        log["settings"] = project_settings(proj, W, H, fps)
        tname = wanted_tl or name
    if not tname:
        return False, f"sb2resolve: タイムライン {wanted_tl} は既にある (プロジェクト {name}。上書きしない)", []
    log["timeline_name"] = tname
    mp = proj.GetMediaPool()
    # 取り込みとタイムラインの作成は、メディアプールの今のフォルダーに入る。今のフォルダーはプロジェクトに保存され、
    # 開き直しても残る (21.1.1 の実機) ので、いちばん上のフォルダーに入れ、終わったら元に戻す。
    # 同じ素材を同じフォルダーに取り込むと既存の項目が返る (重複しない。21.1.1 の実機)
    folder = mp.GetCurrentFolder()
    mp.SetCurrentFolder(mp.GetRootFolder())
    try:
        return build_and_render(args, log, pm, proj, mp, sb, rows, name, tname)
    finally:
        if folder:
            mp.SetCurrentFolder(folder)
        pm.SaveProject()


def build_and_render(args, log, pm, proj, mp, sb, rows, name, tname):
    """取り込み・タイムライン・Fusion・マーカー・書き出し。返す値は run と同じ (成否, 要約, 画面に出す行)。"""
    fps = sb["fps"]
    where = f"プロジェクト {name}、タイムライン {tname}"
    media = import_media(mp, rows, fps)
    log["media"] = {k: {x: v[x] for x in ("fps", "size", "frames")} | {"imported": v["item"] is not None}
                    for k, v in media.items()}
    missing = [k for k, v in media.items() if v["item"] is None]
    if missing:
        return False, f"sb2resolve: 素材を取り込めない {len(missing)} 件 ({where})", missing
    t = time.time()
    tl, items, holds, results = build_timeline(proj, mp, sb, rows, media, tname)
    log["timeline"] = {"created": tl is not None, "seconds": round(time.time() - t, 1), "rows": results}
    near_warns = [f"{r['row']} 行目: 止めたクリップは素材のフレーム {r['hold']['near']['want']} を映せず、"
                  f"隣の {r['hold']['near']['got']} で止めた (Resolve が止められないフレーム)"
                  for r in results if (r.get("hold") or {}).get("near")]
    if tl is None:
        return False, f"sb2resolve: タイムラインを作れない ({where})", []
    t = time.time()
    fu_ok, fu, warns = add_fusion(items, sb, rows, media, C.caption_font(sb), holds)
    log["auto"] = fu.pop("auto", [])
    log["fusion"] = {"ok": fu_ok, "seconds": round(time.time() - t, 1), "rows": fu}
    log["markers"] = add_markers(items, sb)
    warns = log["settings"]["warnings"] + near_warns + marker_warnings(log["markers"]) + warns
    log["warnings"] = warns
    pm.SaveProject()
    lines = [f"NG {r['row']} 行目: " + (r.get("error") or f"位置・素材の範囲・止めたクリップが合わない {r.get('result')} "
                                        f"{r.get('hold', {}).get('result', '')}")
             for r in results if not r.get("match")]
    if not fu_ok:
        lines.append("NG Fusion の合成を追加できない行がある (記録の fusion)")
    total = total_frames(rows)   # 書き出しの全フレーム数 (部分の書き出しの待ち時間は範囲のフレーム数で決める)
    anyway = getattr(args, "render_anyway", False)
    sel = getattr(args, "rows_sel", None)
    want = getattr(args, "render", False) or bool(sel)
    label = "部分の書き出し" if sel else "仮組み" if anyway else "完成版"
    if anyway or (want and not lines):
        # 完成版も仮組みも部分の書き出しも、宛先の親の一時ディレクトリへ書いてから置く (render_via_temp)。
        # 宛先は接続する前に run が決めた値 (--render は preview_target を見ない)
        if anyway or sel:
            out = getattr(args, "preview_target", None)
            if not out:   # run を通らずに呼ぶと決まっていない。完成版のパスに置かないよう止める
                return False, f"sb2resolve: {label}の書き出し先が決まっていない ({where})", lines
        else:
            out = getattr(args, "final_target", None) or final_target(sb, args)[0]
        marks = None
        if sel:
            # MarkIn・MarkOut はタイムラインの絶対フレーム (GetStartFrame を足す。両端を含む。docs/scripting-api.md)
            t0 = tl.GetStartFrame()
            a, b = log["range"]["frames"]
            marks = (t0 + a, t0 + b)
            log["range"] |= {"mark_in": marks[0], "mark_out": marks[1]}
        limit = max(600, (log["range"]["count"] if sel else total) / float(fps) * 20)
        rr = render_via_temp(proj, sb, out, limit, label, getattr(args, "overwrite", False), marks)
        rr["ignored_ng"] = list(lines)   # --render-anyway で NG のまま書き出したときの NG (無ければ空)
        rr["mode"] = "rows" if sel else "preview" if anyway else "final"
        log["render"] = rr
        if (anyway or sel) and rr.get("path"):
            log["output"] = rr["path"]   # 置けたときは宛先。失敗したときは残した一時ファイル
        if not rr["done"]:
            msg = f"NG 書き出しが終わらない・失敗 ({rr['status']}) 宛先 {rr['dest']}"
            if rr.get("path"):
                msg += f" 一時ファイル {rr['path']}"
            if rr.get("still_rendering"):
                msg += f" (Resolve はまだ書き出し中。一時フォルダー {rr['temp_dir']} を残した)"
            lines.append(msg)
    elif want:
        lines.append("NG の行があるので書き出していない (NG を無視して書き出すなら --render-anyway)")
    rr = log.get("render") or {}
    ignored = len(rr.get("ignored_ng") or [])
    # --render (anyway でない) の文は変えない。仮組みと部分の書き出しは、完成版の成功と読み違えないようにする
    kind = (f"部分の書き出し ({sel[0]}〜{sel[1]} 行目、{log['range']['count']} フレーム) " if sel
            else "仮組み " if anyway else "")
    left = not rr.get("done") and rr.get("path")   # 書き出したが宛先へ置けなかった一時ファイル
    summary = (f"sb2resolve: {kind}行 {len(rows)}、{total} フレーム、{where}"
               + (f"、書き出し {rr['path']}" if rr.get("done") else "")
               + (f"、一時ファイル {rr['path']} ({label}の書き出しの成功ではない)" if left else "")
               + (f" (NG {ignored} 件を無視して書き出した)" if rr.get("done") and ignored else "")
               + (" (編集中の確認用。完成版ではない)" if sel and rr.get("done")
                  else " (完成版ではない)" if anyway and rr.get("done") else ""))
    return not lines, summary, lines + ["警告 " + w for w in warns]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("storyboard")
    ap.add_argument("--source", help="素材 (構成表の source より優先)")
    where = ap.add_mutually_exclusive_group()
    where.add_argument("--name", help="新しく作るプロジェクトの名前 (既定は構成表の project か output の名前)")
    where.add_argument("--project", help="組み込む既存のプロジェクトの名前 (新しいタイムラインとして組む。設定は変えない)")
    ap.add_argument("--timeline", help="タイムラインの名前 (既にあれば止める)")
    ap.add_argument("--output", help="書き出し先 (構成表の output より優先。相対パスは今のフォルダーから)")
    ap.add_argument("--render", action="store_true",
                    help="組んだ後に完成版を書き出す (NG の行があれば書き出さない)")
    ap.add_argument("--render-anyway", action="store_true",
                    help="NG の行があっても、完成版とは別のファイルに仮組みとして書き出す (--render を兼ねる)")
    ap.add_argument("--rows", help="組んだ後、構成表のこの行だけを仮組みの書き出し先に書き出す (例 3-5、1 行なら 4。--render とは一緒に使えない)")
    ap.add_argument("--overwrite", action="store_true",
                    help="書き出し先が既にあっても置き換える (付けなければ Resolve に接続する前に止める)")
    ap.add_argument("--dry-run", action="store_true", help="Resolve に触らずに、点検と組む値の計算 (警告) だけをする")
    a = ap.parse_args()
    cfg = C.config()
    path = os.path.join(cfg.out_dir("sb2resolve"), os.path.splitext(os.path.basename(a.storyboard))[0] + ".json")
    log = {"storyboard": os.path.abspath(a.storyboard), "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        ok, summary, lines = run(a, log)
    except (Exception, SystemExit) as e:   # 途中の例外も 1 行と記録の JSON にする (全文は JSON の error)
        log["error"] = traceback.format_exc()
        last = (str(e) or type(e).__name__).strip().splitlines()
        ok, summary, lines = False, "sb2resolve: 途中で止まった", [last[-1] if last else type(e).__name__]
    log["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    log["ok"] = ok
    log["summary"] = summary   # 画面の 1 行目と同じ。接続する前の拒否 (仮組みの書き出し先など) も理由がここに残る
    log["lines"] = lines   # 画面には report.max_lines 行までしか出さないので、全部をここに残す
    C.write_json(path, log)
    return C.report(ok, summary, path, lines)


if __name__ == "__main__":
    sys.exit(main())
