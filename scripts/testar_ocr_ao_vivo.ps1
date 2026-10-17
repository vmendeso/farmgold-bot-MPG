Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$Host.UI.RawUI.WindowTitle = 'Farm Bot - teste de OCR ao vivo'
$taskPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
Write-Host 'Validando as capturas de regressao do OCR...' -ForegroundColor Cyan
& $taskPython -X utf8 -m unittest discover -s tests -p test_log_ocr.py -v
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Teste de regressao falhou. O teste no jogo nao foi iniciado.' -ForegroundColor Red
    return
}
Write-Host ''
Write-Host 'Teste ao vivo: cinco batalhas na fase atual, sem trocar de fase.' -ForegroundColor Cyan
Write-Host 'Mantenha o jogo em primeiro plano. ESC ou Ctrl+C interrompem.'
& $taskPython -u -X utf8 -c "import logging; from farmbot.cli import main; logging.getLogger('farmbot.vision').setLevel(logging.DEBUG); raise SystemExit(main(['--focus', 'observe', '--hover']))"
Write-Host ('Teste encerrado. Codigo de saida: ' + $LASTEXITCODE) -ForegroundColor Cyan
