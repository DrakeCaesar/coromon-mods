# _crop.ps1 - crop a region of a PNG and scale it up, for reading small UI details.
#   powershell -File _crop.ps1 _shot1.png _crop1.png 0 180 700 180 3
param(
  [string]$src, [string]$out,
  [int]$x = 0, [int]$y = 0, [int]$w = 700, [int]$h = 180, [int]$zoom = 3
)
Add-Type -AssemblyName System.Drawing

$image = [System.Drawing.Image]::FromFile((Join-Path (Get-Location) $src))
$rect = New-Object System.Drawing.Rectangle $x, $y, $w, $h
$dstW = $w * $zoom
$dstH = $h * $zoom
$bmp = New-Object System.Drawing.Bitmap $dstW, $dstH
$gfx = [System.Drawing.Graphics]::FromImage($bmp)
$gfx.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
$dstRect = New-Object System.Drawing.Rectangle 0, 0, $dstW, $dstH
$gfx.DrawImage($image, $dstRect, $rect, [System.Drawing.GraphicsUnit]::Pixel)
$bmp.Save((Join-Path (Get-Location) $out), [System.Drawing.Imaging.ImageFormat]::Png)
$gfx.Dispose()
$bmp.Dispose()
$image.Dispose()
Write-Output "wrote $out ($dstW x $dstH from $x,$y $w x $h)"
