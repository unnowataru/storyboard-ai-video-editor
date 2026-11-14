# 設定の読み込み (PowerShell 用)。scripts/settings.py と同じ順で場所を決める:
#   環境変数 → 個人の設定ファイル (config/local.json、環境変数 RESOLVE_CONFIG で変更可) → config/defaults.json の search
# 使い方: . "$PSScriptRoot\config.ps1"; Get-ResolvePath out_dir
$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:Defaults = Get-Content -Raw -Encoding utf8 (Join-Path $RepoRoot 'config/defaults.json') | ConvertFrom-Json
$script:LocalPath = if ($env:RESOLVE_CONFIG) { $env:RESOLVE_CONFIG } else { Join-Path $RepoRoot 'config/local.json' }
$script:Local = if (Test-Path $LocalPath) { Get-Content -Raw -Encoding utf8 $LocalPath | ConvertFrom-Json } else { $null }

function Expand-ResolvePattern([string]$s) {
  $exe = if ($IsWindows -or $env:OS -eq 'Windows_NT') { '.exe' } else { '' }
  $s = $s.Replace('{repo}', $RepoRoot).Replace('{exe}', $exe)
  foreach ($k in $Defaults.paths.PSObject.Properties.Name) {
    $t = '{' + $k + '}'
    if ($s.Contains($t)) {
      $v = Get-ResolvePath $k
      if (-not $v) { return $null }
      $s = $s.Replace($t, $v)
    }
  }
  if ($s.StartsWith('~')) { $s = $HOME + $s.Substring(1) }
  $s = [Environment]::ExpandEnvironmentVariables($s)
  if ($s -notmatch '[*?]') { $s = [IO.Path]::GetFullPath($s) }
  return $s
}

function Get-ResolvePath([string]$Key) {
  $spec = $Defaults.paths.$Key
  if (-not $spec) { throw "設定に paths.$Key が無い" }
  if ($spec.env -and [Environment]::GetEnvironmentVariable($spec.env)) {
    return Expand-ResolvePattern ([Environment]::GetEnvironmentVariable($spec.env))
  }
  if ($Local -and $Local.paths -and $Local.paths.$Key) { return Expand-ResolvePattern $Local.paths.$Key }
  foreach ($pat in $spec.search) {
    if ($pat.StartsWith('PATH:')) {
      $c = Get-Command $pat.Substring(5) -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($c) { return $(if ($spec.check) { Split-Path -Parent $c.Source } else { $c.Source }) }
      continue
    }
    $p = Expand-ResolvePattern $pat
    if (-not $p) { continue }
    if ($spec.create) { return $p }
    $hits = if ($p -match '[*?]') { @(Get-Item -Path $p -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object FullName) } else { @($p) }
    foreach ($h in $hits) {
      if (-not (Test-Path $h)) { continue }
      if ($spec.check -and -not (Test-Path (Join-Path $h ($spec.check.Replace('{exe}', '.exe'))))) { continue }
      return $h
    }
  }
  return $null
}

function Get-ResolveSetting([string]$Section, [string]$Key) {
  if ($Local -and $Local.$Section -and $null -ne $Local.$Section.$Key) { return $Local.$Section.$Key }
  return $Defaults.$Section.$Key
}
