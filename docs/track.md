# 録画の中の文字を追って隠す (track.py・track2masks.py)

録画の中の IP アドレス・ホスト名・指定の文字列・画面の部品を見つけて追いかけ、構成表の動くマスクにする。

1. `track.py` が録画を読み、隠す位置を JSON (位置の JSON) に書く
2. `track2masks.py` が位置の JSON を構成表の行ごとのマスク (`masks`) に直し、マスクを追加した構成表を書く
3. その構成表を `sb2resolve.py` で組む。隠すのは Resolve の中 (Fusion のマスク) で、元の録画は加工しない

マスクの書式は [storyboard.md の「マスク (masks)」](storyboard.md#マスク-masks)。

## 使い方

```
python scripts/track.py 録画.mp4 --ip --host "srv-*" --text admin
python scripts/track2masks.py 構成表.json 作業フォルダー\録画\録画.track.json
```

`track.py` の引数 (`--ip`・`--host`・`--text`・`--template` のどれかが要る):

| 引数 | 中身 |
|---|---|
| `--ip` | IPv4 のアドレス (`数.数.数.数`) を探す |
| `--host 形` | ホスト名を `*` `?` の形で探す (例 `srv-*`。大文字小文字を区別しない)。何度でも |
| `--text 文字列` | 文字列を探す (大文字小文字を区別しない。記号と空白は除いて比べる)。何度でも |
| `--template marks.json:番号` | `marker.html` で囲んだ領域を見本にして、画面の部品 (URL バーなど) を追う。何度でも |
| `--out パス` | 位置の JSON の保存先。既定は作業フォルダーの `<録画の名前>\<録画の名前>.track.json` |
| `--keep-frames` | 文字認識に渡したフレームの画像 (位置の JSON と同じフォルダーの `track-frames`) を消さずに残す |

`track2masks.py` の引数:

| 引数 | 中身 |
|---|---|
| `storyboard` | 構成表 |
| `track_json` | 位置の JSON |
| `--type` | 隠し方 `mosaic`・`blur`・`black`。既定は設定の `track.mask_type` (`mosaic`) |
| `--shape` | 形 `rect`・`ellipse`。既定は設定の `track.mask_shape` (`rect`) |

書き出し先は構成表と同じフォルダーの `<構成表の名前>-masks.json`。元の構成表は書き換えない。

## しくみ

- 読むフレーム: 1 フレーム前から画面が変わったフレーム、最後に読んだフレームから変化が積み重なったフレーム、変化が無くても `track.stride_sec` 秒ごとに 1 フレーム。画面が変わったかは、原寸の灰色の画で明るさが `change_level` を超えて変わった画素が `change_pixels` 個以上あるかで決める
- 文字: 読むフレームを `ocr_scale` 倍に広げて、Windows 標準の文字認識 (`scripts/ocr_windows.ps1`、Windows PowerShell 5.1 で動く) で読む。文字認識は手元の PC で動き、画面の中身を外に送らない。単語が当たれば単語の四角全体を隠す
- `--text` は、5 文字以上なら 1 文字の読み誤り (欠け・余分・置き換え) まで一致とみなす
- 部品: 見本の領域を見本の時刻のフレームから切り出し、OpenCV で照合する。一致度が `template_score` 以上なら当たり
- 読まなかったフレームは、直前に読んだフレームの位置のまま。定期の読み取りで初めて見つかった文字は、直前の読み取りまでさかのぼって隠す
- 認識の抜けの補い: ある読み取りで見つかった文字が次で抜け、`lookahead` 回先までに同じものが読めれば、間の読み取りに両方の位置を含む四角を足す。その場所の画が変わっていなければ (差の平均が `still_level` 未満)、前後の読み取りにも位置を引き継ぐ

文字認識が使えるかは `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/ocr_windows.ps1 -Check` (`check_env.py` も確認する) で分かる。

## 位置の JSON の形

`track.py` が書く。見つけた文字そのものは書かず、種類と通し番号 (`ip-1`・`host-2`・`text-1`・`template-0` など) だけを書く。同じ文字列は同じ番号になる。

```json
{
 "version": 1,
 "video": "<録画の絶対パス>",
 "width": 3840, "height": 2160, "fps": 30.0, "frames": 7350,
 "search": {"ip": true, "host": 1, "text": 1, "templates": []},
 "templates": [],
 "pad": 6,
 "frames_read": 412,
 "segments": [
  {"start": 120, "end": 245, "start_sec": 4.0, "end_sec": 8.167,
   "boxes": [{"label": "ip-1", "x": 1702.0, "y": 96.5, "w": 210.0, "h": 34.0}]}
 ]
}
```

| 項目 | 中身 |
|---|---|
| `version` | 形式の版。`track2masks.py` が読めるのは 1 |
| `video` | 録画の絶対パス。`track2masks.py` は、行の素材がこれと同じ行にだけマスクを足す |
| `width`・`height`・`fps`・`frames` | 録画の大きさ・フレームレート・全フレーム数 |
| `search` | 探したもの (`ip` は真偽、`host`・`text` は指定の数、`templates` は `--template` の値) |
| `templates` | 部品の見本ごとの `label` と、囲んだ領域の `shape` |
| `pad` | 書いた時点の設定の `track.pad` |
| `frames_read` | 読んだフレームの数 |
| `segments` | 隠す範囲の組が同じフレームの続き。範囲が無いフレームは入らない |
| `segments[].start`・`end` | 録画のフレームの番号。`end` は含まない (`start` <= フレーム < `end`) |
| `segments[].start_sec`・`end_sec` | 同じ範囲を秒にしたもの |
| `segments[].boxes` | 隠す四角 (録画の画素、余白なし) と `label` |

## 構成表のマスクにする (track2masks.py)

- 座標: 素材の画素のまま書く。四角を `track.pad` (素材の画素) だけ広げ、外側へ整数に丸める
- 時間: 行の素材の範囲 (`start`〜`end`) に重なる segment の部分を、行の頭からの書き出しの秒 (倍速も含む) に直す。変わり目は、変わった後の素材が映る最初の書き出しのフレームに切り上げる
- 動き: 続いた segment の同じ `label` の四角を、中心の近いものどうしつないで 1 つのマスクにする。四辺のどれかが `track.key_step` (素材の画素) 以上動いた所だけ `keys` にし、変わり目の 1 フレーム前に前の位置の key を置く (段差で動く)。動かないものは `keys` の無い固定のマスク
- マスクは行の全体で出る。文字が映っていない間も、その位置を隠す
- 行の既存の `masks` の後ろに足す。`label` は書かない
- 行の素材が位置の JSON の `video` と違う行には足さない。どの行も違えば何も書かずに終了コード 1

## 設定 (config の track)

| 項目 | 既定 | 中身 |
|---|---|---|
| `stride_sec` | 0.5 | 変化が無くても読む間隔 (秒) |
| `change_level` | 32 | 画素が変わったとみなす明るさの差 (0〜255) |
| `change_pixels` | 20 | フレームが変わったとみなす、変わった画素の数 |
| `template_score` | 0.7 | 部品の照合の一致度の下限 |
| `lookahead` | 3 | 認識の抜けを補うとき、先を見る読み取りの数 |
| `still_level` | 8 | その場所の画が変わっていないとみなす差の平均 |
| `ocr_scale` | 2 | 文字認識に渡す画像の倍率 |
| `pad` | 6 | マスクにするときに四角を広げる幅 (素材の画素) |
| `key_step` | 2 | `keys` にする動きの大きさ (素材の画素) |
| `mask_type` | `"mosaic"` | `track2masks.py --type` の既定 |
| `mask_shape` | `"rect"` | `track2masks.py --shape` の既定 |

文字認識を呼ぶ PowerShell は設定の `apps.windows_powershell` (既定 `powershell.exe`)。

## 見逃し

文字認識が読めない文字 (小さい・背景と見分けにくい・`stride_sec` より短く出て消える) は見つからない。書き出した完成版は人が目で確認する。決まった範囲の変化だけを並べた画像は `python scripts/area_sheet.py 書き出した動画.mp4 --area 名前=x,y,w,h` で作れる。
