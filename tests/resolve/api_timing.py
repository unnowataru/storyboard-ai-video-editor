"""外部の Python から呼んだときの API の時間 (20 回の平均) を記録する。合否は付けない。

時間は PC の性能と負荷で変わるので、固定の上限で合否を決めない。記録を docs/scripting-api.md の表と見比べる。
Studio 21.1.1 の実機では、値を読む呼び出しが 0.1〜0.4 ms、SetProperties が 5.2 ms、
SetCurrentTimecode が 500 ms だった。再生位置の移動は遅いので、フレーム送りはキー操作で行う。

使い方: python scripts/resolve_lock.py run --owner api_timing -- python tests/resolve/api_timing.py [--source 素材]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_helpers import Trial  # noqa: E402

t = Trial("api_timing")
clip = t.clip()
tl = t.timeline("timing")
t.mp.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": 0, "endFrame": 10 * t.fps, "mediaType": 1}])
t.resolve.OpenPage("edit")
it = tl.GetItemListInTrack("video", 1)[0]
tc = tl.GetStartTimecode()
calls = {
    "GetCurrentTimecode": lambda: tl.GetCurrentTimecode(),
    "GetProperties": lambda: it.GetProperties(),
    "GetMarkers": lambda: tl.GetMarkers(),
    "SetProperties ZoomX": lambda: it.SetProperties({"ZoomX": 1.0}),
    "SetCurrentTimecode": lambda: tl.SetCurrentTimecode(tc),
}
for name, fn in calls.items():
    fn()
    t0 = time.perf_counter()
    for _ in range(20):
        fn()
    ms = round((time.perf_counter() - t0) / 20 * 1000, 1)
    t.note(f"{name} (ms)", ms)   # 時間は JSON の info に入る
t.finish()
