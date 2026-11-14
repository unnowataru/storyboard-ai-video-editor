"""行の point (指すマーク。common.point_values) のテスト。Resolve は要らない。"""
import pytest

import common as C

LOOK = C._read_json(C.DEFAULTS)["storyboard"]   # local.json の影響を受けない既定値 (1920x1080)


@pytest.mark.parametrize("p, sign", [
    ({"x": 500, "y": 600, "from": "below"}, 1),     # 左寄りを下から: 左上を指す (左回り)
    ({"x": 1400, "y": 600, "from": "below"}, -1),   # 右寄りを下から: 右上を指す
    ({"x": 500, "y": 300, "from": "above"}, -1),    # 左寄りを上から: 左下を指す
    ({"x": 700, "y": 300, "from": "left"}, 1),      # 上寄りを左から: 手は下へ寄る
    ({"x": 700, "y": 800, "from": "left"}, -1)])
def test_tilt_leans_hand_toward_center(p, sign):
    v = C.point_values(LOOK, p, 1920, 1080)
    assert v["angle"] == sign * LOOK["point"]["tilt"]
    # 回す軸は指す点 (Fusion の座標は下が 0)
    assert v["pivot"] == pytest.approx((p["x"] / 1920, 1 - p["y"] / 1080))


def test_tilt_leans_toward_zoomed_view():
    # 画面の真ん中より左の点でも、寄った範囲 (真ん中 x=300) より右なら、手は左へ寄る (右上を指す)
    row = {"point": {"x": 500, "y": 600}, "zoom": {"x": 0, "y": 300, "w": 600, "h": 338, "seconds": 0}}
    assert C.scene_values(LOOK, row)["point"]["angle"] == -LOOK["point"]["tilt"]
    assert C.scene_values(LOOK, {"point": {"x": 500, "y": 600}})["point"]["angle"] == LOOK["point"]["tilt"]


def test_tilt_zero_and_range():
    assert C.point_values(LOOK, {"x": 500, "y": 600, "tilt": 0}, 1920, 1080)["angle"] == 0
    for bad in (-1, 91, "45"):
        with pytest.raises(ValueError):
            C.point_values(LOOK, {"x": 500, "y": 600, "tilt": bad}, 1920, 1080)


def test_bool_is_not_a_number():
    # True / False は数として通さない (照らし合わせの側 check_final と同じ)
    for p in ({"x": True, "y": 600}, {"x": 500, "y": False}, {"x": 500, "y": 600, "tilt": True},
              {"x": 500, "y": 600, "size": True}):
        with pytest.raises(ValueError):
            C.point_values(LOOK, p, 1920, 1080)


def test_malformed_point_is_value_error():
    # 書式の誤りは ValueError (sb2resolve がその行の誤りとして報告する。TypeError・KeyError で全体を止めない)
    look = dict(LOOK, point=dict(LOOK["point"], glyphs={}))
    for p, lk in (("900,500", LOOK), ({"x": 1, "y": 2, "from": []}, LOOK), ({"x": 1, "y": 2, "style": []}, LOOK),
                  ({"x": 1, "y": 2}, look)):
        with pytest.raises(ValueError):
            C.point_values(lk, p, 1920, 1080)
