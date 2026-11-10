param([Parameter(Mandatory = $true)][string]$ImagePath)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
# Public API references, consulted 2026-11-10:
# https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine
# https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrword
# https://learn.microsoft.com/en-us/uwp/api/windows.graphics.imaging.bitmapdecoder.getsoftwarebitmapasync
# https://learn.microsoft.com/en-us/dotnet/api/system.windowsruntimesystemextensions.astask
# https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrresult.textangle
$stream = $null
$bitmap = $null
try {
    if ($PSVersionTable.PSEdition -ne 'Desktop') {
        throw 'Windows OCR helper requires Windows PowerShell 5.1 (powershell.exe), not PowerShell Core.'
    }
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] > $null
    [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType = WindowsRuntime] > $null
    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime] > $null
    [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime] > $null
    [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] > $null
    [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime] > $null
    [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] > $null
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    if ($null -eq $asTask) { throw 'The .NET Windows Runtime asynchronous-operation bridge is unavailable.' }
    function Wait-WinRT($Operation, [Type]$ResultType) {
        $task = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
        return $task.GetAwaiter().GetResult()
    }
    $languages = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag })
    $requestedLanguage = New-Object Windows.Globalization.Language('en-US')
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($requestedLanguage)
    $warnings = @('Windows.Media.Ocr does not expose word confidence; confidence values are null.')
    if ($null -eq $engine) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
        $warnings += 'English OCR was unavailable; the installed user-profile OCR language was used.'
    }
    if ($null -eq $engine) {
        throw 'No installed Windows OCR recognizer language is available. Install an authorized Windows OCR language capability or use the manual importer.'
    }
    $resolvedPath = (Resolve-Path -LiteralPath $ImagePath).ProviderPath
    $file = Wait-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($resolvedPath)) ([Windows.Storage.StorageFile])
    $stream = Wait-WinRT ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
    $decoder = Wait-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $maximum = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    if ($decoder.PixelWidth -gt $maximum -or $decoder.PixelHeight -gt $maximum) {
        throw "Image exceeds installed Windows OCR maximum dimension $maximum. Prepare a separately named resized input with a new reference coordinate transform."
    }
    $bitmap = Wait-WinRT ($decoder.GetSoftwareBitmapAsync(
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Ignore)) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Wait-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $angle = $null
    if ($null -ne $result.TextAngle) { $angle = [double]$result.TextAngle }
    $allWords = @()
    $lines = @()
    foreach ($line in $result.Lines) {
        $words = @()
        $xmin = [double]::PositiveInfinity; $ymin = [double]::PositiveInfinity
        $xmax = 0.0; $ymax = 0.0
        foreach ($word in $line.Words) {
            $rect = $word.BoundingRect
            $box = @([double]$rect.X, [double]$rect.Y,
                [double]($rect.X + $rect.Width), [double]($rect.Y + $rect.Height))
            $xmin = [Math]::Min($xmin, $box[0]); $ymin = [Math]::Min($ymin, $box[1])
            $xmax = [Math]::Max($xmax, $box[2]); $ymax = [Math]::Max($ymax, $box[3])
            $observation = [ordered]@{ text = $word.Text; bbox = $box; confidence = $null }
            $words += $observation
            $allWords += $observation
        }
        $lineBox = $null
        if ($words.Count -gt 0) { $lineBox = @($xmin, $ymin, $xmax, $ymax) }
        $lines += [ordered]@{ text = $line.Text; bbox = $lineBox; words = @($words) }
    }
    [ordered]@{
        engine = 'Windows.Media.Ocr'; available = $true
        image = [ordered]@{ width = [int]$bitmap.PixelWidth; height = [int]$bitmap.PixelHeight }
        language = $engine.RecognizerLanguage.LanguageTag
        available_languages = @($languages); max_image_dimension = [int]$maximum
        windows_version = [Environment]::OSVersion.Version.ToString()
        powershell_version = $PSVersionTable.PSVersion.ToString()
        text_angle_degrees = $angle
        coordinate_convention = 'OCR engine frame boxes; caller rotates around image center by TextAngle for original image coordinates'
        words = @($allWords); lines = @($lines); warnings = @($warnings)
    } | ConvertTo-Json -Depth 10 -Compress
    exit 0
} catch {
    [ordered]@{ engine = 'Windows.Media.Ocr'; available = $false; error = $_.Exception.Message } |
        ConvertTo-Json -Depth 5 -Compress
    exit 2
} finally {
    if ($null -ne $bitmap) { $bitmap.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
}
