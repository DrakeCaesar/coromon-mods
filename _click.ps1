# _click.ps1 - click into the game window at a CONTENT coordinate.
#
#   powershell -File _click.ps1 256 144          # click content (256,144)
#   powershell -File _click.ps1 256 144 nomove   # move the cursor without clicking
#
# The game reports contentToScreenScale = 5 (a 512x288 content area on a 2560x1440 client), so the
# pixel position is client origin + content * 5. The client origin is asked of Windows rather than
# guessed. Throwaway harness: it only moves the mouse and synthesises one button press.
param([double]$cx, [double]$cy, [string]$mode = 'click')

Add-Type -Namespace Win -Name Api -MemberDefinition @'
[DllImport("user32.dll", SetLastError=true)] public static extern IntPtr FindWindow(string cls, string name);
[DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
[DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
[DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, IntPtr extra);
public struct RECT { public int Left, Top, Right, Bottom; }
public struct POINT { public int X, Y; }
'@

$proc = Get-Process Coromon -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $proc) { Write-Output 'no Coromon window'; exit 1 }
$hwnd = $proc.MainWindowHandle

$rect = New-Object Win.Api+RECT
[void][Win.Api]::GetClientRect($hwnd, [ref]$rect)
$origin = New-Object Win.Api+POINT
[void][Win.Api]::ClientToScreen($hwnd, [ref]$origin)

$scale = 5.0
$px = [int]([math]::Round($origin.X + $cx * $scale))
$py = [int]([math]::Round($origin.Y + $cy * $scale))

Write-Output ("window pid={0} client={1}x{2} origin=({3},{4}) -> content({5},{6}) = screen({7},{8})" -f `
  $proc.Id, $rect.Right, $rect.Bottom, $origin.X, $origin.Y, $cx, $cy, $px, $py)

[void][Win.Api]::SetCursorPos($px, $py)
Start-Sleep -Milliseconds 150
if ($mode -ne 'nomove') {
  [Win.Api]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)   # left down
  Start-Sleep -Milliseconds 60
  [Win.Api]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)   # left up
  Write-Output 'clicked'
} else {
  Write-Output 'moved'
}
