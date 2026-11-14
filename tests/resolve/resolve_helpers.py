"""Resolve が要るテストの共通部品。

テストは scripts/resolve_lock.py run で包んで動かす (Resolve を使う順番のロックを取ってから流す)。包まれていなければ接続する前に終わる。

各テストは新しいプロジェクト test-<テストの名前>-<時刻> を作って、その中だけで組む (今開いているプロジェクトには触らない)。
人が後で GUI で確認するテスト (native_*.py) は、決まった名前のプロジェクトを fresh_project で作り直し、消さずに残す。
素材は機密の無いテスト用の録画 (--source か環境変数 RESOLVE_TRIAL_SOURCE。無ければ scripts/make_test_source.py で作る)。
結果は 出力先 (config の out_dir) の tests/<テストの名前>.json と、短い表。期待と違う項目があれば終了コード 1。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))
import common as C  # noqa: E402
import resolve_lock as RL  # noqa: E402


# resolve_lock.py run で包んで動かしているか (ロックの持ち主が、run が渡した RESOLVE_LOCK_OWNER か) を確認する。
# 包まれていなければ警告を出す (config の resolve.require_lock が true なら、Resolve に接続する前に終わる)。
# 判定は common.connect_resolve と同じ
require_lock = RL.require_lock


def make_check(checks):
    """項目を checks に追加する check(label, ok, actual=None, expected=None) を返す。check の返す値は ok。
    ok に None を渡すと確認できなかった項目 (status が unknown。合格にはしない)。項目の status は pass・fail・unknown。"""
    def check(label, ok, actual=None, expected=None):
        status = "unknown" if ok is None else ("pass" if ok else "fail")
        checks.append({"label": label, "ok": status == "pass", "status": status, "actual": actual, "expected": expected})
        return ok
    return check


def run_storyboard(path, name, created, log, render=True):
    """構成表 path を sb2resolve.run で新しいプロジェクト name に組む (render が真なら書き出しも)。返す値は run の (ok, 要約, 行)。
    例外で止まっても、作られたプロジェクト (log の project) を created に入れる (finish_projects が消す)。"""
    import sb2resolve as S
    args = argparse.Namespace(storyboard=path, source=None, name=name, render=render, dry_run=False,
                              overwrite=True)   # テストの出力は使い捨てで、毎回同じ場所に書く
    try:
        return S.run(args, log)
    finally:
        created.append(log.get("project"))


def cleanup_projects(pm, names, prev):
    """names のプロジェクト (テストが作ったもの。None は飛ばす) を閉じて消し、始めに開いていたプロジェクト prev を開き直す。
    返す値は {名前: 消せたか, "reopened <prev>": 開き直せたか}。開き直しは LoadProject の戻り値とその名前で確認する。"""
    done = {}
    for name in dict.fromkeys(n for n in names if n):
        cur = pm.GetCurrentProject()
        if cur and cur.GetName() == name:
            pm.CloseProject(cur)
        done[name] = bool(pm.DeleteProject(name))
    if prev:
        p = pm.LoadProject(prev)
        done["reopened " + prev] = bool(p) and p.GetName() == prev
    return done



def finish_projects(resolve, name, path, created, prev, info, checks, min_checks=0):
    """テストの終わり: 作ったプロジェクトを消して始めのプロジェクトを開き直し (cleanup_projects)、その成否を checks の項目に追加し、
    結果 {name, info, checks} を path に書き、画面の 1 行と期待と違う項目を出す。返す値は終了コード (sys.exit に渡す)。
    checks の項目が min_checks 件より少なければ (途中で止まった) 合格にしない。"""
    done = cleanup_projects(resolve.GetProjectManager(), created, prev)
    info["cleanup"] = done
    label = ("作ったプロジェクトを消し、始めに開いていたプロジェクトを開き直せた" if any(created)
             else "始めに開いていたプロジェクトを開き直せた")
    checks.append({"label": label, "ok": all(done.values()), "actual": done, "expected": None})
    C.write_json(path, {"name": name, "info": info, "checks": checks})
    bad = [c for c in checks if not c["ok"]]
    lines = [f"{'unknown' if c.get('status') == 'unknown' else 'NG'} {c['label']}  (結果 {c['actual']!r}、期待 {c['expected']!r})"
             for c in bad]
    if len(checks) < min_checks:
        lines.append(f"項目が {len(checks)} 件で、全部を流したときの {min_checks} 件より少ない")
    return C.report(not bad and len(checks) >= min_checks, f"{name}: {len(checks) - len(bad)}/{len(checks)} が期待どおり",
                    path, lines)

def render_job(proj, settings, fmt, codec, limit, interval=0.5):
    """今のタイムラインを、形式 fmt / codec と SetRenderSettings の settings (TargetDir・CustomName を含む) で書き出す。
    返す値は記録の dict: 各呼び出しの戻り値 (set_format・set_settings・job)、GetRenderJobList で読み戻したこの作業 (job_entry)、
    render_jobs の結果 (started・done・status・seconds)、書いたファイル (path・written・bytes)。
    設定が入らない・作業を追加できないときは書き出さない (done は false)。"""
    out = {"settings": dict(settings), "format": fmt, "codec": codec}
    out["set_format"] = bool(proj.SetCurrentRenderFormatAndCodec(fmt, codec))
    out["set_settings"] = bool(proj.SetRenderSettings(settings))
    out["path"] = os.path.join(settings["TargetDir"], f"{settings['CustomName']}.{fmt}")
    out["done"] = False
    if not (out["set_format"] and out["set_settings"]):
        return out
    job = proj.AddRenderJob()
    out["job"] = job
    if not job:
        return out
    out["job_entry"] = next((j for j in (proj.GetRenderJobList() or []) if j.get("JobId") == job), None)
    entry = out["job_entry"] or {}
    if entry.get("TargetDir") and entry.get("OutputFilename"):
        out["path"] = os.path.join(entry["TargetDir"], entry["OutputFilename"])   # Resolve が書くと言っているファイル
    t0 = time.time()
    r = C.render_jobs(proj, [job], interval=interval, limit=limit)
    out.update(started=r["started"], done=r["done"], status=r["status"].get(job), seconds=r["seconds"])
    # 今回の書き出しの後に書かれたものだけを、書けたとみなす (時刻の丸めに 2 秒の幅)
    out["written"] = os.path.isfile(out["path"]) and os.path.getmtime(out["path"]) >= t0 - 2
    out["bytes"] = os.path.getsize(out["path"]) if os.path.isfile(out["path"]) else None
    return out


def frame_md5(video):
    """動画の映像の各フレームを FFmpeg でデコードした画素の MD5 の list (先頭から表示の順。FFmpeg の framemd5)。
    同じ形式で書き出した 2 本の画素が同じかを、フレームごとに比べるのに使う。"""
    r = C.run_tool("ffmpeg", ["-i", video, "-map", "0:v:0", "-f", "framemd5", "-"])
    return [ln.rsplit(",", 1)[1].strip() for ln in r.stdout.splitlines() if ln.strip() and not ln.startswith("#")]


def video_stream(video):
    """動画の映像の符号化の形式・画素の形式・大きさ・フレームレート (ffprobe)。"""
    r = C.run_tool("ffprobe", ["-select_streams", "v:0", "-show_entries",
                               "stream=codec_name,pix_fmt,width,height,r_frame_rate", "-of", "json", video])
    return (json.loads(r.stdout).get("streams") or [{}])[0]


# 可逆の形式の選び方 (render_range.py と native_*.py が使う)。
# 比べるには、フレームごとに独立して符号化され、同じ入力なら同じ値になる形式が要る (H.264 は区間の頭がキーフレームになるので比べられない)。
# 形式と codec は GetRenderFormats の拡張子と GetRenderCodecs の識別子 (21.1.1 の実機の一覧)。先頭の mov / RGB8 が実機で通ったもの。
# mov の ARGB8 は FFmpeg 9.0.2 が読めない (codec_tag 0x20000000) ので入れない
LOSSLESS = [("mov", "RGB8"), ("mov", "RGB10"), ("mov", "YUV422_8"), ("avi", "RGB10")]


def lossless_candidates(formats, codecs_of):
    """LOSSLESS の (形式, codec) のうち、GetRenderFormats (formats は {名前: 拡張子}) と GetRenderCodecs (codecs_of(形式) は
    {説明: codec}) の返す識別子に有るものを、LOSSLESS の順に返す。
    返す値は (候補 [{format, codec, description}], 調べた形式の {形式: {説明: codec}})。"""
    have = set((formats or {}).values())
    seen, cands = {}, []
    for fmt, codec in LOSSLESS:
        if fmt not in have:
            continue
        if fmt not in seen:
            seen[fmt] = codecs_of(fmt) or {}
        desc = next((d for d, c in seen[fmt].items() if c == codec), None)
        if desc is not None:
            cands.append({"format": fmt, "codec": codec, "description": desc})
    return cands, seen


def stop_rendering(proj, limit=10):
    """書き出しを止め、止まり終わるまで (最大 limit 秒) 待つ。StopRendering は待たずに戻り、
    戻った直後は IsRenderingInProgress がまだ真 (21.1.1 の実機で 0.6 秒以内に偽になった)。"""
    proj.StopRendering()
    end = time.time() + limit
    while proj.IsRenderingInProgress() and time.time() < end:
        time.sleep(0.2)


def render_lossless(proj, target_dir, custom_name, limit, max_tries=4):
    """今のタイムラインの全編を可逆の形式で書き出す。lossless_candidates の順に、SetCurrentRenderFormatAndCodec が真を返した
    候補で書き出し、FFmpeg が読めた (frame_md5 が取れた) 最初のものを使う (最大 max_tries 個。読めなかった書き出しは消す)。
    時間切れで書き出しが続いていたら、次の候補は試さない (render_set の 2 本目からも同じ)。
    (Uncompressed ARGB 8-bit の mov は FFmpeg 9.0.2 に読めない。codec_tag 0x20000000。2026-10-19 の実機)
    返す値は {formats: 調べた形式と codec の全部, candidates, tried: 試したもの, chosen: 選んだ {format, codec, description} か None,
    render: 選んだ書き出しの render_job の記録 (stream と frames を追加したもの) か None, md5: フレームごとの MD5 の list}。"""
    cands, seen = lossless_candidates(proj.GetRenderFormats(), proj.GetRenderCodecs)
    out = {"formats": seen, "candidates": cands, "tried": [], "chosen": None, "render": None, "md5": []}
    for c in cands:
        if len(out["tried"]) >= max_tries:
            break
        if not proj.SetCurrentRenderFormatAndCodec(c["format"], c["codec"]):
            continue
        rr = render_job(proj, {"SelectAllFrames": True, "TargetDir": target_dir, "CustomName": custom_name},
                        c["format"], c["codec"], limit)
        attempt = dict(c, status=rr.get("status"))
        out["tried"].append(attempt)
        if proj.IsRenderingInProgress():   # 時間切れで書き出しが続いている。止めて、次の候補は試さない
            attempt["still_rendering"] = True
            stop_rendering(proj)
            break
        if not (rr["done"] and rr["written"]):
            continue
        try:
            md5 = frame_md5(rr["path"])
        except RuntimeError as e:
            attempt["ffmpeg"] = str(e).splitlines()[-1][:300]
            os.remove(rr["path"])
            continue
        rr["stream"] = video_stream(rr["path"])
        rr["frames"] = len(md5)
        out.update(chosen=c, render=rr, md5=md5)
        break
    return out


def render_set(proj, timelines, base, others, target_dir, prefix, limit=600, chosen=None):
    """timelines[base] の全編を render_lossless で書き出して可逆の形式を選び、同じ形式で others の各タイムラインの全編を
    書き出す (名前は <prefix>-<キー>)。前の呼び出しで選んだ形式を chosen に渡すと、base は書き出さずに others だけを書き出す。
    返す値は {chosen: 選んだ形式か None, renders: {キー: render_job の記録}, md5: {キー: framemd5 の list (書けたものだけ)},
    info: 記録に入れる {formats, lossless, renders}}。"""
    if chosen:
        lz = {"formats": None, "candidates": None, "tried": None, "chosen": chosen}
        renders, md5 = {}, {}
    else:
        proj.SetCurrentTimeline(timelines[base])
        lz = render_lossless(proj, target_dir, f"{prefix}-{base}", limit)
        renders, md5 = {base: lz["render"]}, {base: lz["md5"]}
    if lz["chosen"]:
        for key in others:
            proj.SetCurrentTimeline(timelines[key])
            rr = render_job(proj, {"SelectAllFrames": True, "TargetDir": target_dir, "CustomName": f"{prefix}-{key}"},
                            lz["chosen"]["format"], lz["chosen"]["codec"], limit)
            renders[key] = rr
            if proj.IsRenderingInProgress():   # 時間切れで書き出しが続いている。止めて、次のタイムラインに進まない
                rr["still_rendering"] = True   # (止めておけば、呼ぶ側がタイムラインを替えても片付けても重ならない)
                stop_rendering(proj)
                break
            if rr["done"] and rr["written"]:
                md5[key] = frame_md5(rr["path"])
    info = {"formats": lz["formats"], "lossless": {k: lz[k] for k in ("candidates", "tried", "chosen")},
            "renders": {k: {x: v.get(x) for x in ("path", "done", "written", "seconds", "bytes", "status")}
                        for k, v in renders.items() if v}}
    return {"chosen": lz["chosen"], "renders": renders, "md5": md5, "info": info}


def make_source(cfg, W, H, fps, patch=False, seconds=10):
    """機密の無い合成の素材 (FFmpeg の testsrc2、W x H、fps、seconds 秒。絵が 1 フレームごとに変わる) を 作業フォルダー/test-media に作る。
    あれば作り直さない。patch が真なら、右上の 4 分の 1 (x W/2〜W、y 0〜H/2) を動かない灰色 (RGB 96) で塗る
    (重ねたものの色と位置を画素で測る所。native_*.py が使う)。
    色は BT.709 (RGB から YUV への変換も BT.709 の行列で、limited range。色の情報 colorspace・primaries・trc も bt709 を付ける)。
    ファイル名の -bt709 はこれを示す。"""
    base = f"testsrc2-{W}x{H}-{fps}fps-{seconds}s" + ("-patch" if patch else "")
    media = cfg.work_dir("test-media")
    path = os.path.join(media, base + "-bt709.mp4")
    if not os.path.isfile(path):
        box = f"drawbox=x={W // 2}:y=0:w={W - W // 2}:h={H // 2}:color=0x606060:t=fill," if patch else ""
        C.run_tool("ffmpeg", ["-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={fps}:d={seconds}",
                              "-vf", box + C.BT709_VF,
                              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", *C.BT709_TAGS, path])
    return path


def fresh_project(pm, name, W, H, fps):
    """決まった名前 name のプロジェクトを作り直す (今開いていれば閉じ、あれば消してから作る)。人が後で GUI で確認するために
    残すテストが使う。タイムラインの大きさとフレームレートを W x H と fps にする。返す値は (Project か None, 記録)。"""
    rec = {}
    cur = pm.GetCurrentProject()
    if cur and cur.GetName() == name:
        rec["closed"] = bool(pm.CloseProject(cur))
    rec["deleted_old"] = bool(pm.DeleteProject(name))   # 無ければ偽
    proj = pm.CreateProject(name)
    rec["created"] = bool(proj)
    if proj:
        rec["settings"] = bool(proj.SetSettings({"timelineFrameRate": str(fps), "timelineResolutionWidth": str(W),
                                                 "timelineResolutionHeight": str(H)}))
    return proj, rec


def append_at(mp, tl, clip, at, frames, track=1):
    """素材 clip の頭から frames フレームを、映像だけでトラック track の at フレーム目 (タイムラインの頭を 0) に置く。
    トラックが足りなければ先に AddTrack する (無い番号を渡すとトラック 1 に置かれるため。docs/scripting-api.md)。
    返す値は (TimelineItem か None, 読み戻し {start, duration, track})。トラックを追加できなければ置かずに (None, None)。"""
    while tl.GetTrackCount("video") < track:
        if not tl.AddTrack("video"):
            return None, None   # 無い番号のまま置くとトラック 1 に置かれ、別のトラックを書き換えうる
    got = mp.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": 0, "endFrame": frames, "mediaType": 1,
                                "trackIndex": track, "recordFrame": tl.GetStartFrame() + at}]) or []
    item = got[0] if got else None
    rec = {"start": item.GetStart() - tl.GetStartFrame(), "duration": item.GetDuration(),
           "track": item.GetTrackTypeAndIndex()} if item else None
    return item, rec


def differing(md5, base):
    """2 本の書き出しの framemd5 の list で、違うフレームの番号の list。長さが違えば、長いほうの残りも違うとみなす。"""
    return [i for i in range(max(len(md5), len(base))) if i >= len(md5) or i >= len(base) or md5[i] != base[i]]


def spline_keys(kf):
    """Fusion の BezierSpline の GetKeyFrames() の {時刻: {1: 値, ...}} を {整数の時刻: 値 (小数 4 桁)} にする (読み戻しを比べるため)。"""
    out = {}
    for t, v in (kf or {}).items():
        val = v.get(1, v.get(1.0)) if isinstance(v, dict) else v
        out[int(float(t))] = None if val is None else round(float(val), 4)
    return out


def spans(frames):
    """フレームの番号の list を、続いた所ごとの [最初, 最後] (両端を含む) にまとめる (記録を短くするため)。"""
    out = []
    for f in sorted(frames):
        if out and f == out[-1][1] + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return out


def still_source(mp, proj, clip, name):
    """静止画 clip を補助のタイムライン name の V1 に置き、そのタイムラインのメディアプールの項目を返す (返す値は (項目か None, 記録))。
    静止画をそのまま AppendToTimeline で置くと endFrame によらず既定の長さ (150 フレーム。環境設定の Standard still duration) に
    なった (21.1.1、2026-10-19)。環境設定は普段の編集にも効くので変えず、この項目を startFrame・endFrame で切り出して置く。
    置いた後に長さを変える関数は .pyi に無い。今のタイムラインは呼ぶ前のものに戻す。静止画を置けなければ (None, 記録)。"""
    cur = proj.GetCurrentTimeline()
    tl = mp.CreateEmptyTimeline(name)
    rec = {"timeline": bool(tl)}
    if not tl:
        return None, rec
    proj.SetCurrentTimeline(tl)
    try:
        placed, rec["still"] = append_at(mp, tl, clip, 0, 1)
        if not placed:   # 置けなければ、空のタイムラインを静止画として渡さない
            return None, rec
        item = tl.GetMediaPoolItem()
        rec["item"] = {k: item.GetClipProperty(k) for k in ("Start", "End", "Frames", "Duration", "Start TC")} if item else None
        return item, rec
    finally:
        if cur:
            proj.SetCurrentTimeline(cur)


def animated_keys(tool, inp):
    """Fusion の部品 tool の入力 inp につながった BezierSpline のキー ({整数の時刻: 値})。つながっていなければ None。"""
    out = getattr(tool, inp).GetConnectedOutput()
    return spline_keys(out.GetTool().GetKeyFrames()) if out else None


# ---------- 画素の領域 (四角は (x0, y0, x1, y1) の画素。右と下は含まない) ----------
def frames_of(videos, frames, chunk=15):
    """videos {名前: 動画} の同じ番号のフレームを chunk 個ずつ取り出し、(番号, {名前: RGB の配列 (高さ, 幅, 3) か None}) を
    番号の順に返す。取り出したフレームは覚えない (大きな可逆の動画を何本も読むため)。"""
    want = sorted(set(int(n) for n in frames))
    for i in range(0, len(want), chunk):
        part = want[i:i + chunk]
        got = {k: C.frame_reader(v).read(part, keep=False) for k, v in videos.items()}
        for n in part:
            yield n, {k: got[k].get(n) for k in videos}


def box_mean(img, box):
    """四角の中の RGB の平均 (0〜255 の 3 つの値、小数 1 桁)。"""
    x0, y0, x1, y1 = box
    return [round(float(v), 1) for v in img[y0:y1, x0:x1].reshape(-1, img.shape[2]).mean(axis=0)]


def diff_outside(a, b, boxes, pad=2):
    """同じ大きさの 2 枚 a・b の差 (画素ごとに RGB の差の最大、0〜255) の最大を、boxes の四角を pad だけ広げた所を除いて返す。"""
    import numpy as np
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    keep = np.ones(d.shape, bool)
    for x0, y0, x1, y1 in boxes:
        keep[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad] = False
    return int(d[keep].max()) if keep.any() else 0


def changed_box(a, b, threshold):
    """a と b の差 (画素ごとに RGB の差の最大) が threshold より大きい画素を囲む四角。無ければ None。"""
    import numpy as np
    ys, xs = np.nonzero(np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > threshold)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None


class Trial:
    def __init__(self, name, need_source=True):
        ap = argparse.ArgumentParser(description=f"Resolve のテスト: {name}")
        ap.add_argument("--source", help="テスト用の素材 (環境変数 RESOLVE_TRIAL_SOURCE より優先)")
        self.args = ap.parse_args()
        self.name = name
        self.cfg = C.config()
        self.look, _ = self.cfg.storyboard_values()
        self.W, self.H = self.look["timeline"]["width"], self.look["timeline"]["height"]
        self.fps = int(self.look["fps"])
        self.out = self.cfg.out_dir("tests")
        self.checks = []
        self.info = {}
        self.source = None
        if need_source:
            src = self.args.source or self.cfg.path("trial_source")
            if not src or not os.path.isfile(src):
                sys.exit("テスト用の素材が無い。--source か環境変数 RESOLVE_TRIAL_SOURCE で指定する "
                         "(作るなら python scripts/make_test_source.py)")
            self.source = os.path.realpath(src)
        require_lock()
        self.resolve = C.connect_or_exit()
        self.pm = self.resolve.GetProjectManager()
        cur = self.pm.GetCurrentProject()
        self.prev = cur.GetName() if cur else None   # 始めに開いていたプロジェクト (finish_projects で開き直す)
        self.project_name = f"test-{name}-{int(time.time())}"
        self.proj = self.pm.CreateProject(self.project_name)
        if not self.proj:
            sys.exit(f"プロジェクト {self.project_name} を作れない")
        self.proj.SetSettings({"timelineFrameRate": str(self.fps), "timelineResolutionWidth": str(self.W),
                               "timelineResolutionHeight": str(self.H)})
        self.mp = self.proj.GetMediaPool()
        self.info["product"] = f"{self.resolve.GetProductName()} {self.resolve.GetVersionString()}"
        self.info["project"] = self.project_name

    # ---------- 記録 ----------
    def check(self, label, ok, actual=None, expected=None):
        self.checks.append({"label": label, "ok": bool(ok), "actual": actual, "expected": expected})
        return ok

    def note(self, key, value):
        self.info[key] = value

    def finish(self):
        self.pm.SaveProject()
        path = os.path.join(self.out, f"{self.name}.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"name": self.name, "info": self.info, "checks": self.checks}, f, ensure_ascii=False, indent=1,
                      default=str)
        # 画面には件数と期待と違う項目だけを出す。全項目の結果と期待は JSON に残る
        bad = [c for c in self.checks if not c["ok"]]
        sys.exit(C.report(not bad, f"{self.name}: {len(self.checks) - len(bad)}/{len(self.checks)} が期待どおり", path,
                          [f"NG {c['label']}  (結果 {c['actual']!r}、期待 {c['expected']!r})" for c in bad]))

    # ---------- 素材とタイムライン ----------
    def clip(self):
        got = self.mp.ImportMedia([self.source])
        if not got:
            sys.exit(f"素材を取り込めない: {self.source}")
        return got[0]

    def timeline(self, label):
        tl = self.mp.CreateEmptyTimeline(f"{label}-{int(time.time() * 1000) % 100000}")
        self.proj.SetCurrentTimeline(tl)
        return tl

    # ---------- 書き出しとフレームの確認 ----------
    def render(self, tl, custom_name, limit=600):
        rd = self.look["render"]
        self.proj.SetCurrentTimeline(tl)
        self.proj.SetCurrentRenderFormatAndCodec(rd["format"], rd["codec"])
        self.proj.SetRenderSettings({"SelectAllFrames": True, "TargetDir": self.out, "CustomName": custom_name})
        job = self.proj.AddRenderJob()
        t0 = time.time()
        r = C.render_jobs(self.proj, [job], interval=0.5, limit=limit)
        st = r["status"][job]
        if not r["done"]:
            return None, st   # 始まらない・終わらない・Complete でないときは、前回の動画があっても返さない
        # 拾うのは <custom_name>.<書き出し形式> だけ。同じフォルダーの同じ名前の別物 (ExportFusionComp の .comp など) は除く。
        # Resolve の format (GetRenderFormats の値) は拡張子と同じなので、そのまま拡張子として比べる
        name = f"{custom_name}.{rd['format']}".lower()
        hits = [os.path.join(self.out, f) for f in os.listdir(self.out) if f.lower() == name]
        # 同じ名前の古い動画を拾わないよう、今回の書き出しの後に書かれたものだけを返す (時刻の丸めに 2 秒の幅)
        hits = [p for p in hits if os.path.getmtime(p) >= t0 - 2]
        return (hits[0] if hits else None), st

    def frame(self, video, n):
        """書き出した動画の n フレーム目 (0 始まり) を Pillow の画像で返す (common.frame_reader で取り出す)。無ければ RuntimeError。"""
        from PIL import Image
        img = C.frame_reader(video).read([n])[n]
        if img is None:
            raise RuntimeError(f"{video} の {n} フレーム目を取り出せない")
        return Image.fromarray(img)


def near(px, rgb, tol=60):
    """画素が rgb (0〜255) に近いか。H.264 の色のにじみを見込んで幅を持たせる。"""
    return all(abs(a - b) <= tol for a, b in zip(px, rgb))


def add_box_tools(comp, shape="RectangleMask"):
    """マスク・単色・Merge の 3 つを置く (値はまだ入れない)。"""
    return comp.AddTool(shape, -32768, -32768), comp.AddTool("Background", -32768, -32768), comp.AddTool("Merge", -32768, -32768)


def set_box(tools, src_output, rgb, center, width, height):
    """add_box_tools で置いた部品に値を入れてつなぐ。src_output の上に、マスクで切った単色を重ねる。"""
    mk, bg, m = tools
    mk.SetInput("Center", {1: center[0], 2: center[1]})
    mk.SetInput("Width", width)
    mk.SetInput("Height", height)
    mk.SetInput("SoftEdge", 0.0)
    for k, v in zip(("TopLeftRed", "TopLeftGreen", "TopLeftBlue", "TopLeftAlpha"), [x / 255 for x in rgb] + [1.0]):
        bg.SetInput(k, v)
    bg.EffectMask = mk.Mask
    m.Background = src_output
    m.Foreground = bg.Output
    return m


def solid_box(comp, src_output, rgb, center, width, height, shape="RectangleMask"):
    """src_output の上に、マスクで切った単色を重ねる (Lock は使わない)。返すのは Merge。"""
    return set_box(add_box_tools(comp, shape), src_output, rgb, center, width, height)
