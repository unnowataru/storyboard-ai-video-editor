"""機能の一覧 (docs/features.json) を表で出す・JSON で出す・docs/features.md を作り直す。

使い方:
  python scripts/features.py [語]          # 一覧を表で出す。語を渡すと、名前・中身・項目・スクリプトにその語を含む機能だけ
                                       # (語が docs/glossary.md の言い換えの表にあれば、このリポジトリの言葉に読み替えて探す)
  python scripts/features.py --json [語]   # 同じものを JSON で出す (見本の行と、{a,b} を展開した項目を追加したもの)
  python scripts/features.py --write-doc   # docs/features.md を一覧から作り直す
  python scripts/features.py --check       # docs/features.md が一覧から作ったものと同じか (違えば終了コード 1)

一覧の項目 (docs/features.json の features の各要素):
  id・group (分類)・name・what (何ができるか)・storyboard (構成表の項目。行は rows. で始める。全体の項目のうち
  config の storyboard にもあるものは設定の値も兼ねる)・config (config/defaults.json の storyboard 以外の項目)・
  commands (「スクリプト --引数」)・scripts (スクリプト。scripts/ の名前か、リポジトリからのパス)・check_final (照らし合わせの項目)・
  examples (見本の行の選び方。省略すると storyboard の項目を書いた行)・docs (「パス#見出し」)・note (補足)。
  項目の {a,b} は a と b の両方を表す。
見本の行は examples/*.json から数える (手で書かない)。docs/features.md は手で書き換えず、一覧を直してから --write-doc で作る。
機能を追加したら一覧にも追加する。tests/offline/test_features_catalog.py が、構成表と config の項目・scripts/ のスクリプト・
スクリプトの引数・照合の項目 (check_final.ITEMS) が全部どれかの機能に載っているか、リンク先・スクリプト・引数・照合の項目があるか、
docs/features.md が一覧と同じかを検査する。scripts はスクリプトだけ (テストは note か docs に書く)。
"""
import argparse
import glob
import json
import os
import re
import sys

import common as C

CATALOG = os.path.join(C.REPO, "docs", "features.json")
GLOSSARY = os.path.join(C.REPO, "docs", "glossary.md")
DOC = os.path.join(C.REPO, "docs", "features.md")
EXAMPLES = os.path.join(C.REPO, "examples")


def load(path=CATALOG):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def expand(s):
    """"rows.focus.{x,y}" を ["rows.focus.x", "rows.focus.y"] にする ({} は入れ子にしない。いくつ並んでもよい)。"""
    m = re.search(r"\{([^{}]*)\}", s)
    if not m:
        return [s]
    return [y for x in m.group(1).split(",") for y in expand(s[:m.start()] + x.strip() + s[m.end():])]


def expanded(f, field):
    return [y for x in f.get(field) or [] for y in expand(x)]


# ---------- 見本の行 ----------
def _speed(sb, r):
    """見本の行の速さ (使う範囲の長さ ÷ 行の長さ)。行の長さは common.row_clips (組む側と同じ決め方。seconds、無ければ
    seconds_per_row、"source" なら等倍)。fps と seconds_per_row の既定は config から重ねる (見本の構成表は素のまま読む)。"""
    look, _ = C.config().storyboard_values(sb)
    clip = C.row_clips({**look, "source": sb.get("source"), "rows": [r]})[0]
    return (clip["end"] - clip["start"]) * float(look["fps"]) / clip["frames"] if clip["frames"] > 0 else 1.0


# 項目の有無で選べない見本の選び方 (名前は「@」で始める)
PREDICATES = {
    "@倍速": lambda sb, r: _speed(sb, r) > 1 + 1e-6,
    "@遅く": lambda sb, r: _speed(sb, r) < 1 - 1e-6,
    "@切り抜きが複数": lambda sb, r: isinstance((r.get("callout") or {}).get("src"), list)
    and len(r["callout"]["src"]) > 1,
    "@置き場所の省略": lambda sb, r: isinstance(r.get("callout"), dict) and "dst" not in r["callout"],
    "@肩テロップの速さ": lambda sb, r: C.badge_speed_label(r.get("badge")) is not None,
}


def _values(v, parts):
    """v の中の parts の道筋の値 (list は各要素をたどる)。"""
    if isinstance(v, list):
        return [y for x in v for y in _values(x, parts)]
    if not parts:
        return [v]
    if isinstance(v, dict) and parts[0] in v:
        return _values(v[parts[0]], parts[1:])
    return []


def _same(x, want):
    if isinstance(x, bool):
        return str(x).lower() == want.lower()
    if isinstance(x, (int, float)):
        try:
            return float(want) == x
        except ValueError:
            return False
    return isinstance(x, str) and x == want


def _has(v, spec):
    """v に spec の項目があるか。「項目=値」なら値も同じか。"""
    path, eq, want = spec.partition("=")
    got = _values(v, path.split("."))
    return any(_same(x, want) for x in got) if eq else bool(got)


def example_specs(f):
    """見本の行の選び方。examples が無ければ、構成表の項目 (storyboard) を書いた行と全体。"""
    return f["examples"] if "examples" in f else expanded(f, "storyboard")


def example_hits(f, files=None):
    """[(見本の名前, 全体に書いてあるか, 行の番号の list, 行の数)]。当たりの無い見本は入れない。"""
    out = []
    for path in sorted(files or glob.glob(os.path.join(EXAMPLES, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            sb = json.load(fh)
        name = os.path.basename(path).split("-")[0].capitalize()
        specs = example_specs(f)
        top = any(not s.startswith(("rows.", "@")) and _has(sb, s) for s in specs)
        rows = [n for n, r in enumerate(sb.get("rows") or [], 1)
                if any(PREDICATES[s](sb, r) if s.startswith("@") else s.startswith("rows.") and _has(r, s[5:])
                       for s in specs)]
        if top or rows:
            out.append((name, top, rows, len(sb.get("rows") or [])))
    return out


def _ranges(ns):
    out, i = [], 0
    while i < len(ns):
        j = i
        while j + 1 < len(ns) and ns[j + 1] == ns[j] + 1:
            j += 1
        out.append(f"{ns[i]}〜{ns[j]}" if j - i >= 2 else "・".join(str(x) for x in ns[i:j + 1]))
        i = j + 1
    return "・".join(out)


def example_text(f, files=None):
    parts = []
    for name, top, rows, n in example_hits(f, files):
        what = (["全体"] if top else []) + (["全部の行" if len(rows) == n else f"{_ranges(rows)} 行目"] if rows else [])
        parts.append(f"{name} の" + "".join(("と" if i else "") + (" " if w[0].isdigit() else "") + w
                                           for i, w in enumerate(what)))
    return "、".join(parts)


# ---------- 文書 ----------
def slug(heading):
    """GitHub の見出しのアンカー (小文字にし、字・数字・空白・- _ 以外を除き、空白を - にする)。"""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def doc_link(ref):
    """"docs/storyboard.md#見出し" を docs/features.md からのリンクにする。"""
    path, _, head = ref.partition("#")
    rel = os.path.relpath(os.path.join(C.REPO, path), os.path.dirname(DOC)).replace("\\", "/")
    name = os.path.basename(path)
    return f"[{name}「{head}」]({rel}#{slug(head)})" if head else f"[{name}]({rel})"


def key_label(key, look):
    """構成表の項目の種類: 行・全体・全体と設定 (config の storyboard にもある項目)。"""
    if key.startswith("rows."):
        return "行"
    return "全体・設定" if key.split(".")[0] in look else "全体"


def usage_cell(f, look):
    groups = {}
    for k in f.get("storyboard") or []:
        lab = key_label(k, look)
        groups.setdefault(lab, []).append(k[5:] if lab == "行" else k)
    for k in f.get("config") or []:
        groups.setdefault("設定", []).append(k)
    for k in f.get("commands") or []:
        groups.setdefault("引数", []).append(k)
    order = ("行", "全体", "全体・設定", "設定", "引数")
    return "<br>".join(f"{lab}: " + "、".join(f"`{x}`" for x in groups[lab]) for lab in order if lab in groups)


def _cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


HEADER = """# 機能の一覧

このリポジトリで使える機能の一覧。構成表の項目・設定・コマンドの引数から、どの機能かと詳しい説明の場所を引く。言葉は [glossary.md](glossary.md) に従う。

この文書は [features.json](features.json) から `python scripts/features.py --write-doc` で作る。手で書き換えない。機能を追加したら `features.json` に追加して作り直す (`tests/offline/test_features_catalog.py` が、構成表と `config/defaults.json` の項目・スクリプト・スクリプトの引数・照合の項目が全部どれかの機能に載っているか、この文書が一覧と同じかを検査する)。端末では `python scripts/features.py [語]` (表) と `--json` で引ける (語が [glossary.md](glossary.md) の「指示での言い方」なら読み替えて探す)。

## 読み方

- 使い方: 「行」は構成表の行 (`rows`) の項目、「全体」は構成表の全体の項目、「全体・設定」は構成表の全体か `config` の `storyboard` に書く項目 (構成表 → `config/local.json` → `config/defaults.json` の順に決まる。行に同じ名前の項目があっても、全体に書いたものは既定値の上書き)、「設定」は `config/defaults.json` のほかの項目、「引数」はコマンドの引数。`{a,b}` は `a` と `b` の両方
- 構成表と `config/local.json` の `storyboard` の、どの機能にも無い項目は、`sb2resolve.py` が「知らない項目」と警告する (`_` で始まる項目は見ない)
- 照合: `check_final.py` の項目の名前 (`<N>` は番号)。「なし」は照らし合わせない
- 見本: `examples/` の構成表 (Sample1 は `sample1-storyboard.json`、Sample2 は `sample2-storyboard.json`) で、その機能の項目を書いている行 (`features.py` が数える)。構成表の書式は [storyboard.md](storyboard.md)
"""


def render_md(cat=None, files=None):
    cat = cat or load()
    look = C.config().defaults["storyboard"]
    lines = [HEADER]
    n = 0
    for g in cat["groups"]:
        lines.append(f"## {g}\n")
        lines.append("| # | 機能 | 何ができるか | 使い方 | スクリプト | 照合 | 見本 | 詳しい説明 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for f in cat["features"]:
            if f["group"] != g:
                continue
            n += 1
            what = f["what"] + (f" ({f['note']})" if f.get("note") else "")
            tools = "、".join(f"`{s}`" for s in f.get("scripts") or [])
            checks = "、".join(f"`{c}`" for c in f.get("check_final") or []) or "なし"
            docs = "<br>".join(doc_link(d) for d in f.get("docs") or []) or "この表だけ"
            lines.append("| " + " | ".join(_cell(x) for x in (
                n, f["name"], what, usage_cell(f, look) or "なし", tools or "なし", checks,
                example_text(f, files) or "なし", docs)) + " |")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_doc(path=DOC):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_md())
    return path


# ---------- 画面 ----------
def _pad(s, width):
    """全角を 2 桁と数えて、width 桁まで空白で埋める。"""
    import unicodedata
    n = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)
    return s + " " * max(width - n, 1)


def _plain_term(s):
    """表の語から括弧の補足 (「(枠線の色の話で)」など) と ` を除く。"""
    return re.sub(r"\s*[(（][^()（）]*[)）]", "", s).replace("`", "").strip()


def aliases(path=GLOSSARY):
    """docs/glossary.md の「指示で使われる別の言い方」の表の {指示での言い方 (小文字): [このリポジトリの言葉, ...]}。"""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    head = "## 指示で使われる別の言い方"
    out = {}
    for line in (text.split(head, 1)[1] if head in text else "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.startswith("|") or len(cells) != 2 or set(cells[0]) <= set("-") or cells[0] == "指示での言い方":
            continue
        to = [t for t in (x.strip() for x in _plain_term(cells[1]).split("、")) if t]
        for w in (x.strip().lower() for x in _plain_term(cells[0]).split("、")):
            if w:
                out.setdefault(w, [])
                out[w] += [t for t in to if t not in out[w]]
    return out


def matches(f, word, alias=None):
    """機能 f が語 word を含むか。word が glossary の指示での言い方なら、読み替えた言葉でも探す。"""
    alias = aliases() if alias is None else alias
    words = [word.lower()] + [t.lower() for t in alias.get(word.lower(), [])]
    hay = [f["id"], f["name"], f["what"], f.get("note", "")] + expanded(f, "storyboard") + expanded(f, "config") \
        + (f.get("commands") or []) + (f.get("scripts") or []) + (f.get("check_final") or [])
    return any(w in str(x).lower() for w in words for x in hay)


def as_json(cat, word=None):
    out = []
    n = 0
    alias = aliases()
    for g in cat["groups"]:
        for f in cat["features"]:
            if f["group"] != g:
                continue
            n += 1
            if word and not matches(f, word, alias):
                continue
            out.append({"no": n, **f, "storyboard_keys": expanded(f, "storyboard"), "config_keys": expanded(f, "config"),
                        "example_rows": example_text(f)})
    d = {"catalog": os.path.relpath(CATALOG, C.REPO).replace("\\", "/"), "count": len(out), "features": out}
    if word and alias.get(word.lower()):
        d["read_as"] = alias[word.lower()]   # glossary で読み替えた言葉
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("word", nargs="?",
                    help="名前・中身・項目・スクリプトにこの語を含む機能だけ出す (glossary の指示での言い方は読み替える)")
    ap.add_argument("--json", action="store_true", help="JSON で出す")
    ap.add_argument("--write-doc", action="store_true", help="docs/features.md を一覧から作り直す")
    ap.add_argument("--check", action="store_true", help="docs/features.md が一覧と同じか (違えば終了コード 1)")
    a = ap.parse_args()
    cat = load()
    if a.write_doc:
        print(f"OK features: {len(cat['features'])} 件を書いた  詳細: {write_doc()}")
        return 0
    if a.check:
        with open(DOC, encoding="utf-8") as fh:
            same = fh.read() == render_md(cat)
        print(("OK features: docs/features.md は一覧と同じ" if same
               else "NG features: docs/features.md が一覧と違う (python scripts/features.py --write-doc で作り直す)"))
        return 0 if same else 1
    d = as_json(cat, a.word)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 0
    print(f"{'#':>3}  {_pad('分類', 14)} {_pad('機能', 48)} スクリプト")
    for f in d["features"]:
        print(f"{f['no']:>3}  {_pad(f['group'], 14)} {_pad(f['name'], 48)} {', '.join(f.get('scripts') or [])}")
    if d.get("read_as"):
        print(f"「{a.word}」を {'・'.join(d['read_as'])} と読み替えて探した (docs/glossary.md)")
    print(f"{d['count']} 件 (詳しくは docs/features.md、--json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
