# テスト

テストは 2 種類です。

| フォルダー | 中身 | 流し方 |
|---|---|---|
| `tests/offline` | Resolve が要らないテスト。Resolve の API は模擬のオブジェクトで置き換える | pytest でまとめて流す |
| `tests/resolve` | 起動中の Resolve Studio に接続して確認するテスト | 1 本ずつ `resolve_lock.py run` で包んで実行する |

`tests/` の直下の構成表は、テストとスクリプトが使う入力です。

| ファイル | 使うもの |
|---|---|
| `trial-storyboard.json` | `scripts/api_trial.py`、簡易版の組み立て内容のテスト。セーフエリアを 3:2 にして、ズームの警告も確かめる |
| `bench-storyboard.json` | `scripts/sb2ffmpeg_bench.py` の既定の構成表 |

## Resolve が要らないテスト (tests/offline)

```
python -m pytest tests/offline -q
```

`python -m pytest` だけでも同じものが流れます (`tests/conftest.py` が `tests/resolve` を集めない)。GitHub Actions は `python -m pytest tests -q -rs` を流します。

飛ばすテストと条件です。

| 条件 | 飛ばすもの |
|---|---|
| FFmpeg が無い | 動画を作って確認するテスト |
| numpy・Pillow が無い | それを使うテスト |
| Windows でない | `test_sb2ffmpeg_bench.py`、Ctrl+Break を送るロックのテスト |
| pwsh が無い | `test_features_catalog.py` の `.ps1` の引数を読むテスト |
| 書体 Meiryo と Meiryo Bold が無い | 印 `meiryo` のテスト (テロップの幅を Meiryo の寸法で比べる) |

テストが作る動画などは、作業フォルダーの `offline-tests` の下に実行ごとに別の名前のフォルダーを作って置き、終わったら消します。同時に流れる pytest どうしで取り合いません。

簡易版の組み立て内容の期待値は `tests/offline/build-spec/` の JSON です。Resolve 側の組み立てを意図して変えたときは、環境変数 `UPDATE_BUILD_SPEC=1` を付けて `test_sb2ffmpeg.py` を流して作り直し、差分を確認してから入れます。意図しない差分は Resolve 側の組み立ての劣化です。

| ファイル | 確認すること |
|---|---|
| `test_area_sheet.py` | `area_sheet.py` (見る範囲が変わったフレームだけを並べる) |
| `test_audio_place.py` | 音声の配置のテスト (`tests/resolve/audio_place.py`) の測り方の部品 |
| `test_caption_window.py` | テロップの板の形 (`bar_inset`・`corner_radius`・`border_width`・`end_mark`) |
| `test_capture_tools.py` | 録画まわりのスクリプト (`pins.py`・`wallclock_to_tc.py`・`contact_sheet.py`・`marker.html` の保存の形と `?pinkey=`。`?pinkey=` は Node.js があるときだけ) |
| `test_check_final.py` | `check_final.py` (構成表・タイムライン・書き出しの照らし合わせ) |
| `test_cleanup_projects.py` | `cleanup_projects.py` (テストで作ったプロジェクトを消す) |
| `test_cli_help.py` | `--help` が説明を出して 0 で終わり、ロックが無くても止まらず Resolve に接続しないこと |
| `test_config.py` | 設定の読み込み (`settings.py`) |
| `test_features_catalog.py` | 機能の一覧 (`docs/features.json`・`features.py`・`docs/features.md`)。構成表と設定の項目・スクリプト・引数・照らし合わせの項目が全部一覧に載っているか |
| `test_frame_read.py` | 番号を指定したフレームの取り出し (`FrameReader`) |
| `test_layout.py` | 構成表の `layout` (画面を縮めて寄せ、余白をぼかした画面で埋める配置) |
| `test_list_captions.py` | `list_captions.py` (テロップの一覧) と、テロップの行の中の読点の警告 |
| `test_no_user_paths.py` | `.py`・`.ps1`・`.json`・`.comp` に利用者や PC に固有のパスが書かれていないこと |
| `test_point.py` | 行の `point` (指すマーク) |
| `test_probe.py` | `probe.py` (Resolve に接続できるかの確認) |
| `test_projects.py` | `projects.py` (プロジェクトの一覧・書き出し・素材の確認) |
| `test_rconsole.py` | `rconsole.py` (渡した Python を Resolve に接続して実行する) |
| `test_read_markers.py` | `read_markers.py` (マーカーを構成表の行に当てる) |
| `test_resolve_lock.py` | `resolve_lock.py` (Resolve を使う順番のロック) |
| `test_resolve_parts.py` | Resolve に渡す部品 |
| `test_sb2ffmpeg.py` | `sb2ffmpeg.py`・`sb2ffmpeg_render.py`・`resolve_stand_in.py`。依存の向きと、Resolve に組むときと同じ組み立て内容になること |
| `test_sb2ffmpeg_bench.py` | `sb2ffmpeg_bench.py` (FFmpeg は流さない) |
| `test_sb2nle.py` | `sb2nle.py` (FCPXML・OTIO・EDL への変換) |
| `test_sb2resolve.py` | `sb2resolve.py` と Fusion の組み立て (`build_fusion`) |
| `test_sb2resolve_preview.py` | 仮組み (`--render-anyway`) の書き出し先を完成版から離すこと |
| `test_scene_events.py` | `scene_events.py` (合成の配列での判定と、合成の動画での実行) |
| `test_side.py` | 解説の欄 (`layout.side` と行の `side`) |
| `test_track.py` | `track.py` (隠す位置を見つけて JSON に書く) |
| `test_track2masks.py` | `track2masks.py` (位置の JSON から構成表のマスクを作る) |
| `test_wallclock_to_tc.py` | `wallclock_to_tc.py` (壁の時計の時刻を録画の中の時刻に直す) |

## Resolve が要るテスト (tests/resolve)

Resolve Studio を起動しておき、1 本ずつ実行します。

```
python scripts/resolve_lock.py run --owner <名前> -- python tests/resolve/<テスト>.py [--source 素材]
```

- `resolve_lock.py run` で包まないと、警告を出して接続します。config の `resolve.require_lock` が `true` なら、接続する前に終わります
- 素材は機密の無いテスト用の素材です。`--source` か環境変数 `RESOLVE_TRIAL_SOURCE` (`config/local.json` の `paths.trial_source`) で指定し、無ければ `python scripts/make_test_source.py` で作ります。素材を自分で作るテストもあります
- 各テストは新しいプロジェクト `test-<テストの名前>-<時刻>` を作り、その中だけで組みます。今開いているプロジェクトには触りません
- 人が後で GUI で確認するテスト (`native_*.py` の一部) は、決まった名前のプロジェクトを作り直し、消さずに残します。不要になったら `scripts/cleanup_projects.py` で消します
- 結果は出力先の `tests\<テストの名前>.json` と短い表です。項目は pass・fail・unknown で、unknown は合格にしません。期待と違う項目があれば終了コードは 1 です
- 細かい前提 (要る素材、長さ、GUI で確認すること) は各ファイルの先頭の説明にあります

構成表から組む流れの確認

| ファイル | 確認すること |
|---|---|
| `sb2resolve_4k.py` | 4K の素材と 1080p のタイムラインで、構成表どおりに組んで書き出せるか |
| `sb2resolve_long.py` | 長い素材 (約 60 分に 50 行) を現実的な時間で組めるか |
| `stage_timing.py` | 構成表から完成版を確認するまでの工程を順に流し、工程ごとの秒数を測る |
| `api_timing.py` | 外部の Python から呼んだときの API の時間 (合否は付けない) |
| `check_final_cases.py` | `check_final.py` が、わざと入れたずれを実機で見つけるか |
| `hold_check_final.py` | `check_final.py` の止めたクリップの確認が、実機で正しく働くか |
| `auto_border_callout.py` | focus の枠線の色 `auto` と、callout の重ねる順・自動の置き場所 |
| `auto_border_speed.py` | 倍速の行で動く focus の枠線の色 `auto` が、描く位置の背景で決まるか |
| `render_overwrite.py` | 書き出し先に既にファイルがあるとき、完成版と仮組みの書き出しがどう動くか |
| `render_range.py` | 区間の書き出しが、全編の書き出しの同じフレームと画素まで一致するか |

Resolve の API と機能の動きの確認

| ファイル | 確認すること |
|---|---|
| `timeline_build.py` | タイムラインにクリップを並べる方法の違い (`CreateTimelineFromClips` と `AppendToTimeline`) |
| `import_media.py` | `MediaPool.ImportMedia` に渡す形の違い |
| `speed_ripple.py` | `TimelineItem.SetSpeed` の `RippleTimeline` の違い |
| `hold_freeze.py` | 止めたクリップがどの素材のフレームを映し、その中の Fusion の時刻が進むか |
| `fusion_keyframes.py` | Fusion の部品にキーフレームを付ける方法と、マスクに使う部品があるか |
| `fusion_mask_size.py` | Fusion のマスクの `Width` と `Height` の単位 |
| `fusion_lock_render.py` | Fusion の `comp.Lock()` の有無と合成の書き出し・読み込みが、編集ページの書き出しに効くか |
| `mp4_chapters.py` | MP4 のチャプターが、読み込むだけで Resolve のマーカーになるか |
| `audio_place.py` | 音声トラック 2 の指定のフレームに置いた音が、書き出しで期待の時刻に鳴るか |
| `native_annotation.py` | Fusion の標準の部品で重ねた矢印とラベルの位置・色・表示の時刻 |
| `native_callout.py` | Fusion のタイトル「Call Out」を API で置いて書き出しで合うか、開き直した後も残るか |
| `native_fade.py` | Resolve の標準のフェードで、ラベルを終わりの 9 フレームで消せるか |
| `native_overlay.py` | 透過 PNG を重ね、位置・大きさ・不透明度を `SetProperties` で決められるか |
| `native_audio.py` | Speech Generator で作った声の音量をそろえられるか、内蔵の文字起こしの結果 |

`resolve_helpers.py` は Resolve が要るテストの共通の部品です。
