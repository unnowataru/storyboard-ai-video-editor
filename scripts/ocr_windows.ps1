# Windows 標準の文字認識 (Windows.Media.Ocr) で、画像の一覧をまとめて読む。track.py が呼ぶ。
# Windows PowerShell 5.1 で動かす (pwsh 7 は WinRT を直接呼べない)。
# 使い方: powershell.exe -NoProfile -ExecutionPolicy Bypass -File ocr_windows.ps1 -List 画像の一覧.txt -Out 結果.json
# 結果: { "画像のパス": [ { "text": 単語, "l": 何行目か, "x":, "y":, "w":, "h": }, ... ], ... }
# 確認するだけ:powershell.exe -NoProfile -ExecutionPolicy Bypass -File ocr_windows.ps1 -Check
#   文字認識が使えれば {"ok": true, "language": 言語のタグ} を出す (check_env.py が呼ぶ)
# このファイルは BOM 付きの UTF-8 で保存する (Windows PowerShell 5.1 は BOM が無いと日本語を読み違える)
param([string]$List, [string]$Out, [switch]$Check)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(); $task.Result }

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($Check) {
    $lang = if ($engine) { $engine.RecognizerLanguage.LanguageTag } else { $null }
    ConvertTo-Json -Compress ([ordered]@{ ok = [bool]$engine; language = $lang })
    exit 0
}
if (-not $engine) { throw '文字認識の言語が入っていない (設定 → 時刻と言語 → 言語)' }
$result = [ordered]@{}
foreach ($path in [System.IO.File]::ReadAllLines($List)) {
    if (-not $path) { continue }
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
    $li = 0
    $result[$path] = @(foreach ($line in $res.Lines) {
        $li++
        foreach ($w in $line.Words) {
            [ordered]@{ text = $w.Text; l = $li; x = [int]$w.BoundingRect.X; y = [int]$w.BoundingRect.Y
                        w = [int]$w.BoundingRect.Width; h = [int]$w.BoundingRect.Height }
        }
    })
    $stream.Dispose()
}
[System.IO.File]::WriteAllText($Out, (ConvertTo-Json $result -Depth 4 -Compress), (New-Object System.Text.UTF8Encoding $false))
