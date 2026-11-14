"""common.FrameReader (番号を指定したフレームの取り出し) のテスト。

合成した動画 (長い GOP・B フレームあり、30fps と 29.97fps) で、シークで取り出したフレームが、
前からの方式 (ffmpeg -i 動画 -vf select=eq(n,番号) -frames:v 1) と同じ画素になることを確認する。
動画は作業フォルダーの offline-tests\\run-<番号>\\frame_read (実行ごとに別の名前。conftest の offline_work) に作って消す。FFmpeg が無ければ飛ばす。
"""
import os
import shutil
import subprocess

import pytest

import common as C

np = pytest.importorskip("numpy")

W, H = 320, 240
TOTAL = 600       # 20 秒 (30fps)
CUT = 150         # この番号から画が変わる (カットの境目)


def _ffmpeg():
    try:
        return C.Config().tool("ffmpeg")
    except SystemExit:
        return None


pytestmark = pytest.mark.skipif(_ffmpeg() is None, reason="FFmpeg が無い")


@pytest.fixture(scope="module")
def work(offline_work):
    d = os.path.join(offline_work, "frame_read")
    os.makedirs(d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def make(work, name, rate, x264="keyint=250:min-keyint=250:scenecut=0:bframes=3:b-pyramid=normal", vf=None, extra=()):
    """testsrc2 (フレームごとに画が変わる) に、CUT から白い四角を重ねた動画。長い GOP と B フレームで符号化する。"""
    out = os.path.join(work, name)
    vf = vf or f"drawbox=x=40:y=40:w=120:h=90:color=white:t=fill:enable='gte(n,{CUT})'"
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"testsrc2=size={W}x{H}:rate={rate}", "-frames:v", str(TOTAL), "-vf", vf,
                    "-c:v", "libx264", "-preset", "veryfast", "-x264-params", x264, "-pix_fmt", "yuv420p",
                    *extra, out], check=True)
    return out


def old(video, n):
    """前からの方式 (この変更の前の check_final.read_frame と同じ指定)。"""
    raw = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", video, "-vf", f"select=eq(n\\,{n})",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3) if len(raw) == W * H * 3 else None


# 冒頭・カットの境目の前後・中央・GOP の境目・終端の近く
TARGETS = [0, 1, 2, CUT - 1, CUT, CUT + 1, 249, 250, 251, 299, 300, 301, 450, TOTAL - 3, TOTAL - 2, TOTAL - 1]


def same_as_old(video, targets, reader=None):
    r = reader or C.FrameReader(video)
    got = r.read(targets)
    for n in targets:
        want = old(video, n)
        assert want is not None, n
        assert got[n] is not None and np.array_equal(got[n], want), f"{n} フレーム目が前からの方式と違う"
    return r


@pytest.mark.parametrize("rate", ["30", "30000/1001"])
def test_seek_matches_select(work, rate):
    video = make(work, f"gop-{rate.replace('/', '_')}.mp4", rate)
    r = same_as_old(video, TARGETS, C.FrameReader(video, group_gap=0))   # 0 秒: キーフレームを飛ばせるなら別の群にする
    assert r.index()["fallback"] is None and r.stats["fallback"] is None, r.stats
    assert r.stats["seek_groups"] == r.stats["ffmpeg_runs"] >= 2   # 終端の近くは別の群で、前からの方式には戻っていない
    assert r.stats["decoded_estimate"] < TOTAL * 2
    # 隣り合うフレームは画が違う (同じ画を返していれば、上の比べ方で見逃す)
    assert not np.array_equal(r.cache[CUT - 1], r.cache[CUT]) and not np.array_equal(r.cache[299], r.cache[300])


def test_open_gop_and_single_frames(work):
    """開いた GOP (キーフレームより前に表示するフレームがある) でも、1 つずつ求めても同じ。"""
    video = make(work, "open-gop.mp4", "30",
                 "keyint=120:min-keyint=120:scenecut=0:bframes=3:b-pyramid=normal:open-gop=1")
    r = C.FrameReader(video)
    for n in (118, 119, 120, 121, 239, 240, TOTAL - 1):
        same_as_old(video, [n], r)
    assert r.stats["fallback"] is None, r.stats


def test_cache_and_out_of_range(work):
    video = make(work, "cache.mp4", "30")
    r = C.FrameReader(video)
    first = r.read([CUT, CUT + 1])
    runs = r.stats["ffmpeg_runs"]
    again = r.read([CUT + 1, CUT])
    assert r.stats["ffmpeg_runs"] == runs and again[CUT] is first[CUT]   # 同じフレームは FFmpeg を動かさない
    assert r.read([TOTAL, TOTAL + 5]) == {TOTAL: None, TOTAL + 5: None}
    assert r.stats["ffmpeg_runs"] == runs
    assert C.frame_reader(video) is C.frame_reader(video)


def test_cache_limit_and_streaming(work):
    """覚える量は cache_mb まで。keep=False なら覚えない。each は 1 つずつ返す (途中でやめると FFmpeg も止まる)。"""
    video = make(work, "limit.mp4", "30")
    r = C.FrameReader(video, cache_mb=W * H * 3 * 2.5 / 1024 / 1024)   # 2 フレーム分
    r.read([10, 11, 12, 13])
    assert sorted(r.cache) == [10, 11]
    r.cache.clear()
    assert len(r.read([20, 21, 22], keep=False)) == 3 and r.cache == {}
    it = r.each([30, 31, 32, 33], keep=False)
    assert next(it)[0] == 30
    it.close()
    same_as_old(video, [31], r)


def test_many_frames_in_one_run(work):
    """100 を超えるフレームでも 1 回の FFmpeg で取り出せる (select の式を 1 列につなぐと FFmpeg が読めない)。シークでも前からの方式でも同じ画素。"""
    video = make(work, "many.mp4", "30")
    ns = list(range(100, 280))
    a = C.FrameReader(video, cache_mb=0)
    b = C.FrameReader(video, cache_mb=0)
    b.index_table = dict(b.index(), fallback="テストのため前からの方式")
    got = {n: img[::16, ::16].copy() for n, img in a.each(ns)}
    old = {n: img[::16, ::16].copy() for n, img in b.each(ns)}
    assert len(got) == len(old) == 180 and all(np.array_equal(got[n], old[n]) for n in ns)
    assert a.stats["ffmpeg_runs"] == b.stats["ffmpeg_runs"] == 1 and a.stats["fallback"] is None


def test_mismatch_in_group_falls_back_for_the_rest(work):
    """シークで取り出したフレームの時刻が表と合わなければ、そこから後ろを前からの方式で取り直す (前の分はそのまま使う)。"""
    video = make(work, "mismatch.mp4", "30")
    r = C.FrameReader(video)
    r.index()["pts"][CUT] = 1e6   # 表を壊す: CUT の時刻のフレームは出てこない
    same_as_old(video, [CUT - 1, CUT, CUT + 1], r)
    assert r.stats["seek_groups"] == 1 and r.stats["ffmpeg_runs"] == 2 and "シーク" in r.stats["fallback"]


def test_near_frames_share_one_run(work):
    """近いフレーム (group_gap_seconds の内) は 1 回の FFmpeg で取り出す。"""
    video = make(work, "group.mp4", "30")
    r = C.FrameReader(video, group_gap=2)
    same_as_old(video, [CUT - 1, CUT, CUT + 1, CUT + 30], r)
    assert r.stats["ffmpeg_runs"] == 1


def test_vfr_falls_back_to_select(work):
    """フレームの間隔が一定でない動画は、前からの方式で取り出す。"""
    vf = (f"drawbox=x=40:y=40:w=120:h=90:color=white:t=fill:enable='gte(n,{CUT})',"
          "setpts='if(lt(N,200),N,N*2-200)/(30*TB)'")
    video = make(work, "vfr.mp4", "30", vf=vf, extra=("-fps_mode", "vfr"))
    r = C.FrameReader(video)
    assert r.index()["fallback"]
    same_as_old(video, [0, CUT - 1, CUT, CUT + 1, 199, 200, 201, TOTAL - 1], r)
    assert r.stats["seek_groups"] == 0 and r.stats["ffmpeg_runs"] == 1


@pytest.mark.parametrize("fallback", [False, True])
def test_negative_numbers_are_none(work, fallback):
    """負の番号は None。旧方式に戻ったときも、出てきたフレームを負の番号に当てない。"""
    video = make(work, f"negative-{fallback}.mp4", "30")
    r = C.FrameReader(video)
    if fallback:
        r.index_table = dict(r.index(), fallback="テストのため前からの方式")
    got = r.read([-1, 0, 1])
    assert got[-1] is None
    assert np.array_equal(got[0], old(video, 0)) and np.array_equal(got[1], old(video, 1))


def old_source(video, sec):
    """前からの素材のフレームの読み方 (入力側のシークで sec 秒の 1 フレーム)。"""
    raw = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{sec:.6f}", "-i", video,
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3) if len(raw) == W * H * 3 else None


def test_source_frame_with_late_video_start(work):
    """音声が 0 秒、映像が 1 秒から始まる素材で、秒からのフレームが入力側のシークと同じ (開始時刻からの秒で数える)。"""
    import check_final as CF
    out = os.path.join(work, "late.mp4")
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                    "-itsoffset", "1", "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-map", "0:v", "-map", "1:a", "-t", "6", "-c:v", "libx264", "-preset", "veryfast",
                    "-x264-params", "keyint=60:bframes=3", "-pix_fmt", "yuv420p", "-c:a", "aac", out], check=True)
    ix = C.frame_reader(out).index()
    assert ix["fallback"] is None and ix["pts"][0] - ix["start"] > 0.9, (ix.get("start"), ix.get("pts", [None])[:1])
    secs = [0.0, 0.5, 1.0, 1.5, 2.0, 2.51, 3.0]
    got = dict(CF.each_source_frame(out, secs, W, H))
    for s in secs:
        want = old_source(out, s)
        assert want is not None and got[s] is not None and np.array_equal(got[s], want), f"{s} 秒のフレームが違う"
    assert np.array_equal(got[0.0], got[0.5]) and np.array_equal(got[0.0], got[1.0])   # 映像の始まりより前は先頭のフレーム


# ---------- 色の情報と YUV から RGB への式 ----------
def test_rgb_read_choice():
    """色の情報があればそれに従い、無ければ高さ 720 以上は BT.709、それより小さいものは BT.601。範囲は無ければ tv。"""
    assert C.rgb_read({"height": 2160})["matrix"] == "bt709"
    assert C.rgb_read({"height": 2160, "color_space": "unknown"})["matrix"] == "bt709"
    assert C.rgb_read({"height": 480})["matrix"] == "bt601"
    tagged = C.rgb_read({"height": 2160, "color_space": "smpte170m", "color_range": "pc"})
    assert (tagged["matrix"], tagged["range"], tagged["by"]) == ("smpte170m", "pc", "color_space")
    assert C.rgb_read({"height": 480, "color_primaries": "bt709"})["matrix"] == "bt709"
    assert C.rgb_read({"height": 1080, "color_space": "bt2020nc", "color_range": "tv"})["matrix"] == "bt2020"
    assert C.rgb_read({"height": 1080, "color_space": "gbr"})["filter"] == "format=rgb24"
    assert C.rgb_read({"height": 1080})["filter"] == "scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24"


def green_source(work, name, matrix, tags=()):
    """1280x720 の緑一色 (RGB 0,255,0) を、matrix の式で YUV にして符号化した動画。tags が無ければ色の情報を付けない。"""
    out = os.path.join(work, name)
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=0x00FF00:size=1280x720:rate=30", "-frames:v", "30",
                    "-vf", f"scale=out_color_matrix={matrix}:out_range=tv,format=yuv420p"
                           + ("" if tags else ",setparams=color_primaries=unknown:color_trc=unknown:colorspace=unknown"),
                    "-c:v", "libx264", "-preset", "veryfast", *tags, out], check=True)
    return out


def test_untagged_hd_source_is_read_as_bt709(work):
    """色の情報の無い HD の素材 (BT.709 の式で作ったテストの素材の形) は BT.709 で読み、緑が緑に戻る。BT.601 で読むと 12 以上ずれる。"""
    import check_final as CF
    video = green_source(work, "green-untagged.mp4", "bt709")
    ix = C.frame_reader(video).index()
    assert ix["color"]["matrix"] == "bt709" and "720 以上" in ix["color"]["by"]
    img = dict(CF.each_source_frame(video, [0.5], 1280, 720))[0.5]
    px = img[360, 640].astype(int)
    assert abs(px - [0, 255, 0]).max() <= 4, px.tolist()
    raw = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", video, "-vf",
                          "scale=in_color_matrix=bt601:in_range=tv:out_range=pc,format=rgb24", "-frames:v", "1",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    assert abs(np.frombuffer(raw, np.uint8).reshape(720, 1280, 3)[360, 640].astype(int) - px).max() >= 12


def test_tagged_source_follows_its_color_info(work):
    """色の情報 (BT.601 の smpte170m) のある HD の素材は、高さによらずその式で読む。"""
    video = green_source(work, "green-601.mp4", "bt601", ["-colorspace", "smpte170m", "-color_primaries", "smpte170m",
                                                         "-color_trc", "smpte170m", "-color_range", "tv"])
    r = C.FrameReader(video)
    assert (r.index()["color"]["matrix"], r.index()["color"]["by"]) == ("smpte170m", "color_space")
    px = r.read([5])[5][360, 640].astype(int)
    assert abs(px - [0, 255, 0]).max() <= 4, px.tolist()


def test_bt709_source_args_tag_the_video(work):
    """テストの合成の素材を作る指定 (common.BT709_VF・BT709_TAGS) で、色の情報 bt709 が付き、その情報で読む。"""
    out = os.path.join(work, "tagged-709.mp4")
    subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=1280x720:rate=30", "-frames:v", "5", "-vf", C.BT709_VF, *C.BT709_TAGS,
                    "-c:v", "libx264", "-preset", "veryfast", out], check=True)
    color = C.FrameReader(out).index()["color"]
    assert (color["matrix"], color["range"], color["by"]) == ("bt709", "tv", "color_space")
