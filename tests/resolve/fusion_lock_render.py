"""Fusion の comp.Lock() の有無と、合成の書き出し・読み込みが、編集ページの書き出しに効くか。

同じ素材の同じ範囲を 5 つ並べ、それぞれの合成に単色 (マゼンタ) の四角を重ねて書き出し、四角の中心の色を見る。
期待 (Studio 21.1.1 の実機):
- 0 何もしない (比べるための元の画)
- 1 Lock を使わない                                 → 四角が出る
- 2 部品の追加だけ Lock の中、値は Unlock の後         → 四角が出る
- 3 部品の追加も値も Lock の中                         → 四角が出ない (値を読み戻せても描画に使われない)
- 4 3 と同じ作りの後、ExportFusionComp → 消す → ImportFusionComp → 四角が出る

使い方: python scripts/resolve_lock.py run --owner fusion_lock_render -- python tests/resolve/fusion_lock_render.py [--source 素材]   (書き出した動画は 出力先/tests/ に残る)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_helpers import Trial, add_box_tools, near, set_box  # noqa: E402

MAGENTA = (255, 0, 255)
t = Trial("fusion_lock_render")
clip = t.clip()
L = t.fps   # 1 つ 1 秒
tl = t.timeline("lock")
t.mp.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": 0, "endFrame": L, "mediaType": 1} for _ in range(5)])
items = tl.GetItemListInTrack("video", 1)
box = dict(center=(0.4, 0.6), width=0.2, height=0.2)


def io_of(comp):
    return comp.FindTool("MediaIn1"), comp.FindTool("MediaOut1")


# 1 Lock なし
comp = items[1].AddFusionComp()
mi, mo = io_of(comp)
mo.Input = set_box(add_box_tools(comp), mi.Output, MAGENTA, **box).Output
# 2 追加だけ Lock の中
comp = items[2].AddFusionComp()
mi, mo = io_of(comp)
comp.Lock()
tools = add_box_tools(comp)
comp.Unlock()
mo.Input = set_box(tools, mi.Output, MAGENTA, **box).Output
# 3 全部 Lock の中
comp = items[3].AddFusionComp()
mi, mo = io_of(comp)
comp.Lock()
mo.Input = set_box(add_box_tools(comp), mi.Output, MAGENTA, **box).Output
comp.Unlock()
# 4 3 と同じ作り → 書き出し → 消す → 読み込み
comp = items[4].AddFusionComp()
mi, mo = io_of(comp)
comp.Lock()
mo.Input = set_box(add_box_tools(comp), mi.Output, MAGENTA, **box).Output
comp.Unlock()
comp_path = os.path.join(t.out, "fusion_lock_render.comp")
exp = items[4].ExportFusionComp(comp_path, 1)
names = items[4].GetFusionCompNameList() or []
dele = items[4].DeleteFusionCompByName(names[0]) if names else None
imp = items[4].ImportFusionComp(comp_path)
t.note("ExportFusionComp / DeleteFusionCompByName / ImportFusionComp", (exp, dele, bool(imp)))

video, status = t.render(tl, "fusion_lock_render")
t.note("render", {"file": video, "status": status})
if not video:
    t.check("書き出せた", False, status, "動画ができる")
    t.finish()
x, y = round(box["center"][0] * t.W), round((1 - box["center"][1]) * t.H)
px = [t.frame(video, k * L + L // 2).getpixel((x, y)) for k in range(5)]
t.note("四角の中心の色 (0〜4)", px)
t.check("0 何もしない: マゼンタではない (元の画)", not near(px[0], MAGENTA), px[0], "マゼンタ以外")
t.check("1 Lock なし: 四角が出る", near(px[1], MAGENTA), px[1], MAGENTA)
t.check("2 追加だけ Lock の中: 四角が出る", near(px[2], MAGENTA), px[2], MAGENTA)
t.check("3 値も Lock の中: 四角が出ない (元の画と同じ)", near(px[3], px[0], 30), px[3], px[0])
t.check("4 書き出し → 読み込み: 四角が出る", near(px[4], MAGENTA), px[4], MAGENTA)
t.finish()
