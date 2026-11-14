"""Resolve の代わりに呼び出しを受ける部分。構成表を Resolve に組む処理 (sb2resolve.build_and_render) をそのまま流し、
組み立て内容 (タイムラインのクリップの並びと、クリップごとの Fusion の部品のつながり) を JSON にできる形で残す。

簡易版 (sb2ffmpeg.py) のための部品。ルール:
- Resolve 側のコード (sb2resolve.py・fusion.py・layout.py・common) は読み込んで使うだけで、こちらに合わせて書き換えない。
  組み立てのコードが知らない呼び出しをしたら、このモジュールの方を直す
- Resolve 側からこのモジュールを読み込まない (tests/offline/test_sb2ffmpeg.py が確認する)
- 呼び出しの結果は、組み立てのコードが期待する形 (docs/scripting-api.md に書いた 21.1.1 の実機の動き) に合わせる。
  Resolve の細かい動き (書き出しの画素、Fusion の描画) は真似しない

返す値の座標と時刻は Fusion と Resolve の単位のまま (位置は画面を 1 とした値、時刻は合成のフレーム)。
"""
import common as C

# 合成の既定の大きさに従う部品と、描く大きさの入力の名前。実物では UseFrameFormatSettings が 1 で始まり、
# fusion._fit_to_canvas がこれを読む (tests/conftest.py の模擬の Fusion もこれを読み込む)
FRAME_INPUTS = {"Background": ("Width", "Height"), "TextPlus": ("Width", "Height"),
                "RectangleMask": ("MaskWidth", "MaskHeight"), "EllipseMask": ("MaskWidth", "MaskHeight")}


def plain(v):
    """JSON に書く形。Fusion の点 ({1: x, 2: y}) は [x, y]、小数は 6 桁に丸める。"""
    if isinstance(v, dict) and v and all(isinstance(k, int) for k in v):
        return [plain(v[k]) for k in sorted(v)]
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if isinstance(v, float):
        return round(v, 6)
    return v


# ---------- Fusion ----------
class Spline:
    """BezierSpline (時刻と値のキー) と XYPath (X と Y に BezierSpline をつなぐ)。"""

    def __init__(self):
        self.keys, self.links = {}, {}

    def ConnectInput(self, k, src):
        self.links[k] = src
        return True

    def SetInput(self, k, v, t=None):
        if k in self.links:
            self.links[k].keys[t] = v
        else:
            self.keys[t] = v
        return True

    def dump(self):
        if self.links:
            return {"path": {k: s.dump()["keys"] for k, s in sorted(self.links.items())}}
        return {"keys": [[t, plain(v)] for t, v in sorted(self.keys.items(), key=lambda kv: kv[0] or 0)]}


class Out:
    """部品の出力 (Output・Mask など)。入力に入れると、その入力がこの出力につながる。"""

    def __init__(self, tool, name):
        self.tool, self.name = tool, name


class Tool:
    """部品。入力の値は SetInput、つなぐのは入力の名前の属性への代入 (tool.Input = src.Output) か ConnectInput。"""

    def __init__(self, comp, tid, name):
        d = vars(self)
        d.update(ID=tid, comp=comp, name=name, inputs={}, links={})
        if tid in FRAME_INPUTS:
            wk, hk = FRAME_INPUTS[tid]
            self.inputs.update({"UseFrameFormatSettings": 1, wk: comp.width, hk: comp.height})

    def __getattr__(self, name):
        if name[:1].isupper():   # つながった入力か、部品の出力
            return vars(self)["links"].get(name) or Out(self, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name[:1].isupper():
            self.ConnectInput(name, value)
        else:
            vars(self)[name] = value

    def SetAttrs(self, a):
        if "TOOLS_Name" in a:
            vars(self)["name"] = a["TOOLS_Name"]
        return True

    def SetInput(self, k, v, t=None):
        link = self.links.get(k)
        if isinstance(link, Spline):
            link.keys[t] = v
        else:
            self.inputs[k] = v
        return True

    def GetInput(self, k, t=None):
        return self.inputs.get(k)

    def ConnectInput(self, k, src):
        self.links[k] = src
        self.inputs.pop(k, None)
        return True

    def dump(self):
        links = {}
        for k, v in sorted(self.links.items()):
            links[k] = v.dump() if isinstance(v, Spline) else {"tool": v.tool.name, "output": v.name}
        return {"name": self.name, "id": self.ID, "inputs": {k: plain(v) for k, v in sorted(self.inputs.items())},
                "links": links}


class Comp:
    """クリップの Fusion の合成。MediaIn1 と MediaOut1 を持って始まる。大きさは素材の大きさ (実物と同じ)。"""

    def __init__(self, width, height):
        self.width, self.height, self.tools = width, height, []
        for tid in ("MediaIn", "MediaOut"):
            self.AddTool(tid)

    def AddTool(self, tid, x=0, y=0):
        n = 1 + sum(t.ID == tid for t in self.tools)   # 実物と同じく、部品の種類ごとの通し番号の名前で始まる
        self.tools.append(Tool(self, tid, f"{tid}{n}"))
        return self.tools[-1]

    def FindTool(self, name):
        return next((t for t in self.tools if t.name == name), None)

    def GetToolList(self, selected=False):
        return dict(enumerate(self.tools, 1))

    def BezierSpline(self):
        return Spline()

    def XYPath(self):
        return Spline()

    def dump(self):
        return {"size": [self.width, self.height], "tools": [t.dump() for t in self.tools]}


# ---------- タイムライン ----------
class MediaPoolItem:
    """取り込んだ素材。値は ffprobe で測ったもの (sb2resolve.probe_sources)。"""

    def __init__(self, path, info):
        self.path, self.width, self.height, self.fps = path, info["width"], info["height"], float(info["fps"])
        self.frames = round(float(info["duration"]) * self.fps)

    def GetClipProperty(self, k):
        return {"FPS": f"{self.fps:g}", "Resolution": f"{self.width}x{self.height}", "Frames": str(self.frames)}.get(k, "")


class TimelineItem:
    """タイムラインのクリップ。位置はタイムラインの絶対フレーム、素材の範囲は素材のフレーム (終わりは含まない)。"""

    def __init__(self, tl, mpi, start, end, src_start, src_end):
        self.tl, self.mpi, self.start, self.end = tl, mpi, start, end
        self.src_start, self.src_end, self.speed = src_start, src_end, 100.0
        self.markers, self.comp = [], None

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end

    def GetSourceStartFrame(self):
        return self.src_start

    def GetSourceEndFrame(self):
        return self.src_end

    def GetRightOffset(self):
        return max(0, self.mpi.frames - self.src_end)

    def GetSpeed(self):
        return {"Percentage": self.speed}

    def SetSpeed(self, d):
        """RippleTimeline が False なら、タイムラインの長さを保って素材の範囲を伸び縮みさせる (21.1.1 の実機)。
        0 は止めたクリップ: 再生ヘッドがクリップの直後 (以降) なら最後のフレーム、そうでなければ頭のフレームを映す。
        止めた後は GetSourceStartFrame も GetSourceEndFrame もそのフレーム (docs/scripting-api.md)。"""
        pct = float(d["Percentage"])
        if d.get("RippleTimeline", True):
            return False   # 組み立てのコードは False でしか呼ばない。呼ばれたら知らない使い方として失敗にする
        if pct == 0:
            head = self.tl.playhead()
            if head is not None and head >= self.end:
                self.src_start = self.src_end - 1
            self.src_end = self.src_start
        else:
            n = (self.end - self.start) * self.mpi.fps / self.tl.fps * pct / 100
            self.src_end = self.src_start + round(n)
        self.speed = pct
        return True

    def GetLinkedItems(self):
        return []

    def AddMarker(self, frame, color, name, note, duration, custom=""):
        self.markers.append({"frame": frame, "color": color, "name": name, "note": note, "duration": duration,
                             "custom": custom})
        return True

    def AddFusionComp(self):
        self.comp = Comp(self.mpi.width, self.mpi.height)
        return self.comp


class Timeline:
    """タイムライン。頭は 01:00:00:00 (Resolve の既定)。"""

    def __init__(self, name, fps):
        self.name, self.fps, self.items = name, float(fps), []
        self.start = round(self.fps) * 3600
        self.timecode = None

    def GetStartFrame(self):
        return self.start

    def SetCurrentTimecode(self, tc):
        self.timecode = tc
        return True

    def GetCurrentTimecode(self):
        return self.timecode or C.frame_to_timecode(self.start, round(self.fps))

    def playhead(self):
        """再生ヘッドの絶対フレーム。置いていなければ None。"""
        return C.timecode_to_frame(self.timecode, round(self.fps)) if self.timecode else None

    def DeleteClips(self, items, ripple=False):
        self.items = [i for i in self.items if i not in items]
        return True


class MediaPool:
    def __init__(self, info, fps):
        self.info, self.fps, self.items, self.timeline = info, float(fps), {}, None

    def ImportMedia(self, paths):
        p = paths[0]
        if p not in self.info:
            return []   # ffprobe で読めなかった素材 (実物では取り込めないと None か空)
        if p not in self.items:
            self.items[p] = MediaPoolItem(p, self.info[p])
        return [self.items[p]]

    def CreateEmptyTimeline(self, name):
        self.timeline = Timeline(name, self.fps)
        return self.timeline

    def AppendToTimeline(self, infos):
        """素材の範囲 [startFrame, endFrame) を recordFrame に置く。長さは素材とタイムラインのフレームレートで換算する。"""
        tl, out = self.timeline, []
        for d in infos:
            mpi, st, en = d["mediaPoolItem"], d["startFrame"], d["endFrame"]
            rec = d.get("recordFrame", max([i.end for i in tl.items] or [tl.start]))
            it = TimelineItem(tl, mpi, rec, rec + round((en - st) * tl.fps / mpi.fps), st, en)
            tl.items.append(it)
            out.append(it)
        return out


class Project:
    def __init__(self, info, fps):
        self.mp, self.timeline = MediaPool(info, fps), None

    def GetMediaPool(self):
        return self.mp

    def SetCurrentTimeline(self, tl):
        self.timeline = tl
        return True


class ProjectManager:
    def SaveProject(self):
        return True


# ---------- 組み立て内容 ----------
def dump(proj, sb):
    """組み立て内容の dict。clips はタイムラインの頭からのフレームで並べる。素材は media の番号で指す。"""
    tl = proj.mp.timeline
    paths = list(proj.mp.items)
    clips = []
    # 速さは丸めない。簡易版はこの値から comp_frame で映す素材のフレームを決めるので、6 桁に丸めると割り切れない
    # 倍速で切り捨ての結果が変わる (333.333...% の 303 フレーム目が 1010 でなく 1009 になる)
    for it in sorted(tl.items if tl else [], key=lambda i: i.start):
        clips.append({"start": it.start - tl.start, "end": it.end - tl.start, "media": paths.index(it.mpi.path),
                      "source_start": it.src_start, "source_end": it.src_end, "speed": it.speed,
                      "markers": it.markers, "fusion": it.comp.dump() if it.comp else None})
    return {"timeline": {"name": tl.name if tl else None, "width": sb["timeline"]["width"],
                         "height": sb["timeline"]["height"], "fps": plain(float(sb["fps"])),
                         "frames": max([c["end"] for c in clips] or [0])},
            "media": [{"path": p, "width": m.width, "height": m.height, "fps": plain(m.fps), "frames": m.frames}
                      for p, m in proj.mp.items.items()],
            "clips": clips}
