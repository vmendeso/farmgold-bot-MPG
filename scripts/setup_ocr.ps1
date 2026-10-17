# Installs the official Windows build linked by the Tesseract project.
# Files live under .tools; the NSIS installer also registers Tesseract in Windows.
# Sources:
# https://tesseract-ocr.github.io/tessdoc/Installation.html
# https://github.com/UB-Mannheim/tesseract/wiki
# https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.3
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$projectRoot = Split-Path -Parent $PSScriptRoot
$downloadRoot = Join-Path $projectRoot '.tools\downloads'
$installRoot = Join-Path $projectRoot '.tools\tesseract'
$ocrExecutable = Join-Path $installRoot 'tesseract.exe'
$ocrVersion = '5.5.3.20260724'
$installerName = "tesseract-ocr-w64-setup-$ocrVersion.exe"
$installerPath = Join-Path $downloadRoot $installerName
$installerUrl = "https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/$installerName"
$installerHash = 'BEE9E3434BD94FD65387D9BE28CD467A41F61B1275383B55B0F59A1331270AE4'
$dataRevision = '87416418657359cb625c412a48b6e1d6d41c29bd'
$languageHashes = @{
    eng = '7D4322BD2A7749724879683FC3912CB542F19906C83BCC1A52132556427170B2'
    por = 'C4932B937207A9514B7514D518B931A99938C02A28A5A5A553F8599ED58B7DEB'
}

function Get-VerifiedDownload {
    param([string]$Uri, [string]$Destination, [string]$Sha256)
    if (Test-Path -LiteralPath $Destination) {
        $cachedHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
        if ($cachedHash -eq $Sha256) { return }
    }
    Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $Destination
    $actualHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
    if ($actualHash -ne $Sha256) {
        throw "Invalid SHA256 for $Destination. Expected $Sha256; got $actualHash."
    }
}

New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $ocrExecutable)) {
    if ((Test-Path -LiteralPath $installRoot) -and (Get-ChildItem -LiteralPath $installRoot -Force | Select-Object -First 1)) {
        throw "Refusing to install into a nonempty directory without tesseract.exe: $installRoot"
    }
    Get-VerifiedDownload -Uri $installerUrl -Destination $installerPath -Sha256 $installerHash
    # NSIS requires /D to be last and unquoted, even for paths with spaces.
    $installer = Start-Process -FilePath $installerPath -ArgumentList "/S /CURRENTUSER /D=$installRoot" -WindowStyle Hidden -Wait -PassThru
    if ($installer.ExitCode -ne 0) { throw "Tesseract installer exited with code $($installer.ExitCode)." }
}
if (-not (Test-Path -LiteralPath $ocrExecutable)) { throw 'Tesseract executable was not installed.' }

$versionLines = @(& $ocrExecutable --version)
if ($LASTEXITCODE -ne 0) { throw 'Tesseract failed to start.' }
if ($versionLines[0] -ne "tesseract v$ocrVersion") {
    throw "Unexpected existing Tesseract version: $($versionLines[0]). No files were replaced."
}

foreach ($language in @('eng', 'por')) {
    $dataPath = Join-Path $installRoot "tessdata\$language.traineddata"
    $dataUrl = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/$dataRevision/$language.traineddata"
    Get-VerifiedDownload -Uri $dataUrl -Destination $dataPath -Sha256 $languageHashes[$language]
}

$availableLanguages = @(& $ocrExecutable --list-langs)
if ($LASTEXITCODE -ne 0) { throw 'Unable to list installed OCR languages.' }
foreach ($language in @('eng', 'por')) {
    if ($availableLanguages -notcontains $language) { throw "Missing OCR language: $language" }
}
$versionLines[0]
$availableLanguages
Write-Output "vision.tesseract_cmd: $ocrExecutable"
