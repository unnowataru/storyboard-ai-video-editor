"""解説の欄 (layout.side と行の side。common.side_sections・side_values・apply_layout) のテスト。Resolve は要らない。"""
import copy

import pytest

from conftest import patch_common

import common as C

SCREEN = {"x": 600, "y": 40, "w": 2240, "h": 1260}   # 横長の素材の例 (3440x1440) の中のアプリの画面
LOOK = C._read_json(C.DEFAULTS)["storyboard"]   # local.json の影響を受けない既定値 (1920x1080)
SECS = [{"title": "対象", "text": "サンプル A"}, {"title": "種類", "text": "ドキュメント"},
        {"title": "備考", "text": "・手順 1|・待ち長め"}]


def laid(anchor, side=SECS):
    sb = copy.deepcopy(LOOK)
    sb.update({"layout": {"screen": SCREEN, "anchor": anchor, "side": side}, "rows": [{"start": 0, "end": 6}]})
    return C.apply_layout(sb)


def test_sections_from_text_and_list():
    # 2 行以上は左寄せ、1 行は中央。align を書けばそれに従う
    assert C.side_sections("一行目|二行目") == [{"title": None, "lines": ["一行目", "二行目"], "align": "left"}]
    assert C.side_sections(SECS)[2] == {"title": "備考", "lines": ["・手順 1", "・待ち長め"], "align": "left"}
    assert C.side_sections(SECS)[0]["align"] == "center"
    assert C.side_sections([{"text": "a|b", "align": "center"}])[0]["align"] == "center"
    for bad in ([], [{"title": "a"}], [{"text": "a", "x": 1}], [{"text": "a", "align": "right"}], 3):
        with pytest.raises(ValueError):
            C.side_sections(bad)


def test_area_left_for_top_right_and_right_below_badge_for_top_left():
    w = 2240 * 0.78 * 1080 / 1260   # 縮めた画面の幅
    tr = laid("top-right")["side"]["area"]
    assert tr == pytest.approx([12, 12, 1920 - 12 - w - 12, 12 + 0.78 * 1080])
    tl = laid("top-left")["side"]["area"]
    assert tl[0] == pytest.approx(12 + w + 12) and tl[2] == 1920 - 12 and tl[1] > 12   # 肩テロップの下から


def test_sections_fill_area_top_to_bottom():
    sb = laid("top-right")
    v = C.side_values(sb, SECS)
    x0, y0, x1, y1 = sb["side"]["area"]
    secs = v["sections"]
    assert len(secs) == 3 and all(s["title"] and s["rule"] for s in secs)
    tops = [(1 - s["border"]["center"][1] - s["border"]["height"] / 2) * 1080 for s in secs]
    bottoms = [(1 - s["border"]["center"][1] + s["border"]["height"] / 2) * 1080 for s in secs]
    assert tops[0] == pytest.approx(y0) and bottoms[-1] == pytest.approx(y1)
    assert all(b + LOOK["side"]["gap"] == pytest.approx(t) for b, t in zip(bottoms, tops[1:]))
    assert all(s["border"]["width"] * 1920 == pytest.approx(x1 - x0) for s in secs)
    # 2 行の欄は 1 行の欄より高い
    assert secs[2]["border"]["height"] > secs[0]["border"]["height"]
    assert secs[2]["text"]["text"] == "・手順 1\n・待ち長め"
    # 左寄せの中身は、縁と内側の余白と字下げの分だけ欄の左から入った所が行の左の端。1 行の中身は欄の真ん中
    sd = LOOK["side"]
    assert secs[2]["text"]["align"] == "left"
    assert secs[2]["text"]["center"][0] * 1920 == pytest.approx(x0 + sd["border_width"] + sd["pad"] + sd["indent"])
    assert secs[0]["text"]["align"] == "center" and secs[0]["text"]["center"][0] * 1920 == pytest.approx((x0 + x1) / 2)


def test_too_much_text_warns():
    sb = laid("top-right")
    many = [{"title": f"欄{i}", "text": "|".join(["行"] * 6)} for i in range(5)]
    assert [w for w in C.side_values(sb, many)["warnings"] if "高さ" in w]


def test_side_needs_layout():
    with pytest.raises(ValueError):
        C.side_values(copy.deepcopy(LOOK), "a")


def fake_width(monkeypatch):
    # 文の幅を 1 字 = 字の大きさ (px) で測る模擬 (書体のファイルに左右されない)。書体のファイルが None なら測れない
    patch_common(monkeypatch, "text_width", lambda ff, size, text: size * len(text) if ff else None)


def test_long_line_shrinks_only_that_section(monkeypatch):
    # 余白の幅 386.6 px、縁と内側の余白を除いて 350.6 px。測った幅に width_scale 1.08 を掛けるので、
    # size 44 で 1 行 7 字 (308 × 1.08 = 332.6 px) までは縮めない
    fake_width(monkeypatch)
    sb = laid("top-right")
    secs = [{"title": "対象", "text": "テスト用の|設定画面"}, {"title": "名前", "text": "テスト用の設定画面"},
            {"title": "種類", "text": "ドキュメント"}]
    v = C.side_values(sb, secs, "fake.ttf")
    px = [s["text"]["px"] for s in v["sections"]]
    # 9 字 (396 × 1.08 = 427.7 px) の欄だけ、44 × 350.6 ÷ 427.7 = 36.07 を 0.5 px 刻みに切り下げた 36.0 にする
    assert px == [44.0, 36.0, 44.0]
    scale = 1920 * sb["fusion_text_scale"]
    assert [s["text"]["size"] for s in v["sections"]] == pytest.approx([p / scale for p in px])
    assert v["size"] == pytest.approx(44 / scale)   # 上の size は side.size のまま
    assert not [w for w in v["warnings"] if "幅" in w]
    # ほかの欄の字の大きさは、縮む欄が無いときと同じ
    alone = C.side_values(sb, [secs[0], {"title": "名前", "text": "短い"}, secs[2]], "fake.ttf")
    assert [s["text"]["px"] for s in alone["sections"]] == [44.0, 44.0, 44.0]


def test_shrunk_section_height_uses_its_size(monkeypatch):
    # 欄の高さの割り振り (need) は、その欄の字の大きさで計算する
    fake_width(monkeypatch)
    sb = laid("top-right")
    v = C.side_values(sb, [{"text": "テスト用の設定画面"}, {"text": "短い"}], "fake.ttf")
    h = [s["border"]["height"] for s in v["sections"]]
    sd = LOOK["side"]
    need = [2 * sd["border_width"] + 2 * sd["pad"] + p * 1.25 for p in (36.0, 44.0)]
    assert h[0] / h[1] == pytest.approx(need[0] / need[1])


def test_shrink_stops_at_min_size_and_warns(monkeypatch):
    fake_width(monkeypatch)
    sb = laid("top-right")
    assert sb["side"]["min_size"] == 26
    v = C.side_values(sb, [{"text": "あ" * 20}, {"text": "短い"}], "fake.ttf")   # 44 × 350.6 ÷ 880 = 17.5 は下限より小さい
    assert [s["text"]["px"] for s in v["sections"]] == [26.0, 44.0]
    assert [w for w in v["warnings"] if "行を分けるか" in w]


def test_title_is_not_shrunk(monkeypatch):
    fake_width(monkeypatch)
    sb = laid("top-right")
    v = C.side_values(sb, [{"title": "あ" * 12, "text": "短い"}], "fake.ttf")   # 見出し 34 × 12 = 408 px
    assert v["sections"][0]["text"]["px"] == 44.0
    assert v["title_size"] == pytest.approx(34 / (1920 * sb["fusion_text_scale"]))
    assert [w for w in v["warnings"] if "見出し" in w]


def test_unmeasured_keeps_size(monkeypatch):
    fake_width(monkeypatch)
    v = C.side_values(laid("top-right"), [{"text": "テスト用の設定画面"}], None)
    assert v["sections"][0]["text"]["px"] == 44.0
    assert [w for w in v["warnings"] if "測っていない" in w]


def test_bad_layout_side_is_error():
    with pytest.raises(SystemExit):
        laid("top-right", [{"title": "a"}])


def test_side_font_other_than_caption_is_measured():
    # 欄の書体と太さが テロップと違うときも、欄の書体のファイルで幅を測る (テロップの書体のファイルは使わない)
    if not C.config().font_file("Meiryo", "Bold"):
        pytest.skip("Meiryo Bold の書体のファイルが無い")
    pytest.importorskip("PIL")
    sb = laid("top-right")
    sb["caption"].update({"font_name": "Arial", "font_style": "Regular"})
    sb["side"].update({"font_name": "Meiryo", "font_style": "Bold"})
    v = C.side_values(sb, SECS, None)
    assert not [w for w in v["warnings"] if "測っていない" in w]
    assert (v["font_name"], v["font_style"]) == ("Meiryo", "Bold")


def test_merge_side_replaces_only_named_section():
    # 辞書は見出しが同じ欄の text だけを差し替え、ほかの欄・並び・title はそのまま
    got = C.merge_side(SECS, {"種類": "パラメータ"})
    assert [s["title"] for s in got] == ["対象", "種類", "備考"]
    assert got[1]["text"] == "パラメータ" and got[0] == SECS[0] and got[2] == SECS[2]
    assert SECS[1]["text"] == "ドキュメント"   # 全体の欄は書き換えない
    # 並びは全体の順 (辞書に書いた順ではない)
    got = C.merge_side(SECS, {"備考": "・応答を待つ", "対象": "警告"})
    assert [s["text"] for s in got] == ["警告", "ドキュメント", "・応答を待つ"]


def test_merge_side_keeps_text_and_list_as_before():
    assert C.merge_side(SECS, "一行") == "一行"
    other = [{"title": "別", "text": "a"}]
    assert C.merge_side(SECS, other) is other
    assert C.merge_side(SECS, None) is None


def test_merge_side_align():
    # 全体の欄に align が無ければ、差し替えた中身の行数で決める (2 行以上は左寄せ、1 行は中央)
    secs = C.side_sections(C.merge_side(SECS, {"種類": "警告|応答を待つ", "備考": "一行"}))
    assert (secs[1]["align"], secs[1]["lines"]) == ("left", ["警告", "応答を待つ"])
    assert secs[2]["align"] == "center"
    # 全体の欄に align があれば残す
    base = [{"title": "コメント", "text": "a", "align": "center"}, {"title": "メモ", "text": "b"}]
    assert C.side_sections(C.merge_side(base, {"コメント": "警告|応答を待つ"}))[0]["align"] == "center"


@pytest.mark.parametrize("base, side", [
    (SECS, {"知らない": "a"}),           # 全体の欄の見出しに無い
    ("文だけ", {"種類": "a"}),           # 全体が list でない
    (None, {"種類": "a"}),               # 全体に layout.side が無い
    (SECS, {"種類": ["a"]}),             # 値が文でない
    (SECS, {"種類": None}),
    (SECS, {}),                          # 空の辞書
])
def test_merge_side_bad_dict(base, side):
    with pytest.raises(ValueError):
        C.merge_side(base, side)


def test_row_side_dict_in_scene_values():
    sb = laid("top-right")
    sb["rows"] = [{"start": 0, "end": 6, "side": {"備考": "・警告|・応答を待つ"}}]
    texts = [s["text"]["text"] for s in C.scene_values(sb, sb["rows"][0])["side"]["sections"]]
    assert texts == ["サンプル A", "ドキュメント", "・警告\n・応答を待つ"]
    # 行に side が無ければ全体の欄
    texts = [s["text"]["text"] for s in C.scene_values(sb, {"start": 0, "end": 6})["side"]["sections"]]
    assert texts == ["サンプル A", "ドキュメント", "・手順 1\n・待ち長め"]


@pytest.mark.parametrize("layout_side, row_side", [
    (SECS, {"知らない": "a"}), ("文だけ", {"種類": "a"}), (None, {"種類": "a"}), (SECS, {"種類": 3}), (SECS, {}),
])
def test_bad_row_side_dict_stops_on_read_with_row_number(layout_side, row_side):
    sb = copy.deepcopy(LOOK)
    lay = {"screen": SCREEN, "anchor": "top-right"}
    if layout_side is not None:
        lay["side"] = layout_side
    sb.update({"layout": lay, "rows": [{"start": 0, "end": 3}, {"start": 3, "end": 6, "side": row_side}]})
    with pytest.raises(SystemExit) as e:
        C.apply_layout(sb)
    assert str(e.value).startswith("2 行目: ")


def test_good_row_side_dict_passes_read():
    sb = laid("top-right")
    sb["rows"] = [{"start": 0, "end": 6, "side": {"種類": "パラメータ"}}]
    assert C.apply_layout(sb)["rows"][0]["side"] == {"種類": "パラメータ"}
