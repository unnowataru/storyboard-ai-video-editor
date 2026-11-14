"""Fusion のマスク (RectangleMask / EllipseMask) の Width と Height の単位。

期待 (Studio 21.1.1 の実機):
- Width は画面の幅を 1、Height は画面の高さを 1 とした値
- Height を画面の幅で割って入れると、マスクが低くなる (隠し漏れの原因になる)
- EllipseMask も同じ座標で、その四角に内接する楕円になる

マスク (px): 左上 (0.3W, 0.3H)、大きさ 0.3W × 0.2H。単色 (マゼンタ) で塗って書き出し、マスクの上端の少し内側と、四隅の近くの色を見る。
使い方: python scripts/resolve_lock.py run --owner fusion_mask_size -- python tests/resolve/fusion_mask_size.py [--source 素材]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_helpers import Trial, near, solid_box  # noqa: E402

MAGENTA = (255, 0, 255)
t = Trial("fusion_mask_size")
W, H = t.W, t.H
clip = t.clip()
L = t.fps
tl = t.timeline("mask")
t.mp.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": 0, "endFrame": L, "mediaType": 1} for _ in range(4)])
items = tl.GetItemListInTrack("video", 1)
bx, by, bw, bh = 0.3 * W, 0.3 * H, 0.3 * W, 0.2 * H
center = ((bx + bw / 2) / W, 1 - (by + bh / 2) / H)
variants = [None,                                          # 0 元の画
            ("RectangleMask", bh / H),                     # 1 高さを画面の高さで割る
            ("RectangleMask", bh / W),                     # 2 高さを画面の幅で割る
            ("EllipseMask", bh / H)]                       # 3 楕円
for it, v in zip(items, variants):
    if not v:
        continue
    comp = it.AddFusionComp()
    mi, mo = comp.FindTool("MediaIn1"), comp.FindTool("MediaOut1")
    mo.Input = solid_box(comp, mi.Output, MAGENTA, center, bw / W, v[1], shape=v[0]).Output

video, status = t.render(tl, "fusion_mask_size")
t.note("render", {"file": video, "status": status})
if not video:
    t.check("書き出せた", False, status, "動画ができる")
    t.finish()
frames = [t.frame(video, k * L + L // 2) for k in range(4)]
top_in = (round(bx + bw / 2), round(by + bh * 0.1))      # 上端の少し内側
corner = (round(bx + bw * 0.05), round(by + bh * 0.05))  # 左上の角の近く (楕円の外)
mid = (round(bx + bw / 2), round(by + bh / 2))
px = {k: [f.getpixel(p) for f in frames] for k, p in (("top_in", top_in), ("corner", corner), ("mid", mid))}
t.note("色 (0〜3)", px)
t.check("1 高さ / 画面の高さ: 上端の内側まで塗られる", near(px["top_in"][1], MAGENTA), px["top_in"][1], MAGENTA)
t.check("1 高さ / 画面の高さ: 角の近くも塗られる (四角)", near(px["corner"][1], MAGENTA), px["corner"][1], MAGENTA)
t.check("2 高さ / 画面の幅: 上端の内側が塗られない (マスクが低い)", not near(px["top_in"][2], MAGENTA), px["top_in"][2], "マゼンタ以外")
t.check("2 高さ / 画面の幅: 中心は塗られる", near(px["mid"][2], MAGENTA), px["mid"][2], MAGENTA)
t.check("3 楕円: 中心は塗られる", near(px["mid"][3], MAGENTA), px["mid"][3], MAGENTA)
t.check("3 楕円: 上端の内側 (中央) は塗られる", near(px["top_in"][3], MAGENTA), px["top_in"][3], MAGENTA)
t.check("3 楕円: 角の近くは塗られない", not near(px["corner"][3], MAGENTA), px["corner"][3], "マゼンタ以外")
t.finish()
