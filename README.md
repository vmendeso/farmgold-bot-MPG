# Farmbot para My Party Is Grinding

Bot em Python para medir o rendimento de Gold no **My Party Is Grinding**, comparar fases e dificuldades e selecionar a melhor opção entre as fases medidas. O programa lê a interface com capturas de tela e OCR, registra as batalhas em SQLite e gera um ranking em CSV.

## Recursos

- Interface gráfica para configurar, iniciar, parar e acompanhar as medições.
- Reconhecimento de resultados e referências visuais com OpenCV e Tesseract.
- Busca entre fases disponíveis, com reaproveitamento de medições recentes.
- Rodadas periódicas sem sobreposição e exportação de Gold/min e Gold/h estimados.
- Verificações de foco e confirmação visual antes de interagir com o jogo.

Os resultados dependem das batalhas observadas e da calibração da interface. O ranking identifica a melhor fase medida dentro do catálogo explorado.

## Requisitos

- Windows 10 ou 11.
- Python 3.11 ou superior e Git.
- Tesseract OCR com os idiomas `por` e `eng`, instalado pelo script abaixo.
- Jogo visível e referências visuais compatíveis com o tamanho, o zoom e o layout da janela.

## Instalação

No PowerShell, clone o projeto e instale as dependências:

```powershell
git clone https://github.com/vmendeso/farmgold-bot-MPG.git
cd farmgold-bot-MPG
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\scripts\setup_ocr.ps1
```

Use uma instalação do Python 3.11 ou superior no comando `py -3`. Não é necessário ativar o ambiente virtual. O script instala uma versão fixada do Tesseract e dos idiomas, verifica os downloads e exibe o caminho do executável.

Em [config.json](config.json), ajuste `vision.tesseract_cmd` para o caminho exibido pelo script. O arquivo versionado contém um caminho absoluto da máquina de desenvolvimento; o instalador não o altera automaticamente. Em caminhos JSON, use `/` ou escape cada barra invertida como `\\`.

Confira a instalação e as referências:

```powershell
.\.venv\Scripts\python.exe -m farmbot check-config
```

A configuração inclui referências para Ato 4/Pesadelo, Ato 1/Averno e Ato 2/Inferno. Mudanças no layout ou na escala podem exigir [nova calibração](docs/guia-tecnico.md#conferir-a-leitura-de-batalha).

## Primeiros passos

Abra `abrir_interface.cmd` com dois cliques ou execute, na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m farmbot gui
```

Revise os ajustes, deixe o jogo visível e clique em **Iniciar**. A aba **Atividade** acompanha a execução e a aba **Ranking** mostra as medições exportadas. Pare o bot antes de alterar os ajustes e inicie novamente para aplicá-los.

O [config.json](config.json) vem com **cliques habilitados** (`dry_run: false`) e **seleção da melhor fase ao final** (`auto_switch_best: true`). Para simular sem cliques pela interface, marque **Simulação (sem cliques)** e desmarque **Trocar para a melhor fase**. Com a troca final desativada, a busca retorna à fase inicial ao concluir normalmente, salvo quando uma derrota exige recuar para outra fase.

Use **Parar**, **Esc** ou o comando `stop` para solicitar o encerramento, preservando os resultados salvos. Ao perder o foco do jogo, o loop adia a rodada e tenta novamente no próximo horário.

## Uso pelo terminal

Execute os comandos na raiz do projeto. As opções globais `--config`, `--verbose` e `--focus` ficam antes do subcomando.

```powershell
# Medir somente a fase atual, permitindo mover o ponteiro para revelar a legenda.
.\.venv\Scripts\python.exe -m farmbot --focus observe --hover

# Buscar entre até duas fases com nova coleta.
.\.venv\Scripts\python.exe -m farmbot --focus run --execute --max-stages 2

# Repetir a busca a cada cinco minutos.
.\.venv\Scripts\python.exe -m farmbot --focus loop --execute --interval-minutes 5

# Consultar e exportar o ranking salvo.
.\.venv\Scripts\python.exe -m farmbot rank

# Solicitar a parada do bot ativo.
.\.venv\Scripts\python.exe -m farmbot stop
```

Para `observe --hover`, deixe o painel Mundo fechado e a repetição de batalhas ativa no jogo. Esse comando não clica nem muda de fase. `--focus` solicita foco somente no início.

`--execute` habilita cliques mesmo se a simulação estiver configurada. Omitir essa opção mantém o valor de `dry_run` do JSON, portanto não desliga os cliques na configuração fornecida. A opção `--auto-switch` habilita a seleção final; sem ela, vale `auto_switch_best` do JSON.

A configuração busca coletar cinco resultados novos por fase, podendo encerrar antes por queda de rendimento ou pelos limites de coleta. O loop mede até uma fase nova por rodada; resultados confiáveis reutilizados do histórico não consomem esse limite. O cálculo de rendimento considera cinco segundos de reinício por batalha concluída e o tempo das interrupções. Gold/h é uma estimativa, não uma garantia de rendimento contínuo.

## Dados e documentação

| Caminho | Conteúdo |
|---|---|
| `farmbot/` | Código da interface, captura, OCR, navegação e busca |
| `config.json` | Parâmetros de execução e calibração |
| `templates/` | Referências visuais usadas no reconhecimento |
| `data/` | Banco SQLite, ranking CSV, catálogo e log gerados |
| `artifacts/` | Capturas, diagnósticos e resultados da simulação |
| `tests/` | Testes e imagens de regressão |

As pastas `data/`, `artifacts/`, `.venv/` e `.tools/` são locais e não acompanham o clone.

O [guia técnico e histórico de validações](docs/guia-tecnico.md) preserva o conteúdo do README anterior: comandos completos, calibração, regras de medição, diagnóstico de OCR e registros dos testes realizados. Para preparar novas referências visuais, consulte também [templates/README.md](templates/README.md).

## Desenvolvimento

Execute uma demonstração sem acessar o jogo ou o OCR:

```powershell
.\.venv\Scripts\python.exe -m farmbot demo
```

O resultado fica em `artifacts/demo/`. Para executar a suíte de testes:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
