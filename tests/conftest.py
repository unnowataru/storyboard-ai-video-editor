"""pytest の共通設定。scripts/ を import できるようにし、Resolve が要るテスト (tests/resolve) は集めない。"""
import copy
import itertools
import os
import shutil
import sys
import uuid

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

collect_ignore = ["resolve"]


def patch_common(monkeypatch, name, value):
    """common の名前 name を、テストの間だけ value に差し替える (テストから `from conftest import patch_common` で使う)。

    common は分けたモジュール (common.PARTS) の名前を読み込み直しているだけなので、common だけを差し替えると
    スクリプトの C.name には効くが、分けたモジュールの中からの呼び出し (resolve.connect_or_exit → connect_resolve、
    layout.fit_caption → text_width など) には効かない。そこで common と、name を持つ分けたモジュールの全部を差し替える。
    _CFG (config() が覚えている設定) は settings にだけあり、patch_common(monkeypatch, "_CFG", None) で読み直させる。"""
    import common as C
    targets = [m for m in (C, *C.PARTS) if name in vars(m)]
    if not targets:
        raise AttributeError(f"common にも分けたモジュールにも {name} が無い")
    for m in targets:
        monkeypatch.setattr(m, name, value)


def pytest_configure(config):
    config.addinivalue_line("markers", "meiryo: テロップの幅を Meiryo の寸法で比べる。Meiryo が無い PC では飛ばす")


def pytest_collection_modifyitems(config, items):
    """印 meiryo のテストを、Meiryo (標準と Bold) が見つからないときに飛ばす。

    GitHub の Windows の実行環境 (Windows Server) には Meiryo が無く、日本語の追加書体を後から入れる
    Add-WindowsCapability は時間がかかりすぎて使えない。Meiryo のある PC では全部流れる。"""
    marked = [it for it in items if "meiryo" in it.keywords]
    if not marked:
        return
    import common as C
    cfg = C.Config()
    if cfg.font_file("Meiryo") and cfg.font_file("Meiryo Bold"):
        return
    skip = pytest.mark.skip(reason="Meiryo が無い (config の fonts と paths.fonts_dirs で探した)")
    for it in marked:
        it.add_marker(skip)


@pytest.fixture(scope="session")
def offline_work():
    """テストが動画などを作る場所。作業フォルダーの offline-tests の下に、実行ごとに別の名前
    (run-<プロセス番号>-<乱数>) のフォルダーを作り、終わったら消す。同時に流れる pytest どうしで取り合わない。"""
    import common as C
    d = C.Config().work_dir("offline-tests", f"run-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------- 模擬の Fusion (組む側の fusion.py が書き、照合側の check_final.py が読む API) ----------
# 合成の既定の大きさに従う部品 (実物では UseFrameFormatSettings が 1 で、既定の大きさを持つ)。定義は簡易版の
# Resolve の代わりの部分と同じものを使う (2 か所に書かない)
from resolve_stand_in import FRAME_INPUTS  # noqa: E402
# 組む側が入れない入力の既定の値 (実物の既定と同じ)
DEFAULTS = {"Merge": {"Blend": 1.0}, "TextPlus": {"HorizontalLeftCenterRight": 0},
            "Transform": {"Center": {1: 0.5, 2: 0.5}, "Pivot": {1: 0.5, 2: 0.5}, "Size": 1.0, "Angle": 0.0}}
_names = itertools.count(1)


class Spline:
    """BezierSpline (keys は {時刻: 値}。間は直線で、端の後ろは端の値) と XYPath (links の X と Y の BezierSpline)。"""

    def __init__(self, kind):
        self.ID, self.keys, self.links = kind, {}, {}

    def ConnectInput(self, k, src):
        self.links[k] = src
        return True

    def SetInput(self, k, v, t=None):
        self.links[k].keys[t] = v
        return True

    def at(self, t):
        if self.links:
            return {1: self.links["X"].at(t), 2: self.links["Y"].at(t), 3: 0.0}
        ks = sorted(self.keys)
        t0 = max([k for k in ks if k <= t] or ks[:1])
        t1 = min([k for k in ks if k >= t] or ks[-1:])
        v0, v1 = self.keys[t0], self.keys[t1]
        return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)

    def GetKeyFrames(self):   # 実機と同じ形 {時刻: {1: 値, "LH": {...}, "RH": {...}}}
        ts = set(self.keys).union(*(s.keys for s in self.links.values()))
        return {float(t): {1: self.keys.get(t, 0.0), "LH": {1: t - 1}, "RH": {1: t + 1}} for t in ts}

    def GetConnectedOutput(self):   # 入力の名前の属性として読まれる (tool.Blend.GetConnectedOutput().GetTool())
        return self

    def GetTool(self):
        return self


class Inp:
    """つながった先の入力 (部品 tool の入力 ID)。"""

    def __init__(self, tool, inp_id):
        self.tool, self.ID = tool, inp_id

    def GetTool(self):
        return self.tool


class NoLink:
    """つながっていない入力。"""

    def __bool__(self):
        return False

    def GetConnectedOutput(self):
        return None


class Tool:
    """部品。つなぐときは実物と同じく入力の名前の属性に出力を入れる (tool.Input = src.Output)。出力 (Output・Mask) は
    部品そのもので、つながった先は合成の部品の属性から探す。入力の値は数か、時刻を受け取る関数か、スプライン。"""

    def __init__(self, tid, comp=None, **inputs):
        self.ID, self.comp, self.links, self.attrs = tid, comp, {}, {}
        self.Name = f"{tid}{next(_names)}"
        wk, hk = FRAME_INPUTS.get(tid, ("Width", "Height"))
        self.inputs = ({"UseFrameFormatSettings": 1, wk: 1920, hk: 1080} if tid in FRAME_INPUTS else {}) | copy.deepcopy(
            DEFAULTS.get(tid, {})) | inputs
        self.Output = self.Mask = self

    def __getattr__(self, name):
        if name in vars(self).get("links", {}):
            return self.links[name]
        if callable(vars(self).get("inputs", {}).get(name)):
            raise AttributeError(name)   # 時刻で変わるがキーを読めない入力
        if name[:1].isupper():
            return NoLink()
        raise AttributeError(name)

    def SetAttrs(self, a):
        self.attrs.update(a)
        self.Name = a.get("TOOLS_Name", self.Name)

    def GetAttrs(self):
        return self.attrs

    def SetInput(self, k, v, t=None):
        if k in self.links:
            self.links[k].keys[t] = v
        else:
            self.inputs[k] = v
        return True

    def GetInput(self, k, t=None):
        if k in self.links:
            return self.links[k].at(t or 0)
        v = self.inputs.get(k)
        return v(t or 0) if callable(v) else v

    def put(self, **inputs):
        """値を入れ直す (つながったスプラインは外す)。"""
        for k in inputs:
            self.links.pop(k, None)
        self.inputs.update(inputs)

    def ConnectInput(self, k, src):
        self.links[k] = src
        return True

    def GetConnectedOutput(self):   # 入力の属性に入った出力 (= 部品) として読まれる
        return self

    def GetTool(self):
        return self

    def GetConnectedInputs(self):
        ins = [Inp(t, k) for t in (self.comp.tools if self.comp else [])
               for k, v in vars(t).items() if v is self and k not in ("Output", "Mask")]
        return dict(enumerate(ins, 1))


class Comp:
    """合成。MediaIn1 と MediaOut1 を持って始まる。comp[名前] は FindTool。"""

    def __init__(self):
        self.tools = []
        for tid in ("MediaIn", "MediaOut"):
            self.AddTool(tid).SetAttrs({"TOOLS_Name": tid + "1"})

    def AddTool(self, tid, x=0, y=0, **inputs):
        self.tools.append(Tool(tid, self, **inputs))
        return self.tools[-1]

    def GetToolList(self, selected):
        return dict(enumerate(self.tools, 1))

    def FindTool(self, name):
        return next((t for t in self.tools if t.Name == name), None)

    __getitem__ = FindTool

    def BezierSpline(self):
        return Spline("BezierSpline")

    def XYPath(self):
        return Spline("XYPath")


class Item:
    """タイムラインのクリップ。組む側は AddFusionComp と SetProperties、照合側は位置・素材・合成を読む。"""

    def __init__(self, start=0, end=0, src=(0, 0), speed=100.0, mpi=None, comp=None):
        self.start, self.end, self.src, self.speed, self.mpi, self.comp, self.props = start, end, src, speed, mpi, comp, {}

    def SetProperties(self, d):
        self.props.update(d)
        return True

    def AddFusionComp(self):
        self.comp = Comp()
        return self.comp

    def GetStart(self):
        return self.start

    def GetEnd(self):
        return self.end

    def GetSourceStartFrame(self):
        return self.src[0]

    def GetSourceEndFrame(self):
        return self.src[1]

    def GetSpeed(self):
        return {"Percentage": self.speed, "PitchCorrection": True}

    def GetMediaPoolItem(self):
        return self.mpi

    def GetFusionCompCount(self):
        return 1 if self.comp else 0

    def GetFusionCompByIndex(self, i):
        return self.comp
