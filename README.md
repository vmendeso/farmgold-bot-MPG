# Farmbot para My Party Is Grinding

Validação completa de navegação em 19/10/2026, concluída às 10:23: corrigida a confirmação de **Ato 2 já selecionado**, que falhava e levava à procura desnecessária do botão não selecionado. A referência `act2_selected_body.png` preserva o texto e as bordas laterais azuis do estado selecionado; o clique usa `act2_label.png`. O limiar permanece em 0,90, com testes negativos para o ato não selecionado. A descoberta percorreu **Pesadelo, Averno e Inferno**, reconheceu **25 fases** (9, 9 e 7 respectivamente), fechou o Mundo e confirmou o retorno à batalha, encerrando com **código 0**. O tooltip de 2-3/Inferno foi excluído porque o OCR leu `[2 3]`, sem hífen; esse aviso não interrompeu a descoberta. Foram **401 testes aprovados**. Evidência: `artifacts/fix-act2-20261019/live-validation.json`. O teste utilizou `config.validation-discovery.json` e saídas separadas, sem alterar o catálogo principal. O processo de validação terminou.

Validação ao vivo de 19/10/2026, concluída às 10:13: corrigidas a opção Averno, a confirmação de Averno selecionado e a referência do botão Ato 1, mantendo o limiar de 0,90 e a rejeição de alvos ambíguos. A tentativa final selecionou **Averno / Ato 1**, reconheceu **nove fases**, fechou o Mundo e confirmou o retorno à batalha, encerrando com **código 0 e sem avisos**. A suíte executada no clone passou com **400 testes**. O teste usou `config.validation-averno.json` e saídas separadas; não coletou batalhas nem substituiu o catálogo principal. Evidência: `artifacts/fix-averno-20261019/live-validation.json`. O teste terminou; para retomar o farm, execute o comando habitual com `config.json`.

Correção de 19/10/2026: a confirmação de Pesadelo aceita preparações `nearest` e `linear` da referência, preservando o limiar de 0,90 e a rejeição de posições ambíguas. A captura da falha de 09:54 passa com confiança 0,938, e as capturas antigas continuam válidas. O clique em Ato 4 usa uma referência do texto do botão (`act4_label.png`), com confiança 0,961 nessa captura; a confirmação de ato selecionado permanece separada. Suíte: **398 testes aprovados**. Validação com capturas reais; a navegação ao vivo ainda não foi repetida após esta correção.

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

## Revisão da suíte em 19/10/2026

Os 401 testes existentes foram revisados; a suíte agora contém 423 testes,
incluindo rotas com capturas reais e integração de OCR, SQLite e Gold/min.
Veja a [revisão e os cenários ainda pendentes](docs/test-review.md) e o
[inventário individual dos testes](docs/test-inventory.csv).

Para validar sem aceitar testes ignorados por falta de dependências:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/check_tests.py
```

O relatório fica em `artifacts/test-review/results.json`. A suíte não interage
com o jogo. Erros de captura do MSS agora permitem adiar a rodada do `loop`,
sem reutilizar uma imagem antiga para decidir cliques.

**Tela desligada:** o funcionamento com o monitor apagado não foi validado.
O bot exige o jogo visível em primeiro plano e uma sessão ativa; minimizar,
bloquear ou suspender não são modos suportados. Desligar apenas o monitor é
diferente de suspender o computador, mas não há garantia de captura nesse estado.

## Confirmação de legendas — 19/10, após erro 2-58

A legenda agora exige consenso entre tratamentos de imagem e confirmação em
dois frames. Durante a busca, uma fase da mesma dificuldade fora do catálogo
confirmado provoca nova leitura com limite de tentativas, sem contabilizar
batalhas nesse intervalo. Os recortes `artifacts/ocr-stage-*.png` e `.json`
preservam os pixels e votos OCR do instante da rejeição.

Veja [como melhorar e validar o OCR](docs/ocr-quality.md). A suíte passou com
432 testes após esta alteração; o inventário anterior registra a revisão dos
423 testes daquele momento. Reinicie o bot para carregar a nova implementação.

### Interface em 100% — calibração de 19/10 à noite

O menu com 52 pixels de largura, a legenda da batalha e as cinco linhas inferiores do histórico foram conferidos na escala de 100%. As referências nativas de Inferno selecionado e Ato 2 selecionado complementam as referências reduzidas, mantendo o limiar de 0,9 e a rejeição de alvos ambíguos. Veja [a calibração e os cenários de regressão](docs/calibration-100-percent.md).

### OCR híbrido com Groq

A integração opcional consulta a Groq quando o OCR local não confirma o histórico ou a legenda. Sem `GROQ_API_KEY`, o bot continua localmente. Veja [como configurar a chave, limitar chamadas e comparar as capturas](docs/groq-ocr.md). O teste remoto depende da configuração da sua chave; os testes automatizados simulam a API e não usam créditos.
