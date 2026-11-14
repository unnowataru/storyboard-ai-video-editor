"""構成表の layout (画面を縮めて寄せ、余白をぼかした画面で埋め、余白にテロップを出す配置。common.apply_layout・
layout_values・build_fusion の _layout) のテスト。Resolve は要らない。"""
import copy

import pytest

import common as C
from conftest import Item

SCREEN = {"x": 600, "y": 40, "w": 2240, "h": 1260}   # 横長の素材の例 (3440x1440) の中のアプリの画面
SRC = (3440, 1440)


LOOK = C._read_json(C.DEFAULTS)["storyboard"]   # local.json の影響を受けない既定値 (1920x1080)


def storyboard(layout, rows=None):
    sb = copy.deepcopy(LOOK)
    sb.update({"layout": layout, "rows": rows if rows is not None else [{"start": 0, "end": 6, "caption": "a"}]})
    return sb


def to_out(L, x, y, src=SRC):
    """素材の画素 (x, y) が出力のどこに映るか (layout の値の Crop と Merge の Center・Size をそのまま計算する)。
    comp の画素で切り抜いた四角の左上からのずれに、comp の 1 画素の大きさ (size × W / comp の幅) を掛け、置く場所の左上に足す。"""
    cw, ch = L["comp"]
    cx, cy0, sw, sh = L["screen_crop"]
    top = ch - cy0 - sh                                   # 切り抜く四角の上の端 (comp の画素、上から)
    px, py = x + (cw - src[0]) / 2, y + (ch - src[1]) / 2   # 素材の点 (comp の画素、上から)
    s = L["size"] * 1920 / cw                             # comp の 1 画素が出力の何画素か
    left = L["center"][0] * 1920 - sw * s / 2
    upper = (1 - L["center"][1]) * 1080 - sh * s / 2
    return left + (px - cx) * s, upper + (py - top) * s


def test_top_left_places_screen_with_crop_and_merge():
    sb = C.apply_layout(storyboard({"screen": SCREEN, "height": 0.78, "anchor": "top-left", "margin": 12},
                                   [{"start": 0, "end": 6, "masks": [{"type": "mosaic", "x": 900, "y": 400, "w": 50, "h": 20}]}]))
    r = sb["rows"][0]
    # 行にズームも黒塗りも追加しない (行のマスクだけが残る)
    assert "zoom" not in r and [m["type"] for m in r["masks"]] == ["mosaic"]
    v = C.scene_values(sb, r, source_size=SRC)
    L = v["layout"]
    assert v["zoom"] is None and len(v["masks"]) == 1
    # 横長の素材 (3440x1440) は 3440x1935 に広げた comp (fusion_canvas) の画素で切り抜く。Crop の YOffset は左下から。
    # Resolve は Crop の値を整数に丸めるので、半分を切り上げた整数を入れる (1935 − (40 + 247.5) − 1260 = 387.5 → 388)
    assert L["comp"] == [3440, 1935]
    assert L["screen_crop"] == [600, 388, 2240, 1260]
    k = 0.78 * 1080 / 1260
    assert L["scale"] == pytest.approx(k) and L["size"] == pytest.approx(k * 3440 / 1920)
    # 画面の左上が出力の (12, 12)、右下が (12 + 幅, 12 + 高さの 78%)。丸めのずれは comp の 0.5 画素 (出力の約 0.33 画素) まで
    assert to_out(L, SCREEN["x"], SCREEN["y"]) == pytest.approx((12, 12), abs=0.35)
    assert to_out(L, SCREEN["x"] + SCREEN["w"], SCREEN["y"] + SCREEN["h"]) == pytest.approx(
        (12 + 2240 * k, 12 + 0.78 * 1080), abs=0.35)
    assert L["rect"] == pytest.approx([12, 12, 2240 * k, 0.78 * 1080])
    # 背景は切り抜いた画面で comp の全体を覆う (幅か高さのどちらかがちょうど)
    b = L["back_size"]
    assert b * 2240 >= 3440 - 1e-6 and b * 1260 >= 1935 - 1e-6
    assert min(b * 2240 - 3440, b * 1260 - 1935) == pytest.approx(0, abs=1e-6)
    assert (L["blur"], L["dim"]) == (LOOK["layout_background"]["blur"], LOOK["layout_background"]["dim"])
    assert L["filter"] == C.fusion_filter(LOOK["zoom"]["filter_method"])
    assert sb["safe_area"] == {"aspect": "1920:1080"}
    # テロップの帯は下の余白 (下の端から 1080 − 12 − 842.4 px) の上下の真ん中
    band = sb["caption"]["band"]
    assert band == pytest.approx([0, (1080 - 12 - 0.78 * 1080) / 1080])
    fit = C.fit_caption(sb, "a")
    assert fit["bar_center"] == pytest.approx(band[1] / 2) and not [w for w in fit["warnings"] if "余白" in w]


@pytest.mark.parametrize("anchor, left", [("top", (1920 - 2240 * 0.78 * 1080 / 1260) / 2),
                                          ("top-right", 1920 - 12 - 2240 * 0.78 * 1080 / 1260)])
def test_anchor_moves_screen(anchor, left):
    sb = C.apply_layout(storyboard({"screen": SCREEN, "anchor": anchor}))
    L = C.scene_values(sb, sb["rows"][0], source_size=SRC)["layout"]
    assert to_out(L, SCREEN["x"], SCREEN["y"]) == pytest.approx((left, 12), abs=0.35)
    assert L["rect"][0] == pytest.approx(left)


def test_same_aspect_source_crops_source_pixels():
    # 縦横の比がタイムラインと同じ素材 (4K) は広げない。comp は素材の大きさ
    sc = {"x": 100, "y": 200, "w": 1600, "h": 900}
    sb = C.apply_layout(storyboard({"screen": sc, "height": 0.5, "anchor": "top-right", "margin": 0}))
    v = C.scene_values(sb, sb["rows"][0], source_size=(3840, 2160))
    L = v["layout"]
    assert v["canvas"] is None and L["comp"] == [3840, 2160]
    assert L["screen_crop"] == pytest.approx([100, 2160 - 200 - 900, 1600, 900])
    k = 0.5 * 1080 / 900
    assert L["size"] == pytest.approx(k * 2)   # comp の 1 画素は出力の 0.5 画素
    assert to_out(L, 100, 200, (3840, 2160)) == pytest.approx((1920 - 1600 * k, 0))
    assert L["back_size"] == pytest.approx(max(3840 / 1600, 2160 / 900))


def test_background_strength_from_config_and_layout():
    sb = C.apply_layout(storyboard({"screen": SCREEN, "background": {"blur": 5}}))
    assert sb["layout"]["background"] == {"blur": 5.0, "dim": LOOK["layout_background"]["dim"]}
    L = C.scene_values(sb, sb["rows"][0], source_size=SRC)["layout"]
    assert (L["blur"], L["dim"]) == (5.0, LOOK["layout_background"]["dim"])
    # 全体の layout_background (config と同じ名前) でも変えられ、layout.background が上に重なる
    sb = storyboard({"screen": SCREEN, "background": {"dim": 0.3}})
    sb["layout_background"] = {"blur": 7, "dim": 0.9}
    assert C.apply_layout(sb)["layout"]["background"] == {"blur": 7.0, "dim": 0.3}


@pytest.mark.parametrize("bg", [{"blur": -1}, {"blur": "a"}, {"dim": 1.5}, {"dim": -0.1}, {"dim": True}])
def test_background_errors(bg):
    with pytest.raises(SystemExit):
        C.apply_layout(storyboard({"screen": SCREEN, "background": bg}))


def test_without_layout_nothing_changes():
    sb = storyboard(None)
    del sb["layout"]
    before = copy.deepcopy(sb)
    assert C.apply_layout(sb) == before
    assert C.scene_values(sb, sb["rows"][0], source_size=SRC)["layout"] is None


@pytest.mark.parametrize("layout, rows", [
    ({}, None),                                                                  # 空の layout は配置なしにしない
    ({"screen": SCREEN}, [{"start": 0, "end": 6, "zoom": {"x": 0, "y": 0, "w": 100, "h": 100, "seconds": 0}}]),   # zoom と一緒
    ({"screen": SCREEN, "anchor": "left"}, None),
    ({"screen": {"x": 0, "y": 0, "w": 0, "h": 10}}, None),
    ({"screen": SCREEN, "height": 1.5}, None),
    ({"screen": {"x": 0, "y": 0, "w": 4000, "h": 1000}, "height": 0.9}, None)])   # 縮めても幅が出力に収まらない
def test_layout_errors(layout, rows):
    with pytest.raises(SystemExit):
        C.apply_layout(storyboard(layout, rows))


def test_caption_taller_than_band_warns():
    sb = C.apply_layout(storyboard({"screen": SCREEN, "height": 0.95, "margin": 4}))
    assert [w for w in C.fit_caption(sb, "一行目|二行目")["warnings"] if "余白" in w]
    # 結びのテロップは band を使わず中央
    assert C.fit_caption(sb, "おわり", style="closing")["bar_center"] == 0.5


# ---------- Fusion の部品 ----------
def inputs_of(comp, name):
    return comp[name].inputs


def test_build_fusion_layout_parts():
    sb = C.apply_layout(storyboard({"screen": SCREEN, "anchor": "top-right", "background": {"blur": 12, "dim": 0.4}},
                                   [{"start": 0, "end": 6, "caption": "a",
                                     "masks": [{"type": "mosaic", "x": 900, "y": 400, "w": 50, "h": 20}],
                                     "focus": {"x": 700, "y": 300, "w": 400, "h": 200}}]))
    v = C.scene_values(sb, sb["rows"][0], source_size=SRC)
    L = v["layout"]
    it = Item()
    ok, res = C.build_fusion(it, v)
    comp = it.comp
    assert ok and res["layout"] is True
    names = [t.Name for t in comp.tools]
    for n in ("LayoutBase", "LayoutBackCrop", "LayoutBackBlur", "LayoutBack", "LayoutBackShade", "LayoutBackDim",
              "LayoutScreenCrop", "LayoutScreen"):
        assert n in names
    assert not [n for n in names if n and (n.startswith("Zoom") or n.endswith("Black"))]   # ズームも黒塗りも無い
    # 背景はマスクを掛けた後の画 (focus の前) から、画面は focus を掛けた後の画から切り抜く
    assert comp["LayoutBackCrop"].Input is comp["Mask1Mosaic"]
    assert comp["LayoutScreenCrop"].Input is comp["FocusDim"]
    for n in ("LayoutBackCrop", "LayoutScreenCrop"):
        i = inputs_of(comp, n)
        assert [i["XOffset"], i["YOffset"], i["XSize"], i["YSize"]] == pytest.approx(L["screen_crop"])
    assert comp["LayoutBackBlur"].Input is comp["LayoutBackCrop"]
    assert inputs_of(comp, "LayoutBackBlur")["XBlurSize"] == 12.0
    back = comp["LayoutBack"]
    assert back.Background is comp["LayoutBase"] and back.Foreground is comp["LayoutBackBlur"]
    assert back.inputs["Size"] == pytest.approx(L["back_size"]) and back.inputs["Center"] == {1: 0.5, 2: 0.5}
    shade = inputs_of(comp, "LayoutBackShade")
    assert [shade[k] for k in ("TopLeftRed", "TopLeftGreen", "TopLeftBlue")] == [0, 0, 0]
    assert shade["TopLeftAlpha"] == pytest.approx(0.6)
    dim = comp["LayoutBackDim"]
    assert dim.Background is back and dim.Foreground is comp["LayoutBackShade"]
    scr = comp["LayoutScreen"]
    assert scr.Background is dim and scr.Foreground is comp["LayoutScreenCrop"]
    assert scr.inputs["Center"] == {1: pytest.approx(L["center"][0]), 2: pytest.approx(L["center"][1])}
    assert scr.inputs["Size"] == pytest.approx(L["size"])
    assert scr.inputs["FilterMethod"] == back.inputs["FilterMethod"] == L["filter"]
    # テロップは置いた画面の上 (出力に固定)
    assert comp["CaptionBarOver"].Background is scr
    # 横長の素材 (3440x1440) は comp を広げるので、単色の部品も広げた大きさで描く
    assert comp["LayoutBase"].inputs["Width"] == 3440 and comp["LayoutBase"].inputs["Height"] == 1935


def test_point_moves_with_screen():
    """指すマークは画面と一緒に縮む。指の先 (素材の点) が、画面の中のその点が映る所に来る。"""
    sb = C.apply_layout(storyboard({"screen": SCREEN, "anchor": "top-right"},
                                   [{"start": 0, "end": 6, "point": {"x": 1651, "y": 370, "from": "below"}}]))
    v = C.scene_values(sb, sb["rows"][0], source_size=SRC)
    L, pt = v["layout"], v["point"]
    # 寄せた後 (画面の倍率 size) に point.size px に見える大きさで描く
    plain = C.point_values(sb, sb["rows"][0]["point"], *SRC)
    assert pt["size"] == pytest.approx(plain["size"] / L["size"])
    # LayoutPoint (Transform、Pivot は真ん中) が指の先 (pivot、comp の画面を 1 とした値) を動かす先 = 素材の点が映る所
    p = pt["pivot"]
    out = (L["point_center"][0] + L["size"] * (p[0] - 0.5), L["point_center"][1] + L["size"] * (p[1] - 0.5))
    want = to_out(L, 1651, 370)
    assert (out[0] * 1920, (1 - out[1]) * 1080) == pytest.approx(want, abs=0.35)
    it = Item()
    C.build_fusion(it, v)
    comp = it.comp
    mv = comp["LayoutPoint"]
    assert mv.Input is comp["PointTilt"] and mv.inputs["Size"] == pytest.approx(L["size"])
    # マークは置いた画面の上に重ねる (画面の四角で切らない)
    over = comp["PointOver"]
    assert over.Background is comp["LayoutScreen"] and over.Foreground is mv


def test_hold_keeps_layout():
    sb = C.apply_layout(storyboard({"screen": SCREEN}, [{"start": 0, "end": 4, "seconds": 4, "hold": {"seconds": 2},
                                                        "caption": "a", "badge": "x"}]))
    v = C.scene_values(sb, sb["rows"][0], source_size=SRC)
    hv = C.hold_values(v, 119, 120)
    assert hv["layout"] == v["layout"] and hv["badge"] is None
    it = Item()
    ok, res = C.build_fusion(it, hv)
    assert ok and res["layout"] and it.comp["LayoutScreen"]
