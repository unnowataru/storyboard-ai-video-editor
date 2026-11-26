# storyboard-ai-video-editor

構成表 (JSON) から、画面録画を切り出して倍速・テロップ・ズーム・マスクなどを付けた動画を作るツールです。

- 簡易版: FFmpeg だけで作る (`scripts/sb2ffmpeg.py`)。Resolve は要りません。CPU だけで動きます
- 完成版: DaVinci Resolve Studio のタイムラインに組み、書き出す (`scripts/sb2resolve.py`)

構成表の 1 行が動画の 1 区間です。行には素材の範囲 (start〜end)、書き出しの長さ (seconds。倍速になる)、テロップ、肩テロップ、ズーム、マスク (モザイク・ぼかし・黒塗り)、周りを暗くする範囲 (focus) などを書きます。書式は [docs/storyboard.md](docs/storyboard.md)、できることの一覧は [docs/features.md](docs/features.md) にあります。

## 必要なもの

| もの | 使う場面 |
|---|---|
| Windows | 全体 (文字認識・ベンチマーク・Resolve の操作は Windows の機能を使う) |
| Python 3.10 以上と `requirements.txt` のパッケージ | 全体 |
| FFmpeg (full build。libx264 と drawtext が入ったもの) | 簡易版、動画の確認、テスト |
| DaVinci Resolve Studio | 完成版。外部のスクリプトから接続できるのは Studio だけ |
| 書体 Meiryo と Meiryo Bold | テロップと肩テロップ (既定の書体) |
| Windows の文字認識の言語 | `track.py` と `scene_events.py --ocr` だけ |

## 準備

1. Python・FFmpeg (・Resolve Studio) を入れる
2. `python -m pip install -r requirements.txt`
3. 場所が自動で見つからないものは `config/local.json` か環境変数で指定する
4. `python scripts/check_env.py` で、そろっているかを確認する

詳しくは [docs/setup.md](docs/setup.md) にあります。

## 使い方

作業フォルダーの既定は `%USERPROFILE%/Videos/storyboard-work`、出力先はその下の `out` です。見本の構成表 (`examples/sample1-storyboard.json`・`sample2-storyboard.json`) は、作業フォルダーの合成の 4K の素材 `bench-4k.mp4` を使います。

### 簡易版 (FFmpeg)

```
python scripts/sb2ffmpeg_bench.py --make-source
python scripts/sb2ffmpeg.py examples/sample1-storyboard.json
```

- 1 行目は素材 `bench-4k.mp4` を作り (既にあれば作らない)、続けてその PC で速く動くパラメーターを測ります。`--apply` を付けると、測った値を `config/local.json` に書き込みます
- 2 行目は出力先の `sb2ffmpeg\sample1-storyboard.mp4` に簡易版を書きます。組み立て内容は同じ場所の `.build.json` です
- 簡易版は完成版に近いものの同じではありません。マスクは種類によらず少し広げた黒塗り、テロップは Pillow で描いた文字、ズームは FFmpeg の zoompan で、音は入りません

### 完成版 (Resolve Studio)

Resolve Studio を起動し、環境設定 > システム > 一般 >「外部スクリプトに使用」を「ローカル」にしておきます。

```
python scripts/sb2resolve.py examples/sample1-storyboard.json --dry-run
python scripts/resolve_lock.py run --owner me -- python scripts/sb2resolve.py examples/sample1-storyboard.json --render
python scripts/resolve_lock.py run --owner me -- python scripts/check_final.py examples/sample1-storyboard.json --pixel
```

- `--dry-run` は構成表の点検と値の計算だけを行い、Resolve に接続しません
- Resolve に接続するコマンドは `resolve_lock.py run` で包みます。ロックは作業フォルダーの `resolve.lock` で、同時に 1 つの処理だけが Resolve を使うようにします。包まれていないときは警告を出して接続します (config の `resolve.require_lock` を `true` にすると、接続する前に終わります)
- `sb2resolve.py` は既定で新しいプロジェクトを作って組みます。同じ名前のプロジェクトがあれば名前の末尾に時刻を追加し、既存のプロジェクトは上書きしません
- 書き出し先は構成表の `output` (見本では出力先の `sample1-digest.mp4`)。組んだ記録は出力先の `sb2resolve\<構成表の名前>.json` です
- 編集ソフト向けの FCPXML 1.10・OTIO・EDL に変換する試作 (`sb2nle.py`) もあります

### 書き出しを構成表と照らし合わせる

`check_final.py` は、構成表と、Resolve のタイムライン (API の値) と、書き出した動画 (ffprobe) を照らし合わせ、項目ごとに pass・fail・unknown を付けます。結果は出力先の `check-final\<名前>.json` で、fail か unknown があれば終了コードは 1 です。その行の区切りのフレームを並べた画像 (`<名前>.png`) も作ります。

- `--pixel` は、肩テロップが消える前後のフレームの色と、focus の枠線が現れる前の画素も比べます。実際の録画では、板の場所に肩テロップに近い色が映ったり、枠線の点の上で画面が変わったり (カーソル、時刻のずれ) すると、誤って fail になることがあります
- `--no-resolve` は Resolve に接続しません (タイムラインの項目は unknown)
- 照らし合わせるのはマスクの位置と大きさの値です。実際の録画で IP アドレスやホスト名の隠し漏れが無いことまでは保証しません。隠し漏れは `area_sheet.py` で、見る範囲が変わったフレームを全部並べた画像を作り、人が目で確認します

## スクリプトの一覧

各スクリプトの使い方は `python scripts/<名前>.py --help` か、ファイルの先頭の説明にあります。「Resolve」の列が「要る」のものは `resolve_lock.py run` で包んで実行します。

動画を作る

| スクリプト | 役目 | Resolve |
|---|---|---|
| `sb2ffmpeg.py` | 構成表から簡易版を作る | 要らない |
| `sb2ffmpeg_render.py` | 簡易版の組み立て内容を FFmpeg で描く (`sb2ffmpeg.py` が使う) | 要らない |
| `resolve_stand_in.py` | Resolve の代わりに呼び出しを受け、組み立て内容を記録する (`sb2ffmpeg.py` が使う) | 要らない |
| `sb2ffmpeg_bench.py` | 簡易版を速く動かすパラメーターを測る。`--make-source` で見本の素材を作る | 要らない |
| `sb2resolve.py` | 構成表から Resolve のタイムラインを組み、書き出す | 要る (`--dry-run` は要らない) |
| `sb2nle.py` | 構成表を FCPXML・OTIO・EDL に変換する (試作) | 要らない |
| `track.py` | 録画の中の IP アドレス・ホスト名・指定の文字列・画面の部品を見つけて追いかけ、隠す位置を JSON に書く | 要らない |
| `track2masks.py` | `track.py` の JSON を構成表の行ごとのマスクにし、新しい構成表を書く | 要らない |

書き出しを確認する

| スクリプト | 役目 | Resolve |
|---|---|---|
| `check_final.py` | 構成表・タイムライン・書き出しを照らし合わせる | 要る (`--no-resolve` は要らない) |
| `area_sheet.py` | 見る範囲が変わったフレームだけを並べた画像を作る (マスクの漏れの確認) | 要らない |
| `contact_sheet.py` | 動画のフレームを時刻付きで 1 枚の画像に並べる | 要らない |
| `list_captions.py` | 構成表のテロップを Markdown の表に書き出す (誰が何をするかが読み手に分かるかの確認) | 要らない |

録画を下調べする

| スクリプト | 役目 | Resolve |
|---|---|---|
| `marker.html` | ブラウザーで録画を再生し、ピンと領域を付けて `<録画の名前>.marks.json` に保存する | 要らない |
| `pins.py` | 録画のチャプター (OBS Studio のピン) と `marks.json` のピン・領域を時刻の順に出す | 要らない |
| `scene_events.py` | 録画から画面の切り替わり・長い静止・エラーの語の候補を時刻付きで出す | 要らない |
| `wallclock_to_tc.py` | 壁の時計の時刻を、一時停止を挟んだ録画の中の時刻に直す | 要らない |

Resolve を操作する・調べる

| スクリプト | 役目 | Resolve |
|---|---|---|
| `resolve_lock.py` | Resolve を使う順番のロックを取ってコマンドを実行する (`run`)・状態を出す (`status`) | 包む側 |
| `read_markers.py` | タイムラインのマーカーを読み、構成表のどの行に当たるかを出す | 要る |
| `projects.py` | プロジェクトの一覧・`.drp` への書き出し・素材のファイルの有無の確認 | 要る |
| `cleanup_projects.py` | テストで作った `test-` で始まるプロジェクトを消す | 要る |
| `probe.py` | 外部のプロセスから Resolve に接続できるかを確認する | 要る |
| `rconsole.py` | Resolve に接続して、渡した Python を実行する (調べ物用) | 要る |
| `api_trial.py` | `tests/trial-storyboard.json` を段階ごとに組み、成否を記録する | 要る |
| `snap.ps1` | 指定したプロセスの窓を前面に出さずに画像に撮る | 要らない |

準備と設定

| スクリプト | 役目 |
|---|---|
| `check_env.py` | 作業に要るものがそろっているかを確認する |
| `make_test_source.py` | 機密の無いテスト用の素材をテストパターンから作る |
| `common.py` | 共通の部品の入口。`--show-config` で設定の値と出どころを出す |
| `features.py` | 機能の一覧を出す・`docs/features.md` を作り直す |
| `run.ps1` | 設定の Python (`paths.python`) でスクリプトを実行する |
| `config.ps1` | PowerShell のスクリプト用の設定の読み込み |
| `ocr_windows.ps1` | Windows の文字認識で画像を読む (`track.py`・`scene_events.py`・`check_env.py` が使う) |

`settings.py`・`resolve.py`・`schema.py`・`layout.py`・`fusion.py`・`video.py` は `common.py` が読み込む部品です。

## 文書の一覧

| 文書 | 内容 |
|---|---|
| [docs/setup.md](docs/setup.md) | 準備 (Python・FFmpeg・Resolve Studio・書体・文字認識・設定ファイルと環境変数・`check_env.py`) |
| [docs/storyboard.md](docs/storyboard.md) | 構成表の書式 (全項目・既定値・例) |
| [docs/glossary.md](docs/glossary.md) | 言葉の一覧 |
| [docs/features.md](docs/features.md) | 機能の一覧 (`docs/features.json` から作る) |
| [docs/recording.md](docs/recording.md) | 録画を撮るときの注意、時刻の記録 `rec-timeline.log` の形式、`marker.html`・`pins.py`・`scene_events.py` の使い方 |
| [docs/track.md](docs/track.md) | `track.py` と `track2masks.py` の JSON の形と使い方 |
| [docs/scripting-api.md](docs/scripting-api.md) | Resolve のスクリプトの API の使い方と実機での制約 |
| [tests/README.md](tests/README.md) | テストの一覧と流し方 |
| [skills/storyboard-revise/SKILL.md](skills/storyboard-revise/SKILL.md) | Resolve のマーカーを読んで構成表を修正する流れのスキル (Claude Code 用) |
| [CLAUDE.md](CLAUDE.md) | AI のエージェントがこのリポジトリで作業するときのルール |
| [CHANGELOG.md](CHANGELOG.md) | 変更の記録 |

## テスト

```
python -m pytest tests/offline -q
```

Resolve が要らないテストです。Resolve が要るテスト (`tests/resolve`) は 1 本ずつ `resolve_lock.py run` で包んで実行します。一覧と流し方は [tests/README.md](tests/README.md) にあります。GitHub Actions は push・pull request・毎週の定期実行で、その時点の最新の Python・パッケージ・FFmpeg を使って `tests` を流します。

## ライセンス

MIT License ([LICENSE](LICENSE))
