"""Resolve の Speech Generator で作った声を A2 に置き、Resolve の機能で男声と女声の音量をそろえられるか。
あわせて、Resolve 内蔵の文字起こしで 6 本の声を文字に起こし、元の文と並べる (生成した声を聞いて確認する手助け)。

流し方: python scripts/resolve_lock.py run --owner <名前> -- python tests/resolve/native_audio.py
(Resolve Studio を起動しておく。結果は 出力先/tests/native_audio.json。書き出した動画は 作業フォルダー/native_audio に残る)

素材: 作業フォルダーの speech-samples に、声の WAV 6 本と元の文を書いた speech-samples.json を先に置く。
映像は機密の無い合成の素材 (resolve_helpers.make_source、testsrc2 の 12 秒、音声なし) をこのテストが作る。
- speech-samples.json は {"items": [{"key": "s1", "voice": "Female 1", "text": "元の文"}, ...]}。
  文 3 つ (key の 1 つは s1) × 声 2 つ (Female 1・Male 1) の 6 件。音量の項目は s1 の 2 本、文字起こしは 6 本全部を使う
- WAV は Project.GenerateSpeech で作る (48kHz・モノラル・32bit float)。TextInput に元の文、VoiceModel に声、
  Filename に <key>-<声を小文字にして空白を除いたもの> (例 s1-female1)、GenerationID に 1 を渡すと、プロジェクトのメディアの
  フォルダーの Audio Files/SpeechGenerator に <Filename>-<声>-G1-001.wav (例 s1-female1-Female 1-G1-001.wav) ができる。
  それを speech-samples に写す
speech-samples.json か WAV が 1 本でも無ければ、Resolve に接続せずに SKIP と出して終わる (終了コード 0)。

組むもの: 新しいプロジェクト test-native-audio-<時刻> (終わったら消す)。
- 音量 (A): 3 つのタイムラインに、V1 に映像、A2 (追加したトラック) に s1 の女声を 30 フレーム目、s1 の男声を 180 フレーム目から置く
  - audio-none       何もしない (比べる元)
  - audio-volume     男声のクリップに TimelineItem.SetProperties({"AudioVolume": 差}) (差は元の WAV の統合ラウドネスの差、dB)
  - audio-normalize  Timeline.NormalizeAudioLevel([女声, 男声], {normalizationMode, targetLoudness -20, setLevelMode 個別})
  書き出しは mp4 (config の render) に aac 48kHz 16bit (audio_place.py の設定)。測り方は、書き出しの音声の各クリップの区間を
  FFmpeg の ebur128 (統合ラウドネス、LUFS) と astats (平均、dB) の値を ffprobe の JSON で読んで測り、女声と男声の差を出す。
  正規化の方式 (ITU-R BS.1770-1)・個々に (resolve.NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT)・字幕の言語 (resolve.AUTO_CAPTION_JAPANESE)
  は決めた値で使い、使えなければその項目を fail にして理由を残す (別の設定では試さない)
- 文字起こし (B): 6 本を MediaPool に読み込み、MediaPoolItem.TranscribeAudio() と GetTranscription() で起こす。
  別のタイムライン subs の A1 に 6 本を並べ、Timeline.CreateSubtitlesFromAudio で字幕を作り、字幕のトラックの項目の名前を読む
- 保存して閉じ (SaveProject・CloseProject)、開き直し (LoadProject)、AudioVolume・正規化の結果・文字起こし・字幕が
  残るかを読み、audio-volume と audio-normalize を書き出し直して、クリップごとに前の書き出しと同じ音量か (0.5 LUFS の内) を測る
- 書き出しの待ちが時間切れになったら書き出しを止め (resolve_helpers.stop_rendering)、止められなければその後の項目を進めず、
  プロジェクトも消さずに終える

MediaPoolItem.TranscribeAudio が偽を返したときは、引数を変えて 3 通り (引数なし・(False,)・(False, False))、
transcriptionLanguage を ja と始めの値 (auto) で試し、全部偽ならフォルダーの TranscribeAudio を 1 度試して記録する。

GUI で確認すること (人): メディアプールで WAV を右クリックして文字起こしの項目 (Transcribe Audio) を選び、エラーの画面が出るか
(拡張が無いと偽を返すかの確認)、Extras Download Manager に文字起こしの拡張の言語モデルがあるか。

確認する項目 (pass で合格。確認できないものは unknown で、合格にしない)。記録 (info) の measure に区間ごとの値、
transcribe に起こした文と元の文の違いが残る。
"""
import difflib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))
import common as C  # noqa: E402
from video import LOW_PRIORITY  # noqa: E402
from resolve_helpers import finish_projects, make_check, make_source, render_job, require_lock, stop_rendering  # noqa: E402

NAME = "native_audio"
RATE = 48000
SRC_SECONDS = 12
PLACE = [("female", "s1", "Female 1", 30), ("male", "s1", "Male 1", 180)]   # (役, 文, 声, 置き始めのフレーム)
TARGET_LUFS = -20.0
NORMALIZE_MODE = "ITU-R BS.1770-1"   # GetNormalizeAudioModes の名前
TOL_DB = 1.0
TOL_REOPEN = 0.5   # 開き直した後の書き出しと前の書き出しの、クリップごとの統合ラウドネスの幅 (LUFS)
WAIT = 300   # 文字起こし・字幕の作成を待つ上限 (秒)


def sample_path(dirpath, key, voice):
    tag = voice.lower().replace(" ", "")
    return os.path.join(dirpath, f"{key}-{tag}-{voice}-G1-001.wav")


def loudness(path, start=None, dur=None):
    """path の音声 (start 秒から dur 秒) の統合ラウドネス (LUFS)・平均 (RMS) と最大 (dB) を、FFmpeg の ebur128 と astats が
    フレームに付ける値 (metadata) を ffprobe の JSON で読んで測る。どれも頭からの積算で、値のある最後のフレームのものを使う。
    (ebur128 の要約の行・volumedetect と小数第 1 位まで同じ値になる)"""
    trim = f",atrim=start={start:.3f}:duration={dur:.3f},asetpts=PTS-STARTPTS" if start is not None else ""
    graph = f"amovie='{os.path.basename(path)}'{trim},ebur128=metadata=1,astats=metadata=1:reset=0"
    keys = {"lufs": "lavfi.r128.I", "mean": "lavfi.astats.Overall.RMS_level", "max": "lavfi.astats.Overall.Peak_level"}
    r = subprocess.run([C.config().tool("ffprobe"), "-v", "error", "-f", "lavfi", "-i", graph,
                        "-show_entries", "frame_tags=" + ",".join(keys.values()), "-of", "json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=os.path.dirname(path), creationflags=LOW_PRIORITY)   # amovie には名前だけ渡す (パスの区切りを避ける)
    if r.returncode:
        raise RuntimeError(f"ffprobe で {path} を測れない: {r.stderr.strip()[-300:]}")
    tags = [f.get("tags") or {} for f in json.loads(r.stdout).get("frames") or []]

    def last(k):
        v = next((t[k] for t in reversed(tags) if t.get(k) is not None), None)
        return round(float(v), 3) if v not in (None, "-inf", "inf", "nan") else None
    return {k: last(v) for k, v in keys.items()}


def audio_streams(path):
    r = C.run_tool("ffprobe", ["-select_streams", "a", "-show_entries",
                               "stream=codec_name,sample_rate,channels,duration", "-of", "json", path])
    return json.loads(r.stdout).get("streams") or []


def segments(video, spans):
    """書き出し video の区間 spans {役: (始まりの秒, 長さの秒)} を測り、女声と男声の差 (女声 - 男声) を追加して返す。"""
    out = {k: loudness(video, a, d) for k, (a, d) in spans.items()}
    f, m = out.get("female") or {}, out.get("male") or {}
    out["diff_lufs"] = round(f["lufs"] - m["lufs"], 2) if f.get("lufs") is not None and m.get("lufs") is not None else None
    out["diff_mean"] = round(f["mean"] - m["mean"], 2) if f.get("mean") is not None and m.get("mean") is not None else None
    return out


def norm_text(s):
    """比べるために、空白と句読点を除く。"""
    return re.sub(r"[\s、。,.，．!?！？「」]", "", s or "")


def text_diff(want, got):
    """元の文 want と起こした文 got の違い [(元, 起こした)] (空白と句読点は除いて比べる)。"""
    a, b = norm_text(want), norm_text(got)
    return [(a[i1:i2], b[j1:j2]) for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes() if op != "equal"]


def transcription_text(tr):
    if not isinstance(tr, dict):
        return None
    segs = tr.get("segments") or []
    return "".join(s.get("text", "") for s in segs if s.get("text") != "(...)")


def wait_status(get, limit=WAIT, interval=1.0):
    """get() の JobStatus が終わり (Complete・Failed・Cancelled・Inactive) になるまで待つ。返す値は (最後の状態, 見た状態の list)。"""
    seen, t0, st = [], time.time(), None
    while time.time() - t0 < limit:
        st = get() or {}
        js = st.get("JobStatus")
        if not seen or seen[-1] != js:
            seen.append(js)
        if js in ("Complete", "Failed", "Cancelled", "Inactive", None):
            break
        time.sleep(interval)
    return st, seen


def a2_items(tl):
    return tl.GetItemListInTrack("audio", 2) or []


def vol_of(item):
    p = item.GetProperties() or {}
    return {k: p.get(k) for k in ("AudioVolumeEnabled", "AudioVolume")}


class RenderStuck(RuntimeError):
    """書き出しの待ちが時間切れで、止めても書き出しが続いている (その後の項目を進めず、プロジェクトも消さない)。"""


def ensure_stopped(proj, rr):
    """書き出しの待ちが時間切れで Resolve がまだ書き出していれば止める (stop_rendering)。止まらなければ RenderStuck を出す。"""
    if proj.IsRenderingInProgress():
        rr["still_rendering"] = True
        stop_rendering(proj)
        rr["stopped"] = not proj.IsRenderingInProgress()
        if not rr["stopped"]:
            raise RenderStuck("書き出しの待ちが時間切れで、StopRendering の後も書き出しが続いている")


if __name__ == "__main__":
    t_all = time.time()
    cfg = C.config()
    look, _ = cfg.storyboard_values()
    W, H, FPS = look["timeline"]["width"], look["timeline"]["height"], int(look["fps"])
    OUT, DIR = cfg.out_dir("tests"), cfg.work_dir(NAME)
    SAMPLES = cfg.work_dir("speech-samples")
    checks, info, created = [], {"wav": {}, "measure": {}, "transcribe": {}}, []
    check = make_check(checks)
    path = os.path.join(OUT, f"{NAME}.json")

    # Resolve を使わない準備: 素材・元の文・元の WAV の音量
    texts_path = os.path.join(SAMPLES, "speech-samples.json")
    if not os.path.isfile(texts_path):
        print(f"SKIP {NAME}: 声の素材が無い ({texts_path}。作り方はこのテストの説明)")
        sys.exit(0)
    with open(texts_path, encoding="utf-8") as fp:
        texts = {(it["key"], it["voice"]): it["text"] for it in json.load(fp)["items"]}
    missing = [sample_path(SAMPLES, key, voice) for key, voice in texts
               if not os.path.isfile(sample_path(SAMPLES, key, voice))]
    if missing:
        print(f"SKIP {NAME}: 声の WAV が {len(missing)} 本無い (最初は {missing[0]}。作り方はこのテストの説明)")
        sys.exit(0)
    wavs = {}
    for (key, voice), _ in texts.items():
        wp = sample_path(SAMPLES, key, voice)
        st = (audio_streams(wp) or [{}])[0] if os.path.isfile(wp) else {}
        wavs[(key, voice)] = wp
        info["wav"][f"{key} {voice}"] = {"path": wp, "stream": st, "level": loudness(wp) if st else None}
    ok_wav = all(info["wav"][f"{k} {v}"]["stream"].get("sample_rate") == str(RATE) for k, v in wavs)
    check("speech-samples の WAV 6 本があり、48kHz", ok_wav and len(wavs) == 6,
          {k: v["stream"].get("sample_rate") for k, v in info["wav"].items()}, RATE)
    lv = {role: info["wav"][f"{key} {voice}"]["level"] for role, key, voice, _ in PLACE}
    GAIN = round(lv["female"]["lufs"] - lv["male"]["lufs"], 1)
    info["gain"] = {"female": lv["female"], "male": lv["male"], "male_gain_db": GAIN}
    src = make_source(cfg, W, H, FPS, seconds=SRC_SECONDS)
    info["source"] = C.probe_video(src) | {"path": src}

    require_lock()
    resolve = C.connect_resolve(quiet=True)
    if not resolve:
        sys.exit(C.report(False, f"{NAME}: Resolve に接続できない", None))
    pm = resolve.GetProjectManager()
    cur = pm.GetCurrentProject()
    prev = cur.GetName() if cur else None
    info["previous_project"] = prev
    info["product"] = f"{resolve.GetProductName()} {resolve.GetVersionString()}"
    try:
        pname = f"test-native-audio-{int(time.time())}"
        proj = pm.CreateProject(pname)
        if not proj:
            raise RuntimeError(f"プロジェクト {pname} を作れない")
        created.append(pname)
        info["project"] = pname
        proj.SetSettings({"timelineFrameRate": str(FPS), "timelineResolutionWidth": str(W),
                          "timelineResolutionHeight": str(H)})
        mp = proj.GetMediaPool()
        vclip = (mp.ImportMedia([src]) or [None])[0]
        got = mp.ImportMedia(list(wavs.values())) or []
        by_name = {c.GetClipProperty("File Name"): c for c in got}
        clips = {kv: by_name.get(os.path.basename(p)) for kv, p in wavs.items()}
        check("映像と WAV 6 本を読み込めた", bool(vclip) and all(clips.values()), {f"{k} {v}": bool(c) for (k, v), c in clips.items()})
        mclip = clips[("s1", "Male 1")]
        mprops = mclip.GetClipProperty() if mclip else {}
        info["mediapool_audio_props"] = {k: v for k, v in (mprops or {}).items()
                                         if re.search(r"audio|volume|gain|level|loud|sample|transcri", k, re.I)}
        info["mediapool_all_keys"] = sorted((mprops or {}).keys())
        vframes = int(round(info["source"]["duration"] * FPS))
        frames = {}
        for role, key, voice, _ in PLACE:
            frames[role] = int(float(info["wav"][f"{key} {voice}"]["stream"]["duration"]) * FPS)
        spans = {role: (rec / FPS, frames[role] / FPS) for role, _, _, rec in PLACE}
        info["spans_seconds"] = spans

        def build(label):
            tl = mp.CreateEmptyTimeline(label)
            proj.SetCurrentTimeline(tl)
            t0 = tl.GetStartFrame()
            mp.AppendToTimeline([{"mediaPoolItem": vclip, "startFrame": 0, "endFrame": vframes, "trackIndex": 1,
                                  "recordFrame": t0}])
            while tl.GetTrackCount("audio") < 2:
                if not (tl.AddTrack("audio", "mono") or tl.AddTrack("audio")):
                    break
            items = {}
            for role, key, voice, rec in PLACE:
                g = mp.AppendToTimeline([{"mediaPoolItem": clips[(key, voice)], "startFrame": 0, "endFrame": frames[role],
                                          "mediaType": 2, "trackIndex": 2, "recordFrame": t0 + rec}]) or []
                items[role] = g[0] if g else None
            rec = {"audio_tracks": tl.GetTrackCount("audio"), "a2_subtype": tl.GetTrackSubType("audio", 2),
                   "a2": [{"start": i.GetStart() - t0, "end": i.GetEnd() - t0, "name": i.GetName()} for i in a2_items(tl)]}
            return tl, items, rec

        tls, items, builds = {}, {}, {}
        for key in ("none", "volume", "normalize"):
            tls[key], items[key], builds[key] = build(f"audio-{key}")
        info["build"] = builds
        want_a2 = [{"start": rec, "end": rec + frames[role]} for role, _, _, rec in PLACE]
        check("どのタイムラインも A2 に女声 [30,..) と男声 [180,..) を置けた",
              all([{"start": a["start"], "end": a["end"]} for a in b["a2"]] == want_a2 for b in builds.values()),
              {k: b["a2"] for k, b in builds.items()}, want_a2)
        props0 = items["none"]["male"].GetProperties() if items["none"]["male"] else {}
        info["audio_item_properties"] = props0
        check("A2 のクリップの GetProperties に AudioVolume がある", "AudioVolume" in (props0 or {}),
              sorted(k for k in (props0 or {}) if k.startswith("Audio")))

        # (1) AudioVolume
        mv = items["volume"]["male"]
        proj.SetCurrentTimeline(tls["volume"])
        set1 = bool(mv.SetProperties({"AudioVolume": float(GAIN)})) if mv else False
        info["volume"] = {"set": set1, "readback": vol_of(mv) if mv else None, "female_readback": vol_of(items["volume"]["female"])}
        rb = (info["volume"]["readback"] or {}).get("AudioVolume")
        check("AudioVolume: 男声に SetProperties で入り、読み戻しが入れた値", set1 and rb is not None and abs(float(rb) - GAIN) < 0.05,
              info["volume"], GAIN)

        # (2) NormalizeAudioLevel
        tln = tls["normalize"]
        proj.SetCurrentTimeline(tln)
        modes = tln.GetNormalizeAudioModes() or []
        indep = getattr(resolve, "NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT", None)
        why = [m for m, bad in ((f"モードの一覧に {NORMALIZE_MODE} が無い", NORMALIZE_MODE not in modes),
                                ("resolve.NORMALIZE_AUDIO_SET_LEVEL_INDEPENDENT が無い", indep is None)) if bad]
        opts = {"normalizationMode": NORMALIZE_MODE, "targetLoudness": TARGET_LUFS, "setLevelMode": indep}
        before = {r: vol_of(i) for r, i in items["normalize"].items()}
        t1 = time.time()
        nres = False if why else bool(tln.NormalizeAudioLevel([items["normalize"]["female"], items["normalize"]["male"]], opts))
        info["normalize"] = {"modes": modes, "options": {k: (v if k != "setLevelMode" else "INDEPENDENT") for k, v in opts.items()},
                             "not_called": why or None, "result": nres, "seconds": round(time.time() - t1, 2), "before": before,
                             "after": {r: vol_of(i) for r, i in items["normalize"].items()}}
        check(f"NormalizeAudioLevel: {NORMALIZE_MODE}・個々にで呼び出しが真", nres, info["normalize"])

        # 書き出しと測定
        rd = look["render"]

        def render(key, suffix=""):
            proj.SetCurrentTimeline(tls[key])
            base = {"SelectAllFrames": True, "TargetDir": DIR, "CustomName": f"{NAME}-{key}{suffix}", "ExportVideo": True,
                    "ExportAudio": True, "AudioSampleRate": RATE, "AudioCodec": "aac", "AudioBitDepth": 16}
            rr = render_job(proj, base, rd["format"], rd["codec"], 600)
            ensure_stopped(proj, rr)
            ok = bool(rr["done"] and rr.get("written") and audio_streams(rr["path"]))
            m = segments(rr["path"], spans) if ok else None
            return ok, rr, m

        for key in ("none", "volume", "normalize"):
            ok, rr, m = render(key)
            info["measure"][key] = {"path": rr["path"], "written": ok, "status": rr.get("status")} | (m or {})
            check(f"audio-{key}: 音声付きで書き出せた", ok, rr.get("status"))
        mn = info["measure"]["none"].get("diff_lufs")
        check("audio-none: 女声と男声の差が元の WAV の差 (±1dB)", mn is not None and abs(mn - GAIN) <= TOL_DB, mn, GAIN)
        d = info["measure"]["volume"].get("diff_lufs")
        check("audio-volume: 女声と男声の差が 1dB の内 (統合ラウドネス)", d is not None and abs(d) <= TOL_DB, d, f"|差| <= {TOL_DB}")
        mz = info["measure"]["normalize"]
        each = {r: (mz.get(r) or {}).get("lufs") for r in ("female", "male")}
        d = mz.get("diff_lufs")
        check(f"audio-normalize: 女声と男声がそれぞれ {TARGET_LUFS:g} LUFS の 1dB の内で、差も 1dB の内 (統合ラウドネス)",
              d is not None and abs(d) <= TOL_DB and all(v is not None and abs(v - TARGET_LUFS) <= TOL_DB for v in each.values()),
              each | {"diff": d}, f"|値 - {TARGET_LUFS:g}| <= {TOL_DB}、|差| <= {TOL_DB}")

        # (B) 文字起こし
        lang0 = proj.GetSetting("transcriptionLanguage")
        set_lang = bool(proj.SetSettings({"transcriptionLanguage": "ja"}))
        info["transcribe_setting"] = {"before": lang0, "set_ja": set_lang, "after": proj.GetSetting("transcriptionLanguage")}

        def transcribe_one(c):
            """呼び方を 3 通り試す (引数なし・話者の検出なし・入れ子のクリップなし)。真を返した所で止める。"""
            tries = []
            for args in ((), (False,), (False, False)):
                r = bool(c.TranscribeAudio(*args))
                tries.append({"args": list(args), "result": r, "status": proj.GetTranscribeAudioStatus()})
                if r:
                    break
            return tries

        langs_tried = {}
        for (key, voice), c in clips.items():
            k = f"{key} {voice}"
            rec = {"want": texts[(key, voice)]}
            t1 = time.time()
            try:
                rec["tries"] = transcribe_one(c)
                if not any(t["result"] for t in rec["tries"]) and not langs_tried:
                    # ja で断られたら、始めの設定 (auto) に戻してもう 1 度
                    langs_tried["ja"] = rec["tries"]
                    proj.SetSettings({"transcriptionLanguage": lang0 or "auto"})
                    rec["tries_lang0"] = transcribe_one(c)
                    langs_tried[lang0 or "auto"] = rec["tries_lang0"]
                    if not any(t["result"] for t in rec["tries_lang0"]):
                        proj.SetSettings({"transcriptionLanguage": "ja"})
                rec["call"] = any(t["result"] for t in rec["tries"] + rec.get("tries_lang0", []))
                rec["lang"] = proj.GetSetting("transcriptionLanguage")
                rec["call_seconds"] = round(time.time() - t1, 1)
                st, seen = wait_status(proj.GetTranscribeAudioStatus)
                rec["status"], rec["status_seen"] = st, seen
                rec["seconds"] = round(time.time() - t1, 1)
                tr = c.GetTranscription()
                t2 = time.time()
                while rec["call"] and not transcription_text(tr) and time.time() - t2 < 60:   # 状態が先に終わりを返したとき
                    time.sleep(2)
                    tr = c.GetTranscription()
                rec["raw"] = tr
                rec["got"] = transcription_text(tr)
                rec["diff"] = text_diff(rec["want"], rec["got"]) if rec["got"] else None
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {e}"
            rec["clip_props"] = {x: c.GetClipProperty(x) for x in ("Transcription", "Transcription Status")}
            info["transcribe"][k] = rec
            check(f"文字起こし {k}: TranscribeAudio が真で、GetTranscription に文がある", rec.get("call") and bool(rec.get("got")),
                  {x: rec.get(x) for x in ("call", "status", "got", "error")})
        if not any(r.get("call") for r in info["transcribe"].values()):
            # クリップごとに断られたら、フォルダーの TranscribeAudio を 1 度だけ試す (記録だけ)
            fold = mp.GetRootFolder()
            fr = {"result": bool(fold.TranscribeAudio())}
            st, seen = wait_status(proj.GetTranscribeAudioStatus)
            fr.update(status=st, status_seen=seen,
                      got={f"{k} {v}": transcription_text(c.GetTranscription()) for (k, v), c in clips.items()})
            info["transcribe_folder"] = fr

        # 字幕 (CreateSubtitlesFromAudio)
        tls_sub = mp.CreateEmptyTimeline("subs")
        proj.SetCurrentTimeline(tls_sub)
        t0 = tls_sub.GetStartFrame()
        at, sub_place = 15, {}
        for (key, voice), c in clips.items():
            n = int(float(info["wav"][f"{key} {voice}"]["stream"]["duration"]) * FPS)
            g = mp.AppendToTimeline([{"mediaPoolItem": c, "startFrame": 0, "endFrame": n, "mediaType": 2, "trackIndex": 1,
                                      "recordFrame": t0 + at}]) or []
            sub_place[f"{key} {voice}"] = {"start": at, "end": at + n, "placed": bool(g)}
            at += n + FPS
        lang_c = getattr(resolve, "AUTO_CAPTION_JAPANESE", None)
        t1 = time.time()
        if lang_c is None:   # 別の言語では作らない
            sres, sst, sseen = False, {"not_called": "resolve.AUTO_CAPTION_JAPANESE が無い"}, []
        else:
            sres = bool(tls_sub.CreateSubtitlesFromAudio({"language": lang_c}))
            sst, sseen = wait_status(proj.GetCreateSubtitlesFromAudioStatus)

        def read_subs(tl):
            t00 = tl.GetStartFrame()
            out = []
            for i in range(1, (tl.GetTrackCount("subtitle") or 0) + 1):
                for it in tl.GetItemListInTrack("subtitle", i) or []:
                    out.append({"track": i, "start": it.GetStart() - t00, "end": it.GetEnd() - t00, "name": it.GetName()})
            return out

        subs = read_subs(tls_sub)
        for k, p in sub_place.items():
            p["subs"] = "".join(s["name"] or "" for s in subs if s["start"] < p["end"] and s["end"] > p["start"])
            p["diff"] = text_diff(texts[tuple(k.split(" ", 1))], p["subs"])
        info["transcribe_after_subs"] = {f"{k} {v}": transcription_text(c.GetTranscription()) for (k, v), c in clips.items()}
        info["subtitles"] = {"language_constant": lang_c, "result": sres, "seconds": round(time.time() - t1, 1), "status": sst, "status_seen": sseen,
                             "tracks": tls_sub.GetTrackCount("subtitle"), "items": subs, "by_clip": sub_place}
        check("CreateSubtitlesFromAudio: 真で、字幕のトラックに項目がある", sres and bool(subs),
              {"result": sres, "status": sst, "items": len(subs)})

        # 保存して閉じ、開き直す (どれか 1 つでも偽なら、開き直しの項目は合格にしない)
        reo = {"saved": bool(pm.SaveProject())}
        reo["closed"] = bool(reo["saved"] and pm.CloseProject(proj))   # CloseProject は保存しないので、保存できたときだけ閉じる
        cur2 = pm.GetCurrentProject()
        reo["current_after_close"] = cur2.GetName() if cur2 else None
        if reo["closed"]:
            proj = pm.LoadProject(pname)
            reo["reloaded"] = bool(proj) and proj.GetName() == pname
        reopened = all(reo.get(k) for k in ("saved", "closed", "reloaded"))
        info["reopen"] = reo
        check("保存して閉じ、開き直せた", reopened, reo)
        if reopened:
            mp = proj.GetMediaPool()
            by = {}
            for i in range(1, proj.GetTimelineCount() + 1):
                t = proj.GetTimelineByIndex(i)
                by[t.GetName()] = t
            tls = {k: by.get(f"audio-{k}") for k in ("none", "volume", "normalize")}
            after = {}
            for k in ("volume", "normalize"):
                proj.SetCurrentTimeline(tls[k])
                it = a2_items(tls[k])
                after[k] = [vol_of(i) for i in it]
            info["after_reopen"] = after
            rb2 = (after["volume"][1] if len(after["volume"]) > 1 else {}).get("AudioVolume")
            check("開き直した後も男声の AudioVolume が入れた値", rb2 is not None and abs(float(rb2) - GAIN) < 0.05, after["volume"], GAIN)
            nb = info["normalize"]["after"]
            na = after["normalize"]
            same_n = len(na) == 2 and all(
                (a.get("AudioVolume") is not None and nb[r].get("AudioVolume") is not None
                 and abs(float(a["AudioVolume"]) - float(nb[r]["AudioVolume"])) < 0.05) for r, a in zip(("female", "male"), na))
            check("開き直した後も正規化の後の AudioVolume が同じ", same_n, na, nb)
            for k in ("volume", "normalize"):
                ok, rr, m = render(k, "-reopen")
                info["measure"][f"{k}-reopen"] = {"path": rr["path"], "written": ok} | (m or {})
                m0 = info["measure"][k]
                d0, d1 = m0.get("diff_lufs"), (m or {}).get("diff_lufs")
                per = {r: ((m0.get(r) or {}).get("lufs"), ((m or {}).get(r) or {}).get("lufs")) for r in ("female", "male")}
                same_each = all(a is not None and b is not None and abs(b - a) <= TOL_REOPEN for a, b in per.values())
                check(f"audio-{k}: 開き直した後の書き出しも差が 1dB の内で前の差と 0.2dB の内、"
                      f"女声と男声がそれぞれ前の書き出しと {TOL_REOPEN} LUFS の内",
                      d1 is not None and abs(d1) <= TOL_DB and d0 is not None and abs(d1 - d0) <= 0.2 and same_each,
                      {"diff": d1, "before_after": per},
                      {"diff_before": d0, "tol": TOL_REOPEN})
            # 文字起こしと字幕が残るか
            got2 = {}
            for (key, voice), p in wavs.items():
                c = next((x for x in (mp.GetRootFolder().GetClipList() or []) if x.GetClipProperty("File Name") == os.path.basename(p)),
                         None)
                got2[f"{key} {voice}"] = transcription_text(c.GetTranscription()) if c else None
            info["transcribe_after_reopen"] = got2
            check("開き直した後も GetTranscription の文が同じ",
                  all(got2.get(k) == v.get("got") and v.get("got") for k, v in info["transcribe"].items()), got2)
            subs2 = read_subs(by["subs"]) if by.get("subs") else []
            info["subtitles_after_reopen"] = subs2
            check("開き直した後も字幕の項目が同じ",
                  bool(subs2) and [s["name"] for s in subs2] == [s["name"] for s in subs], len(subs2), len(subs))
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        if isinstance(e, RenderStuck):
            info["render_stuck"] = str(e)
        check("途中で例外が出ない", False, str(e))
    finally:
        info["seconds"] = round(time.time() - t_all, 1)
        if info.get("render_stuck"):   # 書き出しが止まらない。プロジェクトを閉じたり消したりせずに終える
            C.write_json(path, {"name": NAME, "info": info, "checks": checks})
            code = C.report(False, f"{NAME}: 書き出しを止められず、途中で終えた (プロジェクト {created} は残る)", path,
                            [info["render_stuck"]])
        else:
            code = finish_projects(resolve, NAME, path, created, prev, info, checks, min_checks=27)
    sys.exit(code)
