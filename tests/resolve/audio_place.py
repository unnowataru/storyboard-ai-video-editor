"""合成の WAV を音声トラック 2 (A2) の指定のフレームに置き、書き出しで音が期待の時刻に鳴るか。

流し方: python scripts/resolve_lock.py run --owner <名前> -- python tests/resolve/audio_place.py
(Resolve Studio を起動しておく。結果は 出力先/tests/audio_place.json。WAV と書き出した動画は 作業フォルダー/audio_place に残る)

組むもの:
- FFmpeg で合成の WAV を 2 本作る (48kHz・24bit・ステレオ。1 本目は 440Hz を 1.0 秒、2 本目は 880Hz を 2.5 秒。
  音の始まりと終わりが分かるよう、前後に無音を付けない。振幅は FFmpeg の sine の既定 (1/8))
- 機密の無い合成の映像 (resolve_helpers.make_source。testsrc2 の 10 秒で、音声は無い) で新しいプロジェクト test-audio_place-<時刻> を
  作り、空のタイムラインの映像トラック 1 (V1) の頭に置く。mediaType を付けずに置くので、素材に音があれば音声トラック 1 (A1) に入る。
  素材が無音なことは info の source_audio_streams (ffprobe の音声の数) と A1 のクリップの数に残す
- WAV を MediaPool.ImportMedia で読み込み、音声トラックが 1 本なら Timeline.AddTrack("audio", "stereo") で A2 を追加する
  (トラックが無い番号の trackIndex は、エラーにならずトラック 1 に置かれる。docs/scripting-api.md)。
  AppendToTimeline の clipInfo (mediaType 2・trackIndex 2・recordFrame・startFrame 0・endFrame は WAV の長さのフレーム数) で、
  1 本目をタイムラインの 30 フレーム目、2 本目を 120 フレーム目から置く (recordFrame は GetStartFrame を足した値)
- 書き出し: 形式と映像の codec は構成表の既定 (config の render。mp4 / H264)、SetRenderSettings に ExportVideo・ExportAudio を真、
  AudioCodec "aac"・AudioSampleRate 48000・AudioBitDepth 16 を渡して全編を書き出す。この設定が入らなければ、AudioCodec と
  AudioBitDepth を外して 1 回だけ試し直す (どちらで書いたかは info の render に残る)
- 測り方: 書き出しの音声を FFmpeg でモノラル 48kHz の 16bit の PCM に直し、サンプルの絶対値が閾値 (最大の 1%) を超える所を音とみなす。
  1 フレーム (48000 / fps サンプル) ごとの最大値でフレームを「音あり」「音なし」に分け、サンプルの単位で音の始まりと終わりの時刻も出す。
  30fps の期待は 1 本目 [1.0, 2.0) 秒 (フレーム [30, 60))、2 本目 [4.0, 6.5) 秒 (フレーム [120, 195))

確認する項目 (どれも pass で合格。確認できないものは unknown で、合格にしない):
- 合成の WAV の長さ・サンプルレートが作ったとおり (ffprobe)
- 映像を V1 の頭に置ける。素材の音声の数と A1 のクリップの数が合う (素材が無音なら A1 は空)
- WAV を 2 本読み込め、メディアプールのクリップの属性 (Sample Rate が 48000、Duration か Frames が WAV の長さ) が合う
- A2 がある (追加した後の GetTrackCount と GetTrackSubType も記録)
- AppendToTimeline の戻り値が 1 件ずつで、GetTrackTypeAndIndex が ["audio", 2]
- GetItemListInTrack("audio", 2) が 2 件で、各クリップの GetStart・GetEnd・GetDuration (タイムラインの頭を 0 としたフレーム) が期待どおり。
  別々のクリップ (GetUniqueId が違い、映像とつながっていない) として残る
- 音声を置いた後も V1 のクリップの位置と A1 のクリップの数が変わらない
- 音声付きで全編を書き出せる (GetRenderJobList の IsExportAudio が真、ffprobe で音声の流れがある)
- 書き出しの音の区間が 2 つで、始まりと終わりが期待から 1 フレーム未満。期待の区間の中のフレームは全部「音あり」で途切れない。
  期待の区間の前後 1 フレームを除いた外のフレームは全部「音なし」
- 作ったプロジェクトを消し、始めのプロジェクトを開き直せる

記録だけで合否を付けないもの: 音の区間ごとの最大値 (割れの目安)、書き出しの音声の codec・チャンネル数、GetRenderJobList で読み戻した設定。

使う Resolve の標準の API: MediaPool.ImportMedia・CreateEmptyTimeline・AppendToTimeline (AppendClipInfo の mediaType・trackIndex・
recordFrame・startFrame・endFrame)、Timeline.AddTrack・GetTrackCount・GetTrackSubType・GetItemListInTrack、TimelineItem.GetStart・
GetEnd・GetDuration・GetTrackTypeAndIndex・GetLinkedItems、MediaPoolItem.GetClipProperty、Project.SetRenderSettings
(ExportVideo・ExportAudio・AudioCodec・AudioSampleRate・AudioBitDepth)・AddRenderJob・GetRenderJobList (IsExportAudio)。

選ばなかった候補:
- Project.InsertAudioToCurrentTrackAtPlayhead (サンプル単位で置ける): 置き先が「今のトラック」と再生ヘッドで、画面の状態
  (Fairlight ページで選んでいるトラック) に左右される。置き先をトラックの番号とフレームで渡せる AppendToTimeline を使う
- Fairlight の画面でしかできないもの: トラックのフェーダー・クリップのフェードの取っ手・音量のオートメーションの書き込み・
  EQ とダイナミクスの調整・トラックの追加の画面での操作。このテストは置く位置と書き出しを確認するもので、音量の調整は native_audio.py で調べる
- Project.GenerateSpeech (Resolve の音声の生成) と Timeline.NormalizeAudioLevel: 置く位置の確認には要らない (音量の調整は native_audio.py で調べる)
"""
import array
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))
import common as C  # noqa: E402
from resolve_helpers import finish_projects, make_check, make_source, render_job, require_lock  # noqa: E402

NAME = "audio_place"
RATE = 48000
# 置く WAV: (名前, 周波数, 秒, タイムラインの頭を 0 とした置き始めのフレーム)
TONES = [("tone440-1.0s", 440, 1.0, 30), ("tone880-2.5s", 880, 2.5, 120)]
THRESHOLD = 0.01   # 音とみなすサンプルの絶対値 (最大を 1 とした割合)。sine の既定の振幅は 1/8


def make_wav(path, freq, seconds):
    """FFmpeg の sine で、前後に無音の無い 48kHz・24bit・ステレオの WAV を作る。"""
    C.run_tool("ffmpeg", ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate={RATE}:duration={seconds}",
                          "-af", "aformat=channel_layouts=stereo", "-c:a", "pcm_s24le", path])


def audio_streams(path):
    """ファイルの音声の流れ (codec・サンプルレート・チャンネル数・長さ) の list (ffprobe)。"""
    r = C.run_tool("ffprobe", ["-select_streams", "a", "-show_entries",
                               "stream=codec_name,sample_rate,channels,bits_per_raw_sample,duration", "-of", "json", path])
    return json.loads(r.stdout).get("streams") or []


def read_samples(video, raw):
    """動画の最初の音声の流れを、モノラル 48kHz の 16bit の PCM (raw に書く) にして array('h') で返す。
    入れ物の時刻で音声が遅れて始まるときも、その分を無音で埋めて、サンプル 0 を動画の 0 秒にそろえる (aresample の first_pts=0)。"""
    C.run_tool("ffmpeg", ["-i", video, "-map", "0:a:0", "-ac", "1", "-af", f"aresample={RATE}:async=1:first_pts=0",
                          "-f", "s16le", "-c:a", "pcm_s16le", raw])
    a = array.array("h")
    with open(raw, "rb") as f:
        a.frombytes(f.read())
    if sys.byteorder != "little":
        a.byteswap()
    return a


def sound_spans(samples, rate, fps, threshold=THRESHOLD):
    """サンプルの列から音の区間を出す。返す値は
    loud: フレームごとに、そのフレームのサンプルの絶対値の最大が閾値以上か (1 フレームは rate / fps サンプル)
    runs: 閾値以上のサンプルがつながった区間 [{start, end (秒、end は最後のサンプルの次), peak (最大を 1 とした割合)}]。
          1 フレームより短い隙間 (波が 0 を横切る所など) はつなげる"""
    block = int(round(rate / fps))
    limit = threshold * 32768
    loud = [max((abs(x) for x in samples[i:i + block]), default=0) >= limit for i in range(0, len(samples), block)]
    runs, cur = [], None
    for i, x in enumerate(samples):
        if abs(x) < limit:
            continue
        if cur and i - cur["last"] <= block:
            cur["last"], cur["peak"] = i, max(cur["peak"], abs(x))
        else:
            cur = {"first": i, "last": i, "peak": abs(x)}
            runs.append(cur)
    return {"loud": loud, "runs": [{"start": r["first"] / rate, "end": (r["last"] + 1) / rate,
                                    "peak": round(r["peak"] / 32768, 4)} for r in runs]}


def judge_spans(spans, want, fps):
    """sound_spans の結果を期待の区間 want [(最初のフレーム, 終わりのフレーム (含まない))] と比べる。返す値は
    times: 区間の数が同じで、各区間の始まりと終わりが期待から 1 フレーム未満か
    inside: 期待の区間の中のフレームが全部「音あり」か (途切れない)
    outside: 期待の区間の前後 1 フレームを除いた外のフレームが全部「音なし」か"""
    loud, runs = spans["loud"], spans["runs"]
    times = len(runs) == len(want) and all(abs(r["start"] - a / fps) < 1 / fps and abs(r["end"] - b / fps) < 1 / fps
                                           for r, (a, b) in zip(runs, want))
    inside = [f for a, b in want for f in range(a, b) if not (f < len(loud) and loud[f])]
    near = {f for a, b in want for f in range(a - 1, b + 1)}
    outside = [f for f, x in enumerate(loud) if x and f not in near]
    return {"times": times, "inside": not inside, "outside": not outside, "quiet_inside": inside[:20],
            "loud_outside": outside[:20]}


def num(v):
    """GetClipProperty の文字の値から数を取り出す ('48000' など)。取り出せなければ None。"""
    digits = "".join(ch for ch in str(v or "") if ch.isdigit() or ch == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def sample_rate(v):
    """GetClipProperty の Sample Rate を Hz に ('48000' も '48 kHz' も 48000)。読めなければ None。"""
    n = num(v)
    return n * 1000 if n is not None and "khz" in str(v).lower() else n


if __name__ == "__main__":
    t_all = time.time()
    cfg = C.config()
    look, _ = cfg.storyboard_values()
    W, H, FPS = look["timeline"]["width"], look["timeline"]["height"], int(look["fps"])
    OUT, DIR = cfg.out_dir("tests"), cfg.work_dir(NAME)
    checks, info, created = [], {"wav": {}, "clips": {}, "placed": {}}, []
    check = make_check(checks)
    path = os.path.join(OUT, f"{NAME}.json")
    want = [(rec, rec + int(round(sec * FPS))) for _, _, sec, rec in TONES]   # タイムラインの頭を 0 としたフレーム
    info["expected"] = {"frames": want, "seconds": [[a / FPS, b / FPS] for a, b in want], "fps": FPS}

    # Resolve を使わない準備 (素材と WAV) はロックを確認する前に済ませる
    src = make_source(cfg, W, H, FPS)
    wavs = {}
    for key, freq, sec, _ in TONES:
        wp = os.path.join(DIR, f"{NAME}-{key}.wav")
        make_wav(wp, freq, sec)
        wavs[key] = wp
        st = (audio_streams(wp) or [{}])[0]
        info["wav"][key] = st
        dur = float(st.get("duration") or 0)
        check(f"{key}: 合成の WAV の長さとサンプルレートが作ったとおり",
              abs(dur - sec) < 1 / RATE and str(st.get("sample_rate")) == str(RATE),
              {"duration": dur, "sample_rate": st.get("sample_rate")}, {"duration": sec, "sample_rate": RATE})
    src_audio = len(audio_streams(src))
    info["source"] = C.probe_video(src) | {"path": src, "audio_streams": src_audio}
    info["source_audio_streams"] = src_audio

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
        pname = f"test-{NAME}-{int(time.time())}"
        proj = pm.CreateProject(pname)
        if not proj:
            raise RuntimeError(f"プロジェクト {pname} を作れない")
        created.append(pname)
        info["project"] = pname
        proj.SetSettings({"timelineFrameRate": str(FPS), "timelineResolutionWidth": str(W),
                          "timelineResolutionHeight": str(H)})
        mp = proj.GetMediaPool()

        # 映像を V1 の頭に置く (mediaType なし。素材に音があれば A1 に入る)
        vclip = (mp.ImportMedia([src]) or [None])[0]
        tl = mp.CreateEmptyTimeline(f"{NAME}-{int(time.time() * 1000) % 100000}")
        proj.SetCurrentTimeline(tl)
        t0 = tl.GetStartFrame()
        vframes = int(round(info["source"]["duration"] * FPS))
        mp.AppendToTimeline([{"mediaPoolItem": vclip, "startFrame": 0, "endFrame": vframes, "trackIndex": 1,
                              "recordFrame": t0}])

        def video_spans():
            return [(i.GetStart() - t0, i.GetEnd() - t0) for i in (tl.GetItemListInTrack("video", 1) or [])]

        def a1_count():
            return len(tl.GetItemListInTrack("audio", 1) or [])

        v_before, a1_before = video_spans(), a1_count()
        info["timeline"] = {"start": t0, "video_before": v_before, "a1_before": a1_before,
                            "audio_tracks_before": tl.GetTrackCount("audio")}
        check("映像を V1 の頭に置ける", v_before == [(0, vframes)], v_before, [(0, vframes)])
        check("素材の音声の数と A1 のクリップの数が合う (素材が無音なら A1 は空)", a1_before == src_audio,
              {"a1_items": a1_before, "source_audio_streams": src_audio}, src_audio)

        # WAV を読み込み、メディアプールのクリップの属性を読む
        got = mp.ImportMedia(list(wavs.values())) or []
        by_name = {c.GetClipProperty("File Name"): c for c in got}
        clips = {}
        for key, _, sec, _ in TONES:
            c = by_name.get(os.path.basename(wavs[key]))
            clips[key] = c
            props = {k: c.GetClipProperty(k) for k in ("Sample Rate", "Duration", "Frames", "Audio Ch", "Audio Bit Depth",
                                                         "Audio Codec", "FPS")} if c else None
            info["clips"][key] = props
            n = int(round(sec * FPS))
            if not props:
                ok = False
            elif sample_rate(props["Sample Rate"]) is None or not (props["Duration"] or props["Frames"]):
                ok = None
            else:
                ok = sample_rate(props["Sample Rate"]) == RATE and (props["Duration"] == C.frame_to_timecode(n, FPS) or num(props["Frames"]) == n)
            check(f"{key}: 読み込め、クリップの属性 (Sample Rate・Duration か Frames) が WAV と合う", ok, props,
                  {"Sample Rate": RATE, "Duration": C.frame_to_timecode(n, FPS), "Frames": n})

        # A2 を用意する (無い番号の trackIndex はトラック 1 に置かれるため)
        added = []
        while tl.GetTrackCount("audio") < 2:
            r = bool(tl.AddTrack("audio", "stereo"))
            if not r:   # subTrackType の値は .pyi に一覧が無い。断られたら付けずに 1 回だけ
                r = bool(tl.AddTrack("audio"))
            added.append(r)
            if not r:
                break
        n_audio = tl.GetTrackCount("audio")
        info["timeline"].update(add_track=added, audio_tracks=n_audio,
                                a2_subtype=tl.GetTrackSubType("audio", 2) if n_audio >= 2 else None)
        check("音声トラック 2 (A2) がある", n_audio >= 2, {"audio_tracks": n_audio, "add_track": added}, ">= 2")

        # WAV を A2 に置く (1 本ずつ)
        for key, _, sec, rec in TONES:
            c, n = clips[key], int(round(sec * FPS))
            placed = []
            if c and n_audio >= 2:
                placed = mp.AppendToTimeline([{"mediaPoolItem": c, "startFrame": 0, "endFrame": n, "mediaType": 2,
                                               "trackIndex": 2, "recordFrame": t0 + rec}]) or []
            tt = [g.GetTrackTypeAndIndex() for g in placed]
            info["placed"][key] = {"returned": len(placed), "track": tt}
            check(f"{key}: AppendToTimeline の戻り値が 1 件で A2", tt == [["audio", 2]], tt, [["audio", 2]])

        # 読み戻し
        items = tl.GetItemListInTrack("audio", 2) or []
        got_spans = [{"start": i.GetStart() - t0, "end": i.GetEnd() - t0, "duration": i.GetDuration(),
                      "name": i.GetName(), "id": i.GetUniqueId(),
                      "linked": [x.GetTrackTypeAndIndex() for x in (i.GetLinkedItems() or [])]} for i in items]
        info["a2_items"] = got_spans
        want_spans = [{"start": a, "end": b, "duration": b - a} for a, b in want]
        check("A2 のクリップが 2 件で、GetStart・GetEnd・GetDuration が期待どおり",
              [{k: s[k] for k in ("start", "end", "duration")} for s in got_spans] == want_spans, got_spans, want_spans)
        check("A2 のクリップは別々で、映像とつながっていない",
              len(got_spans) == 2 and len({s["id"] for s in got_spans}) == 2 and not any(s["linked"] for s in got_spans),
              [(s["id"], s["linked"]) for s in got_spans], "2 件、つながり無し")
        v_after, a1_after = video_spans(), a1_count()
        info["timeline"].update(video_after=v_after, a1_after=a1_after, end=tl.GetEndFrame())
        check("音声を置いた後も V1 の位置と A1 のクリップの数が変わらない", v_after == v_before and a1_after == a1_before,
              {"video": v_after, "a1": a1_after}, {"video": v_before, "a1": a1_before})

        # 音声付きで全編を書き出す
        rd = look["render"]
        base = {"SelectAllFrames": True, "TargetDir": DIR, "CustomName": f"{NAME}-all", "ExportVideo": True,
                "ExportAudio": True, "AudioSampleRate": RATE}
        limit = max(600, vframes / float(FPS) * 20)
        rr = render_job(proj, base | {"AudioCodec": "aac", "AudioBitDepth": 16}, rd["format"], rd["codec"], limit)
        tried = [{"settings": rr["settings"], "set_settings": rr["set_settings"], "done": rr["done"]}]
        if not rr["set_settings"]:
            rr = render_job(proj, base, rd["format"], rd["codec"], limit)
            tried.append({"settings": rr["settings"], "set_settings": rr["set_settings"], "done": rr["done"]})
        info["render"] = rr | {"tried": tried}
        entry = rr.get("job_entry") or {}
        written = rr["done"] and rr.get("written")
        streams = audio_streams(rr["path"]) if written else []
        info["render_audio"] = streams
        check("音声付きで全編を書き出せる (IsExportAudio が真、音声の流れがある)",
              bool(written and entry.get("IsExportAudio") and streams),
              {"status": rr.get("status"), "IsExportAudio": entry.get("IsExportAudio"), "audio_streams": streams})

        # 書き出しの音を測る
        res = None
        if written and streams:
            samples = read_samples(rr["path"], os.path.join(DIR, f"{NAME}-all.s16le"))
            spans = sound_spans(samples, RATE, FPS)
            res = judge_spans(spans, want, FPS)
            info["measure"] = {"runs": spans["runs"], "frames": len(spans["loud"]), "threshold": THRESHOLD} | res
        exp_sec = info["expected"]["seconds"]
        check("書き出しの音の区間が 2 つで、始まりと終わりが期待から 1 フレーム未満", res["times"] if res else None,
              res and info["measure"]["runs"], exp_sec)
        check("期待の区間の中のフレームは全部音あり (途切れない)", res["inside"] if res else None,
              res and res["quiet_inside"], [])
        check("期待の区間の前後 1 フレームを除いた外のフレームは全部音なし", res["outside"] if res else None,
              res and res["loud_outside"], [])
    except Exception as e:
        import traceback
        info["error"] = traceback.format_exc()[-2000:]
        check("途中で例外が出ない", False, str(e))
    finally:
        info["seconds"] = round(time.time() - t_all, 1)
        code = finish_projects(resolve, NAME, path, created, prev, info, checks, min_checks=17)
    sys.exit(code)
