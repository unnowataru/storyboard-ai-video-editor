# このリポジトリで作業するときのルール

AI のエージェント (Claude Code など) がこのリポジトリで作業するときのルールです。ツールの概要は [README.md](README.md)、準備は [docs/setup.md](docs/setup.md) にあります。

## コマンドとテスト

- スクリプトはリポジトリのフォルダーで `python scripts/xxx.py` の形で実行する。使い方は `--help` かファイルの先頭の説明にある
- 変更したら `python -m pytest tests/offline -q` を流す。Resolve が要らないテストで、`tests/resolve` は集めない
- Resolve が要るテスト (`tests/resolve`) は 1 本ずつ `resolve_lock.py run` で包んで実行する。一覧は [tests/README.md](tests/README.md)
- 簡易版の組み立て内容の期待値 (`tests/offline/build-spec/`) は、Resolve 側の組み立てを意図して変えたときだけ `UPDATE_BUILD_SPEC=1` で作り直し、差分を確認してから入れる
- `.py`・`.ps1`・`.json` に利用者や PC に固有のパスを書かない (`tests/offline/test_no_user_paths.py` が確認する)。場所は `config/defaults.json` の `paths` と、個人の設定ファイル `config/local.json` (git に入れない) で決める
- 設定の値と出どころは `python scripts/common.py --show-config [構成表.json]` で確認する

## Resolve のロック

Resolve に接続する処理は、必ず `scripts/resolve_lock.py run` で包む。同時に 1 つの処理だけが Resolve を使うようにするためのロックで、作業フォルダーの `resolve.lock` フォルダーがあれば使用中。

```
python scripts/resolve_lock.py status
python scripts/resolve_lock.py run --owner <名前> -- python scripts/sb2resolve.py 構成表.json
```

- `--owner` は同時に動く処理ごとに違う名前にする。`run` はコマンドに環境変数 `RESOLVE_LOCK_OWNER` を渡し、`common.connect_resolve` と `tests/resolve` のテストは、それがロックの持ち主と同じでなければ警告を出す。config の `resolve.require_lock` を `true` にすると、警告ではなく接続する前に終わる (既定は `false`)
- ロックが使用中なら `run` はコマンドを実行せず、すぐ終了コード 1 で終わる (待たない)。`status` は空いていれば 0、使用中なら持ち主と時刻を出して 1
- `run` はコマンドが終わると (失敗・例外でも) 自分が取ったロックを外す。コマンドの起動後に Ctrl+C などで中断すると、ロックは残り終了コードは 130
- 持ち主が分からないロックや、止まった処理のロックをエージェントが消さない。Resolve を使う処理と空くのを待つ処理がすべて停止していることを確認してから、人が消す
- 包まなくてよいもの: `sb2resolve.py --dry-run`、`check_final.py --no-resolve`、`sb2ffmpeg.py`、`--help`

## Resolve のプロジェクト

Resolve での作業は、新しく作ったテスト用のプロジェクトだけで行う。利用者のプロジェクトを開かない・書き換えない。

- `sb2resolve.py` は既定で新しいプロジェクトを作って組む。`--project` (既存のプロジェクトに組む) は、テスト用に作ったプロジェクトにだけ使う
- `rconsole.py` に渡したファイルは、今開いているプロジェクトとタイムラインをそのまま操作できる。保存しなくても Resolve の中では変わるので、読むだけにするか、テスト用の新しいプロジェクトを開いてから実行する
- `tests/resolve` のテストは `test-<テストの名前>-<時刻>` のプロジェクトを作り、その中だけで組む
- テストで作ったプロジェクトは `cleanup_projects.py` で消す。消せるのは `test-` で始まる名前だけ。先に `--dry-run` で対象を確認する
- `projects.py check` はプロジェクトを切り替える (切り替える前に今のプロジェクトを保存し、終わったら開き直す)
- `sb2resolve.py` で既にある書き出し先を置き換えるのは `--overwrite` を付けたときだけ

## 機密を含む録画の扱い

画面録画には IP アドレス・ホスト名・アカウント名などが映ることがある。

- 録画・書き出し・途中のフレーム画像はリポジトリに入れない。作業フォルダー (既定は `%USERPROFILE%/Videos/storyboard-work`) と出力先に置く
- テスト・見本・不具合の再現には、機密の無い合成の素材を使う (`python scripts/make_test_source.py`・`python scripts/sb2ffmpeg_bench.py --make-source`)
- `track.py` と `scene_events.py --ocr` の文字認識は手元の Windows で動き、画面の内容を外部へ送信しない。結果の JSON には読んだ文字そのものを書かず、種類と番号 (`ip-1` など) や当たった語だけを書く。この形を変えない
- `check_final.py` はマスクの位置と大きさの値を照らし合わせるだけで、隠し漏れが無いことは保証しない。実際の録画では `area_sheet.py` の画像を人が見て確認する

## 機能の一覧の更新

機能・構成表の項目・設定の項目・スクリプト・引数・`check_final.py` の照らし合わせの項目を追加したら、機能の一覧も更新する。

1. `docs/features.json` に追加する (項目の説明は `python scripts/features.py --help`)
2. `python scripts/features.py --write-doc` で `docs/features.md` を作り直す。`docs/features.md` は手で書き換えない
3. `python -m pytest tests/offline/test_features_catalog.py -q` で、全部の項目が一覧に載っているかとリンク先があるかを確認する

`python scripts/features.py --check` は、`docs/features.md` が一覧から作ったものと同じかを確認する (違えば終了コード 1)。
