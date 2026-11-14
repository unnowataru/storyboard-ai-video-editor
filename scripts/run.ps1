# 設定の Python (config の paths.python。環境変数 RESOLVE_PYTHON が優先) でスクリプトを動かす。
# 使い方: pwsh -File scripts/run.ps1 scripts/check_env.py (Resolve に接続するものは resolve_lock.py run で包む)
. "$PSScriptRoot\config.ps1"
$py = Get-ResolvePath python
if (-not $py) { Write-Error 'Python が見つからない。環境変数 RESOLVE_PYTHON か config/local.json の paths.python で指定する'; exit 1 }
& $py @args
exit $LASTEXITCODE
