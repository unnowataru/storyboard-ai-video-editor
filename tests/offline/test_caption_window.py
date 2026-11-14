"""テロップの板の形 (caption の bar_inset・corner_radius・border_width・end_mark。common.caption_window) のテスト。Resolve は要らない。"""
import copy

import pytest

import common as C

LOOK = C._read_json(C.DEFAULTS)["storyboard"]   # local.json の影響を受けない既定値 (1920x1080)


def look(**kv):
    sb = copy.deepcopy(LOOK)
    sb["caption"] |= kv
    return sb


def test_default_is_full_width_plain_bar():
    v = C.scene_values(LOOK, {"caption": "a"})["caption"]
    assert v["bar_width"] == 1 and v["border"] is None and v["corner_radius"] is None and v["mark"] is None


def test_inset_border_and_mark():
    sb = look(bar_inset=40, border_width=6, corner_radius=0.15, end_mark="▼")
    v = C.scene_values(sb, {"caption": "a"})["caption"]
    fit = v["fit"]
    assert v["border"]["width"] == pytest.approx(1 - 80 / 1920) and v["border"]["height"] == pytest.approx(fit["bar_height"])
    assert v["bar_width"] == pytest.approx(1 - 92 / 1920) and v["bar_height"] == pytest.approx(fit["bar_height"] - 12 / 1080)
    assert v["border"]["rgba"] == [255, 255, 255, 255] and v["corner_radius"] == 0.15
    # 行の終わりのマークは板の右下の内側
    mx, my = v["mark"]["center"]
    assert 0.5 < mx < 1 - 46 / 1920 and fit["bar_center"] - fit["bar_height"] / 2 < my < fit["bar_center"]
    assert v["mark"]["size"] == pytest.approx(v["size"] * 0.6)


def test_inset_narrows_text_room():
    # 縁の内側に収まらない長さの文は、寄せないときより小さくなる
    text = "あ" * 21
    wide = C.fit_caption(LOOK, text, C.caption_font(LOOK))
    if wide["warnings"] and "測っていない" in wide["warnings"][0]:
        pytest.skip("書体のファイルが無い")
    narrow = C.fit_caption(look(bar_inset=300, border_width=10), text, C.caption_font(LOOK))
    assert narrow["size"] < wide["size"] or len(narrow["lines"]) > len(wide["lines"])


@pytest.mark.parametrize("kv", [{}, {"bar_inset": 200, "border_width": 6}, {"margin_x": 10}])
def test_text_stays_left_of_end_mark(kv):
    # セーフエリアが全幅のとき、板いっぱいの文でも、文の右の端 (書体で測った幅) がマークの左の端より左にある
    sb = look(end_mark="▼", **kv)
    sb["safe_area"] = {"aspect": "1920:1080"}
    font = C.caption_font(LOOK)
    W = sb["timeline"]["width"]
    for text in ("あ" * 30, "あ" * 60):
        v = C.scene_values(sb, {"caption": text}, font)["caption"]
        fit = v["fit"]
        if fit["warnings"] and "測っていない" in fit["warnings"][0]:
            pytest.skip("書体のファイルが無い")
        tw = max(C.text_width(font, fit["size"], x) for x in fit["lines"])
        text_right = W * v["center"][0] + tw / 2   # 文は板の中央に置く
        mark_left = v["mark"]["center"][0] * W - fit["size"] * 0.6 / 2
        assert text_right < mark_left
