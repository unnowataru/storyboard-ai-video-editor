# 構成表の書式

構成表は、録画のどこを使い、何を重ねるかを書いた JSON。`sb2resolve.py` (完成版)・`sb2ffmpeg.py` (簡易版)・`check_final.py` (照らし合わせ)・`sb2nle.py` (ほかの形式への変換) が同じ構成表を読む。書ける項目の正は `scripts/schema.py` (`ROW_KEYS`・`STORYBOARD_KEYS`・`storyboard_keys`)。機能ごとの一覧は [features.md](features.md)、言葉は [glossary.md](glossary.md)。

見本は [examples/sample1-storyboard.json](../examples/sample1-storyboard.json) と [examples/sample2-storyboard.json](../examples/sample2-storyboard.json)。素材は `python scripts/sb2ffmpeg_bench.py --make-source` が作る合成の 4K の動画 (作業フォルダーの `bench-4k.mp4`)。

## 全体の形

```json
{
  "source": "{work_dir}/bench-4k.mp4",
  "output": "{out_dir}/sample2-digest.mp4",
  "seconds_per_row": 5,
  "fps": 30,
  "timeline": {"width": 1920, "height": 1080},
  "rows": [
    {"start": "0:03", "end": "0:08", "caption": "4 件の値を|まとめて入力する"},
    {"start": "0:08", "end": "1:00", "seconds": 4, "caption": "時刻の表示を|読み込む", "badge": "13倍速"}
  ]
}
```

- 全体の項目と、行 (`rows`) の list で書く。1 行が書き出しの 1 区切り (クリップ 1 つ)
- `_` で始まる項目 (`_memo` など) は読まない。メモに使える
- 書ける項目に無い名前は「知らない項目」として警告する (読まない)。`sb2resolve.py`・`check_final.py`・`sb2nle.py` が同じ文で出す。`python scripts/sb2resolve.py 構成表.json --dry-run` で Resolve に触らずに確認できる

## 値の決まり方

全体の項目のうち、`config/defaults.json` の `storyboard` にある節 (`caption`・`badge`・`fps` など) は、次の順に決まる (上が優先)。

1. 構成表の全体の項目
2. `config/local.json` の `storyboard`
3. `config/defaults.json` の `storyboard`

節の中は項目ごとに重なる。たとえば構成表に `"caption": {"size": 104.0}` とだけ書けば、ほかの `caption` の値は設定のまま。どの値がどこから来たかは次で表示する。

```
python scripts/common.py --show-config 構成表.json
```

設定ファイルの置き場所と書き方は [setup.md](setup.md)。

## 座標と時刻

| 種類 | 使う項目 | 単位 |
|---|---|---|
| 素材の画素 (左上が 0,0) | `masks`・`zoom`・`focus`・`callout.src`・`point` の `x` `y`・`layout.screen` | 録画のファイルの画素。4K の録画なら 3840 x 2160 の座標 |
| 書き出しの画素 (左上が 0,0) | `callout.dst`・`callout.gap`・枠線の太さ (`focus.border.width`) | `timeline` の大きさの画面の画素 |
| 素材の時刻 | 行の `start`・`end`、`hold.at` | 秒の数か、`"h:mm:ss"`・`"mm:ss"`・`"mm:ss.s"` の文字 |
| 行の頭からの書き出しの秒 | `zoom.seconds`・`focus.start`・`focus.seconds`・`focus.keys[].t`・`masks[].keys[].t`・`callout.start`・`callout.in` | 書き出した動画で、その行が始まってからの秒 (倍速の行でも書き出しの秒) |

素材の画素は、素材の解像度からタイムラインの画素に直してから組む (素材をタイムラインに全体を収めて置く前提。倍率は縦横の小さいほう、余りは上下か左右に均等)。素材の解像度が分からない行で座標を使う項目があれば誤りにして組まない。

## 全体の項目

| 項目 | 型 | 既定 | 中身 |
|---|---|---|---|
| `source` | 文字 | (必須) | 素材の録画のパス。行の `source` が無い行はこれを使う。引数 `--source` が優先 |
| `output` | 文字 | 無し | 完成版の書き出し先。`sb2resolve.py --output` が優先 |
| `project` | 文字 | 無し | 作るプロジェクトの名前 ([書き出しと名前](#書き出しと名前-sb2resolvepy)) |
| `fps` | 数 | 30 | タイムラインと書き出しのフレームレート |
| `seconds_per_row` | 数か `"source"` | 6 | `seconds` の無い行の秒数。`"source"` なら使う範囲の長さ (等倍) |
| `timeline` | `{width, height}` | 1920 x 1080 | タイムラインと書き出しの解像度 |
| `safe_area` | `{aspect}` | `null` (タイムラインの比) | 大事なものを収める中央の範囲の縦横の比 ([セーフエリア](#セーフエリア)) |
| `masks` | list | 無し | どの行にも掛けるマスク ([マスク](#マスク-masks)) |
| `layout` | dict | 無し | 画面を縮めて寄せ、余白にテロップを出す ([配置](#配置-layout)) |
| `rows` | list | (必須) | 行の list ([行の項目](#行の項目)) |
| `caption` ほか | dict | 設定 | `config` の `storyboard` の節。見た目の既定を上書きする ([設定の節の一覧](#設定の節の一覧)) |

### パスの書き方

- `source`・`output`・行の `source` には、場所の設定の名前を `{work_dir}` `{out_dir}` `{trial_source}` のように書ける。場所の設定 (環境変数 → `config/local.json` の `paths` → `config/defaults.json` の探し方) で置き換える。決まらなければ止まる
- 環境変数 (`%USERPROFILE%` など) と `~` も展開する
- 相対パスは構成表のフォルダーから数える。`--output` の相対パスだけは今のフォルダーから

## 行の項目

| 項目 | 型 | 中身 |
|---|---|---|
| `start` | 時刻 | 素材の使い始め |
| `end` | 時刻 | 素材の使い終わり。`start` より後 |
| `seconds` | 数 | 書き出しでの行の長さ (秒)。無ければ全体の `seconds_per_row` |
| `source` | 文字 | この行だけ別の素材を使う |
| `caption` | 文字 | テロップ ([テロップ](#テロップ-caption)) |
| `caption_style` | `"closing"` | 結びのテロップにする ([結びのテロップ](#結びのテロップ-caption_style)) |
| `badge` | 文字 | 肩テロップ ([肩テロップ](#肩テロップ-badge)) |
| `masks` | list | この行だけのマスク ([マスク](#マスク-masks)) |
| `zoom` | dict | ズーム ([ズーム](#ズーム-zoom)) |
| `focus` | dict | 周りを暗くする ([周りを暗くする](#周りを暗くする-focus)) |
| `hold` | dict | 行の後ろで映像を止める ([映像を止める](#映像を止める-hold)) |
| `callout` | dict | 拡大の重ね表示 ([拡大の重ね表示](#拡大の重ね表示-callout)) |
| `point` | dict | 指すマーク ([指すマーク](#指すマーク-point)) |
| `side` | 文字・list・dict | 解説の欄の差し替え ([解説の欄](#解説の欄-side)) |

### 秒数と速さ

- 行の速さは「使う範囲 (`end` − `start`) ÷ 書き出しの長さ (`seconds`)」。`seconds` が範囲より短ければ倍速、長ければ遅くする
- `seconds_per_row` を `"source"` にすると、`seconds` を書かない行は等倍
- 遅くする行は、素材に `start` + `seconds` まで要る。素材の長さを越えれば誤り
- 止めた区間 (`hold`) は行の長さに入れない。後ろの行がその分だけずれる

組む前の点検で次を誤りにする: 素材が無い、`end` が `start` より前か同じ、秒数が 0 以下、素材の長さを越える。

## テロップ (caption)

```json
{"start": 101.0, "end": 109.0, "seconds": 8.0, "caption": "結果の一覧が出る|3 つの項目が並ぶ"}
```

- `|` か改行で行を区切る
- 区切った行のまま `caption.size` から 2 px ずつ `caption.min_size` まで縮め、セーフエリアの幅 (左右の `margin_x` を除く) に収める
- `min_size` でも収まらなければ、区切った行ごとに折り返す。折り返す位置は句読点・括弧・字の種類の変わり目を優先し、英数字の語の途中と禁則 (行の頭の句読点・閉じ括弧・小さい仮名・長音、行の末の開き括弧) を避ける
- 行数が `caption.max_lines` を超えるか、幅を超えれば警告
- 位置は `caption.position` (`bottom`・`top`・`center`) と `caption.lift` (画面の端からの距離。画面の高さを 1 とした値)
- 字の幅は書体のファイルを Pillow で測る。書体のファイルが見つからなければ測らずに警告
- 区切った行の中に読点 (「、」「，」と、数字に挟まれていない「,」) があれば警告 (止めない)。結びのテロップも同じ

### テロップの文の書き方

誰が何をするかがテロップの文だけで分かるように書く。

- 主語を省いてよいのは、肩テロップ (`badge`) が出ている間に、肩テロップと同じ主語を省くときだけ。肩テロップの無い行と、肩テロップが消えた後 (行の頭から `badge.seconds` 秒より後) では主語を書く
- 1 つのテロップの中で主語を変えない。主語が変わるなら `|` で 2 行目に分け、2 行目にも主語を書く
- 1 行の中に読点を打たない。区切りは `|` の改行で示す
- 主語と述語を合わせる。「X は、Y を、Z する」のように 1 行に主語と読点を詰め込まない
- 続く行に同じ文を出すときは、全部の行で同じ文にそろえる
- 動画の内容の用語集があれば、語はそれに従う

書き終えたら、テロップの一覧を作り、構成表の内容を知らない読み手 (人か別の AI モデル) に一覧だけを読ませて、行ごとに誰が何をするかを答えてもらう。答えが意図と違う行の文を直す。

```
python scripts/list_captions.py 構成表.json > captions.md
```

一覧は Markdown の表で、列は行の番号・書き出しの時刻 (行の頭)・肩テロップ・テロップの 1 行目・2 行目。結びのテロップは肩テロップの列に「(結び)」と書く。続く行のテロップと肩テロップが同じなら 1 つにまとめ、行の番号を「3〜5」のように書く。

### テロップの板の形

`caption` の次の項目で背景 (板) の形を変える。`closing` にも同じ項目を書ける。

| 項目 | 中身 |
|---|---|
| `bar_inset` | 板の左右を内側に寄せる幅 (px)。0 なら画面の幅いっぱい |
| `corner_radius` | 板の角の丸み (Fusion の RectangleMask の CornerRadius) |
| `border_width`・`border_rgb` | 板の外側の縁の太さ (px) と色。0 なら縁なし |
| `end_mark`・`end_mark_scale` | 板の右下の内側に置く字 (`"▼"` など) と、その字の大きさ (テロップの字の大きさに対する倍率) |

## 結びのテロップ (caption_style)

```json
{"start": 241.0, "end": 244.5, "seconds": 3.5, "caption": "手順どおりに進めれば|結果まで確認できる", "caption_style": "closing"}
```

- `caption_style` に書けるのは `"closing"` だけ
- `caption` に `closing` を重ねた見た目で、画面の中央に出す。位置 (`position`・`lift`・`band`) と `fusion_size` は使わない
- 字の大きさは `closing.size` (書き出しの px)。Text+ の Size には `fusion_text_scale` で直す
- `closing.bar` が `false` なら背景を出さない

## 肩テロップ (badge)

```json
{"start": 41.0, "end": 101.0, "seconds": 3, "caption": "処理が進む", "badge": "20倍速"}
```

- 画面の右上 (セーフエリアの右上の角から `badge.margin` だけ内側) に、`badge.prefix` (既定 `"▶▶ "`) を付けた文を板に載せて出す
- 出るのは行の頭から `badge.seconds` 秒 (既定 2 秒)。止めた区間には出さない
- 板の大きさは `badge.width`・`badge.height` を下限に、文の幅と高さに `pad_x`・`pad_y` を足した大きさまで左と下へ広がる
- `""` (空の文) なら接頭辞だけの肩テロップ
- 文に「N 倍速」(全角の数字も読む) か「等倍」があれば、行の実際の速さと比べる。「N 倍速」は実際の速さを N の桁で四捨五入して N と同じなら合う。「等倍」は 1 との差が 1 % まで。合わなければ組む前の点検で誤り

## マスク (masks)

```json
"masks": [{"type": "mosaic", "shape": "rect", "x": 0, "y": 0, "w": 120, "h": 44}]
```

全体の `masks` はどの行にも、行の `masks` はその行に掛ける (全体の後ろに行の分)。

| 項目 | 型 | 既定 | 中身 |
|---|---|---|---|
| `x` `y` `w` `h` | 数 | (必須) | 隠す範囲 (素材の画素)。`w` と `h` は 0 より大きい |
| `type` | 文字 | `"blur"` | `mosaic` (モザイク)・`blur` (ぼかし)・`black` (黒塗り) |
| `shape` | 文字 | `"rect"` | `rect` (四角)・`ellipse` (楕円) |
| `pixel_frequency` | 数 | 設定 `mask` | モザイクの細かさ |
| `blur_size` | 数 | 設定 `mask` | ぼかしの強さ |
| `black_rgba` | `[R, G, B, A]` | 設定 `mask` | 黒塗りの色 (0〜255) |
| `keys` | list | 無し | 動くマスク。`[{"t": 秒, "x", "y", "w", "h"}, ...]` |

`keys` の `t` は行の頭からの書き出しの秒 (0 以上)。キーの間は直線で動き、最初のキーより前は最初の位置、最後のキーより後は最後の位置のまま。`keys` を書いても `x` `y` `w` `h` は要る。録画の中の文字を追ってマスクを作るのは [track.md](track.md)。

## ズーム (zoom)

```json
"zoom": {"x": 2200, "y": 1200, "w": 1360, "h": 765, "seconds": 1.5}
```

| 項目 | 型 | 既定 | 中身 |
|---|---|---|---|
| `x` `y` `w` `h` | 数 | (必須) | ズーム範囲 (素材の画素) |
| `seconds` | 数 | (必須) | 行の頭から、ズーム範囲が画面いっぱいになるまでの秒。0 なら行の頭から寄った状態 |
| `direction` | 文字 | `"in"` | `out` なら、ズーム範囲から全体へ引く |

- 倍率は、ズーム範囲が画面に収まる大きさ (縦と横の小さいほう)。中心はズーム範囲の中心
- 拡大の補間は全体の `zoom.filter_method` (既定 `Lanczos`。Fusion の Transform の FilterMethod の名前か番号)
- 寄せた後の範囲がセーフエリアを超えれば警告 (位置は動かさない)
- `layout` と同じ構成表では使えない

## 周りを暗くする (focus)

```json
"focus": {"x": 1300, "y": 1000, "w": 520, "h": 120, "dim": 0.25, "feather": 12, "seconds": 5.5,
          "border": {"color": "#FFC800", "width": 6, "radius": 8}}
```

| 項目 | 型 | 既定 | 中身 |
|---|---|---|---|
| `x` `y` `w` `h` | 数 | (必須) | 明るいまま残す範囲 (素材の画素) |
| `dim` | 数 0〜1 | 0.5 | 範囲の外の明るさの倍率 (小さいほど暗い) |
| `feather` | 数 | 40 | 境目のぼかし (素材の画素) |
| `start` | 数 | 0 | 暗くなり始める時刻 (行の頭からの書き出しの秒) |
| `seconds` | 数 | 0.5 | 暗くなりきるまでの秒 |
| `keys` | list | 無し | 範囲を動かす。書き方はマスクの `keys` と同じ |
| `border` | `true` か dict | 無し | 範囲の枠線 |

`start` が行の長さ (止めた区間を含む) 以降なら現れないので誤り。

### 枠線 (border)

`true` なら全体の `focus_border` の値で、dict なら項目ごとに上書きして枠線を出す。枠線は暗くなるのと同じ時刻に同じ速さで現れる。

| 項目 | 既定 | 中身 |
|---|---|---|
| `color` | `"#FFC800"` | `#RRGGBB` か `auto` |
| `width` | 6 | 太さ (書き出しの画素。ズームで寄せるとその倍率で太くなる) |
| `radius` | 8 | 角の丸み (書き出しの画素) |
| `glow` | `false` | `true` なら、ぼかした同じ色を重ねて光らせる |
| `glow_blur` | 12 | 光のぼかしの強さ |

### 枠線の色を背景の明るさで決める (auto)

`color` を `auto` にすると、枠線が現れ始めるときの素材のフレームを読み、範囲の辺の内側 `inside` 画素・外側 `outside` 画素の帯の明るさ (Rec.709 の輝度の `measure`。`median` か `mean`、0〜255) で色を決める。値は全体の `focus_border.auto`。

| 項目 | 既定 | 中身 |
|---|---|---|
| `dark` | `"#FFC800"` | 明るさが `threshold` 未満 (暗い背景) のときの色 |
| `light` | `"#FF3B30"` | `threshold` 以上 (明るい背景) のときの色 |
| `threshold` | 128 | 色を分ける明るさ |
| `near` | 16 | 明るさと `threshold` の差がこれより小さければ、色を書くよう警告 |
| `measure` | `"median"` | 帯の明るさの取り方 |
| `inside`・`outside` | 24・24 | 帯の幅 (素材の画素) |

決めた色と測った明るさは `sb2resolve.py` の記録の `auto` に残る (`--dry-run` でも同じ値を出す)。素材を読めず色を決められなければ誤り。

## 映像を止める (hold)

```json
"hold": {"seconds": 3.5}
```

| 項目 | 型 | 既定 | 中身 |
|---|---|---|---|
| `seconds` | 数 | (必須) | 止めて見せる秒 (0 より大きい) |
| `at` | `"end"` か時刻 | `"end"` | 止める素材のフレーム。`end` はその行で最後に映ったフレーム、時刻なら素材のその時刻 |

- 行のクリップの後ろに、1 枚の画を映し続けるクリップを置く。後ろの行はその分ずれる
- 止めた区間では、テロップ・指すマーク・解説の欄・`layout` はそのまま出る。肩テロップは出ない
- マスクと `focus` の範囲は止めた瞬間の位置で止まる。暗くなる・枠線・`callout`・ズームの動きは行の頭からの時刻のまま続く
- `at` が素材の範囲の外、または止めたい時刻が素材の頭に近すぎて置けなければ誤り

## 拡大の重ね表示 (callout)

素材の一部を切り抜いて拡大し、画面の上に重ねる。切り抜きは素材の画素のまま拡大するので、縮めた画面を拡大するより字がくっきりする。

```json
"callout": {"src": {"x": 1310, "y": 1005, "w": 500, "h": 110}, "dst": {"x": 700, "y": 260, "w": 520},
            "border": {"color": "#FFFFFF", "width": 3}, "shadow": true, "in": 0.4, "start": 5.0,
            "fill": {"color": "#000000", "opacity": 0.7}}
```

| 項目 | 型 | 既定 | 中身 |
|---|---|---|---|
| `src` | `{x, y, w, h}` か、その list | (必須) | 切り抜く範囲 (素材の画素) |
| `dst` | `{x, y, w}` | 自動 | 重ねる全体の左上と幅 (書き出しの画素)。省くと自動の置き場所 |
| `order` | list | 書いた順 | 切り抜きを上から並べる順 (`src` の番号、1 から) |
| `gap` | 数 | 4 | 切り抜きの間 (書き出しの画素) |
| `start` | 数 | 0 | 現れ始める時刻 (行の頭からの書き出しの秒) |
| `in` | 数 | 0.4 | 現れきるまでの秒 |
| `fill` | `true`・`false` か `{color, opacity}` | 設定 | 重ねた全体の四角を埋める半透明の色 |
| `border` | `true`・`false` か `{color, width}` | 設定 | 四角の外側の縁の線 (`width` は書き出しの画素) |
| `shadow` | 真偽 | `true` | 四角の外側に影を付ける |
| `shadow_rgba`・`shadow_offset`・`shadow_blur` | | 設定 | 影の色 (0〜255)・右下へのずれ (px)・ぼかし |

- `src` が list なら、切り抜きを左をそろえて上から縦に並べる (画面で折り返された文を 1 本ずつ切り抜く使い方)。どの切り抜きも同じ倍率で、いちばん幅の広い切り抜きが `dst.w` の幅になる
- `start` が行の長さ (止めた区間を含む) 以降なら誤り

### 重ねる順 (order)

`order` は `src` の番号を 1 回ずつ並べた list。`[3, 1, 2]` なら 3 番目の切り抜きをいちばん上に置く。番号の抜け・重なりは誤り。

### 置き場所の省略 (自動の置き場所)

`dst` を省くと、全体の `callout.auto_dst` で置き場所を決める。

| 項目 | 既定 | 中身 |
|---|---|---|
| `max_scale` | 4 | 拡大の上限 (タイムラインに映る大きさの何倍まで) |
| `max_width` | 1540 | いちばん幅の広い切り抜きの書き出しの幅の上限 (px)。セーフエリアの幅から左右の `margin` を引いた幅も超えない |
| `margin` | 24 | 空きの端から空ける幅 (px) |
| `valign` | `"bottom"` | `bottom` なら空きの下の端、`center` なら空きの中央 |

- 縦の空きはセーフエリアの上から下まで。テロップがあれば、テロップの帯の上と下のうち高いほう
- 空きに収まらなければ倍率を下げる。タイムラインに映る大きさより小さくなれば警告。空きが無ければ誤り
- 横は画面の中央。決めた `dst` と `order` は `sb2resolve.py` の記録の `auto` に残る
- `dst` が無く、知らない項目 (`dts` など) があれば、`dst` の書き間違いとみなして誤りにする

## 指すマーク (point)

画面の 1 点を 👆 などの字で指す。行に `point` を書くと、全体の `point` の値に重ねて使う。

```json
"point": {"x": 1560, "y": 1060, "from": "below"}
```

| 項目 | 型 | 既定 | 中身 |
|---|---|---|---|
| `x` `y` | 数 | (必須) | 指す点 (素材の画素) |
| `from` | 文字 | `"below"` | マークを点のどちら側に置くか: `below`・`above`・`left`・`right` |
| `tilt` | 数 0〜90 | 60 | 指の先を軸に傾ける角度 (度)。手の側が見えている範囲の真ん中へ寄る向き |
| `style` | 文字 | `"emoji"` | `point.fonts` の名前。`emoji` (Segoe UI Emoji) か `line` (Segoe UI Symbol) |
| `size` | 数 | 96 | 寄せた後に見える字の大きさ (px) |
| `color` | `[R, G, B]` | `[255, 200, 0]` | `line` の字の色。`emoji` は書体の色で出る |

- マークはズームの前に置くので、画と一緒に寄る。`layout` があれば画面と一緒に縮む
- 字と指の先の位置は全体の `point.glyphs` (`emoji-up` などの向きごとの `glyph` と、字の中心から指の先までのずれ `tip`。字の大きさを 1 とした値)

## 配置 (layout)

画面の一部 (`layout.screen`) を縮めて上に寄せ、空いた余白にテロップ・肩テロップ・解説の欄を出す (L 字・逆 L 字・額縁の形)。余白は、画面を広げてぼかし暗くしたもので埋める。構成表の全体に書き、全部の行に効く。

```json
"layout": {"screen": {"x": 0, "y": 40, "w": 3840, "h": 2040}, "height": 0.78, "anchor": "top-left", "margin": 12,
           "side": [{"title": "手順", "text": "1. 開く|2. 入力する"}]}
```

| 項目 | 型 | 既定 | 中身 |
|---|---|---|---|
| `screen` | `{x, y, w, h}` | (必須) | 映す範囲 (素材の画素)。窓の題名やタスクバーを外すのに使う |
| `height` | 数 0〜1 | 0.78 | 縮めた画面の高さ (出力の高さに対する割合) |
| `anchor` | 文字 | `"top-left"` | `top-left` (左上)・`top` (上の真ん中)・`top-right` (右上) |
| `margin` | 数 | 12 | 画面と出力の端の間 (px) |
| `background` | `{blur, dim}` | 6.0・0.75 | 余白の背景のぼかし (Fusion の Blur の XBlurSize) と明るさの倍率 (0 で真っ黒) |
| `side` | 文字か list | 無し | 解説の欄 ([解説の欄](#解説の欄-side)) |

- `height`・`anchor`・`margin` の既定は全体の `layout_place`、`background` の既定は全体の `layout_background`
- `layout` があると、セーフエリアは出力の全体になる。テロップは下の余白の上下の真ん中に置く (`caption.band` に余白の範囲が入る)
- どの行にも `zoom` を書けない (誤り)。縮めた画面が出力に収まらなければ止まる

## 解説の欄 (side)

`layout` が空けた右 (`anchor` が `top-right` なら左) の余白に、見出しと中身の欄を上から並べる。`layout` と一緒に使う。

- `layout.side` に文 1 つか、`{title, text, align}` の list を書く。`title` と `align` は省ける。`text` の `|` か改行で行を区切る
- `align` は `left` か `center`。省くと、2 行以上の中身は左寄せ、1 行は中央
- 行の `side` が文か list なら、その行の欄を全部差し替える。`{見出し: 中身}` の dict なら、`layout.side` の同じ見出しの欄の中身だけを差し替える (見出しが無い・中身が文でない・空の dict は誤り)
- 欄ごとに、いちばん長い行が欄の幅に収まらなければ `side.min_size` まで縮める。それでも収まらなければ警告
- 余白の幅が 80 px より狭ければ欄を出せない

## セーフエリア

`safe_area.aspect` は、書き出しの中で大事なものを収める中央の範囲の縦横の比 (`"3:2"` のような文字か、`1.5` のような数)。既定 (`null`) はタイムラインの比で、書き出しの全体がセーフエリアになる。テロップはこの幅に収め、肩テロップはこの右上の角に付ける。ズーム範囲が寄せた後にこの範囲を超えると警告する。`layout` があれば、指定にかかわらず出力の全体。

画面の左右を空けておきたいときは、構成表の全体か `config/local.json` の `storyboard` に書く。

```json
"safe_area": {"aspect": "3:2"}
```

タイムラインより横長の比を書くと、上下を外した範囲になる。

## 設定の節の一覧

構成表の全体に書いて上書きできる、`config/defaults.json` の `storyboard` の節と既定値。上の節で説明した項目は省く。

| 節 | 項目と既定値 |
|---|---|
| `fusion_text_scale` | 0.5。Text+ の Size 1 が、書き出しの画面の幅の何倍の字になるか |
| `caption` | `size` 80 (px)、`min_size` 56、`max_lines` 2、`margin_x` 50 (px、セーフエリアの内側の左右)、`line_gap` 18 (px、行の間)、`pad_y` 15 (px、背景の上下の余白)、`text_rgb` [255,255,255]、`bar_rgba` [10,20,45,220]、`bar_inset` 0、`corner_radius` 0、`border_width` 0、`border_rgb` [255,255,255]、`end_mark` null、`end_mark_scale` 0.6、`position` "bottom"、`band` null、`lift` 0.05、`font_name` "Meiryo"、`font_style` "Bold"、`fusion_size` 0.06 (`size` の字に当たる Text+ の Size) |
| `closing` | `size` 96、`min_size` 72、`max_lines` 2、`line_gap` 24、`pad_y` 28、`bar` true。ほかに `caption` の色・書体・余白・板の形の項目を書ける (書かなければ `caption` の値) |
| `badge` | `rgba` [230,120,20,235] (板の色)、`text_rgb` [255,255,255]、`prefix` "▶▶ "、`seconds` 2、`font_name` "Meiryo"、`font_style` "Bold"、`fusion_size` 0.045、`width` 0.22 (画面の幅を 1)、`height` 0.05 (画面の高さを 1)、`corner_radius` 0.3、`margin` 0.03 (画面の高さを 1)、`pad_x` 24 (px)、`pad_y` 10 (px)、`text_allowance` 1.08 (Pillow で測った文の幅に掛ける倍率) |
| `focus` | `dim` 0.5、`feather` 40、`seconds` 0.5、`start` 0 |
| `focus_border` | `color` "#FFC800"、`width` 6、`radius` 8、`glow` false、`glow_blur` 12、`auto` ([auto](#枠線の色を背景の明るさで決める-auto)) |
| `point` | `size` 96、`from` "below"、`tilt` 60、`style` "emoji"、`color` [255,200,0]、`fonts` (`emoji`・`line` の書体の名前)、`glyphs` (向きごとの字と指の先) |
| `side` | `size` 44 (px、中身)、`min_size` 26、`width_scale` 1.08 (Pillow で測った幅に掛ける倍率)、`title_size` 34 (px、見出し)、`title_band` 1.6 (見出しの帯の高さ。見出しの字の大きさの倍率)、`line_height` 1.25 (中身の 1 行の高さ。字の大きさの倍率)、`pad` 14、`indent` 12 (左寄せの欄の字下げ)、`gap` 16 (欄の間)、`border_width` 4、`corner_radius` 0.06、`panel_rgba` [0,0,0,255]、`border_rgb` [255,255,255]、`text_rgb` [255,255,255]、`font_name` null と `font_style` null (null なら `caption` の書体)、`area` null (`layout` が入れる余白の四角) |
| `callout` | `border` {color "#FFFFFF", width 3}、`fill` {color "#000000", opacity 0.7}、`shadow` true、`shadow_rgba` [0,0,0,140]、`shadow_offset` 10、`shadow_blur` 12、`in` 0.4、`start` 0、`gap` 4、`auto_dst` ([置き場所の省略](#置き場所の省略-自動の置き場所)) |
| `mask` | `pixel_frequency` 20.0、`blur_size` 40.0、`black_rgba` [0,0,0,255] |
| `zoom` | `filter_method` "Lanczos" |
| `layout_place` | `height` 0.78、`anchor` "top-left"、`margin` 12 |
| `layout_background` | `blur` 6.0、`dim` 0.75 |
| `marker` | `row_color` "Blue" (行の頭に付けるクリップのマーカーの色)、`head_color` "Red" (`api_trial.py` がタイムラインの頭に付けるマーカーの色) |
| `render` | `format` "mp4"、`codec` "H264" |

## 書き出しと名前 (sb2resolve.py)

使い方の全体は `python scripts/sb2resolve.py --help`。構成表の項目に関わる決まりは次のとおり。

- プロジェクトの名前: `--name`、構成表の `project`、`output` のファイルの名前、構成表のファイルの名前の順。同じ名前のプロジェクトがあれば末尾に時刻を付ける (`--name` で決めた名前があれば止める)。`--project` なら既存のプロジェクトに新しいタイムラインとして組む
- 行の頭のクリップには、`marker.row_color` の色のマーカーを付ける (名前はテロップの頭、メモは肩テロップ、customData は `row<番号>`)
- 完成版 (`--render`) の書き出し先は `--output` か `output`。拡張子は `render.format` で決まる。NG の行があれば書き出さない
- 仮組み (`--render-anyway`) は NG があっても書き出す。書き出し先は `--output`、無ければ仮組みの書き出し先 (`paths.preview_dir`、既定 `%USERPROFILE%/Videos/storyboard-preview`) の `<完成版の名前>-preview.<形式>`
- 部分の書き出し (`--rows 3-5`) は、その行 (止めた区間を含む) だけを `<完成版の名前>-rows3-5-preview.<形式>` に書く
- 書き出し先が既にあれば、`--overwrite` が無い限り Resolve に接続する前に止める。仮組みの書き出し先が完成版と同じファイルになるときも止める
- どの書き出しも、書き出し先のフォルダーの中の一時フォルダーに書いてから書き出し先へ置く。置けなければ一時ファイルを残す
- 記録は出力先 (`paths.out_dir`) の `sb2resolve\<構成表の名前>.json`。行ごとの結果、`auto` (自動で決めた値)、`render` (書き出しの結果。`mode` は `final`・`preview`・`rows`、`ignored_ng` は無視した NG) などが入る
