# Guia técnico e histórico de validações

Documentação detalhada do Farmbot para My Party Is Grinding, com os procedimentos de operação, calibração, diagnóstico e os registros de validação do README anterior.

Para instalar e começar a usar o projeto, consulte o [README principal](../README.md). Execute os comandos deste guia a partir da raiz do projeto.

## Contexto dos registros

O conteúdo abaixo preserva as informações do README anterior à reorganização. Os relatos datados descrevem o ambiente e os testes daquele momento; caminhos locais, quantidades de testes e menções ao ambiente já preparado não representam uma instalação nova. As evidências em `data/` e `artifacts/` são arquivos locais e não acompanham o clone.

**Configuração de execução:** o [config.json](../config.json) versionado usa `dry_run: false` e `auto_switch_best: true`. As menções abaixo à troca final desligada por padrão descrevem configurações anteriores. Omitir `--execute` ou `--auto-switch` mantém os valores do JSON. Para uma simulação pela interface, marque **Simulação (sem cliques)** e desmarque **Trocar para a melhor fase**. O caminho de `vision.tesseract_cmd` deve ser ajustado após copiar o projeto.

## Consulta rápida

- [Referência de comandos](#comandos)
- [Conferência da leitura de batalha](#conferir-a-leitura-de-batalha)
- [Medições, repetição e busca](#medições-repetição-e-busca)
- [Arquivos e diagnóstico](#arquivos-e-diagnóstico)

## Registros de validação

Monitoramento de 17/10/2026: a rodada iniciada às 09:47 registrou oito vitórias e 15.588 Gold, mas encerrou às 10:01 durante a coleta de 2-8/Inferno. Uma linha anterior de 2-7 (`1:13.71`, 2.123 Gold e 5.290 EXP) produzia um sinal `+` extra no separador em várias ampliações, impedindo o consenso. Acrescentada uma sétima alternativa em cinza a 4×, mantendo o parser, duas leituras concordantes e o prazo total de 6 s. As três capturas da falha passaram com cinco linhas exatas em 2,57–3,05 s. Uma coleta isolada ao vivo terminou às 10:06 com uma vitória nova em **2-8/Inferno**, sem avisos de OCR: **2.123 Gold em 73,06 s + 5 s de reinício = 1.631,82 Gold/min**. A suíte passou com **396 testes**. Evidências: `artifacts/ocr-20261017-monitor/validation.json` e `artifacts/ocr-20261017-monitor/live-validation.json`. Esse teste curto usa `config.validation-monitor.json` e banco separado; a configuração principal continua com cinco batalhas por fase. A execução original e o teste terminaram, sem um bot deixado em execução.

Validação ao vivo de 17/10/2026, concluída às 09:26: corrigido o OCR que bloqueava o painel quando uma linha antiga de 4-7 perdia o separador decimal. A sexta variante, binária em 2×, recuperou **15/15 linhas** em três painéis reais, mantendo duas leituras concordantes e as ocorrências de linhas idênticas. O teste no jogo coletou **5/5 vitórias em 4-6/Pesadelo**, com cinco IDs distintos e nenhuma interrupção. Uma animação encobriu o histórico em uma captura; a leitura foi descartada e recuperada na tentativa seguinte. O lote somou **8.000 Gold em 207,63 s de batalha + 25 s de reinício = 232,63 s**, equivalente a **2.063,36 Gold/min**. O ajuste de **5 s por batalha concluída** também é usado no histórico e nas decisões, sem alterar as durações originais. O ranking completo preserva a interrupção anterior e, por isso, difere deste lote isolado. Foram **394 testes aprovados**. Evidências: `artifacts/ocr-20261017/validation.json` e `artifacts/ocr-20261017/live-validation.json`.

Validação ao vivo de 16/10/2026, concluída às 17:02: corrigida a legenda de **1-1/Averno**, cujo primeiro `1` era lido como `|` porque a máscara removia as bordas claras do dígito. Após as duas leituras anteriores ficarem incompletas, uma terceira máscara preserva esses pixels neutros. A leitura continua exigindo fase e dificuldade completas e duas capturas concordantes, sem substituir caracteres. Foram reconhecidos **41/41 quadros** (17 de Averno, antes 0/17, e 24 antigos de Inferno). No jogo, três confirmações consecutivas levaram de 1,67 a 1,98 segundos, e a rota **1-1 → 1-2 → 1-1/Averno** terminou sem erro. A suíte passou com **370 testes**. Evidências: `artifacts/label-averno/offline-validation.json` e `artifacts/label-averno/live-validation.json`.

Validação ao vivo de 16/10/2026, concluída às 16:53: a busca reutilizou o histórico para pular oito fases de Inferno abaixo da melhor referência, confirmou a nova mensagem `1-9 falhou` e continuou em **1-8/Averno**. Essa coleta atingiu o limite de 180 segundos sem resultado novo; a rodada limitada a duas novas fases terminou com código 0 e seleção confirmada de **2-1/Inferno**. A saída após o primeiro resultado inferior em 3 Gold/min foi validada nos testes automatizados; nessa rodada ao vivo não houve vitória nova para acionar esse caso. Foram **366 testes aprovados**. Evidência: `artifacts/validation-profitability-20261016/report.json`.

Validação ao vivo de 16/10/2026, concluída às 16:14: a legenda de 2-8/Inferno confundia `8` com `B` na ampliação original. Uma segunda ampliação é tentada quando a leitura está incompleta; duas capturas consecutivas ainda precisam concordar, sem substituir caracteres nem usar a fase esperada. Os 24 quadros da sequência capturada passaram após a correção (antes, 2/24).

O teste de cinco fases usou dados separados e uma batalha por coleta: vitórias em 2-8, 2-7, 2-9 e 2-6/Inferno; derrota real em 1-8/Averno, seguida de recuo do próprio jogo para 1-7. A execução inicial parou nesse recuo. A quinta coleta foi retomada após acrescentar o reconhecimento da mensagem antiga `1-8 falhou`, sem duração, e terminou com retorno confirmado a 2-8/Inferno. Essa mensagem preserva a sequência do histórico e exige consenso OCR; quando nova para a fase coletada, interrompe a medição sem inventar duração. As interrupções anteriores foram preservadas. Evidência: `artifacts/validation-five-20261016/report.json`. A suíte passou com 282 testes. A configuração principal permanece com cinco batalhas por fase.

O reconhecimento de um marcador dourado previamente observado também retenta novas capturas por até `navigation.timeout` quando ele desaparece ou fica ambíguo. A espera não clica nem usa a posição antiga como prova de disponibilidade. Após recuperar o marcador, a seleção ainda exige o tooltip correto na mesma captura do clique. O template `node_gold` usa redução linear com escala exata para preservar o reconhecimento na captura da falha de 15:12 em 16/10/2026, mantendo o limiar de 0,80 e o filtro de cor dourada.

O mapa também pode ser ancorado pelo rodapé exclusivo com os botões “Conselho da IA” e “Varredura de chefe de ato” (`navigation.world_footer` e `world_footer_anchor`). Essa referência é reconhecida na captura atual quando o tooltip encobre o título Mundo. Se nenhuma âncora estiver visível, a leitura dos nós tenta novas capturas por até `navigation.timeout` (10 segundos atualmente), com esperas de `navigation.poll_interval`, sem repetir cliques. O clique de seleção ainda exige nó dourado e tooltip da fase correta na mesma captura. Se a espera esgotar, o comando avulso encerra; no modo `loop`, a rodada é adiada e tentada novamente no próximo intervalo. ESC, Ctrl+C e guardas de foco continuam ativos. Reinicie o processo do bot para carregar alterações de código e configuração.

Validação de navegação em 16/10/2026: após corrigir a redução dos templates da lista de dificuldades e dos atos, `discover --execute` percorreu as três páginas configuradas sem erros. Foram reconhecidas 26 fases disponíveis: nove em Ato 4/Pesadelo, nove em Ato 1/Averno e oito em Ato 2/Inferno. O Mundo foi fechado e a batalha inicial 2-6/Inferno foi confirmada ao final. A suíte passou com 257 testes. Esse teste valida descoberta e retorno; não inclui uma nova execução completa de coleta e seleção da melhor fase.

Aplicação Python para medir o rendimento real de Gold pela interface do jogo e comparar fases e dificuldades. Captura a área cliente da janela, reconhece resultados com OpenCV/Tesseract, registra batalhas em SQLite e exporta um ranking CSV. Usa somente capturas e interações normais com a interface.

**Este workspace já tem dependências, Tesseract e referências visuais reais configurados.** O modo ativo é `navigation.mode: "icon_map"`, com três páginas calibradas: ato 4/Pesadelo, ato 1/Averno e ato 2/Inferno. Foram coletadas cinco batalhas reais em 4-7/Pesadelo e cinco em 4-8/Pesadelo; a seleção de 4-8 pela busca e o retorno a 4-7 pelo comando `select` foram confirmados na janela do jogo. A execução completa de `run` ainda não foi repetida após a correção do reconhecimento usado no retorno. `navigation.calibrated: true` indica que as referências foram preenchidas, não que todas as combinações do jogo foram testadas. A configuração atual usa **`dry_run: false`**, com cliques habilitados, e **`auto_switch_best: true`**, com seleção final da melhor fase medida.

## Interface de controle

Abra `abrir_interface.cmd` com dois cliques, ou execute na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m farmbot gui
```

**Iniciar** valida e salva os ajustes antes de executar o loop. **Parar** solicita o encerramento e aguarda o salvamento dos resultados. **Salvar ajustes** grava as opções no JSON de forma atômica, preservando as referências de calibração; as mudanças serão usadas no próximo início. Para aplicar novos ajustes a um bot ativo, pare, altere as opções e inicie novamente.

| Controle | Efeito |
|---|---|
| Intervalo (minutos) | Tempo entre os horários de início das rodadas; inicialmente 5 minutos |
| Batalhas por fase | Quantidade de resultados novos a coletar em cada fase; inicialmente 5 |
| Fases por rodada | Limite de fases com nova coleta por rodada; inicialmente 1, sem contar cache confiável |
| Limite por batalha (s) | Limite de espera sem um resultado novo de batalha |
| Validade dos resultados (h) | Janela de recência para reutilizar resultados confiáveis |
| Sucesso mínimo (%) | Taxa mínima para considerar uma fase confiável; inicialmente 80% |
| Queda para trocar (Gold/min) | Queda absoluta de rendimento que encerra a coleta antecipadamente; 0 desativa |
| Reinício entre batalhas (s) | Tempo adicional por batalha concluída no cálculo de rendimento; configurado em 5 s |
| Simulação (sem cliques) | Ativa `dry_run`; desmarcada, permite navegar e selecionar fases |
| Trocar para a melhor fase | Seleciona a melhor fase confiável ao fim de uma rodada concluída; desmarcada, retorna à fase inicial |
| Focar o jogo ao iniciar | Solicita foco somente na primeira tentativa; não recupera foco durante o loop |

A aba **Atividade** mostra as mensagens do bot e os adiamentos. A aba **Ranking** exibe o CSV atualizado após a exportação dos resultados; Gold/min e Gold/h são estimativas calculadas a partir das batalhas observadas. Os limites adicionais de coleta (`stage_timeout`) e OCR (`ocr_timeout`, `max_ocr_failures`) continuam disponíveis no arquivo de configuração.

`gui --start` abre a interface e inicia com os ajustes salvos, respeitando a opção de simulação. Na configuração atual, a simulação está desmarcada e a troca final para a melhor fase permanece desligada. **Esc** e **Parar** encerram o bot; fechar a interface também solicita a parada do processo iniciado por ela e aguarda sua saída. A interface não mata esse processo à força.

Clicar em outra aplicação, inclusive na própria interface de controle, pode adiar a rodada em andamento por perda de foco. O loop permanece aberto e tenta novamente no próximo horário, sem trazer o jogo para a frente automaticamente. Os detalhes da cadência e o comando de parada pelo terminal estão abaixo.

## Medições reais conferidas

`observe --hover` coletou cinco vitórias em **4-7/Pesadelo**. Depois, a busca selecionou **4-8/Pesadelo** e coletou mais cinco. A recompensa exibida e confirmada naquele teste foi **1.362 Gold por batalha**. Os valores por minuto e por hora abaixo são projeções históricas calculadas somente com a soma dos tempos dos logs, antes do acréscimo de 5 segundos por reinício; não houve uma medição contínua de uma hora. O ranking atual recalcula os registros com o acréscimo configurado.

| Fase | Dificuldade | Vitórias | Gold/batalha | Tempo médio | Gold/min estimado | Gold/h estimado |
|---|---|---:|---:|---:|---:|---:|
| 4-8 | Pesadelo | 5 | 1.362 | 43,280 s | 1.888,17 | 113.290,20 |
| 4-7 | Pesadelo | 5 | 1.362 | 43,338 s | 1.885,64 | 113.138,59 |

Uma auditoria independente abriu o SQLite em modo somente leitura: os dois lotes contêm dez vitórias com dez IDs de evento distintos e nenhuma derrota ou interrupção. Cada fase somou 6.810 Gold. O lote 4-7 somou 216,69 segundos e 14.825 EXP; o lote 4-8 somou 216,40 segundos e 15.275 EXP. Os dois resultados de 43,45 segundos em 4-7 foram preservados como ocorrências diferentes, com IDs próprios.

Esse é o ranking dessas duas amostras, dentro do catálogo testado. A busca selecionou 4-8 e coletou seu lote. O retorno a 4-7/Pesadelo foi validado pelo comando `select`, com clique no nó, fechamento do Mundo e confirmação da legenda final, após corrigir o reconhecimento do tooltip. A primeira busca havia parado com segurança no retorno; uma execução completa e ininterrupta de `run` ainda não foi repetida com essa correção.

A descoberta completa das duas páginas também terminou com sucesso após a correção, fechando o Mundo e confirmando novamente a fase inicial 4-7/Pesadelo. Foram identificados **16 nós dourados disponíveis**, registrados em `data/catalog.json`:

| Página | Nós identificados | Fases |
|---|---:|---|
| Ato 4 / Pesadelo | 7 | 4-1, 4-3, 4-4, 4-5, 4-6, 4-8, 4-9 |
| Ato 1 / Averno | 9 | 1-1 a 1-9 |

Essas 16 fases foram identificadas no mapa; **naquela validação, somente 4-7 e 4-8/Pesadelo tinham lotes completos**. O loop pode acrescentar medições posteriores, disponíveis no ranking e no banco. Na captura usada para descoberta, o título encobria o nó 4-2 e os personagens encobriam o marcador da fase atual 4-7. O programa não inferiu esses nós ocultos. Durante `run`, a fase inicial visualmente confirmada na batalha é acrescentada ao catálogo mesmo quando seu marcador não aparece no mapa.

## Começar neste workspace

O ambiente `.venv` usa Python 3.14. O Tesseract 5.5.3, build `5.5.3.20260724`, está em `.tools/tesseract/tesseract.exe`, com os idiomas `por` e `eng`. A janela identificada tem título exato `MyPartyIsGrinding`.

```powershell
.\.venv\Scripts\python.exe -m farmbot check-config
.\.venv\Scripts\python.exe -m farmbot --focus observe --hover
.\.venv\Scripts\python.exe -m farmbot rank
```

`observe --hover` mede somente a fase atual e permite mover o ponteiro para revelar sua legenda; não clica nem muda de fase. Deixe o painel Mundo fechado: na interface observada, o rodapé da batalha só aparece com o Mundo fechado e o ponteiro sobre a área de batalha. O observador não fecha esse painel por conta própria. O mapa revela os números das fases em tooltips ao passar o ponteiro sobre os nós.

Para a busca com navegação, use:

```powershell
.\.venv\Scripts\python.exe -m farmbot --focus run --execute
```

Esse comando autoriza as mudanças de fase necessárias aos testes. Ao terminar normalmente, restaura a fase inicial. Para selecionar a melhor fase confiável ao final, acrescente `--auto-switch`; a opção permanece desligada por padrão.

Para uma execução com orçamento menor, sem alterar `config.json`:

```powershell
.\.venv\Scripts\python.exe -m farmbot --focus run --execute --max-stages 2
```

O limite conta fases submetidas a nova coleta, incluindo a fase inicial quando ela precisa ser medida. Resultados confiáveis reutilizados do cache não consomem esse orçamento. O limite não reduz a descoberta das páginas configuradas nem define uma duração máxima para toda a execução.

Para um teste rápido de até cinco fases, coletando um resultado novo por fase:

```powershell
.\.venv\Scripts\python.exe -m farmbot --focus run --execute --max-stages 5 --battles-per-stage 1
```

`--battles-per-stage N` aceita um inteiro positivo e altera a quantidade somente nesta execução de `run`, sem salvar mudanças em `config.json`. O cache continua sendo reutilizado; uma amostra por fase serve para testar a execução, mas oferece menos evidência para comparar rendimentos.

## Repetir a cada cinco minutos

```powershell
.\.venv\Scripts\python.exe -m farmbot --focus loop --execute --interval-minutes 5
```

A primeira rodada começa após a espera inicial de cinco segundos. As seguintes usam horários de início separados por cinco minutos. Cada rodada coleta no máximo uma fase nova, com as cinco batalhas configuradas, reutiliza o cache recente e atualiza o ranking. Use `--max-stages N` para alterar o orçamento por rodada. A troca final para a melhor fase continua opcional, com `--auto-switch`; por padrão, cada rodada retorna à fase em que começou.

Se uma rodada ultrapassar o próximo horário, esse horário é pulado: as rodadas nunca se sobrepõem e não são executadas em sequência para compensar atrasos. Por exemplo, uma rodada iniciada às 14:00 que termine às 14:06 terá a próxima às 14:10.

Perda de foco, jogo ausente e falhas operacionais de OCR ou navegação **adiam a rodada até o próximo horário**. Clicar em outro aplicativo não encerra o loop. A rodada seguinte procura novamente a janela e verifica o estado visual; se o jogo continuar indisponível ou em segundo plano, ela também será adiada. O programa não força a recuperação do foco: volte manualmente ao jogo para permitir uma próxima rodada. `--focus` solicita foco somente na primeira tentativa de execução.

**Esc**, **Ctrl+C**, o botão **Parar** e o comando abaixo encerram o loop, inclusive durante a espera, preservando as medições já salvas:

```powershell
.\.venv\Scripts\python.exe -m farmbot stop
```

`stop` envia uma solicitação cooperativa por `data/farmbot.stop`, consumida pelo controlador de parada; não mata o processo. Uma chamada de OCR em andamento pode precisar terminar ou atingir seu timeout antes da saída. Erros de configuração, o fail-safe de canto da tela e erros inesperados continuam encerrando a execução, sem novas tentativas automáticas. Uma rodada interrompida não tenta cliques de restauração.

O bloqueio em `data/farmbot.lock` impede outro comando de navegação, seleção, observação ou busca neste projeto enquanto o loop estiver ativo. O arquivo permanece após a saída; o bloqueio pertence ao processo e é liberado ao encerrá-lo. Não é necessário apagar esse arquivo. `rank` continua disponível para consultar as medições enquanto o loop roda.

## Instalação no Windows

Requer Windows 10/11 e Python 3.11 ou superior. No PowerShell, dentro desta pasta:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\scripts\setup_ocr.ps1
.\.venv\Scripts\python.exe -m farmbot demo
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Se usar outra versão, ajuste `-3.12`. Não é necessário ativar o ambiente virtual. Os passos de criação e instalação são para uma máquina nova; neste workspace o ambiente já está preparado.

O script [scripts/setup_ocr.ps1](../scripts/setup_ocr.ps1) instala uma versão fixada do **executável Tesseract OCR** e dos dados `por`/`eng`, verificando os downloads por SHA-256. Os arquivos ficam em `.tools/tesseract`, mas o instalador também pode registrar o Tesseract no Windows; não se trata de um pacote puramente portátil. Instalar apenas `pytesseract` pelo pip não instala o executável. Também é possível seguir as [instruções do projeto Tesseract para Windows](https://tesseract-ocr.github.io/tessdoc/Installation.html).

Ao copiar o projeto para outra pasta ou máquina, ajuste `vision.tesseract_cmd` em `config.json` para o caminho exibido pelo script. A configuração deste workspace contém um caminho absoluto local.

```powershell
& '.\.tools\tesseract\tesseract.exe' --version
& '.\.tools\tesseract\tesseract.exe' --list-langs
.\.venv\Scripts\python.exe -m farmbot check-config
```

`check-config` retorna código 2 se faltarem Tesseract, idiomas, calibração ou referências. A validação da navegação ocorre antes dos cliques, incluindo referências usadas em etapas posteriores. Um resultado válido nessa checagem confirma os arquivos e parâmetros; a interação real também precisa passar pelas verificações visuais durante a execução.

## Comandos

Use os comandos na raiz do projeto. As opções globais `--config caminho.json`, `--verbose` e `--focus` devem ficar **antes** do subcomando.

| Comando | Função |
|---|---|
| `gui` | Abre a interface para ajustar opções e iniciar ou parar o loop |
| `gui --start` | Abre a interface e inicia o loop com as opções salvas |
| `stop` | Solicita a parada cooperativa do bot ativo neste projeto |
| `demo` | Simula 15 batalhas, incluindo resultados idênticos; gera `artifacts/demo/ranking.csv` sem acessar o jogo |
| `check-config` | Valida configuração, Tesseract, idiomas e referências |
| `capture --output artifacts/battle.png` | Salva uma captura da área cliente |
| `ocr --image artifacts/battle.png` | Diagnóstico do texto na região proporcional `logs_roi`, com confiança, posição e interpretação de batalhas |
| `ocr --image artifacts/world.png --all-text` | Exibe o texto de toda a imagem para calibrar menus e mapas |
| `locate templates/menu.target.json --image artifacts/battle.png` | Localiza um alvo e salva uma imagem anotada em `artifacts/locate.png` |
| `crop --image arquivo.png --box X Y W H --output templates/alvo.png` | Recorta um template, com coordenadas em pixels da imagem |
| `navigate --flow open_world` | Abre o Mundo conforme `dry_run` da configuração, inclusive no modo `icon_map` |
| `navigate --flow open_world --execute` | Abre o Mundo e confirma o estado visual |
| `navigate --flow return_to_battle --execute` | Fecha o Mundo e confirma a legenda da batalha |
| `discover --execute` | Percorre as páginas configuradas e registra fases positivamente desbloqueadas |
| `discover --page nome --execute` | Testa somente uma página do catálogo |
| `select --stage 4-7 --difficulty Pesadelo` | Seleciona conforme `dry_run` da configuração, sem iniciar coleta |
| `select --stage 4-7 --difficulty Pesadelo --execute` | Seleciona a fase e confirma fase/dificuldade na batalha, sem coletar resultados |
| `observe` | Mede a fase atual sem clicar; a legenda precisa estar visível |
| `observe --hover` | Permite mover o ponteiro para revelar a legenda e medir a fase atual, sem cliques |
| `run` | Executa a busca conforme `dry_run` da configuração |
| `run --execute` | Mede a fase atual, descobre candidatos e testa os mais promissores |
| `run --execute --max-stages 2` | Limita esta execução a duas fases com nova coleta |
| `loop --execute --interval-minutes 5` | Repete a busca a cada cinco minutos, com até uma fase nova por rodada e sem sobreposição |
| `run --execute --auto-switch` | Faz a busca e seleciona a melhor fase confiável ao terminar |
| `rank` | Mostra e exporta as medições recentes já salvas |

Exemplo de teste de uma das páginas calibradas no modo ativo:

```powershell
.\.venv\Scripts\python.exe -m farmbot --focus discover --page ato4_nightmare --execute
```

`discover --page ato1_averno --execute` testa a outra página. Os comandos `navigate --flow ...` também funcionam no modo `icon_map`: usam a âncora do menu e os marcadores reais de Mundo/fechar. Para fechar um mapa aberto antes de iniciar uma observação:

```powershell
.\.venv\Scripts\python.exe -m farmbot --focus navigate --flow return_to_battle --execute
```

Para selecionar diretamente uma fase das páginas calibradas, inclusive restaurar uma fase após um teste interrompido:

```powershell
.\.venv\Scripts\python.exe -m farmbot --focus select --stage 4-7 --difficulty Pesadelo --execute
```

`select` usa as mesmas verificações visuais de disponibilidade, tooltip e fase final da busca. Ele não coleta batalhas nem altera os resultados salvos. Sem `--execute`, respeita `dry_run` da configuração; no arquivo atual, `false` permite cliques. Para um diagnóstico sem cliques, salve `dry_run: true` e omita `--execute`. O nome da dificuldade deve coincidir com o exibido no jogo e com uma página configurada.

Os comandos que acessam a janela aguardam cinco segundos para você colocar o jogo em primeiro plano. Ajuste `safety.start_delay` se necessário. Com `--focus`, o programa solicita foco para a janela identificada **uma vez, no início**. Ele não recupera foco à força durante a execução: se outra janela assumir o foco, as proteções de entrada continuam impedindo interações. **ESC interrompe**, inclusive durante esperas; Ctrl+C também funciona. PyAutoGUI mantém seu fail-safe de canto da tela habilitado. Chamadas de OCR em andamento têm timeout; nenhum novo clique é realizado depois que a parada é detectada.

`--execute` habilita os cliques naquela execução mesmo quando `dry_run: true` estiver salvo. O arquivo atual já usa `dry_run: false`; omitir `--execute` não desativa os cliques. `--auto-switch` não habilita cliques por si só. A busca ativa necessariamente muda de fase para medir os candidatos; por padrão, **restaura a fase inicial ao terminar normalmente**, pois a troca final para a melhor fase vem desativada. Em erro ou parada de emergência, não tenta novos cliques de restauração.

## Calibração ativa: mapa de ícones e regiões ancoradas

A janela observada tem uma grande área cliente transparente, de 2560 × 2560 pixels, enquanto o painel visível do jogo ocupa apenas parte dela. Por isso, as posições da imagem original de 1068 × 420 não podem ser reutilizadas diretamente na captura atual. O programa localiza `templates/menu.png` em uma captura nova e calcula as regiões a partir desse marcador, dentro da área cliente, sem coordenadas fixas do desktop.

Os controles do mapa acompanham o cabeçalho Mundo reconhecido na captura atual, usando `world_header_anchor`. A aba e o cabeçalho são procurados dentro de `panel_search_offset`, permitindo painéis acima ou abaixo da batalha. Os deslocamentos são escalados pela largura do menu, com referência de 52 pixels. Sem cabeçalho único, não há seleção de nós. Cada template tem suas próprias escalas de busca.

`measurement.logs_anchor` usa a mesma referência para encontrar as cinco linhas do log. Se a âncora estiver configurada, mas não for encontrada, ou a região calculada sair da imagem, a coleta rejeita a leitura; não troca silenciosamente para `logs_roi`. Essa região proporcional continua disponível para o diagnóstico `ocr` e para configurações sem âncora.

Os ajustes atuais de OCR são `ocr_scale: 2`, `min_confidence: 0`, `green_logs: false`, `logs_linewise: true`, `logs_line_psm: 7` e `language: "por+eng"`. O processamento por linha separa as faixas de texto antes do OCR e preserva linhas ilegíveis como falhas de leitura, sem removê-las da sequência. Nas linhas predominantemente verdes, retira os pixels do ícone amarelo de moeda e preserva os tons do texto; notificações e derrotas mantêm suas outras cores em escala de cinza. O timeout de OCR limita a leitura do conjunto de linhas.

Cada linha de batalha precisa de duas leituras concordantes de fase, duração, Gold, EXP e resultado, usando preparações diferentes da imagem. Fontes maiores usam até três escalas em cinza. Para caracteres com até 12 pixels de altura, há até sete tentativas: a escala configurada, 3× em cinza, 3× e 4× com contraste binário aplicado depois da ampliação, 6× em cinza e, se necessário, 2× com contraste binário e 4× em cinza. A última alternativa recupera também separadores que algumas ampliações confundem com um sinal `+`, sem relaxar o parser. O processamento para assim que duas leituras concordam e respeita o mesmo prazo total de OCR. Isso preserva decimais pequenos mesmo quando a mesma batalha muda de posição no histórico e seus pixels mudam. As capturas que falharam às 12:24 e nos testes seguintes de 16/10/2026, além dos painéis de 17/10/2026, fazem parte dos testes de regressão.

Quando a leitura do log falha, o aviso identifica as linhas rejeitadas ou a falta da âncora/região. A primeira falha de log de cada coleta salva seu próprio recorte e diagnóstico JSON em `output.artifacts`, sem uma nova captura. Avisos repetidos são limitados à primeira tentativa, a cada cinco e à última; a recuperação também é registrada. A falta de consenso continua bloqueando o painel inteiro, preservando a sequência das batalhas.

`vision.logs_ocr_timeout`, atualmente 6 segundos, limita o conjunto inteiro de tentativas do painel; `ocr_timeout` continua valendo para as demais leituras. Nenhum dígito é inserido e nenhuma recompensa é presumida. Sem consenso, a linha continua inválida. O parser reconhece o ruído `~-` observado no separador entre duração e recompensa, preservando a rejeição de sinais negativos anexados aos números. Esses controles reduzem erros, mas não garantem que duas leituras nunca concordem num valor incorreto.

Um cache limitado a 128 recortes reaproveita somente texto já reconhecido quando os pixels e a configuração são idênticos. Ele não guarda eventos: linhas repetidas continuam ocupando posições separadas, e o coletor ainda exige duas capturas consecutivas concordantes. Pixels alterados e leituras malsucedidas são reconhecidos novamente. Os testes com capturas reais cobrem o decimal perdido de 4-3, as linhas iguais, notificações de loot e a fonte menor de 2-6, com durações acima de um minuto.

As referências visuais deste workspace aceitam escalas 0,75 e 1, mantendo os limiares de confiança. Na captura atual, a âncora do menu tinha 39 pixels em vez dos 52 originais; o recorte proporcional dos logs foi conferido com as cinco linhas de 2-6. Essa conferência valida a leitura do painel, não uma busca completa pelas páginas do mapa nessa escala.

Para acompanhar um teste pelo terminal, execute `powershell -NoExit -ExecutionPolicy Bypass -File .\scripts\testar_ocr_ao_vivo.ps1`. Ele testa primeiro as capturas de regressão e depois executa `--focus observe --hover`, com diagnóstico detalhado das linhas sem consenso, coletando a quantidade de batalhas configurada na fase atual, sem trocar de fase. Deixe o jogo em primeiro plano; ESC ou Ctrl+C interrompem. As medições reais entram no mesmo banco e ranking do bot.

**Validação ao vivo em 16/10/2026:** o teste acompanhado no terminal concluiu cinco vitórias em 2-6/Inferno, com tempos de 69,65; 67,88; 70,26; 70,00 e 68,19 segundos. Cada vitória exibiu 1.849 Gold e 5.050 EXP, totalizando 9.245 Gold e 25.250 EXP. Uma leitura isolada foi rejeitada, e a coleta se recuperou sem encerrar. A auditoria do SQLite confirmou cinco IDs de evento diferentes e nenhuma interrupção nesse lote; os testes anteriores que falharam permanecem no histórico. Evidência: `artifacts/ocr-debug/live-validation-2026-10-16.json`. A suíte completa passou com 251 testes. Isso valida esse lote observado, não todas as fontes, recompensas ou telas possíveis.

O limite de confiança zero evita apagar palavras e números antes de verificar a linha completa; não dispensa o parser estrito, a confirmação em capturas consecutivas ou as verificações de fase. Nenhum dígito é corrigido para coincidir com uma fase esperada. Para o rodapé, o `MapNavigator` usa um leitor separado com `white_text: true`, isolando texto branco sobre o cenário animado, e só aceita a fase/dificuldade após duas capturas concordantes.

O usuário confirmou `log_order: "oldest_first"`: **as novas linhas entram embaixo**. A frequência de observação é de 0,5 segundo, além do tempo gasto nas capturas, nas confirmações do rodapé e no OCR.

As referências em `templates/` foram recortadas das telas reais fornecidas durante a calibração:

| Arquivos | Uso na configuração atual |
|---|---|
| `menu.png`, `world_tab.png`, `world_ready.png`, `close_world.png` | Localizar o menu, entrar no Mundo e confirmar abertura/retorno |
| `difficulty_arrow.png`, `normal_option.png` | Abrir e reconhecer o seletor de dificuldade |
| `nightmare_option.png`, `nightmare_selected.png`, `act4_selected.png` | Selecionar e confirmar ato 4/Pesadelo |
| `averno_option.png`, `averno_selected.png`, `act1_selected.png` | Selecionar e confirmar ato 1/Averno |
| `node_gold.png` | Reconhecer positivamente os nós dourados disponíveis |
| `locked_marker.png` | Referência observada do cadeado preto; o modo ativo seleciona somente nós dourados |

No mapa observado, os nós dourados representam fases disponíveis, e cadeados pretos indicam bloqueio. O `MapNavigator` exige o template dourado, confirmação de cor e um número de fase estável no tooltip. Ele não deduz o número pela ordem dos ícones. Chefes vermelhos e outros tipos de marcador ficam fora dos candidatos nesta implementação. A ausência de cadeado, por si só, nunca autoriza uma fase.

`ato4_nightmare`, `ato1_averno` e `ato2_inferno` estão cadastradas; a descoberta ao vivo confirmou 16 nós dourados nessas duas páginas, com o escopo detalhado acima. Ter templates de outros atos ou os cinco nomes em `difficulty_names` não significa que todas as rotas estejam calibradas. Para ampliar o catálogo, capture as páginas adicionais, configure `enter` e uma lista `ready` que confirme simultaneamente ato e dificuldade, e teste cada página com `discover --page nome --execute`.

## Conferir a leitura de batalha

1. Deixe o jogo visível, sem outras janelas por cima, e capture a área cliente:

   ```powershell
   .\.venv\Scripts\python.exe -m farmbot --focus capture --output artifacts/battle.png
   ```

2. As regiões `roi` são `[x, y, largura, altura]` **proporcionais à área cliente**, de 0 a 1. Os campos `offset` do modo ativo usam pixels da referência do menu, conforme explicado acima. O cabeçalho e as bordas do Windows não fazem parte da captura. Para a legenda atual, ajuste `navigation.current_stage_offset` e `battle_hover_offset`; não inclua o número do portal da fase seguinte. `current_stage_roi` pertence ao modo genérico descrito adiante.

3. Teste o reconhecimento:

   ```powershell
   .\.venv\Scripts\python.exe -m farmbot ocr --image artifacts/battle.png
   .\.venv\Scripts\python.exe -m farmbot locate templates/menu.target.json --image artifacts/battle.png
   ```

   O exemplo `4-8 concluída · 0:43.37 · +1,362 · +3,055 EXP` resulta em 43,37 segundos, 1362 Gold e 3055 EXP. A linha real `4-7 concluida - 0:43.10 - € +1,362 - +2.965 EXP` também é aceita, sem corrigir dígitos. Cada linha recebe uma posição própria no diagnóstico, mesmo que o texto seja idêntico ao de outra. No comando `ocr`, notificações de itens podem aparecer com `parsed: null`; durante a coleta, as notificações conhecidas são tratadas como marcadores da sequência.

4. Para outra fonte ou escala, ajuste `vision.ocr_scale` entre 1 e 6 e confira o resultado completo de cada linha. Com `logs_linewise: true`, `logs_line_psm: 7` controla o OCR de cada faixa; `logs_psm: 6` é a alternativa para leitura do bloco quando o modo por linha estiver desativado. A calibração atual usa escala 2 e preserva notificações de outras cores no painel. O filtro branco do rodapé é separado desse processamento. Veja a [documentação de qualidade e segmentação do Tesseract](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html).

5. Mantenha `measurement.log_order: "oldest_first"` para a interface já observada. Se outra versão do jogo colocar os resultados novos em cima, confirme o movimento e use `newest_first` nessa configuração.

O detector genérico `kind: menu` usa cor, formato e três barras horizontais. O modo ativo já usa o template real `templates/menu.png`. Para obter outra referência, capture seu próprio tamanho de janela e recorte o ícone:

```powershell
# Exemplo de recorte apenas para a referência de 1068 x 420; ajuste à sua captura.
.\.venv\Scripts\python.exe -m farmbot crop --image artifacts/battle.png --box 989 357 52 52 --output templates/menu.png
```

O recorte acima só corresponde à imagem original de 1068 × 420; não execute essas coordenadas na captura transparente atual. No `icon_map`, atualize `navigation.menu`, `measurement.logs_anchor.target` e as larguras de referência se necessário. A posição de clique sempre vem de uma captura atual. Ao mudar muito a proporção, zoom ou layout, recalibre regiões e referências.

## Modo genérico de rotas: configuração alternativa

Esta seção descreve `farmbot/navigation.py`, usado quando `navigation.mode` não é `icon_map`. Os campos `open_world`, `return_to_battle`, `stages_roi`, `card_padding`, `select_expect` e `select_confirm` abaixo **não são o formato do mapa de ícones ativo**. Use outra configuração JSON para uma interface que exiba IDs diretamente nos cartões e exija essas rotas. Para o jogo já calibrado neste workspace, use as instruções de `icon_map` acima.

Abra manualmente o menu e o Mundo para capturar as telas necessárias. `capture` também funciona quando a janela do jogo está mostrando o mapa. Use `ocr --all-text` para descobrir quais rótulos são reconhecidos. Use `crop` para os ícones e marcadores que o OCR não identifica com segurança.

Para montar uma configuração genérica, estas são referências possíveis. Algumas já existem no workspace, outras só devem ser criadas se essa tela realmente existir; a tabela não lista os requisitos do `config.json` ativo:

| Referência | O que deve mostrar |
|---|---|
| `world_ready.png` | Marcador exclusivo da tela Mundo |
| `close_world.png` | Botão que retorna à batalha |
| `battle_ready.png` | Marcador estável da tela de batalha |
| `act4.png` / `act4_ready.png` | Seletor do ato e evidência de que ele foi aberto |
| `act4_nightmare_ready.png` | Evidência específica de ato 4 **e** dificuldade Pesadelo selecionados |
| `unlocked_marker.png` | Indicador positivo exclusivo de um cartão disponível |
| `locked_marker.png` | Indicador de bloqueio; sua presença sempre veta a seleção |
| `stage_details.png` | Tela/estado esperado imediatamente após selecionar uma fase |
| `start_battle.png` | Botão de confirmação, se existir nessa interface |

Use recortes pequenos, com detalhes distintivos, sem animações. Evite fundos uniformes ou elementos repetidos que confundam o detector. Um template de prontidão que apareça em todos os atos/dificuldades não confirma a página selecionada.

Cada passo de `open_world`, `return_to_battle`, `enter` e `select_confirm` contém:

```json
{
  "click": {"text": "^Mundo$", "threshold": 0.85},
  "expect": {"template": "templates/world_ready.png", "threshold": 0.90}
}
```

`click` precisa ser localizado na captura atual. Após o clique, `expect` precisa aparecer antes do próximo passo. O bot repete tentativas de **reconhecimento**, com limite; não repete cegamente um clique se a tela esperada não aparecer. Os alvos aceitam exatamente uma modalidade: `text` (expressão regular), `template` (PNG) ou `kind: menu`, além de `roi`, `threshold` e, para templates, `scales`.

Adicione uma entrada em `navigation.pages` para cada combinação de ato, dificuldade e página de mapa que deseja explorar. Não é necessário cadastrar cada fase: os IDs são lidos pelo OCR dentro de `stages_roi`. A rota `enter` parte da tela Mundo e deve alcançar aquela página; páginas seguintes podem incluir o botão de avançar com confirmação visual. `available` é um alvo opcional que prova a disponibilidade da página antes de entrar; se não aparecer, a página é pulada.

Para cada ID de fase, `card_padding` define as margens `[esquerda, cima, direita, baixo]` ao redor do texto, proporcionais à janela inteira. Ajuste essas margens para incluir **somente o marcador daquele cartão**. Regiões que incluam o centro de outro ID são rejeitadas. Se existir um `roi` no alvo `unlocked` ou `locked`, ele também é relativo à janela inteira e será intersectado com a região do cartão.

**Ausência de cadeado não comprova desbloqueio.** Se a UI não tiver um ícone dedicado, use outro sinal positivo real, como um botão de entrada habilitado e visualmente distinto dentro do cartão. Não configure um marcador genérico presente também em cartões bloqueados. Antes de cada seleção, a disponibilidade é verificada novamente.

`select_expect` é obrigatório para confirmar a mudança após selecionar um cartão. Se a fase abrir diretamente, use um marcador da batalha e deixe `select_confirm: []`. Se aparecer uma tela de detalhes, configure os passos de confirmação. A seleção só termina quando a legenda da batalha confirmar **exatamente a fase e a dificuldade pedidas**.

Depois de preencher as referências, marque `navigation.calibrated: true`. Teste primeiro os alvos offline, depois as rotas e `discover --page ... --execute`. `discover` exige as referências do mapa e retorno; `run` exige também todas as referências de seleção. Cadastre em `difficulty_names` os nomes que o jogo realmente exibe; os nomes no exemplo não afirmam quais dificuldades existem na sua versão.

## Medições, repetição e busca

Por padrão, são coletados cinco resultados novos confirmados por fase/dificuldade. Ajuste `measurement.battles_per_stage`. O observador exige duas leituras consecutivas concordantes antes de aceitar uma mudança nos logs. Resultados já presentes ao iniciar a observação formam a referência inicial; não entram como novas batalhas, pois podem pertencer a uma dificuldade anterior.

**Cada linha é uma ocorrência própria, mesmo quando o texto se repete.** Duas linhas de resultado idênticas representam duas batalhas distintas. A detecção de repetição compara a sequência e a posição entre capturas, preservando todas as ocorrências dentro de uma imagem. A persistência usa IDs de evento, nunca um hash do conteúdo como identidade da batalha. Se todo o painel contiver linhas idênticas e uma nova linha idêntica substituir outra sem qualquer mudança visível, uma captura estática não consegue revelar essa substituição. Nesse caso, o bot aguarda evidência observável; não inventa resultados pelo relógio. O log de diagnóstico registra essa limitação. Aumentar a frequência ajuda quando há movimento observável, mas não resolve pixels completamente iguais.

O painel de cinco linhas mistura batalhas e notificações, como `Caixa obtida`, `Caixa de Chefe obtida`, `Caixa de Chefe aberta` e `Você obteve Escudo do Guardião x1`. Essas mensagens permanecem na sequência como âncoras: não são descartadas nem contabilizadas como batalhas, Gold ou EXP, e não reiniciam o timeout de batalha. As variantes de OCR observadas `Calxa`/`Chafe` são reconhecidas apenas nos prefixos de caixa. Linhas desconhecidas ou resultados incompletos ainda invalidam a leitura inteira. Opcionalmente, `measurement.notification_patterns` pode acrescentar expressões regulares de mensagens conhecidas; elas precisam corresponder ao texto inteiro normalizado em minúsculas, sem acentos e com espaços simples. Uma regra adicional não transforma resultado de batalha incompleto em notificação.

O cálculo é:

```text
Tempo total = soma dos tempos dos logs + 5 s × batalhas concluídas + tempo das interrupções
Gold/segundo = Gold observado / Tempo total
Gold/minuto  = Gold/segundo × 60
Gold/hora    = Gold/minuto × 60
```

Derrotas com duração explícita são registradas como derrotas. Sem um resultado visual válido, o bot nunca presume vitória ou derrota: registra uma interrupção ao atingir timeout ou ao parar a coleta. Interrupções têm recompensa zero e seu tempo decorrido entra no denominador. Os tempos das batalhas concluídas vêm dos logs; o custo de navegar entre mapas não integra a estimativa de farm contínuo.

`measurement.battle_restart_seconds`, configurado em **5**, acrescenta o reinício da fase a cada vitória ou derrota com duração registrada. Esse tempo entra em `total_seconds`, `mean_seconds`, Gold/s, Gold/min e Gold/h, inclusive ao recalcular o histórico e decidir a troca por rendimento. Os registros originais do OCR permanecem intactos. Interrupções já usam o tempo decorrido e não recebem esse acréscimo. A opção aceita 0 para desativar; configurações antigas sem o campo mantêm 0. Por exemplo, uma vitória de 1.600 Gold em 41,42 s passa a usar 46,42 s no rendimento. Isso não acrescenta uma espera ao bot: contabiliza o reinício que o jogo já executa.

`battle_timeout` limita a espera sem um resultado novo, `stage_timeout` limita a coleta inteira e `max_ocr_failures` limita falhas consecutivas do OCR. Um painel vazio pode aguardar a primeira batalha até o timeout. `max_log_gap` limita lacunas entre observações antes de reconstruir uma referência sem contabilizar linhas antigas.

O ranking inclui vitórias, derrotas, interrupções, recompensas totais, EXP, médias, Gold/s, Gold/min e Gold/h. Uma fase é confiável se tiver o mínimo de resultados completos e `min_win_rate` de sucesso (80% por padrão); interrupções também reduzem essa taxa. O orçamento `search.max_stages` limita as novas medições, incluindo a fase inicial quando medida. `run --max-stages N` aceita um inteiro positivo e substitui esse limite apenas na execução atual, sem salvar mudanças no arquivo. Se a fase inicial já tiver cache confiável, `--max-stages 1` pode medir outra fase. A busca prioriza vizinhas, acompanha as regiões com melhor rendimento e explora outra dificuldade periodicamente. O resultado é a **melhor fase medida dentro do catálogo e orçamento**, sem garantia de ótimo global.

Resultados confiáveis das últimas `search.cache_hours` horas são reutilizados (padrão 6). Ao mudar personagens, equipamentos, bônus ou progressão relevante, use outro caminho em `output.database` para não misturar configurações de equipe. `rank` também respeita essa janela de recência.

`search.early_stop_gold_per_minute: 3` ativa a troca antecipada quando o rendimento médio recente da fase fica **3 Gold/min ou mais abaixo** da melhor fase confiável conhecida. São 3 Gold/min absolutos, não 3%. A decisão usa o histórico salvo dentro de `search.cache_hours` e os resultados novos; após a primeira batalha confirmada já pode encerrar a coleta e seguir para outra fase. Uma fase que já apresenta essa queda no histórico recente pode ser pulada antes de iniciar outra coleta. As batalhas coletadas continuam salvas; a troca antecipada não é uma falha de OCR nem uma derrota. No JSON, omitir a opção ou usar `null` desativa a regra; na interface, use 0. Reinicie o bot para aplicar os ajustes.

Na busca (`run` ou `loop`), uma nova mensagem `falhou`, confirmada em duas capturas e como avanço do log, faz o bot selecionar a fase acessível imediatamente anterior na mesma dificuldade. Se o jogo já recuou sozinho, a fase menor é confirmada antes de continuar. Mensagens antigas no painel não provocam recuo. Sem uma fase inferior confirmada, a busca para. Uma derrota sem tempo exibido preserva apenas o tempo observado como interrupção, sem inventar uma duração de batalha. `observe` continua sem mudar de fase.

Derrotas confirmadas também são reutilizadas pelo histórico: a busca evita repetir essas fases dentro de `search.cache_hours`, até que expirem ou haja uma vitória posterior registrada. Erros de OCR, foco e outras interrupções não são tratados como derrotas. Se uma nova rodada começar numa fase com essa derrota registrada, o bot seleciona a alternativa inferior confirmada.

Timeouts transitórios de aquisição do hover são retentados com nova captura e novo reconhecimento do alvo, dentro do prazo da navegação. Erros de foco, janela, geometria, captura vencida e ESC continuam interrompendo; a retentativa não repete cliques.

Se o jogo exige ativar repetição, faça isso manualmente antes de `observe`, ou calibre o bloco opcional abaixo para a busca ativa:

```json
"repeat": {
  "active": {"template": "templates/repeat_on.png"},
  "enable": {
    "click": {"template": "templates/repeat_off.png"},
    "expect": {"template": "templates/repeat_on.png"}
  }
}
```

Insira esse bloco dentro de `navigation`. O alvo `repeat_off` deve comprovar o estado desligado; apenas não encontrar `repeat_on` não é suficiente. Se `repeat` estiver configurado, a busca verifica/ativa repetição antes das medições. Ao selecionar a melhor fase, confirma o estado ativo. Sem esse bloco, o jogo precisa repetir as batalhas por conta própria. O bot pode encerrar depois da seleção; o farm contínuo é executado pelo próprio jogo.

## Arquivos e diagnóstico

```text
farmbot/
  cli.py          comandos de execução e calibração
  gui.py          interface de ajustes, início, parada e acompanhamento
  config.py       carregamento e validação
  models.py       fases, resultados e estatísticas
  desktop.py      janela Windows, captura MSS e cliques protegidos
  safety.py       parada por ESC/sinal de arquivo e esperas interrompíveis
  instance.py     bloqueio de execução simultânea neste projeto
  vision.py       OCR, templates e detector de menu
  navigation.py   rotas, desbloqueios e confirmações visuais
  map_navigation.py mapa real de ícones, âncora de menu e tooltips
  results.py      interpretação e rastreamento das ocorrências
  storage.py      SQLite, estatísticas e exportação CSV
  search.py       escolha dos próximos candidatos
  runner.py       coleta e coordenação da busca
  continuous.py   nova preparação visual e adiamento de cada rodada
  loop.py         horários regulares sem sobreposição de rodadas
tests/            testes unitários e de integração simulada
templates/        recortes reais das telas e alvos de diagnóstico
scripts/setup_ocr.ps1 instalação verificada do Tesseract e idiomas
config.json       parâmetros atuais e três páginas do mapa calibradas
requirements.txt  bibliotecas Python
pyproject.toml    metadados e comando opcional farmbot
data/             SQLite, ranking, catálogo e log (gerados)
artifacts/        screenshots, diagnóstico e simulação (gerados)
```

As batalhas são persistidas individualmente. O CSV usa UTF-8 com BOM para facilitar a abertura no Excel; se necessário, importe com separador vírgula. `data/catalog.json` contém as fases cuja disponibilidade foi confirmada na última descoberta.

Use `--verbose` antes do comando para ver o rastreamento dos logs. Erros tentam salvar uma captura; se a janela perdeu o foco ou foi fechada, o bot pode salvar a última captura válida com sufixo `last-successful-frame`. Esse arquivo é anterior ao erro. Se nenhuma captura foi obtida, o log explica por que não há screenshot.

Antes de interagir, são verificados HWND, processo, foco, geometria, idade da captura, limites da área cliente e a janela que ocupa o ponto do clique. Não há leitura da memória do jogo, alteração do cliente ou interceptação de rede. Mantenha a janela visível durante toda a execução.

A suíte valida parser, multiplicidade de linhas, logs mistos, regiões ancoradas, perdas/interrupções, persistência, busca, guardas de clique/hover, navegação simulada, reconhecimento OpenCV em imagens sintéticas, horários do loop e adiamento de rodadas. Tesseract e referências já foram conferidos com screenshots reais, incluindo o painel misto de batalhas e itens; os dois lotes ao vivo de cinco batalhas em 4-7/Pesadelo e 4-8/Pesadelo também foram auditados no SQLite. A seleção de 4-8 pela busca, o retorno a 4-7 via `select` e a descoberta das páginas com fechamento do Mundo foram confirmados ao vivo. A rodada limitada de 16:53 também confirmou recuperação após derrota e seleção final da melhor fase histórica. Os testes simulados e o comando `demo` não são um benchmark real do jogo.

Referências técnicas: [captura MSS com OpenCV/NumPy](https://python-mss.readthedocs.io/stable/examples.html), [fail-safe do PyAutoGUI](https://pyautogui.readthedocs.io/en/latest/index.html), [documentação do Tesseract](https://tesseract-ocr.github.io/tessdoc/).

Em 16/10/2026, o teste ao vivo da correção abriu e fechou o Mundo, reconheceu oito fases disponíveis em Ato 2/Inferno e retornou à batalha em 2-6/Inferno. A suíte passou com 255 testes, incluindo capturas reais de painéis acima e abaixo da batalha. Uma busca completa após esta alteração ainda não foi validada.
