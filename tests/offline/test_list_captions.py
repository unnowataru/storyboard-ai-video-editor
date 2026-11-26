"""テロップの一覧 (list_captions.py) と、テロップの行の中の読点の警告 (common.fit_caption) のテスト。Resolve は要らない。"""
import os
import subprocess
import sys

import common as C
import list_captions as L
import sb2resolve as S

LOOK = C._read_json(C.DEFAULTS)["storyboard"]   # local.json の影響を受けない既定値 (1920x1080)


# ---------- 読点の警告 ----------
def test_caption_comma_detects_commas_but_not_digit_separators():
    assert C.caption_comma("手順どおりに進めれば、")
    assert C.caption_comma("値を入力し，確認する")
    assert C.caption_comma("Open the file, then save")
    assert not C.caption_comma("1,000 件を読み込む")
    assert not C.caption_comma("結果まで確認できる")


def comma_warnings(fit):
    return [w for w in fit["warnings"] if "読点" in w]


def test_fit_caption_warns_per_line_with_comma():
    """`|` と改行で区切った行ごとに見る。書体が無くても (幅を測らなくても) 警告する。"""
    fit = C.fit_caption(LOOK, "値を入力し、|画面を確認する")
    assert comma_warnings(fit) == ["テロップの行「値を入力し、」に読点がある。区切りは | の改行で示す"]
    assert len(comma_warnings(C.fit_caption(LOOK, "一つ目、\n二つ目、"))) == 2
    assert comma_warnings(C.fit_caption(LOOK, "値を入力する|画面を確認する")) == []
    # 結びのテロップも同じ
    assert comma_warnings(C.fit_caption(LOOK, "手順どおりに進めれば、|結果まで確認できる", style="closing"))
    # 警告だけで、文は変えない
    assert fit["lines"] == ["値を入力し、", "画面を確認する"]


def test_dry_run_values_report_comma_with_row_number(tmp_path):
    """組む前の点検 (row_values。--dry-run と組むときが使う) で、行の番号付きの警告になり、誤りにはしない。"""
    src = tmp_path / "rec.mp4"
    src.write_bytes(b"")
    rows = [{"start": 0, "end": 5, "caption": "値を入力する"},
            {"start": 5, "end": 10, "caption": "値を入力し、|画面を確認する"}]
    sb = dict(LOOK, source=str(src), output=str(tmp_path / "o.mp4"), rows=rows)
    _, warns, errs = S.row_values(sb, S.plan(sb), {str(src): (1920, 1080)}, None)
    assert [w for w in warns if "読点" in w] == ["2 行目: テロップの行「値を入力し、」に読点がある。区切りは | の改行で示す"]
    assert errs == []


def test_examples_have_no_comma_in_caption_lines():
    """見本の構成表のテロップは、行の中に読点を打たない (docs/storyboard.md のテロップの文の書き方)。"""
    for name in ("sample1-storyboard.json", "sample2-storyboard.json"):
        sb = C._read_json(os.path.join(C.REPO, "examples", name))
        for r in sb["rows"]:
            for line in str(r.get("caption") or "").replace("|", "\n").split("\n"):
                assert not C.caption_comma(line), (name, line)


# ---------- 一覧 ----------
SB = {"seconds_per_row": 4, "fps": 30, "rows": [
    {"start": 0, "end": 4, "caption": "設定画面を開く|人が値を入力する", "badge": "人間の作業"},
    {"start": 4, "end": 30, "seconds": 2, "caption": "処理が進む", "badge": "13倍速"},
    {"start": 30, "end": 34},
    {"start": 34, "end": 38, "caption": "結果を確認する"},
    {"start": 38, "end": 42, "caption": "結果を確認する", "hold": {"seconds": 1}},
    {"start": 42, "end": 46, "caption": "結果を確認する", "badge": ""},
    {"start": 46, "end": 50, "caption": "一覧が|そろう|続きの行", "caption_style": "closing"},
]}


def test_caption_rows_times_and_lines():
    rows = L.caption_rows(SB)
    assert [x["row"] for x in rows] == [1, 2, 4, 5, 6, 7]   # テロップの無い行は出さない
    # 書き出しの時刻は行の頭 (秒数は seconds、無ければ seconds_per_row。止めた区間の分だけ後ろがずれる)
    assert [x["at"] for x in rows] == [0, 4, 10, 14, 19, 23]
    assert rows[0]["lines"] == ["設定画面を開く", "人が値を入力する"]


def test_markdown_merges_same_consecutive_captions():
    md = L.to_markdown(L.merge_same(L.caption_rows(SB))).split("\n")
    assert md == [
        "| 行 | 時刻 | 肩テロップ | 1 行目 | 2 行目 |",
        "|---|---|---|---|---|",
        "| 1 | 0:00.0 | 人間の作業 | 設定画面を開く | 人が値を入力する |",
        "| 2 | 0:04.0 | 13倍速 | 処理が進む |  |",
        "| 4〜5 | 0:10.0 |  | 結果を確認する |  |",
        "| 6 | 0:19.0 | (接頭辞だけ) | 結果を確認する |  |",   # 肩テロップが違えばまとめない
        "| 7 | 0:23.0 | (結び) | 一覧が | そろう / 続きの行 |",
    ]


def test_list_captions_cli_uses_config_defaults(tmp_path):
    """fps と seconds_per_row を書かない構成表は config の値で時刻を出す。表の中の | は逃がす。"""
    p = tmp_path / "sb.json"
    p.write_text('{"rows": [{"start": 0, "end": 1, "caption": "a"}, {"start": 1, "end": 2, "caption": "b"}]}',
                 encoding="utf-8")
    r = subprocess.run([sys.executable, os.path.join(C.REPO, "scripts", "list_captions.py"), str(p)],
                       capture_output=True, text=True, encoding="utf-8", check=True)
    per = C.config().storyboard_values()[0]["seconds_per_row"]
    lines = r.stdout.strip().split("\n")
    assert lines[2] == "| 1 | 0:00.0 |  | a |  |"
    if isinstance(per, (int, float)):
        assert lines[3] == f"| 2 | {C.sec_to_mss(per)} |  | b |  |"
    assert L._cell("a|b") == "a\\|b"
