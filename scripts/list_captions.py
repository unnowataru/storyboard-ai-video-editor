"""構成表のテロップを Markdown の表に書き出す。誰が何をするかが文から読み取れるかを、構成表の内容を知らない読み手に
確認してもらうときに使う (docs/storyboard.md の「テロップ (caption)」)。

使い方: python scripts/list_captions.py 構成表.json
  列は 行の番号・書き出しの時刻 (行の頭)・肩テロップ (badge)・テロップの 1 行目・2 行目 (3 行目より後は 2 行目の列に続ける)。
  結びのテロップは肩テロップの列に「(結び)」と書く。テロップの無い行は出さない。
  続く行のテロップと肩テロップが同じなら 1 つにまとめ、行の番号を「3〜5」のように書く。
書き出しの時刻は common.row_spans (組む側と同じ決め方)。fps と seconds_per_row の既定は config から重ねる。
"""
import argparse

import common as C


def caption_rows(sb):
    """テロップのある行の list。要素は {row, at (書き出しの秒), badge, closing, lines}。"""
    look, _ = C.config().storyboard_values(sb)
    sb = {**look, "rows": sb["rows"]}
    fps = float(sb["fps"])
    out = []
    for i, (r, (at, _)) in enumerate(zip(sb["rows"], C.row_spans(sb))):
        if not r.get("caption"):
            continue
        out.append({"row": i + 1, "at": at / fps, "badge": r.get("badge"),
                    "closing": r.get("caption_style") == "closing",
                    "lines": str(r["caption"]).replace("|", "\n").split("\n")})
    return out


def merge_same(rows):
    """続く行 (行の番号が続き、テロップ・肩テロップ・結びかどうかが同じ) を 1 つにまとめる。まとめた最後の行を last に書く。"""
    out = []
    for x in rows:
        p = out[-1] if out else None
        if (p and x["row"] == p["last"] + 1 and
                (x["lines"], x["badge"], x["closing"]) == (p["lines"], p["badge"], p["closing"])):
            p["last"] = x["row"]
        else:
            out.append({**x, "last": x["row"]})
    return out


def _cell(s):
    return str(s).replace("|", "\\|")


def to_markdown(rows):
    """caption_rows (か merge_same) の行を Markdown の表にする。"""
    out = ["| 行 | 時刻 | 肩テロップ | 1 行目 | 2 行目 |", "|---|---|---|---|---|"]
    for x in rows:
        last = x.get("last", x["row"])
        n = str(x["row"]) if last == x["row"] else f"{x['row']}〜{last}"
        badge = "" if x["badge"] is None else (x["badge"] or "(接頭辞だけ)")
        if x["closing"]:
            badge = (badge + " " if badge else "") + "(結び)"
        first = x["lines"][0]
        rest = " / ".join(x["lines"][1:])
        out.append(f"| {n} | {C.sec_to_mss(x['at'])} | {_cell(badge)} | {_cell(first)} | {_cell(rest)} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("storyboard")
    a = ap.parse_args()
    rows = caption_rows(C._read_json(a.storyboard))
    print(to_markdown(merge_same(rows)))


if __name__ == "__main__":
    main()
