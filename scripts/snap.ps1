param([string]$Process, [string]$Out)
# 指定プロセスの見えている最上位の窓を PrintWindow で撮る (前面に出さない)
# 既定: プロセス名は config の apps.resolve_process、保存先は出力先 (config の out_dir) の snap.png
. "$PSScriptRoot\config.ps1"
if (-not $Process) { $Process = Get-ResolveSetting apps resolve_process }
if (-not $Out) { $Out = Join-Path (Get-ResolvePath out_dir) 'snap.png' }
New-Item -ItemType Directory -Force (Split-Path -Parent $Out) | Out-Null
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text; using System.Collections.Generic;
public class W {
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr p);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  public struct RECT { public int L, T, R, B; }
  public static List<IntPtr> Of(uint pid) { var l = new List<IntPtr>();
    EnumWindows((h, p) => { uint x; GetWindowThreadProcessId(h, out x); if (x == pid && IsWindowVisible(h)) l.Add(h); return true; }, IntPtr.Zero); return l; }
}
"@
[void][W]::SetProcessDPIAware()  # 拡大率の影響で窓の大きさを小さく読まないように
$i = 0
foreach ($p in Get-Process $Process) {
  foreach ($h in [W]::Of($p.Id)) {
    $sb = New-Object Text.StringBuilder 256; [void][W]::GetWindowText($h, $sb, 256)
    $r = New-Object W+RECT; [void][W]::GetWindowRect($h, [ref]$r)
    $w = $r.R - $r.L; $hh = $r.B - $r.T
    if ($w -lt 50 -or $hh -lt 50) { continue }
    $bmp = New-Object Drawing.Bitmap $w, $hh; $g = [Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
    [void][W]::PrintWindow($h, $dc, 2); $g.ReleaseHdc($dc)
    $f = $Out -replace '\.png$', "-$i.png"; $bmp.Save($f); $g.Dispose(); $bmp.Dispose()
    "$f  [$($sb.ToString())] ${w}x$hh"; $i++
  }
}
