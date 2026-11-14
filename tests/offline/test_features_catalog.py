"""機能の一覧 (docs/features.json、scripts/features.py、docs/features.md) のテスト。Resolve と FFmpeg は要らない。

構成表に書ける項目は common.storyboard_keys (sb2resolve.py の「知らない項目」の点検と同じもの)、設定の項目は
config/defaults.json から読む。ここに項目の一覧を書かない。
"""
import argparse
import ast
import functools
import glob
import json
import os
import re
import shutil
import subprocess

import pytest

from conftest import patch_common

import common as C
import features as F
import sb2resolve as S

CAT = F.load()
FEATURES = CAT["features"]


def _defaults():
    with open(C.DEFAULTS, encoding="utf-8") as f:
        return json.load(f)


def _config_leaves(d):
    """config/defaults.json の storyboard 以外の項目。paths は場所の名前ごと (その下の env・search は 1 つとみなす)。"""
    out = []

    def walk(v, key):
        if isinstance(v, dict) and not key.startswith("paths."):
            for k, x in v.items():
                walk(x, f"{key}.{k}" if key else k)
        else:
            out.append(key)
    walk({k: v for k, v in d.items() if k != "storyboard"}, "")
    return out


def _all(field):
    return {k for f in FEATURES for k in F.expanded(f, field)}


# ---------- (a) 項目が全部どれかの機能に載っている ----------
def test_every_storyboard_key_is_in_a_feature():
    """構成表の行と全体の項目 (config の storyboard の見た目の項目を含む) は、どれかの機能の storyboard に載っている。"""
    paths = C.key_paths(C.storyboard_keys(_defaults()))
    leaves = {p for p, leaf in paths if leaf}
    missing = sorted(leaves - _all("storyboard"))
    assert not missing, f"機能の一覧 (docs/features.json) に無い構成表の項目: {missing}"


def test_every_listed_storyboard_key_exists():
    """一覧に書いた構成表の項目は、どれも構成表に書ける項目 (古い名前・書き間違いが無い)。"""
    known = {p for p, _ in C.key_paths(C.storyboard_keys(_defaults()))}
    bad = sorted(_all("storyboard") - known)
    assert not bad, f"構成表に無い項目が一覧にある: {bad}"


def test_every_config_key_is_in_a_feature():
    """config/defaults.json の storyboard 以外の項目は、どれかの機能の config に載っていて、一覧の項目はどれも有る。"""
    leaves = set(_config_leaves(_defaults()))
    listed = _all("config")
    assert not sorted(leaves - listed), f"機能の一覧に無い設定の項目: {sorted(leaves - listed)}"
    assert not sorted(listed - leaves), f"config/defaults.json に無い項目が一覧にある: {sorted(listed - leaves)}"


def test_storyboard_look_keys_come_from_defaults():
    """全体の項目には config の storyboard の全部の節が入る (二重に書いていない)。"""
    d = _defaults()
    shape = C.storyboard_keys(d)
    for sec, v in d["storyboard"].items():
        assert sec in shape
        if isinstance(v, dict):
            assert set(v) <= set(shape[sec])
    # 結びのテロップには caption の色・書体・余白も書ける (位置と fusion_size は除く)
    assert {"text_rgb", "bar_rgba", "font_name", "margin_x"} <= set(shape["closing"])
    assert not {"position", "lift", "fusion_size"} & set(shape["closing"])


# ---------- (b) 文書へのリンク ----------
def _headings(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"```.*?```", "", text, flags=re.S)   # コードの中の # は見出しではない
    return {m.group(1).strip() for m in re.finditer(r"^#{1,6}\s+(.+)$", text, flags=re.M)}


def test_doc_links_exist():
    for f in FEATURES:
        for ref in f.get("docs") or []:
            path, _, head = ref.partition("#")
            full = os.path.join(C.REPO, path)
            assert os.path.isfile(full), f"{f['id']}: {path} が無い"
            if head:
                assert head in _headings(full), f"{f['id']}: {path} に見出し「{head}」が無い"


# ---------- (c) スクリプトと引数 ----------
def _tool_path(name):
    return os.path.join(C.REPO, name) if "/" in name else os.path.join(C.SCRIPTS, name)


def test_scripts_exist():
    for f in FEATURES:
        for s in f.get("scripts") or []:
            assert os.path.isfile(_tool_path(s)), f"{f['id']}: スクリプト {s} が無い"


def test_command_flags_exist():
    """commands の「スクリプト --引数」の引数と下位コマンドが、そのスクリプトにある (_script_flags で拾ったもの。
    pwsh が無ければ .ps1 は飛ばす)。"""
    for f in FEATURES:
        for cmd in f.get("commands") or []:
            script, *words = cmd.split()
            path = _tool_path(script)
            assert os.path.isfile(path), f"{f['id']}: {script} が無い"
            flags = _script_flags(path)
            if flags is None:
                continue
            missing = [w for w in words if w not in flags]
            assert not missing, f"{f['id']}: {script} に {missing} が無い"


def _check_final_text():
    with open(os.path.join(C.SCRIPTS, "check_final.py"), encoding="utf-8") as fh:
        return fh.read()


def test_check_final_items_exist():
    """照合の項目の名前は、check_final.ITEMS (照らし合わせの項目の名前の一覧) のどれかと同じ (前方一致では通さない)。"""
    import check_final as CF
    for f in FEATURES:
        for item in f.get("check_final") or []:
            assert item in CF.ITEMS, f"{f['id']}: check_final.py の ITEMS に {item} が無い"


def test_check_final_items_in_code_are_listed():
    """check_final.py の _c に書いた項目の名前 (文字の値と f 文字列) は、ITEMS にある。
    変数で組む名前 (マスク・focus・callout の item と、その f"{item}_type") は ITEMS に手で書いてある。"""
    import check_final as CF
    found = re.findall(r'_c\(checks,\s*[^,]+,\s*(f?)"([^"]+)"', _check_final_text())
    assert len(found) > 20
    for fmt, name in found:
        if fmt:
            if "{item}" in name:
                continue
            name = re.sub(r"\{k\}", "<項目>", name)
            name = re.sub(r"\{t\}", "<フレーム>", name)
        assert name in CF.ITEMS, f"check_final.py の項目 {name} が ITEMS に無い"


def test_every_check_final_item_is_in_a_feature():
    """check_final.ITEMS の項目は、どれも一覧のどれかの機能の check_final に載っている。"""
    import check_final as CF
    missing = sorted(set(CF.ITEMS) - {c for f in FEATURES for c in f.get("check_final") or []})
    assert not missing, f"機能の一覧に無い照合の項目: {missing}"


# ---------- (e) スクリプトと引数が全部どれかの機能に載っている ----------
def test_every_script_is_in_a_feature():
    """scripts/ のスクリプト (.py・.ps1・.html) は、どれもどれかの機能の scripts に載っている。
    common.py が読み込み直す部品 (common.PARTS の settings.py・layout.py など) は単独で使うスクリプトではないので、一覧には載せない。"""
    listed = {s for f in FEATURES for s in f.get("scripts") or []}
    parts = {os.path.basename(m.__file__) for m in C.PARTS}
    files = {os.path.basename(p) for ext in ("py", "ps1", "html") for p in glob.glob(os.path.join(C.SCRIPTS, f"*.{ext}"))}
    missing = sorted(files - listed - parts)
    assert not missing, f"機能の一覧に無いスクリプト: {missing}"


# PowerShell の構文解析で、各 .ps1 の param のブロックの変数名を 1 行に「ファイル名<TAB>名前」で出す
PS_PARAMS = r"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
foreach ($p in @(FILES)) {
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { "ERROR`t$p`t$($errors[0].Message)"; continue }
    if ($ast.ParamBlock) {
        foreach ($q in $ast.ParamBlock.Parameters) { "$([System.IO.Path]::GetFileName($p))`t$($q.Name.VariablePath.UserPath)" }
    }
}
"""


@functools.lru_cache(maxsize=1)
def _ps1_params():
    """scripts/*.ps1 の param の名前 {ファイル名: {-名前, ...}}。pwsh が無ければ None (CI の Ubuntu など)。"""
    pwsh = shutil.which("pwsh")
    if not pwsh:
        return None
    files = sorted(glob.glob(os.path.join(C.SCRIPTS, "*.ps1")))
    quoted = ", ".join("'" + p.replace("'", "''") + "'" for p in files)
    r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", PS_PARAMS.replace("FILES", quoted)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr
    out = {os.path.basename(p): set() for p in files}
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        name, value, *rest = line.split("\t")
        assert name != "ERROR", f"{value} の構文の誤り: {rest}"
        out[name].add("-" + value)
    return out


def _script_flags(path):
    """スクリプトの引数の名前。.py は add_argument の呼び出しの文字の引数のうち -- で始まるものと、add_parser の
    最初の引数 (下位コマンド) を ast で拾う。.ps1 は param のブロックの変数名を PowerShell の構文解析で拾う
    (pwsh が無ければ None)。"""
    if path.endswith(".ps1"):
        ps = _ps1_params()
        return None if ps is None else ps[os.path.basename(path)]
    with open(path, encoding="utf-8-sig") as fh:
        tree = ast.parse(fh.read(), path)
    flags = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        strs = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if node.func.attr == "add_argument":
            flags |= {s for s in strs if s.startswith("--")}
        elif node.func.attr == "add_parser" and node.args and strs and node.args[0].value == strs[0]:
            flags.add(strs[0])
    return flags


def test_script_flags_reads_code_not_text(tmp_path):
    """単一引用符の引数を拾い、コメントの中の文字と、文字でない引数は拾わない。"""
    p = tmp_path / "x.py"
    p.write_text("ap.add_argument('--one', help='x')\n"
                 "ap.add_argument(\"--two\", \"-t\")  # ap.add_argument(\"--not-this\")\n"
                 "sub.add_parser('run')\n"
                 "# sub.add_parser(\"nope\")\n", encoding="utf-8")
    assert _script_flags(str(p)) == {"--one", "--two", "run"}


@pytest.mark.skipif(not shutil.which("pwsh"), reason="pwsh が無い")
def test_ps1_params_ignore_default_values():
    """.ps1 の param の既定値 ($env:TEMP など) は引数とみなさない。"""
    got = _ps1_params()
    assert got and all(not any(x.lower() in ("-env", "-temp", "-psscriptroot") for x in v) for v in got.values()), got


def test_every_flag_is_in_a_feature():
    """スクリプトの引数 (argparse の --引数・下位コマンド、.ps1 の param) は、どれもどれかの機能の commands に載っている。"""
    listed = {}
    for f in FEATURES:
        for cmd in f.get("commands") or []:
            script, *words = cmd.split()
            listed.setdefault(script, set()).update(words)
    missing = []
    for p in glob.glob(os.path.join(C.SCRIPTS, "*.py")) + glob.glob(os.path.join(C.SCRIPTS, "*.ps1")):
        name = os.path.basename(p)
        flags = _script_flags(p)
        if flags is None:   # pwsh が無い環境では .ps1 を飛ばす
            continue
        missing += [f"{name} {x}" for x in sorted(flags - listed.get(name, set()))]
    assert not missing, f"機能の一覧の commands に無い引数: {missing}"


def test_scripts_are_tools_not_tests():
    """一覧のスクリプトの欄にはテスト (tests/) を載せない (実機で確認したテストは docs と note に書く)。"""
    bad = [(f["id"], s) for f in FEATURES for s in f.get("scripts") or [] if s.startswith("tests/")]
    assert not bad, f"スクリプトの欄にテストがある: {bad}"


def test_catalog_shape():
    ids = [f["id"] for f in FEATURES]
    assert len(ids) == len(set(ids)), "id が重なっている"
    for f in FEATURES:
        assert f["group"] in CAT["groups"], f"{f['id']}: 分類 {f['group']} が groups に無い"
        assert f["name"] and f["what"], f["id"]
        for s in f.get("examples") or []:
            if s.startswith("@"):
                assert s in F.PREDICATES, f"{f['id']}: 見本の選び方 {s} が無い"


# ---------- (d) docs/features.md が一覧から作ったものと同じ ----------
def test_doc_matches_catalog():
    with open(F.DOC, encoding="utf-8") as fh:
        got = fh.read()
    assert got == F.render_md(CAT), "docs/features.md が一覧と違う。python scripts/features.py --write-doc で作り直す"
    assert not got.startswith(chr(0xFEFF))   # BOM なし (改行は読むときに LF にそろう)


def test_doc_lists_every_feature():
    text = F.render_md(CAT)
    for f in FEATURES:
        assert f"| {f['name']} |" in text


# ---------- 見本の行と、展開 ----------
def test_expand_braces():
    assert F.expand("rows.focus.{x,y}") == ["rows.focus.x", "rows.focus.y"]
    assert F.expand("{a,b}.{c,d}") == ["a.c", "a.d", "b.c", "b.d"]
    assert F.expand("fps") == ["fps"]


def test_examples_rows_are_counted(tmp_path):
    """見本の行は構成表の項目から数える。倍速は使う範囲と秒数から、値つきの選び方は値も比べる。"""
    sb = {"source": "a.mp4", "seconds_per_row": 6, "caption": {"size": 70},
          "rows": [{"start": 0, "end": 6, "caption": "a"},
                   {"start": 6, "end": 30, "caption": "b", "zoom": {"x": 0, "y": 0, "w": 10, "h": 10, "seconds": 0}},
                   {"start": 30, "end": 31, "seconds": 4, "badge": ""}]}
    p = tmp_path / "case9-storyboard.json"
    p.write_text(json.dumps(sb), encoding="utf-8")
    files = [str(p)]
    assert F.example_text({"storyboard": ["rows.caption", "caption.size"]}, files) == "Case9 の全体と 1・2 行目"
    assert F.example_text({"examples": ["@倍速"]}, files) == "Case9 の 2 行目"
    assert F.example_text({"examples": ["@遅く"]}, files) == "Case9 の 3 行目"
    assert F.example_text({"examples": ["rows.zoom.seconds=0"]}, files) == "Case9 の 2 行目"
    assert F.example_text({"examples": ["rows.badge="]}, files) == "Case9 の 3 行目"
    assert F.example_text({"storyboard": ["rows.start"]}, files) == "Case9 の全部の行"
    assert F.example_text({"examples": []}, files) == ""


def test_features_cli_json(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["features.py", "--json", "focus_border.auto"])
    assert F.main() == 0
    d = json.loads(capsys.readouterr().out)
    assert [f["id"] for f in d["features"]] == ["focus-border-auto"]
    assert "focus_border.auto.dark" in d["features"][0]["storyboard_keys"]
    monkeypatch.setattr("sys.argv", ["features.py", "--check"])
    assert F.main() == 0


# ---------- 知らない項目の点検 (sb2resolve.py) ----------
def test_examples_have_no_unknown_keys():
    """見本の構成表 (examples/) とテスト用の構成表は、知らない項目を使っていない (項目の形が実際の書き方と合っている)。"""
    for path in glob.glob(os.path.join(C.REPO, "examples", "*.json")) + [os.path.join(C.REPO, "tests", "trial-storyboard.json")]:
        with open(path, encoding="utf-8") as fh:
            sb = json.load(fh)
        assert C.unknown_keys(sb) == [], os.path.basename(path)


def test_unknown_keys_found():
    sb = {"source": "a", "captoin": {}, "caption": {"sise": 1, "size": 80}, "_memo": "読まない",
          "masks": [{"x": 0, "y": 0, "w": 1, "h": 1, "keys": [{"t": 0, "x": 0, "y": 0, "w": 1, "h": 1, "z": 1}]}],
          "rows": [{"start": 0, "end": 1, "fucus": {}},
                   {"start": 0, "end": 1, "focus": {"x": 0, "y": 0, "w": 1, "h": 1, "border": True, "bordr": 1}},
                   {"start": 0, "end": 1, "callout": {"src": [{"x": 0, "y": 0, "w": 1, "h": 1, "q": 1}], "order": [1]},
                    "_note": "読まない", "closing": 1}]}
    assert C.unknown_keys(sb) == ["全体の captoin", "全体の caption.sise", "全体の masks.keys.z", "1 行目の fucus",
                                  "2 行目の focus.bordr", "3 行目の callout.src.q", "3 行目の closing"]
    # 結びのテロップの色は書ける。border が true でも dict でもよい
    assert C.unknown_keys({"closing": {"text_rgb": [1, 2, 3]}, "rows": [
        {"focus": {"border": {"color": "auto", "glow_blur": 4}}}]}) == []


def test_dry_run_warns_unknown_keys(tmp_path, monkeypatch):
    """--dry-run で、知らない項目を警告に出す (誤りにはしない)。"""
    src = tmp_path / "rec.mp4"
    src.write_bytes(b"")
    path = tmp_path / "sb.json"
    C.write_json(str(path), {"source": str(src), "output": str(tmp_path / "o.mp4"), "fps": 30,
                             "rows": [{"start": 0, "end": 5, "seconds": 5, "caption": "a", "fucus": {"x": 1}}]})
    monkeypatch.setattr(S, "probe_sources", lambda rows: {str(src): {"width": 1920, "height": 1080, "fps": 30.0,
                                                                      "duration": 60.0}})
    patch_common(monkeypatch, "connect_resolve", lambda *a, **k: pytest.fail("接続した"))
    log = {}
    ok, summary, lines = S.run(argparse.Namespace(storyboard=str(path), source=None, name=None, render=False,
                                                  dry_run=True), log)
    assert ok and "誤り 0" in summary
    assert any("知らない項目 1 行目の fucus" in x for x in log["warnings"])


def test_search_reads_glossary_aliases(capsys, monkeypatch):
    """語で絞るとき、docs/glossary.md の「指示での言い方」(札・黒バックなど) はこのリポジトリの言葉に読み替えて探す。"""
    a = F.aliases()
    assert a["札"] == ["肩テロップ"] and a["黒バック"] == ["暗い背景", "明るい背景"]
    assert a["帯"] == ["テロップの背景", "見る範囲"]   # 括弧の中の読点で切らない
    d = F.as_json(CAT, "札")
    assert d["read_as"] == ["肩テロップ"] and "badge" in [f["id"] for f in d["features"]]
    assert "focus-border-auto" in [f["id"] for f in F.as_json(CAT, "黒バック")["features"]]
    assert "read_as" not in F.as_json(CAT, "肩テロップ")
