"""Fusion の部品にキーフレームを付ける方法と、マスクに使う部品があるか。

期待 (Studio 21.1.1 の実機):
- comp.BezierSpline() を ConnectInput でつなぎ、SetInput(名前, 値, 時刻) で入れると直線で補間される
  (Size 1.0 → 3.0 を 0 → 90 フレーム。45 フレームで 2.0)
- spline.SetKeyFrames({時刻: {1: 値}}, True) でも同じ
- Center は comp.XYPath() をつなぎ、X と Y に BezierSpline をつなぐと、両方が補間される
- Resolve FX のモザイク ofx.com.blackmagicdesign.resolvefx.MosaicBlur を Fusion の部品として置ける。入力に PixelFrequency がある
- Transform の FilterMethod の既定は 2 (Linear)。8 (Lanczos) を入れると読み戻せる

使い方: python scripts/resolve_lock.py run --owner fusion_keyframes -- python tests/resolve/fusion_keyframes.py [--source 素材]   (値を読み戻すだけで、書き出しはしない)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_helpers import C, Trial  # noqa: E402

t = Trial("fusion_keyframes")
clip = t.clip()
tl = t.timeline("keyframes")
t.mp.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": 0, "endFrame": 120, "mediaType": 1}])
it = tl.GetItemListInTrack("video", 1)[0]
comp = it.AddFusionComp()
t.check("AddFusionComp が合成を返す", bool(comp), bool(comp), True)


def approx(a, b, tol=0.01):
    return a is not None and abs(float(a) - b) <= tol


a = comp.AddTool("Transform", -32768, -32768)
a.ConnectInput("Size", comp.BezierSpline())
a.SetInput("Size", 1.0, 0)
a.SetInput("Size", 3.0, 90)
v = [a.GetInput("Size", f) for f in (0, 45, 90, 120)]
t.check("SetInput に時刻を付けると直線で補間 (0, 45, 90, 120 フレーム)", all(approx(x, y) for x, y in zip(v, (1, 2, 3, 3))), v,
        [1, 2, 3, 3])

b = comp.AddTool("Transform", -32768, -32768)
sb = comp.BezierSpline()
b.ConnectInput("Size", sb)
sb.SetKeyFrames({0: {1: 1.0}, 90: {1: 3.0}}, True)
v = [b.GetInput("Size", f) for f in (0, 45, 90)]
t.check("SetKeyFrames でも同じ", all(approx(x, y) for x, y in zip(v, (1, 2, 3))), v, [1, 2, 3])

c = comp.AddTool("Transform", -32768, -32768)
path, sx, sy = comp.XYPath(), comp.BezierSpline(), comp.BezierSpline()
c.ConnectInput("Center", path)
path.ConnectInput("X", sx)
path.ConnectInput("Y", sy)
sx.SetKeyFrames({0: {1: 0.5}, 90: {1: 0.9}}, True)
sy.SetKeyFrames({0: {1: 0.5}, 90: {1: 0.1}}, True)
mid = c.GetInput("Center", 45) or {}
t.check("XYPath で Center の X と Y が補間される (45 フレームで 0.7, 0.3)",
        approx(mid.get(1), 0.7) and approx(mid.get(2), 0.3), mid, {1: 0.7, 2: 0.3})

m = comp.AddTool("ofx.com.blackmagicdesign.resolvefx.MosaicBlur", -32768, -32768)
t.check("Resolve FX のモザイクを Fusion に置ける", bool(m), m.ID if m else None, "ofx.com.blackmagicdesign.resolvefx.MosaicBlur")
if m:
    ids = [x.ID for x in (m.GetInputList() or {}).values()]
    t.check("モザイクに PixelFrequency がある", "PixelFrequency" in ids, "PixelFrequency" in ids, True)

d = comp.AddTool("Transform", -32768, -32768)
default = d.GetInput("FilterMethod")
t.check("Transform の FilterMethod の既定は 2 (Linear)", approx(default, 2, 0), default, 2)
want = C.fusion_filter("Lanczos")
d.SetInput("FilterMethod", want)
t.check("FilterMethod に Lanczos (8) を入れて読み戻せる", approx(d.GetInput("FilterMethod"), want, 0), d.GetInput("FilterMethod"), want)
t.finish()
