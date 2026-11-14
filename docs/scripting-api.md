# DaVinci Resolve のスクリプトの API の使い方

このリポジトリが、外部の Python から DaVinci Resolve Studio のスクリプトの API をどう使っているか。使う関数、スクリプトでの包み方、実機で確認した動きと、それに合わせたコードの対応を書く。構成表の書式は [storyboard.md](storyboard.md)、準備 (Resolve の場所と設定) は [setup.md](setup.md)。

この文書の表の動きは、Resolve Studio 21.1.1.10 (config の `resolve.verified_version`) で確認したもの。`python scripts/resolve_lock.py run --owner <名前> -- python scripts/check_env.py --connect` は、起動中の Resolve の版がこれと違えば「確認し直しが要る」と出す。そのときは表の各行の「確認するテスト」(`tests/resolve`) を流して確認し直す。

## 接続

- `scripts/resolve.py` の `import_dvr` が `DaVinciResolveScript` を読み込む。場所は config の `paths.resolve_script_api` と `paths.resolve_script_lib` (`fusionscript.dll`) で、環境変数 `RESOLVE_SCRIPT_API`・`RESOLVE_SCRIPT_LIB` が優先する
- `connect_resolve` が `dvr.scriptapp("Resolve")` で起動中の Resolve に接続する。接続する前に、`resolve_lock.py run` で包まれていて、ロックの持ち主がこの処理かを確認し、違えば警告を出す (config の `resolve.require_lock` が `true` なら終わる)。起動中の Resolve は 1 つで、同時に動く処理が同じものを操作するため
- 外部からの接続は Studio だけ (`IsStudio()`)。Resolve の 環境設定 > システム > 一般 > 「外部スクリプトに使用」を「ローカル」にしておく
- Resolve を使うコマンドは、どれも `python scripts/resolve_lock.py run --owner <名前> -- <コマンド>` で包む。ロックが使用中なら、コマンドを流さずにすぐ 1 で終わる (待たない)

## スクリプトの包み方

| スクリプト | Resolve の API の使い方 |
|---|---|
| `scripts/resolve.py` | 接続 (`import_dvr`・`connect_resolve`・`connect_or_exit`)、メディアプールの項目のフレームレートと解像度 (`source_fps`・`clip_resolution`)、書き出しを始めて終わるまで待つ (`render_jobs`)、タイムコードとフレームの変換 |
| `scripts/fusion.py` | クリップに Fusion の合成を追加し、部品を Python で置いてつなぐ (`build_fusion`) |
| `scripts/layout.py` | Fusion の部品の位置と大きさの計算 (API は呼ばない) |
| `scripts/sb2resolve.py` | 構成表からプロジェクト・タイムライン・Fusion・マーカーを組み、書き出す |
| `scripts/read_markers.py` | 今開いているタイムラインのマーカーを読み、構成表の行に当てる (読むだけ) |
| `scripts/check_final.py` | 組んだタイムラインのクリップと Fusion の部品の値を読み、構成表と照らし合わせる |
| `scripts/projects.py` | プロジェクトの一覧、`.drp` への書き出し、素材のファイルの有無の確認 |
| `scripts/cleanup_projects.py` | `test-` で始まるプロジェクトを消す |
| `scripts/probe.py`・`scripts/check_env.py --connect` | 接続できるか、製品名・版・Studio か |
| `scripts/rconsole.py` | 渡した Python を、`resolve`・`pm`・`proj`・`mp`・`tl` を用意して実行する (調べ物用) |
| `scripts/api_trial.py` | テスト用の構成表を `sb2resolve.py` と同じ部品で組み、段階ごとの成否を記録する |
| `scripts/resolve_stand_in.py` | Resolve の代わりに呼び出しを受け、組み立ての内容を JSON にする (簡易版 `sb2ffmpeg.py` の部品)。返す値は下の表の動きに合わせてある |

## 使う関数

| オブジェクト | 関数 |
|---|---|
| Resolve | `GetProjectManager`・`GetProductName`・`GetVersionString`・`IsStudio` |
| ProjectManager | `CreateProject`・`LoadProject`・`DeleteProject`・`SaveProject`・`ExportProject`・`GetCurrentProject`・`GetProjectListInCurrentFolder`・`GetFolderListInCurrentFolder`・`OpenFolder`・`GetCurrentDatabase` |
| Project | `SetSettings`・`GetSetting`・`GetMediaPool`・`GetTimelineCount`・`GetTimelineByIndex`・`GetCurrentTimeline`・`SetCurrentTimeline`・`GetName`・`SetCurrentRenderFormatAndCodec`・`SetRenderSettings`・`AddRenderJob`・`StartRendering`・`IsRenderingInProgress`・`GetRenderJobStatus` |
| MediaPool | `ImportMedia`・`CreateEmptyTimeline`・`AppendToTimeline`・`DeleteClips`・`GetRootFolder`・`GetCurrentFolder`・`SetCurrentFolder` |
| MediaPoolItem | `GetClipProperty` (`FPS`・`Resolution`・`Frames`・`File Path`) |
| Timeline | `GetStartFrame`・`GetItemListInTrack`・`GetTrackCount`・`GetSetting`・`GetMarkers`・`SetCurrentTimecode`・`GetCurrentTimecode`・`GetName` |
| TimelineItem | `GetStart`・`GetEnd`・`GetSourceStartFrame`・`GetSourceEndFrame`・`GetRightOffset`・`SetSpeed`・`GetSpeed`・`GetLinkedItems`・`GetMediaPoolItem`・`AddMarker`・`GetMarkers`・`AddFusionComp`・`GetFusionCompCount`・`GetFusionCompByIndex` |
| Fusion の合成 (comp) | `AddTool`・`FindTool`・`GetToolList`・`BezierSpline`・`XYPath` |
| Fusion の部品 (tool) | `SetInput`・`GetInput`・`SetAttrs` (`TOOLS_Name`)・`GetAttrs`・`GetKeyFrames`・`GetConnectedOutput` |

## 実機で確認した動き

各表の「確認するテスト」は `tests/resolve` のスクリプト (流し方は各スクリプトの先頭の説明)。テストの無い行は、コードのコメントに書いてある実機の観測。

### プロジェクトとタイムライン

| 動き | コードの対応 | 確認するテスト |
|---|---|---|
| 再生フレームレート (`timelinePlaybackFrameRate`) とモニターの形式 (`videoMonitorFormat`) は、作った直後に `SetSettings` で入れても False で 24 のまま残る | 入れてみて読み戻し、違えば GUI での直し方を警告に出す。直し方は [setup.md](setup.md#再生のフレームレート) | なし |
| タイムラインのフレームレートと解像度 (`timelineFrameRate`・`timelineResolutionWidth`・`timelineResolutionHeight`) は `SetSettings` で入れ、`GetSetting` で読み戻す | `sb2resolve.py` は作ったばかりのプロジェクトに、取り込みとタイムラインの作成より前に入れる。`--project` の既存のプロジェクトは変えず、構成表と違えば何も追加せずに止める | なし |
| 同じ名前のタイムラインは作れない (`CreateEmptyTimeline` が None) | 先に名前で調べ、`-2`・`-3` を追加する | なし |
| 取り込みとタイムラインの作成はメディアプールの今のフォルダーに入り、今のフォルダーはプロジェクトに保存される | いちばん上のフォルダーに切り替えて取り込み、終わったら元に戻す | なし |
| 開いているプロジェクトは消せない | `cleanup_projects.py` は `--reopen` のプロジェクトを開いてから消す | なし |
| `ExportProject` が書き出すのは保存済みの内容 | `projects.py export` の前に Resolve で保存しておく | なし |

### 取り込みと配置

| 動き | コードの対応 | 確認するテスト |
|---|---|---|
| `ImportMedia` に辞書の list (`[{"FilePath": パス}]`) を渡すと None か空が返る。パスの文字列の list なら取り込める | パスの文字列で渡す | `import_media.py` |
| 同じファイルを同じフォルダーに何度取り込んでも、メディアプールの項目は 1 つ (既存の項目が返る) | 素材は 1 回ずつ取り込む | `import_media.py` |
| `CreateTimelineFromClips` は先頭の 1 件しか並べない | `CreateEmptyTimeline` の後に `AppendToTimeline` を 1 回で呼ぶ | `timeline_build.py` |
| `AppendToTimeline` の `startFrame`・`endFrame` は素材のフレームで、`endFrame` は含まない | 秒から素材のフレームレート (クリップ属性 `FPS`) で換算する | `timeline_build.py` |
| `recordFrame` はタイムラインの絶対フレーム | `GetStartFrame()` を足して渡す | `timeline_build.py` |
| `mediaType` を付けないと映像と音声の両方が並ぶが、戻り値は映像の項目だけ。音声は `GetLinkedItems()` で引ける | 戻り値の項目をそのまま使い (トラックを読み直さない)、行との対応は置いた位置で取る。消すときはつながった音声も一緒に消す | `timeline_build.py` |
| 無い番号のトラックを `trackIndex` に渡すと、エラーにならずにトラック 1 に置かれる | 先に `AddTrack` で足りないトラックを追加する | `timeline_build.py`・`audio_place.py` |
| 素材の最後まで届くクリップでは、`GetSourceEndFrame` が 1〜2 少なく出る | 右の余り (`GetRightOffset`) が 0 なら、終わりは 2 フレームまでの差を許す (`sb2resolve.py`・`check_final.py`) | なし |
| `AppendToTimeline` の後に、再生ヘッドが置いたクリップの直後へ動かないことがある | 止める前に `SetCurrentTimecode` で再生ヘッドをクリップの直後に置き、`GetCurrentTimecode` で読み戻す (読めなければ記録の `playhead` は None) | なし |

### 速さ

| 動き | コードの対応 | 確認するテスト |
|---|---|---|
| `SetSpeed({"Percentage": p, "RippleTimeline": False})` はタイムライン上の長さを保ち、素材側の範囲を伸び縮みさせる。後ろのクリップは動かない | 各行をタイムラインでの長さだけ等速で置いてから速さを変える | `speed_ripple.py` |
| `RippleTimeline: True` では長さが変わって後ろが詰まり、長さが 1 フレーム短く出ることがある | 使わない | `speed_ripple.py` |
| 割り切れない倍速では、素材側の範囲が 1 フレームずれることがある | 素材のフレームは始まりと終わりで 1 フレームまでの差を許す | なし |
| 速さを変えたクリップの中の Fusion の時刻は素材の速さで進む (4 倍速の書き出しの 15 フレーム目は合成の 60)。割り切れない倍速では切り捨てた時刻 (1.5 倍速の 15 フレーム目は合成の 22) | `resolve.comp_frame` で換算する。素材とタイムラインのフレームレートが違うときは確認していない | `sb2resolve_4k.py` |
| `Percentage` 0 (止めたクリップ) は 1 枚の画を映し続ける。止めるときに再生ヘッドがクリップの直後にあれば範囲の最後のフレーム、上か前にあれば頭のフレームを映す | 止めたいフレームで終わる範囲を 1 つずつ置き、再生ヘッドをクリップの直後に置いてからすぐ止める | `hold_freeze.py` |
| まとめて置いてから止めると、最初に止めたものとタイムラインの先頭のもの以外は範囲の頭のフレームを映す | まとめて置かない | `hold_freeze.py` |
| 止めた後は `GetSourceStartFrame` も `GetSourceEndFrame` も映すフレーム | `GetSourceStartFrame` で映すフレームを確認し、違えば消して置き直す (3 回まで) | `hold_freeze.py` |
| 止められない素材のフレームがあり、置いた範囲をずらしても、そのフレームを飛ばして隣を映す | 2 回続けて同じ隣のフレーム (±1) を映したら、そこで止めて記録の `near` に残す。`check_final.py` は記録の `near` と同じときだけ 1 の違いを認める | なし |
| 止めたクリップの中の Fusion の時刻は等速で進む | 画に付く位置 (マスク・focus) は止めた瞬間の位置で止め、現れる動きは続ける (`fusion.hold_values`) | `hold_freeze.py` |

### マーカー

| 動き | コードの対応 | 確認するテスト |
|---|---|---|
| `TimelineItem.AddMarker(フレーム, 色, 名前, メモ, 長さ, customData)` でクリップにマーカーを付けられる | 行の頭のクリップに、customData `row<番号>` の目印を付ける | なし |
| MP4 のチャプターは、読み込むだけでクリップの青のマーカーになる (0 秒のチャプターも)。タイムラインのマーカーは増えない | なし | `mp4_chapters.py` |
| 素材にマーカーがあると、そのクリップのマーカーは素材と共有され、頭のフレーム 0 に別のマーカーがあると目印を付けられない | 付けられなければ警告を出す。`read_markers.py` は `sb2resolve.py` の記録の行の位置で行を決める | なし |
| タイムラインを複製したり手で切ったりしても、クリップのマーカーはクリップと一緒に残る | `read_markers.py` は記録が合わなければ目印で行を決める | なし |

### Fusion

| 動き | コードの対応 | 確認するテスト |
|---|---|---|
| `comp.Lock()` の中で入れた値は、読み戻せても書き出しに出ない | `Lock` を使わない | `fusion_lock_render.py` |
| マスク (`RectangleMask`・`EllipseMask`) の `Width` は画面の幅、`Height` は画面の高さを 1 とした値。`EllipseMask` はその四角に内接する楕円 | `fusion.set_rect` はこの単位で入れる。`Height` を画面の幅で割ると低くなり、隠し漏れになる | `fusion_mask_size.py` |
| キーフレームは `comp.BezierSpline()` を入力につないで `SetInput(名前, 値, 時刻)` で入れると直線で補間される。`Center` は `comp.XYPath()` をつなぐ | 位置は `XYPath`、大きさと倍率は `BezierSpline` | `fusion_keyframes.py` |
| Lua の `Execute` で部品を付ける方法は、時刻つきの値が最後の 1 つしか残らない | Python で部品を作ってつなぐ | なし |
| Resolve FX のモザイク `ofx.com.blackmagicdesign.resolvefx.MosaicBlur` を Fusion の部品として置ける (入力 `PixelFrequency`) | モザイクのマスクに使う | `fusion_keyframes.py` |
| クリップの Fusion の合成の画面は素材の大きさになる。`SetPrefs` で広げても、組んだ後に素材の大きさに戻る | 縦横の比が違う素材は画面を広げ、部品ごとに `UseFrameFormatSettings` を 0 にして大きさを入れる | なし |
| `Crop` の値は整数に丸められる (`YOffset` 343.5 を入れると 344 が読み戻る) | 入れる前に整数にする | なし |
| `Text+` の `Size` 1 は、書き出しの画面の幅の 0.5 倍 (config の `fusion_text_scale`) の大きさの字になる | 字の大きさの見積もりに使う | `sb2resolve_4k.py` |
| `Text+` の `HorizontalLeftCenterRight` を -1 にすると、どの行も左の端が `Center` に揃う | 左揃えの欄に使う | なし |
| 輪 (枠線) は、内のマスクを反転して外のマスクの `EffectMask` につなぎ、外の `PaintMode` を `Multiply` にすると作れる | focus の枠線に使う | なし |

部品は `AddTool(ID, -32768, -32768)` で置き、`SetAttrs({"TOOLS_Name": 名前})` で決まった名前 (`Zoom`・`CaptionText`・`Mask1`・`Mask1Mosaic` など) を付ける。`check_final.py` はこの名前で部品を探し、人は Fusion ページで探す。

### 拡大の補間 (Transform の FilterMethod)

ズームは Fusion の `Transform` の `Size` と `Center` で行う。拡大の補間は `FilterMethod` で、番号は次の順。

| 番号 | 名前 |
|---|---|
| 0〜5 | `Nearest`・`Box`・`Linear`・`Quadratic`・`Cubic`・`Catmull-Rom` |
| 6〜10 | `Gaussian`・`Mitchell`・`Lanczos`・`Sinc`・`Bessel` |

- Resolve の既定は 2 (`Linear`) で、拡大するとぼける。8 (`Lanczos`) を入れると読み戻せる (`fusion_keyframes.py`)
- このリポジトリの既定は `Lanczos`。config の `storyboard.zoom.filter_method` に名前か番号で書く。layout の画面の拡大にも同じ値を使う

### 書き出し

| 動き | コードの対応 | 確認するテスト |
|---|---|---|
| 書き出し先に同じ名前のファイルがあると、確認なしで消してから書く。空のファイルを作っておいても守られない | 書き出し先の親に作った一時フォルダーへ書き、宛先が無いとき (`--overwrite` なら有っても) だけ置く | `render_overwrite.py` |
| `SetRenderSettings` の `MarkIn`・`MarkOut` はタイムラインの絶対フレームで、両端を含む。`SelectAllFrames` を偽にするとその範囲だけ書き出し、全編の同じフレームと画素まで一致する | `GetStartFrame()` を足して渡す (`--rows`) | `render_range.py` |
| `MarkIn` が `MarkOut` より後でも止まらない | `--rows` の範囲は Resolve に接続する前に確認する | なし |
| `SetCurrentRenderFormatAndCodec`・`SetRenderSettings` が失敗したまま書き出すと、前の設定のまま別の場所や形式に書かれる | どちらかが失敗したら書き出さない | なし |

書き出しは `AddRenderJob` で作業を追加し、`StartRendering([作業], isInteractiveMode=False)` で始め、`IsRenderingInProgress` が偽になるまで待つ (`resolve.render_jobs`)。成否は `GetRenderJobStatus` の `JobStatus` が `Complete` か。待つ上限を過ぎてもまだ書き出し中なら、一時フォルダーを消さずに失敗にする。

### 呼び出しの時間

外部の Python から呼んだときの 1 回の時間 (20 回の平均)。PC の性能と負荷で変わるので、合否には使わない。`tests/resolve/api_timing.py` の記録とこの表を比べる。

| 呼び出し | 時間 |
|---|---|
| 値を読む呼び出し (`GetCurrentTimecode`・`GetProperties`・`GetMarkers`) | 0.1〜0.4 ms |
| `SetProperties` | 5.2 ms |
| `SetCurrentTimecode` | 500 ms |

再生位置の移動は遅いので、何フレームも順に送る操作には使わない。
