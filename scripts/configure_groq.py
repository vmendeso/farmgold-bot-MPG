"""Configure and verify the Windows user environment without displaying secrets."""
import argparse
import getpass
import hmac
import os
from pathlib import Path
import sys
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from farmbot.groq_ocr import api_key


def stored_key():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as handle:
            value, _ = winreg.QueryValueEx(handle, 'GROQ_API_KEY')
    except FileNotFoundError:
        return ''
    return value.strip() if isinstance(value, str) else ''


def save_key(key):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_SET_VALUE) as handle:
        winreg.SetValueEx(handle, 'GROQ_API_KEY', 0, winreg.REG_SZ, key)
    if not hmac.compare_digest(stored_key().encode(), key.encode()):
        raise OSError('verification failed')


def check():
    process = os.environ.get('GROQ_API_KEY', '').strip()
    saved = stored_key()
    disabled = os.environ.get('FARMBOT_DISABLE_REMOTE_OCR') == '1'
    different = bool(process and saved and not hmac.compare_digest(process.encode(), saved.encode()))
    print('Python:', sys.executable)
    print('Projeto:', ROOT)
    print('Chave no terminal:', 'presente' if process else 'ausente')
    print('Chave salva no Windows:', 'presente' if saved else 'ausente')
    print('OCR remoto bloqueado por variável de teste:', 'sim' if disabled else 'não')
    if different:
        print('A chave do terminal difere da salva. Execute: Remove-Item Env:GROQ_API_KEY')
    if disabled:
        print('Para liberar neste terminal: Remove-Item Env:FARMBOT_DISABLE_REMOTE_OCR')
    available = bool(api_key())
    print('Chave encontrada pelo bot:', 'sim' if available else 'não')
    print('Nenhuma chamada à API foi feita; a validade da chave não foi testada.')
    return 0 if available and not different else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Diagnóstico sem exibir a chave, gravar ou acessar a API')
    args = parser.parse_args(argv)
    if os.name != 'nt':
        print('Este configurador requer Windows. Configure GROQ_API_KEY no seu ambiente.')
        return 1
    try:
        if args.check:
            return check()
        if not sys.stdin.isatty():
            print('Execute em um terminal interativo do PowerShell, sem redirecionar a entrada.')
            return 1
        print('Cole apenas a chave da Groq. A entrada ficará oculta; pressione Enter ao terminar.')
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            key = getpass.getpass('Chave: ').strip()
        if not key or len(key) > 2048 or any(c.isspace() or not c.isascii() or not c.isprintable() or c in "\"'`" for c in key):
            print('Entrada inválida. Cole somente a chave, sem aspas, espaços ou comandos.')
            return 1
        save_key(key)
        print('Chave salva e relida com sucesso nas variáveis do usuário do Windows.')
        print('Execute agora: .\\.venv\\Scripts\\python.exe scripts/configure_groq.py --check')
        print('Se o diagnóstico apontar uma chave antiga no terminal, remova apenas a variável deste terminal:')
        print('Remove-Item Env:GROQ_API_KEY -ErrorAction SilentlyContinue')
        return 0
    except (getpass.GetPassWarning, EOFError):
        print('Não foi possível ler de forma oculta. Use um terminal interativo do PowerShell.')
        return 1
    except KeyboardInterrupt:
        print('\nConfiguração interrompida.')
        return 1
    except OSError:
        print('Não foi possível gravar ou verificar a variável do usuário no Windows. Nenhuma chave foi exibida.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
