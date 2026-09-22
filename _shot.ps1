# _shot.ps1 - capture the game screen to a PNG so it can be looked at.
#
# The game runs borderless at the desktop origin (client 2560x1440 at 0,0), so a capture of the
# primary screen is the game. Throwaway.
param([string]$out = "shot.png", [int]$w = 2560, [int]$h = 1440)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$bmp = New-Object System.Drawing.Bitmap $w, $h
$gfx = [System.Drawing.Graphics]::FromImage($bmp)
$gfx.CopyFromScreen(0, 0, 0, 0, $bmp.Size)
$path = Join-Path (Get-Location) $out
$bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
$gfx.Dispose()
$bmp.Dispose()
Write-Output "wrote $path"
