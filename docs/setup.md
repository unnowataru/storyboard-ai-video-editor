# 準備

このリポジトリのスクリプトを Windows の PC で動かすまでの準備です。最後に `python scripts/check_env.py` で、そろっているかを確認します。

## Python とパッケージ

Python 3.10 以上を入れ、リポジトリのフォルダーでパッケージを入れます。

```
python -m pip install -r requirements.txt
```

| パッケージ | 使うもの |
|---|---|
| pillow | テロップの幅を測る、`check_env.py`、`track.py`、フレームの色の確認 (必須) |
| numpy・opencv-python | `track.py` (画面の変化の判定、部品の照合) |
| pytest | `tests/offline` |

Resolve のスクリプトのモジュール (`DaVinciResolveScript`) は Resolve Studio に付いてきます。pip では入れません。

スクリプトは `python scripts/xxx.py` で実行します。PowerShell から設定の Python (`paths.python`) で実行するときは `pwsh -File scripts/run.ps1 scripts/check_env.py` の形も使えます。

## FFmpeg

`ffmpeg` と `ffprobe` を使います。libx264 と drawtext の入った full build が要ります (`contact_sheet.py` が drawtext を使う)。

```
winget install Gyan.FFmpeg
```

場所は次の順に探します: 環境変数 `FFMPEG_DIR` → `config/local.json` の `paths.ffmpeg_dir` → `PATH` の `ffmpeg` → WinGet で入れた Gyan.FFmpeg のフォルダー。

## DaVinci Resolve Studio

完成版 (`sb2resolve.py`) と、Resolve に接続するスクリプト・テストで使います。簡易版 (`sb2ffmpeg.py`) だけなら要りません。

- 外部のスクリプトから接続できるのは Studio だけです
- 環境設定 > システム > 一般 >「外部スクリプトに使用」を「ローカル」にします
- 接続するときは Resolve を起動しておきます
- 接続の確認は `python scripts/resolve_lock.py run --owner me -- python scripts/check_env.py --connect` か `python scripts/resolve_lock.py run --owner me -- python scripts/probe.py`

既定の場所は次のとおりで、違う場所に入れたときは環境変数か `config/local.json` で指定します。

| 設定の名前 | 環境変数 | 既定の場所 |
|---|---|---|
| `resolve_install_dir` | `RESOLVE_INSTALL_DIR` | `%PROGRAMFILES%/Blackmagic Design/DaVinci Resolve` |
| `resolve_script_api` | `RESOLVE_SCRIPT_API` | `%PROGRAMDATA%/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting` |
| `resolve_script_lib` | `RESOLVE_SCRIPT_LIB` | インストール先の `fusionscript.dll` |

スクリプトの API の動きを確認した Resolve の版は `config/defaults.json` の `resolve.verified_version` (21.1.1.10) です。`check_env.py --connect` は、接続した Resolve の版がこれと違うと「確認し直しが要る」と出します。API の使い方と実機での制約は [scripting-api.md](scripting-api.md) にあります。

### 再生のフレームレート

Resolve のスクリプトの API では、プロジェクトの再生のフレームレートを変えられません。新しいプロジェクトは 24 のままになることがあり、`sb2resolve.py` は違うときに警告を出します。そのときは Resolve の画面で次のようにします。

1. メニューの「ファイル」>「プロジェクト設定…」を開く
2. 「マスター設定」の「再生フレームレート」を 30 (構成表の `fps`) にする
3. 「保存」を押す

新しいプロジェクトをこの値で始めるには、プロジェクト設定の「…」>「現在の設定をデフォルトプリセットに設定」を 1 回行います。直したプロジェクトは `sb2resolve.py --project` で使い回せます。

### ロック

Resolve に接続する処理は `resolve_lock.py run` で包みます。包まれていないときは警告を出して接続します。包まれていない処理を接続させないなら、`config/local.json` に次のように書きます。

```json
{"resolve": {"require_lock": true}}
```

## 書体

テロップと肩テロップの既定の書体は Meiryo (太字は Meiryo Bold) です。`contact_sheet.py` の時刻の字は Arial で、無ければ Meiryo を使います。

書体は `config/defaults.json` の `fonts` に書いた「書体の名前: ファイル名の一覧」で、書体のフォルダー (`paths.fonts_dirs`。既定は `%WINDIR%/Fonts` と `%LOCALAPPDATA%/Microsoft/Windows/Fonts`) から探します。ほかの書体を使うときは、書体を入れてから `config/local.json` の `fonts` に名前とファイル名を追加します。

```json
{
  "fonts": {
    "Noto Sans JP": ["NotoSansJP-Regular.ttf"]
  }
}
```

太字は「名前 Bold」(例 `Meiryo Bold`) の項目を先に探します。`check_env.py` は、使う書体のファイルが見つかるかと、日本語を描けるかを確認します。

## 文字認識

`track.py` と `scene_events.py --ocr` は Windows 標準の文字認識を使います。文字認識は手元の Windows で動き、画面の内容を外部へ送信しません。

- 設定 > 時刻と言語 > 言語 で、文字認識に使える言語を入れておきます
- 呼び出しは Windows PowerShell 5.1 (`powershell.exe`。`config/defaults.json` の `apps.windows_powershell`) の `scripts/ocr_windows.ps1` です
- 使えるかは `check_env.py` が確認します (`ocr_windows.ps1 -Check`)

ほかのスクリプトは文字認識を使いません。

## 設定ファイルと環境変数

設定は `config/defaults.json` にあり、個人の設定ファイル `config/local.json` で上書きします。`config/local.json` は git に入れません。見本は `config/local.example.json` です。個人の設定ファイルの場所は環境変数 `RESOLVE_CONFIG` で変えられます。

```json
{
  "paths": {
    "trial_source": "{work_dir}/test-media/testsrc.mp4"
  },
  "storyboard": {
    "caption": {
      "lift": 0.08
    }
  }
}
```

どの値がどこから来たかは次のコマンドで出せます。構成表を渡すと、その構成表の値を重ねた結果を出します。

```
python scripts/common.py --show-config [構成表.json]
```

### 場所

場所 (`paths`) は、環境変数 → `config/local.json` の `paths` → `config/defaults.json` の `search` の順に探した場所、の順で決まります (上が優先)。値には `%USERPROFILE%` のような環境変数、`~`、`{work_dir}` のようなほかの場所の名前を書けます。`fonts_dirs` は複数の場所を取り、環境変数では `;` で区切ります。

Resolve と FFmpeg の場所は上の節にあります。そのほかの場所は次のとおりです。

| 設定の名前 | 環境変数 | 既定 | 使い道 |
|---|---|---|---|
| `work_dir` | `RESOLVE_WORK` | `%USERPROFILE%/Videos/storyboard-work` | 作業フォルダー。素材・途中のファイル・`resolve.lock` を置く |
| `out_dir` | `RESOLVE_OUT_DIR` | `{work_dir}/out` | 出力先。結果の JSON と書き出し |
| `preview_dir` | `RESOLVE_PREVIEW_DIR` | `%USERPROFILE%/Videos/storyboard-preview` | 仮組み (`sb2resolve.py --render-anyway`・`--rows`) の書き出し先 |
| `downloads_dir` | `RESOLVE_DOWNLOADS` | `%USERPROFILE%/Downloads` | `pins.py` が `marks.json` を探す場所の 1 つ |
| `trial_source` | `RESOLVE_TRIAL_SOURCE` | 無し | テスト用の素材 |
| `python` | `RESOLVE_PYTHON` | `PATH` の `python` | `run.ps1` が使う Python |
| `fonts_dirs` | `RESOLVE_FONTS_DIR` | `%WINDIR%/Fonts`・`%LOCALAPPDATA%/Microsoft/Windows/Fonts` | 書体を探すフォルダー |

### 作業フォルダーと出力先

作業フォルダーと出力先は AppData の外に置きます。パッケージ化されたアプリから AppData の下に書いたファイルは別の場所に振り替えられ、Resolve から見えないことがあります。`check_env.py` は AppData の下を指していると警告します。

### 見た目と出力

`config/defaults.json` の `storyboard` は、構成表の全体の項目の既定値です (フレームレート・タイムラインの解像度・テロップ・肩テロップ・ズーム・マスク・書き出しなど)。値は、構成表の値 → `config/local.json` の `storyboard` → `config/defaults.json` の順で決まります (上が優先)。項目の意味は [storyboard.md](storyboard.md) にあります。

### そのほかの値

`track`・`scene_events`・`contact_sheet`・`area_sheet`・`frame_read`・`sb2ffmpeg`・`report`・`apps`・`resolve` は、各スクリプトの数の値と設定です。`config/local.json` に同じ名前で書いた値が `config/defaults.json` より優先します。どのスクリプトがどの値を使うかは、各スクリプトの先頭の説明にあります。

## テスト用の素材

機密の無い合成の素材を FFmpeg で作れます。

| コマンド | 作るもの | 使う場面 |
|---|---|---|
| `python scripts/sb2ffmpeg_bench.py --make-source` | 作業フォルダーの `bench-4k.mp4` (4K、245 秒) | 見本の構成表 (`examples/`)、`sb2ffmpeg_bench.py` |
| `python scripts/make_test_source.py [--seconds 40] [--out パス]` | 作業フォルダーの `test-media\testsrc.mp4` | Resolve が要るテスト、`tests/trial-storyboard.json` |

`make_test_source.py` で作ったパスは、環境変数 `RESOLVE_TRIAL_SOURCE` か `config/local.json` の `paths.trial_source` に入れます。`sb2ffmpeg_bench.py --make-source` は素材を作った後に、簡易版を速く動かすパラメーターも測ります (`--apply` で `config/local.json` の `sb2ffmpeg` に書き込む)。

## 準備の確認

```
python scripts/check_env.py [--json]
python scripts/resolve_lock.py run --owner me -- python scripts/check_env.py --connect [--json]
```

- Python とパッケージ、Resolve の場所と版、FFmpeg (libx264・drawtext・ffprobe)、書体、文字認識、作業フォルダーと出力先、テスト用の素材、個人の設定ファイルを確認します
- 画面の 1 行目は件数と結果の JSON (出力先の `check-env.json`) のパスです。続けて、欠けている必須のものと、注意 (確認し直しが要る、作業フォルダーか出力先が AppData の下にある) だけを出します
- 必須のものが欠けていると終了コードは 1 です。任意のもの (numpy・opencv-python・文字認識・Resolve の起動など) は欠けていても 0 です
- Resolve のインストール先・版・スクリプト API・`fusionscript.dll` は、`--connect` を付けたときだけ必須です。付けなければ任意の項目として出すので、簡易版 (FFmpeg だけ) の準備でも終了コードは 0 になります
- `--connect` は起動中の Resolve に接続し、製品名・版・Studio かを確認します。Resolve に接続するので `resolve_lock.py run` で包みます
