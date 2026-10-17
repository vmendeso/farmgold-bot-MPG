Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$Host.UI.RawUI.WindowTitle = 'Farm Bot - teste de cinco fases'
$taskPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
Write-Host 'Teste: cinco fases, uma batalha nova por fase.' -ForegroundColor Cyan
Write-Host 'Mantenha o jogo em primeiro plano. ESC ou Ctrl+C interrompem.'
& $taskPython -u -X utf8 -m farmbot --config config.validation-five.json --focus run --execute --max-stages 5 --battles-per-stage 1
$taskExitCode = $LASTEXITCODE
Set-Content -LiteralPath 'data/validation-five-20261016.exit.txt' -Value $taskExitCode
Write-Host ('Teste encerrado. Codigo de saida: ' + $taskExitCode) -ForegroundColor Cyan
