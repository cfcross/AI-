param(
    [string]$ImagePath,
    [Parameter(Mandatory=$true)][string]$PrinterName,
    [Parameter(Mandatory=$true)][string]$ReceiptPath,
    [string]$DocumentName = 'AI Printer A4 Color',
    [ValidateSet('LongEdge','ShortEdge','Simplex')][string]$DuplexMode = 'LongEdge',
    [ValidateSet('Cover','Contain')][string]$Layout = 'Cover',
    [ValidateRange(-10,10)][double]$OffsetXmm = 0,
    [ValidateRange(-10,10)][double]$OffsetYmm = 0,
    [ValidateRange(-10,10)][double]$BackOffsetXmm = 0,
    [ValidateRange(-10,10)][double]$BackOffsetYmm = 0,
    [switch]$MeasureOnly,
    [switch]$Calibration
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class PrintDeviceMetrics {
    [DllImport("gdi32.dll")] public static extern int GetDeviceCaps(IntPtr hdc, int index);
}
'@
$image = $null
$document = $null
$receiptFull = [IO.Path]::GetFullPath($ReceiptPath)
if (Test-Path -LiteralPath $receiptFull) {
    throw "Receipt already exists; check the previous job before printing again: $receiptFull"
}
$imageFull = if ($Calibration) { '' } else {
    if (-not $ImagePath) { throw 'ImagePath is required unless Calibration is set' }
    (Resolve-Path -LiteralPath $ImagePath).Path
}
$receipt = [ordered]@{
    document = $DocumentName; printer = $PrinterName; image = $imageFull
    paper = 'A4'; color = $true; copies = 1; duplex = $DuplexMode; layout = $Layout
    back_content = $(if ($Calibration) {'calibration_side_2'} else {'same_image'})
    calibration = [bool]$Calibration
    pages = $(if ($DuplexMode -eq 'Simplex') {1} else {2})
    status = 'preparing'; time = [DateTimeOffset]::Now.ToString('o')
    alignment = 'physical_paper_center'; page_geometry = [Collections.Generic.List[object]]::new()
}
function Save-Receipt {
    $parent = [IO.Path]::GetDirectoryName($receiptFull)
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $json = $receipt | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText($receiptFull, $json, (New-Object Text.UTF8Encoding($false)))
}
try {
    # Persist the current stage before any driver call: IsValid itself can hang.
    $receipt['stage'] = 'loading_image'
    Save-Receipt
    if (-not $Calibration) { $image = [Drawing.Image]::FromFile($imageFull) }
    $receipt['stage'] = 'creating_document'; Save-Receipt
    $document = New-Object Drawing.Printing.PrintDocument
    $document.PrinterSettings.PrinterName = $PrinterName
    $receipt['stage'] = 'checking_printer'; Save-Receipt
    if (-not $document.PrinterSettings.IsValid) { throw 'Printer is not available' }
    $receipt['stage'] = 'checking_capabilities'; Save-Receipt
    if (-not $document.PrinterSettings.SupportsColor) { throw 'Printer does not support color' }
    if ($DuplexMode -ne 'Simplex' -and -not $document.PrinterSettings.CanDuplex) {
        throw 'Printer does not support automatic duplex; no pages were submitted'
    }
    $a4 = @($document.PrinterSettings.PaperSizes | Where-Object { $_.Kind -eq [Drawing.Printing.PaperKind]::A4 })
    if ($a4.Count -eq 0) { throw 'Printer driver does not expose A4' }
    $document.DefaultPageSettings.PaperSize = $a4[0]
    $document.DefaultPageSettings.Color = $true
    $document.DefaultPageSettings.Landscape = $false
    $document.DefaultPageSettings.Margins = New-Object Drawing.Printing.Margins(0,0,0,0)
    $document.PrinterSettings.Copies = 1
    $document.PrinterSettings.Duplex = switch ($DuplexMode) {
        'LongEdge' { [Drawing.Printing.Duplex]::Vertical }
        'ShortEdge' { [Drawing.Printing.Duplex]::Horizontal }
        'Simplex' { [Drawing.Printing.Duplex]::Simplex }
    }
    $document.OriginAtMargins = $false
    $document.DocumentName = $DocumentName
    $document.PrintController = New-Object Drawing.Printing.StandardPrintController
    $receipt['stage'] = 'reading_page_geometry'; Save-Receipt
    $area = $document.DefaultPageSettings.PrintableArea
    $receipt['printable_area_mm'] = @{
        x = [Math]::Round($area.X * .254, 2); y = [Math]::Round($area.Y * .254, 2)
        width = [Math]::Round($area.Width * .254, 2); height = [Math]::Round($area.Height * .254, 2)
    }
    $receipt['driver_duplex'] = $document.PrinterSettings.Duplex.ToString()
    $pageCounter = @{ value = 0 }
    $pageCount = $receipt.pages
    $document.add_PrintPage({
        param($sender, $eventArgs)
        # Query the actual per-page printer DC, including duplex/driver offsets.
        # PageSettings.HardMarginX/Y can lose precision and may describe a default DC.
        $graphics = $eventArgs.Graphics
        $hdc = $graphics.GetHdc()
        try {
            $dpiX = [PrintDeviceMetrics]::GetDeviceCaps($hdc,88)
            $dpiY = [PrintDeviceMetrics]::GetDeviceCaps($hdc,90)
            $paperW = [PrintDeviceMetrics]::GetDeviceCaps($hdc,110)
            $paperH = [PrintDeviceMetrics]::GetDeviceCaps($hdc,111)
            $left = [PrintDeviceMetrics]::GetDeviceCaps($hdc,112)
            $top = [PrintDeviceMetrics]::GetDeviceCaps($hdc,113)
            $printW = [PrintDeviceMetrics]::GetDeviceCaps($hdc,8)
            $printH = [PrintDeviceMetrics]::GetDeviceCaps($hdc,10)
        } finally { $graphics.ReleaseHdc($hdc) }
        if ($dpiX -le 0 -or $dpiY -le 0 -or $paperW -le 0 -or $paperH -le 0) {
            throw 'Printer did not return valid page geometry'
        }
        $graphics.ResetTransform()
        $graphics.PageUnit = [Drawing.GraphicsUnit]::Display
        $graphics.PageScale = 1
        if ($Layout -eq 'Cover') {
            # Cover the whole physical A4 sheet, centered on paper. Hardware clips
            # the unprintable edge; we do not create an additional software edge.
            $x = [single](-$left * 100.0 / $dpiX)
            $y = [single](-$top * 100.0 / $dpiY)
            $width = [single]($paperW * 100.0 / $dpiX)
            $height = [single]($paperH * 100.0 / $dpiY)
        } else {
            $x = [single]0; $y = [single]0
            $width = [single]($printW * 100.0 / $dpiX)
            $height = [single]($printH * 100.0 / $dpiY)
        }
        $offsetX = $OffsetXmm; $offsetY = $OffsetYmm
        if ($pageCounter.value -eq 1) { $offsetX += $BackOffsetXmm; $offsetY += $BackOffsetYmm }
        $x += [single]($offsetX / .254); $y += [single]($offsetY / .254)
        $receipt.page_geometry.Add(@{
            page = $pageCounter.value + 1
            hardware_margins_mm = @{
                left = $left * 25.4 / $dpiX; top = $top * 25.4 / $dpiY
                right = ($paperW-$left-$printW)*25.4/$dpiX
                bottom = ($paperH-$top-$printH)*25.4/$dpiY
            }
            offset_x_mm = $offsetX; offset_y_mm = $offsetY
            paper_width_mm = $paperW * 25.4 / $dpiX; paper_height_mm = $paperH * 25.4 / $dpiY
        })
        if ($Calibration) {
            # A controlled edge target, independent of an image's own white pixels.
            # Draw over the physical sheet; only the printer's clip limits the ink.
            $graphics.FillRectangle([Drawing.Brushes]::SteelBlue, [single](-$left*100.0/$dpiX),
                [single](-$top*100.0/$dpiY), [single]($paperW*100.0/$dpiX), [single]($paperH*100.0/$dpiY))
            $labelFont = New-Object Drawing.Font('Microsoft YaHei',24)
            $smallFont = New-Object Drawing.Font('Microsoft YaHei',12)
            $guidePen = New-Object Drawing.Pen([Drawing.Color]::White,0.4)
            try {
                # Use real mm for evenly spaced inset guide rectangles.
                for ($inset = 5; $inset -le 15; $inset += 5) {
                    $gx = [single]($inset/.254 - $left*100.0/$dpiX)
                    $gy = [single]($inset/.254 - $top*100.0/$dpiY)
                    $gw = [single]($paperW*100.0/$dpiX - 2*$inset/.254)
                    $gh = [single]($paperH*100.0/$dpiY - 2*$inset/.254)
                    $graphics.DrawRectangle($guidePen,$gx,$gy,$gw,$gh)
                }
                $side = $pageCounter.value + 1
                $graphics.DrawString("SIDE $side / TOP",$labelFont,[Drawing.Brushes]::White,[single]150,[single]120)
                $graphics.DrawString('LEFT',$smallFont,[Drawing.Brushes]::White,[single]60,[single]400)
                $graphics.DrawString('RIGHT',$smallFont,[Drawing.Brushes]::White,[single]630,[single]400)
                $graphics.DrawString('BOTTOM',$labelFont,[Drawing.Brushes]::White,[single]200,[single]850)
                $graphics.DrawString('A4 duplex calibration / Measure white edges only',
                    $smallFont,[Drawing.Brushes]::White,[single]70,[single]520)
                $graphics.DrawString('Guide rectangles: 5 / 10 / 15 mm from paper edge',
                    $smallFont,[Drawing.Brushes]::White,[single]70,[single]560)
            } finally { $labelFont.Dispose(); $smallFont.Dispose(); $guidePen.Dispose() }
        } else {
        $source = New-Object Drawing.RectangleF(0,0,$image.Width,$image.Height)
        if ($Layout -eq 'Cover') {
            $scale = [Math]::Max($width / [double]$image.Width, $height / [double]$image.Height)
            $sw = [single]($width / $scale)
            $sh = [single]($height / $scale)
            $source = New-Object Drawing.RectangleF(([single](($image.Width-$sw)/2)),([single](($image.Height-$sh)/2)),$sw,$sh)
        } else {
            $scale = [Math]::Min($width / [double]$image.Width, $height / [double]$image.Height)
            $drawWidth = [single]($image.Width * $scale)
            $drawHeight = [single]($image.Height * $scale)
            $x += ($width-$drawWidth)/2
            $y += ($height-$drawHeight)/2
            $width=$drawWidth; $height=$drawHeight
        }
        $eventArgs.Graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $rectangle = New-Object Drawing.RectangleF($x, $y, $width, $height)
        $eventArgs.Graphics.DrawImage($image, $rectangle, $source, [Drawing.GraphicsUnit]::Pixel)
        }
        $pageCounter.value++
        $eventArgs.HasMorePages = $pageCounter.value -lt $pageCount
    }.GetNewClosure())
    if ($MeasureOnly) {
        $receipt.status = 'measured_only'
        Save-Receipt
        $receipt | ConvertTo-Json -Depth 4
        return
    }
    # Record intent BEFORE Print; a crash must not trigger blind reprinting.
    $receipt.status = 'submitting'
    $receipt['stage'] = 'printing'
    Save-Receipt
    $document.Print()
    $receipt['rendered_pages'] = $pageCounter.value
    $receipt.status = 'spooled'
    $receipt['stage'] = 'completed'
    $receipt['message'] = 'Windows accepted the print call; physical output must be confirmed separately.'
    Save-Receipt
    $receipt | ConvertTo-Json -Depth 4
} catch {
    if ($receipt.status -eq 'submitting') { $receipt.status = 'submission_unknown' }
    else { $receipt.status = 'failed' }
    $receipt['error'] = $_.Exception.Message
    Save-Receipt
    throw
} finally {
    if ($document) { $document.Dispose() }
    if ($image) { $image.Dispose() }
}
