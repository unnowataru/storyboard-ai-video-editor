"""FFmpeg と、動画のフレームを番号で取り出す FrameReader (scripts/common.py から読み込み直す部品)。

ffmpeg / ffprobe を低い優先度で動かす (run_tool・ffmpeg_popen・stop_ffmpeg)、動画の幅・高さ・フレームレート・長さを測る (probe_video・video_info)、
YUV から RGB への式の決め方 (rgb_read)、番号を指定したフレームの取り出し (FrameReader・frame_reader)。
"""
import json
import os

from settings import config, path_key


# ---------- FFmpeg ----------
# Windows では低い優先度 (BELOW_NORMAL_PRIORITY_CLASS) で動かす。人が同じ PC で別の作業をしていても重くしない
LOW_PRIORITY = 0x00004000 if os.name == "nt" else 0


def run_tool(name, args):
    """ffmpeg / ffprobe を低い優先度で動かす。引数はリストで渡す (シェルを通さない)。失敗したら RuntimeError。"""
    import subprocess
    cmd = [config().tool(name), "-hide_banner", "-loglevel", "error"] + (["-y"] if name == "ffmpeg" else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=LOW_PRIORITY)
    if r.returncode != 0:
        raise RuntimeError("失敗: " + " ".join(cmd) + "\n" + r.stderr)
    return r


def probe_video(path, count_frames=False):
    """動画の幅・高さ・フレームレート・長さ (秒) を ffprobe で測る {"width", "height", "fps", "duration"}。
    count_frames=True ならパケットを数えてフレーム数 ("frames"。-count_packets の nb_read_packets) も返す
    (全部のパケットを読むので時間がかかる。check_final.py が書き出した動画を測るときだけ)。
    count_frames=True のときは長さを使わないので、入れ物に長さが無い動画でも止めない ("duration" は None)。
    組む側 (sb2resolve.probe_sources)・照らし合わせ (check_final.probe)・FCPXML (sb2nle.probe) はここで測る。"""
    entries = "stream=width,height,r_frame_rate" + (",nb_read_packets" if count_frames else "") + ":format=duration"
    r = run_tool("ffprobe", (["-count_packets"] if count_frames else []) + ["-select_streams", "v:0", "-show_entries",
                                                                             entries, "-of", "json", path])
    return video_info(json.loads(r.stdout), count_frames)


def video_info(j, count_frames=False):
    """ffprobe の JSON (-select_streams v:0 の stream=width,height,r_frame_rate と format=duration) から、
    probe_video と同じ dict を作る。count_frames=True なら stream の nb_read_packets も要る。
    同じ ffprobe で別の項目も読む関数 (area_sheet.probe) が、値の読み方を probe_video と 1 か所にそろえるために使う。"""
    st = j["streams"][0]
    n, d = st["r_frame_rate"].split("/")
    dur = (j.get("format") or {}).get("duration")
    if count_frames and dur in (None, "N/A"):
        dur = None
    else:
        dur = float(dur)
    out = {"width": int(st["width"]), "height": int(st["height"]), "fps": float(n) / float(d), "duration": dur}
    if count_frames:
        out["frames"] = int(st["nb_read_packets"])
    return out


# ---------- 動画のフレームを番号で取り出す ----------
# フレームの番号は、先頭から表示の順に数えた番号 (0 始まり)。FFmpeg の select=eq(n,番号) の n と同じ。
# 番号を時刻に直すときは、計算 (番号 / fps) ではなく動画のパケットの時刻 (pts) を並べた表を使い、
# 取り出したフレームの時刻が求めた時刻と同じかを showinfo で確認する。合わなければ select=eq(n,...) に戻す。
CFR_TOL = 0.25      # フレームの間隔のずれの許容 (1 フレームに対する割合)。これを超える動画は一定のフレームレートとみなさない

# ffprobe の color_space・color_primaries の値 → FFmpeg の scale の in_color_matrix (YUV から RGB への式)
COLOR_MATRIX = {"bt709": "bt709", "smpte170m": "smpte170m", "bt470bg": "bt470bg", "fcc": "fcc",
                "smpte240m": "smpte240m", "bt2020nc": "bt2020", "bt2020c": "bt2020"}
PRIMARIES_MATRIX = {"bt709": "bt709", "smpte170m": "smpte170m", "bt470bg": "bt470bg", "smpte240m": "smpte240m",
                    "bt2020": "bt2020"}
HD_HEIGHT = 720     # 色の情報の無い素材を BT.709 で読む高さ (これ以上)。Resolve はこの大きさの素材を BT.709 として扱う
# テストの合成の素材を BT.709 で作る FFmpeg の指定 (入口を rgb24 にそろえ、RGB から YUV への式も色の情報も bt709、範囲は tv)。
# -color_* だけでは primaries と trc が付かない (FFmpeg 9.0.2 の実測) ので、setparams と両方を使う。前に追加するフィルターは「,」でつなぐ
BT709_VF = ("format=rgb24,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
            "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv")
BT709_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


def rgb_read(stream):
    """動画を rgb24 に直すときの読み方。stream は ffprobe の v:0 (height・color_space・color_range・color_primaries)。
    式は color_space があればそれ、無ければ color_primaries から、どちらも無ければ高さ HD_HEIGHT 以上は BT.709、
    それより小さいものは BT.601。範囲は color_range があればそれ、無ければ tv。color_space が gbr (RGB のまま) なら式は無し。
    読み分けないもの: color_primaries だけのときは原色から式を推定しただけ。高さ 720 未満で情報の無いものは BT.601 のまま。
    bt2020c は bt2020 (非定輝度) の式で読み、HDR の伝達特性 (smpte2084・arib-std-b67) は見ない (トーンマップしない)。
    返す値は {"matrix", "range", "by", "filter"}。by はどの情報で決めたか、filter は ffmpeg の -vf の後ろに追加する指定。"""
    cs, cp, cr = (stream.get(k) or "unknown" for k in ("color_space", "color_primaries", "color_range"))
    if cs == "gbr":
        matrix, by = None, "color_space"
    elif cs in COLOR_MATRIX:
        matrix, by = COLOR_MATRIX[cs], "color_space"
    elif cp in PRIMARIES_MATRIX:
        matrix, by = PRIMARIES_MATRIX[cp], "color_primaries"
    elif int(stream.get("height") or 0) >= HD_HEIGHT:
        matrix, by = "bt709", f"色の情報が無く高さ {HD_HEIGHT} 以上"
    else:
        matrix, by = "bt601", f"色の情報が無く高さ {HD_HEIGHT} 未満"
    rng = cr if cr in ("tv", "pc") else "tv"
    by += "" if cr in ("tv", "pc") else "、範囲は情報が無いので tv"
    scale = f"scale=in_color_matrix={matrix}:in_range={rng}:out_range=pc," if matrix else ""
    return {"matrix": matrix, "range": rng, "by": by, "filter": scale + "format=rgb24"}


def _rate(s):
    try:
        n, d = (s or "0/0").split("/")
        return float(n) / float(d) if float(d) else 0.0
    except ValueError:
        return 0.0


def _any_of(terms):
    """select に渡す「どれかに当たる」式。項を + で 1 列につなぐと、FFmpeg の式の読み込みが入れ子の深さの上限
    (約 100) で失敗する (100 フレームで Error initializing filters)。2 つずつ括弧でまとめ、深さを項の数の log にする。"""
    if len(terms) == 1:
        return terms[0]
    mid = len(terms) // 2
    return f"({_any_of(terms[:mid])}+{_any_of(terms[mid:])})"


def ffmpeg_popen(args, loglevel="error", stderr=False):
    """ffmpeg を低い優先度で動かし、標準出力を読む Popen を返す。stderr=True なら標準エラーも読める。"""
    import subprocess
    cmd = [config().tool("ffmpeg"), "-hide_banner", "-nostats", "-loglevel", loglevel] + list(args)
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE if stderr else subprocess.DEVNULL,
                            creationflags=LOW_PRIORITY)


def stop_ffmpeg(p):
    """読み終えた (または途中でやめた) FFmpeg を止め、パイプを閉じる。"""
    if p.poll() is None:
        p.kill()
    for f in (p.stdout, p.stderr):
        if f:
            f.close()
    p.wait()


class FrameReader:
    """1 本の動画から、番号を指定したフレームを RGB の配列 (高さ, 幅, 3) で取り出す。

    近い番号を群にまとめ、群ごとに 1 回、群の最初のフレームより前のキーフレームへ入力側でシーク (-ss) して、
    群の最後のフレームまでだけデコードする。次の動画は、番号と時刻の対応を保証できないので、先頭からデコードして
    select=eq(n,...) で選ぶ (前からの方式):
    フレームレートが読めない、フレームの間隔が一定でない (VFR)、pts の無いパケットや捨てることを示すフラグ (D) のパケットがある、
    最初のパケットがキーフレームでない。シークで取り出したフレームの時刻が求めた時刻と違ったときは、その群の残りを前からの方式で取り直す。
    YUV から RGB への式と範囲は、動画の色の情報から rgb_read で決める (index の color に残す)。
    FFmpeg の出力は 1 フレームずつ読んで渡すので、一度に持つのは 1 フレームと、覚えておく分 (frame_read.cache_mb まで) だけ。
    覚えたフレームは、同じ番号を求められたら FFmpeg を動かさずに返す。
    stats に FFmpeg を動かした回数、デコードしたフレームの数の見込み、前からの方式に戻った理由を残す (計測用)。
    """

    def __init__(self, video, group_gap=None, cache_mb=None):
        fr = config().section("frame_read")
        self.video = video
        self.group_gap = float(fr["group_gap_seconds"] if group_gap is None else group_gap)
        self.cache_limit = float(fr["cache_mb"] if cache_mb is None else cache_mb) * 1024 * 1024
        self.cache = {}
        self.stats = {"ffmpeg_runs": 0, "decoded_estimate": 0, "seek_groups": 0, "fallback": None}
        self.index_table = None

    # --- 番号と時刻の表 ---
    def index(self):
        """パケットの表を読み、番号から時刻を引く表を作る。前からの方式に戻すときは fallback に理由が入る。"""
        if self.index_table is not None:
            return self.index_table
        ix = {"fallback": None}
        try:
            r = run_tool("ffprobe", ["-select_streams", "v:0", "-show_entries",
                                     "stream=width,height,r_frame_rate,time_base,color_space,color_range,color_primaries"
                                     ":format=start_time:packet=pts,dts,flags",
                                     "-of", "json", self.video])
            j = json.loads(r.stdout)
            st = j["streams"][0]
            ix["width"], ix["height"] = int(st["width"]), int(st["height"])
            ix["color"] = rgb_read(st)
        except (RuntimeError, KeyError, IndexError, ValueError) as e:
            ix["fallback"] = "ffprobe で読めない: " + str(e)[-200:]
            ix["width"] = ix["height"] = None
            self.index_table = ix
            return ix
        fps, tb = _rate(st.get("r_frame_rate")), _rate(st.get("time_base"))
        ix["fps"] = fps
        pk = j.get("packets") or []
        ix["frames"] = len(pk)
        why = None
        if fps <= 0 or tb <= 0:
            why = "フレームレートか time_base が読めない"
        elif not pk:
            why = "パケットが無い"
        elif any("pts" not in p or "dts" not in p for p in pk):
            why = "pts か dts の無いパケットがある"
        elif any("D" in p.get("flags", "") for p in pk):
            why = "捨てることを示すフラグ (D) のパケットがある"
        elif "K" not in pk[0].get("flags", ""):
            why = "最初のパケットがキーフレームでない"
        if why is None:
            pts = [int(p["pts"]) * tb for p in pk]
            order = sorted(range(len(pk)), key=lambda i: pts[i])   # 表示の順の番号 → パケットの順 (デコードの順)
            step = 1 / fps
            if any(abs(pts[b] - pts[a] - step) > step * CFR_TOL for a, b in zip(order, order[1:])):
                why = "フレームの間隔が一定でない (VFR か、フレームレートの表示と合わない)"
        if why:
            ix["fallback"] = why
        else:
            ix["pts"] = [pts[i] for i in order]                      # 番号 → 時刻 (秒、ファイルの時刻のまま)
            ix["pos"] = order                                         # 番号 → パケットの順
            ix["keys"] = [i for i, p in enumerate(pk) if "K" in p.get("flags", "")]
            ix["kpts"] = [pts[i] for i in ix["keys"]]
            ix["kdts"] = [int(pk[i]["dts"]) * tb for i in ix["keys"]]
            try:
                ix["start"] = float(j.get("format", {}).get("start_time", 0) or 0)
            except ValueError:
                ix["start"] = 0.0
        self.index_table = ix
        return ix

    # --- 取り出し ---
    def read(self, frames, keep=True):
        """frames の各番号のフレームを {番号: 配列 または None} で返す。None は動画に無い番号か、取り出せなかったもの。
        返す dict に全部のフレームが載るので、大きな動画で多くのフレームを見るときは each を使う。"""
        return dict(self.each(frames, keep))

    def each(self, frames, keep=True):
        """frames の各番号 (重なりを除く) について (番号, 配列 または None) を、取り出せた順に 1 つずつ返す。
        覚えているフレームを先に返す。keep=True なら新しく取り出したフレームを frame_read.cache_mb まで覚える。"""
        want = sorted(set(int(n) for n in frames))
        new = []
        for n in want:
            if n in self.cache:
                yield n, self.cache[n]
            else:
                new.append(n)
        for n, img in self._extract(new):
            if keep and img is not None and self._cache_bytes() + img.nbytes <= self.cache_limit:
                self.cache[n] = img
            yield n, img

    def _cache_bytes(self):
        return sum(v.nbytes for v in self.cache.values())

    def _extract(self, targets):
        for n in targets:   # 負の番号のフレームは無い。旧方式に渡すと、出てきたフレームを前から当てるときにずれる
            if n < 0:
                yield n, None
        targets = [n for n in targets if n >= 0]
        if not targets:
            return
        ix = self.index()
        if ix["width"] is None:
            for n in targets:
                yield n, None
            return
        if ix["fallback"]:
            self.stats["fallback"] = ix["fallback"]
            yield from self._by_select(targets)
            return
        for n in targets:
            if not 0 <= n < len(ix["pts"]):
                yield n, None
        inside = [n for n in targets if 0 <= n < len(ix["pts"])]
        for g in self._groups(inside):
            yield from self._by_seek(g)

    def _key_for(self, n):
        """番号 n のフレームを正しくデコードできる、いちばん近いキーフレームの位置 (keys の添え字)。"""
        import bisect
        ix = self.index()
        k = max(0, bisect.bisect_right(ix["keys"], ix["pos"][n]) - 1)
        if ix["kpts"][k] > ix["pts"][n] and k > 0:   # キーフレームより前に表示するフレーム (開いた GOP) は 1 つ前から
            k -= 1
        return k

    def _groups(self, targets):
        """近い番号を群にまとめる。次の番号のキーフレームまで、前の番号から group_gap 秒より離れていれば新しい群にする。"""
        ix = self.index()
        groups = []
        for n in targets:
            k = self._key_for(n)
            if groups and (ix["kdts"][k] - ix["pts"][groups[-1][-1][0]] <= self.group_gap or k <= groups[-1][0][1]):
                groups[-1].append((n, k))
            else:
                groups.append([(n, k)])
        return [[(n, min(k for _, k in g)) for n, _ in g] for g in groups]

    def _by_seek(self, group):
        """群の最初のキーフレームへシークし、群の番号のフレームを時刻で選んで 1 つずつ返す。
        各フレームの時刻を showinfo の行で確認し、合わない (または出てこない) ところから後ろは前からの方式で取り直す。"""
        import queue
        import re
        import threading
        import numpy as np
        ix = self.index()
        W, H, fps = ix["width"], ix["height"], ix["fps"]
        k = group[0][1]
        ns = [n for n, _ in group]
        times = [ix["pts"][n] for n in ns]
        half = 0.5 / fps
        ss = ix["kdts"][k] - ix["start"] - half   # キーフレームの dts の半フレーム前 (シークはその前のキーフレームに着く)
        sel = _any_of([f"lt(abs(t-{t:.9f})\\,{half:.9f})" for t in times])
        args = (["-ss", f"{ss:.6f}", "-noaccurate_seek"] if ss > 0 else []) + [
            "-copyts", "-i", self.video, "-map", "0:v:0", "-vf", f"select='{sel}',showinfo,{ix['color']['filter']}",
            "-fps_mode", "passthrough", "-frames:v", str(len(ns)), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.stats["ffmpeg_runs"] += 1
        self.stats["seek_groups"] += 1
        self.stats["decoded_estimate"] += max(ix["pos"][n] for n in ns) - ix["keys"][k] + 1
        p = ffmpeg_popen(args, "info", stderr=True)
        seen = queue.Queue()
        pat = re.compile(rb"Parsed_showinfo.*?\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:(-?[\d.]+(?:e[-+]?\d+)?)")

        def pump():   # 標準エラーを読み続ける (止めるとパイプが詰まる)。showinfo の時刻だけを渡す
            try:
                for line in p.stderr:
                    m = pat.search(line)
                    if m:
                        seen.put(float(m.group(1)))
            except (ValueError, OSError):   # 途中でやめて閉じたとき
                pass
            seen.put(None)
        threading.Thread(target=pump, daemon=True).start()
        size, done, why = W * H * 3, 0, None
        try:
            for n, want in zip(ns, times):
                raw = p.stdout.read(size)
                if len(raw) != size:
                    why = "フレームが足りない"
                    break
                try:   # showinfo の行はフレームを書き出す前に出るので、ここでは待たずに届いている
                    t = seen.get(timeout=30)
                except queue.Empty:
                    t = None
                if t is None or abs(t - want) >= half:
                    why = f"時刻 {t} が求めた {want:.6f} と違う"
                    break
                yield n, np.frombuffer(raw, np.uint8).reshape(H, W, 3)
                done += 1
        finally:
            stop_ffmpeg(p)
        if done < len(ns):
            self.stats["fallback"] = f"シークで取り出したフレームが合わない (番号 {ns[done]}〜{ns[-1]}、{why})"
            yield from self._by_select(ns[done:])

    def _by_select(self, targets):
        """前からの方式: 先頭からデコードし、select=eq(n,...) で選んで 1 つずつ返す。
        動画に無い番号は後ろから欠けるので、出た数だけ前から当て、残りは None。"""
        import numpy as np
        ix = self.index()
        W, H = ix["width"], ix["height"]
        sel = _any_of([f"eq(n\\,{n})" for n in targets])
        self.stats["ffmpeg_runs"] += 1
        self.stats["decoded_estimate"] += max(targets) + 1
        p = ffmpeg_popen(["-i", self.video, "-map", "0:v:0", "-vf", f"select='{sel}',{ix['color']['filter']}",
                           "-fps_mode", "passthrough",
                           "-frames:v", str(len(targets)), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        size, ended = W * H * 3, False
        try:
            for n in targets:
                raw = b"" if ended else p.stdout.read(size)
                ended = len(raw) != size
                yield n, (None if ended else np.frombuffer(raw, np.uint8).reshape(H, W, 3))
        finally:
            stop_ffmpeg(p)


_READERS = {}
READERS_KEPT = 4    # 覚えておく動画の数 (書き出しと素材を交互に読むため)。覚えたフレームは最後に使った 1 本の分だけ残す


def frame_reader(video):
    """video の FrameReader。同じファイル (パス・大きさ・更新時刻が同じ) なら、前に作ったものを返す (番号と時刻の表と、覚えたフレームを使い回す)。"""
    p = os.path.abspath(video)
    try:
        s = os.stat(p)
        key = (path_key(p), s.st_size, s.st_mtime_ns)
    except OSError:
        key = (path_key(p), None, None)
    r = _READERS.pop(key, None) or FrameReader(p)
    for other in _READERS.values():
        other.cache.clear()   # 覚えたフレームは最後に使った 1 本の分だけ (frame_read.cache_mb を超えないように)
    _READERS[key] = r
    while len(_READERS) > READERS_KEPT:
        _READERS.pop(next(iter(_READERS)))
    return r
