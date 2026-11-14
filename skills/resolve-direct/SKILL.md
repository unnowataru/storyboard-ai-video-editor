---
name: resolve-direct
description: 人が DaVinci Resolve のタイムラインに打ったマーカーを読み (scripts/read_markers.py)、マーカーの名前とメモに書かれた修正の指示を構成表 (JSON) に反映し、sb2resolve.py で新しいタイムラインに組んで、人がプレビューする流れ。「Resolve にマーカーを打った」「マーカーのとおりに直して」「タイムラインを組み直して」と言われたときに使う。構成表の書式は docs/storyboard.md、Resolve の API の動きは docs/scripting-api.md。
---

# resolve-direct

人が Resolve の画面でタイムラインにマーカーを打って修正の指示を伝え、AI が構成表を修正して新しいタイムラインに組む。人の作ったタイムラインは書き換えない。

## 前提

- 対象のタイムラインは `sb2resolve.py` で組んだもの。行は、`sb2resolve.py` の記録 (出力先の `sb2resolve\<構成表の名前>.json`) の位置か、各行のクリップの頭に付けた目印 (customData `row<番号>`) で決まる
- Resolve Studio が起動していて、外部スクリプトに接続できる (`docs/setup.md`)
- Resolve を使うコマンドは `python scripts/resolve_lock.py run --owner <名前> -- ...` で包む。ロックが使用中なら 1 ですぐ終わるので、待ち続けずに人に伝える
- 構成表のパスと、組み込むプロジェクトの名前は人に確認する。推測で決めない

## 流れ

1. 人: Resolve で対象のタイムラインを開き、修正したい所にタイムラインのマーカーを打つ。マーカーの名前かメモに、修正したいこと (テロップの文、行を短くする、ズームを足すなど) を書く。クリップのマーカーは行の目印なので使わない
2. AI: マーカーを読む (Resolve は読むだけ)

   ```text
   python scripts/resolve_lock.py run --owner <名前> -- python scripts/read_markers.py --storyboard 構成表.json
   ```

   画面にマーカーごとの 1 行 (名前・時刻・行・テロップ・メモ)、出力先の `read_markers\<タイムラインの名前>.json` に全部が出る。JSON の `markers` の各項目の意味:

   | 項目 | 内容 |
   |---|---|
   | `frame`・`mss` | タイムラインの頭 (01:00:00:00) からのフレームと `m:ss.s` |
   | `name`・`note`・`color` | マーカーの名前・メモ・色 |
   | `row` | 当たった構成表の行の番号 (1 始まり)。クリップの外なら null |
   | `in_hold` | その行の止めている所 (hold) なら true |
   | `into_clip` | 行のクリップの頭からの秒 (書き出しの秒) |
   | `caption`・`badge`・`start`・`end` | その行のテロップ・肩テロップ・素材の秒 (`--storyboard` のとき) |

   `--storyboard` を付けると、`seconds` は行の `seconds` で上書きされることがある。マーカーの時刻は `frame` か `mss` で読む。JSON の `rows_from` が `record` なら行は記録から、`tags` なら目印から決めている。目印の足りない警告が出たら、行がずれていないかを人に確認する
3. AI: マーカーの内容に合わせて構成表を修正する (`docs/storyboard.md`)。素材の時刻は、行の `start` に `into_clip` × 行の速さを足して求める。止めている所のマーカー (`in_hold`) は、その行の hold の修正として読む。意図が読み取れないマーカーは、推測で修正せずに人に聞く
4. AI: Resolve に触らずに点検する

   ```text
   python scripts/sb2resolve.py 構成表.json --dry-run
   ```

   誤りがあれば修正して流し直す。警告は人に伝える
5. AI: 新しいタイムラインに組む。人が使っているプロジェクトに組むときは、人が名前を指定したプロジェクトにだけ、新しい名前のタイムラインとして追加する

   ```text
   python scripts/resolve_lock.py run --owner <名前> -- python scripts/sb2resolve.py 構成表.json --project <プロジェクトの名前> --timeline <新しいタイムラインの名前>
   ```

   - `--project` はプロジェクトの設定を変えない。タイムラインのフレームレートか解像度が構成表と違えば、何も追加せずに止める
   - `--timeline` の名前が既にあれば止める (上書きしない)
   - `--project` を付けなければ、新しいプロジェクトを作って組む
   - 修正した行だけを書き出して確認するなら `--rows 3-5` を足す。書き出し先は仮組みの書き出し先 (config の `preview_dir`) で、完成版とは別のファイル
6. 人: 新しいタイムライン (または `--rows` の書き出し) をプレビューする。修正が残っていれば、新しいタイムラインにマーカーを打って 1 から繰り返す
7. 人が良いと言ったら、完成版を書き出す: `--render` を付けて組む (`python scripts/sb2resolve.py --help`)

## 注意

- 組んだ後のタイムラインは今開いているタイムラインになる。`read_markers.py` は今開いているタイムラインを読むので、人がどのタイムラインにマーカーを打ったかを確認してから読む
- `sb2resolve.py` の記録は構成表のファイル名ごとに 1 つで、組み直すと上書きされる。前のタイムラインのマーカーは、組み直す前に読む (組み直した後は目印で行を決める)
- 素材にマーカー (録画ツールのピンが入ったチャプター) があると、行の目印を付けられない。そのときは記録だけが頼りなので、上の順番を守る (`docs/scripting-api.md` の「マーカー」)
- 人のタイムラインのクリップやマーカーを API で動かさない・消さない
