"""TimelineItem.SetSpeed の RippleTimeline の違い。

期待 (Studio 21.1.1 の実機):
- RippleTimeline=True: 20 秒のクリップを 400% にすると、タイムライン上で約 5 秒になり、後ろのクリップが詰まる。
  長さは 150 フレームではなく 149 フレームになることがある。149 か 150 を期待どおりとし、値を記録する
- RippleTimeline=False: タイムライン上の長さは変わらず、素材側の範囲が伸び縮みする。後ろのクリップは動かない
- GetSpeed() は {"Percentage": 400.0, ...} を返す

使い方: python scripts/resolve_lock.py run --owner speed_ripple -- python tests/resolve/speed_ripple.py [--source 素材]   (素材は 30 秒以上)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_helpers import Trial  # noqa: E402

t = Trial("speed_ripple")
clip = t.clip()
fps = t.fps
seg = 20 * fps   # 20 秒
nxt = 5 * fps

# RippleTimeline=True
tl = t.timeline("ripple-true")
t.mp.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": 0, "endFrame": seg, "mediaType": 1},
                       {"mediaPoolItem": clip, "startFrame": seg, "endFrame": seg + nxt, "mediaType": 1}])
a, b = tl.GetItemListInTrack("video", 1)
ok = a.SetSpeed({"Percentage": 400.0, "RippleTimeline": True})
t.check("SetSpeed (RippleTimeline=True) が True を返す", ok, ok, True)
a, b = tl.GetItemListInTrack("video", 1)
length = a.GetEnd() - a.GetStart()
t.check("400% で 20 秒が約 5 秒 (149 か 150 フレーム)", length in (seg // 4 - 1, seg // 4), length, f"{seg // 4 - 1} か {seg // 4}")
t.check("後ろのクリップが詰まる", b.GetStart() == a.GetEnd(), (a.GetEnd(), b.GetStart()), "同じ値")
sp = a.GetSpeed() or {}
t.check("GetSpeed の Percentage", abs(float(sp.get("Percentage", 0)) - 400.0) < 1e-3, sp, 400.0)

# RippleTimeline=False
short = 5 * fps
tl = t.timeline("ripple-false")
t.mp.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": 0, "endFrame": short, "mediaType": 1},
                       {"mediaPoolItem": clip, "startFrame": seg + nxt, "endFrame": seg + 2 * nxt, "mediaType": 1}])
a, b = tl.GetItemListInTrack("video", 1)
b_start = b.GetStart()
ok = a.SetSpeed({"Percentage": 400.0, "RippleTimeline": False})
a, b = tl.GetItemListInTrack("video", 1)
length = a.GetEnd() - a.GetStart()
t.check("RippleTimeline=False: タイムライン上の長さは変わらない", length == short, length, short)
t.check("RippleTimeline=False: 後ろのクリップは動かない", b.GetStart() == b_start, b.GetStart(), b_start)
src = a.GetSourceEndFrame() - a.GetSourceStartFrame()
t.check("RippleTimeline=False: 素材の範囲が約 4 倍に伸びる", abs(src - short * 4) <= 2, src, short * 4)
t.finish()
