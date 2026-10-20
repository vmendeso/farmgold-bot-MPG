# Revisão dos testes — 19/10/2026

Foram lidos os 401 métodos de teste existentes em 19 arquivos, incluindo os
asserts, dados de entrada e simulações usados por cada um. A execução inicial
passou sem testes ignorados. O inventário individual está em
[test-inventory.csv](test-inventory.csv): uma linha por método, com decisão e
limite da evidência. A coluna de limite se aplica ao grupo do teste; não é uma
declaração de que o cenário foi validado no jogo.

## O que explicava a falsa sensação de estabilidade

- Os testes de navegação com `VisionFake` são úteis para regras de controle,
  cancelamento e tentativas, mas devolvem os botões pelo nome. Eles não detectam
  uma imagem de referência que deixou de corresponder aos pixels do jogo.
- As capturas reais eram testadas principalmente uma por vez. Faltava executar
  a rota que abre o Mundo, abre a lista, confirma a dificuldade e muda o ato,
  passando imagens reais pelo reconhecimento em cada etapa.
- A navegação simulada usava prazos reais de 30 ms com esperas que não avançavam
  um relógio simulado. A carga da máquina podia determinar o resultado.
- Algumas regressões exigiam que a configuração antiga ou um leitor sem o
  último fallback continuasse falhando. Uma melhoria legítima do OCR/OpenCV
  poderia quebrar esses testes sem prejudicar o bot.
- O teste de retorno à fase inicial sem vencedor confiável simulava derrotas
  sem lançar `StageDefeated`, contrariando o comportamento do coletor na busca.
  Agora usa coleta inconclusiva com interrupções; as derrotas continuam cobertas
  pelos testes específicos de recuo.
- A execução comum do `unittest` aceita `OK (skipped=...)`. A ausência do
  Tesseract podia retirar justamente os testes de OCR real da validação.

## Alterações feitas

Os 401 testes foram mantidos. Os testes de navegação simulada agora usam relógio
virtual; foram retiradas as exigências de que algoritmos antigos falhem. As
verificações positivas, negativas e de ambiguidade permanecem. A busca de páginas
nas regressões do painel usa o nome da página em vez da posição na lista.

Foram acrescentados 22 métodos, totalizando 423:

| Arquivo | Antes → depois | Avaliação e ação |
| --- | ---: | --- |
| `test_cli.py` | 18 → 18 | Manter: argumentos, despacho, modo simulado e preservação do arquivo. Não prova reconhecimento visual. |
| `test_config.py` | 5 → 9 | Ampliar: contagens fracionárias/booleanas, tempos inválidos, região fora da imagem, ordem do histórico e atraso inicial. |
| `test_continuous.py` | 16 → 17 | Manter e integrar falha real da camada de captura ao adiamento/reabertura da rodada. |
| `test_desktop.py` | 23 → 30 | Ampliar: foco/geometria/processo durante captura, cópia BGR, imagem antiga, duração da captura e erro MSS. |
| `test_gui.py` | 19 → 19 | Manter: ajustes, gravação atômica, comando e ciclo de vida do processo. Falta ensaio da janela Tk em diferentes DPI. |
| `test_instance.py` | 7 → 7 | Manter: inclui processos reais concorrentes e liberação após saída abrupta. |
| `test_log_ocr.py` | 21 → 22 | Manter e integrar pixels reais → OCR → rastreador → coletor → SQLite → Gold/min com reinício de 5 s. |
| `test_loop.py` | 15 → 15 | Manter: cadência, rodadas lentas, adiamento, cancelamento e ausência de sobreposição. |
| `test_map_navigation.py` | 67 → 67 | Corrigir relógio dos testes simulados e retirar exigência de falha do OCR antigo. Mantém regras de ouro, tooltip, foco e repetição limitada. |
| `test_navigation.py` | 22 → 22 | Manter: modo genérico ainda é suportado. Não confundir com validação do mapa de ícones. |
| `test_navigation_replay.py` | 0 → 9 | Novo: rotas com OpenCV real, capturas observadas, atrasos, deslocamento da imagem, estados errados, cancelamento e tela vazia. |
| `test_panel_position.py` | 12 → 12 | Manter capturas reais e negativos; retirar exigências de falha antiga e dependência da ordem das páginas. |
| `test_profitability.py` | 18 → 18 | Manter: limite absoluto de 3 Gold/min, histórico recente e acessível, média ponderada e reinício de 5 s nos dois lados. |
| `test_recovery.py` | 15 → 15 | Manter: recuo só para fase inferior confirmada da mesma dificuldade; validade temporal da derrota. |
| `test_results.py` | 33 → 33 | Manter: formatos, números inválidos, notificações, linhas iguais e ambiguidades de sequência. |
| `test_runner.py` | 68 → 68 | Corrigir o cenário de ausência de vencedor para não simular uma derrota impossível; manter coleta/retirada/diagnóstico. |
| `test_search.py` | 12 → 12 | Manter: catálogo, orçamento, exploração e exclusões pelo histórico. |
| `test_stop_file.py` | 2 → 2 | Manter: parada anterior ao início e sinal persistente durante a sessão. |
| `test_storage.py` | 12 → 12 | Manter: identidade de eventos, SQLite, horário UTC e cálculo ponderado. |
| `test_vision.py` | 16 → 16 | Manter: imagens sintéticas, capturas reais, ambiguidade, ROI, máscaras e alternativas de interpolação. |

O novo teste do MSS falhou antes da correção: `ScreenShotError` escapava da
camada de captura sem ser classificado como `WindowError`. Agora o `loop` pode
adiar a rodada e criar uma nova sessão de captura na próxima. A imagem anterior
fica disponível apenas para diagnóstico. Exceções inesperadas continuam fatais;
as verificações de foco, janela, processo e idade da imagem não foram relaxadas.

O teste integrado de OCR usa duas capturas de 4-6 com deslocamento do histórico.
Uma duração de 41,45 s aparece nas duas imagens. Ele verifica que apenas a nova
ocorrência entra no banco, com 1.600 Gold, e que o denominador do rendimento é
46,45 s. Os testes anteriores continuam verificando duas linhas novas idênticas
como dois eventos distintos.

## Como validar antes de executar no jogo

No diretório do projeto:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/check_tests.py
```

O comando executa a suíte completa, mostra os nomes dos testes e grava
`artifacts/test-review/results.json`. Sai com código 1 se houver falhas, erros,
testes ignorados, falhas esperadas ou nenhum teste descoberto. Instale as
dependências e os idiomas `por`/`eng` do Tesseract; `TESSERACT_CMD` pode indicar
o executável. Os testes não clicam no jogo nem precisam que ele esteja aberto.

O comando habitual continua disponível para investigação, mas um resultado com
`skipped` não é validação completa:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
```

## Limites e próximos cenários

1. **Cobertura visual ainda limitada ao material capturado.** O replay positivo
   completo cobre Inventário → Mundo/Inferno/Ato 2 → Pesadelo/Ato 4. A seleção
   de Averno é coberta até a confirmação da dificuldade; o caso de Ato 1 testa
   clique sem efeito e parada. Faltam capturas consecutivas da transição bem
   sucedida para Ato 1 e do retorno de Pesadelo/Ato 4 a Inferno/Ato 2. Não se deve
   reutilizar uma lista aberta com Inferno/Ato 2 selecionados como se tivesse
   sido aberta em Pesadelo/Ato 4: isso criaria um estado de teste impossível.
2. **Escala e renderização.** Deslocar uma captura testa coordenadas relativas,
   mas não simula uma nova renderização do jogo em outro DPI. Acrescentar
   capturas reais de cada escala suportada, painéis acima/abaixo e estados
   selecionado/não selecionado, sempre com negativos de outras dificuldades.
3. **Tooltip 2-3 sem hífen.** Há evidência de OCR `[2 3]` em execução anterior.
   A recusa é conservadora; falta um conjunto de capturas para melhorar a
   leitura sem inventar o número. Não aprovar a fase pela posição no mapa.
4. **Uma sessão longa no jogo.** Replays não medem latência do computador,
   animações novas, menus sobrepostos ou intervenção manual. Ainda é necessário
   ensaio ao vivo após mudanças de templates, escala ou versão do jogo.
5. **Energia e sessão do Windows.** Testes simulam perda de foco e falha de
   captura, não comprovam comportamento físico ao desligar/religar monitor,
   bloquear/desbloquear, suspender/retomar ou desconectar uma sessão remota.
6. **Dependências.** `requirements.txt` aceita intervalos de versões. Registrar
   as versões em uma falha e executar as capturas reais antes de atualizar OCR,
   OpenCV ou MSS. Não afirmar compatibilidade com toda versão permitida com
   base na execução de um único ambiente.

## Tela desligada

O bot atual depende de capturas de uma região da tela via MSS e do jogo em
primeiro plano. Não há captura independente da área de trabalho nem modo
headless. Minimizar o jogo ou perder o foco faz o bot recusar a operação;
em suspensão ele não executa. Bloquear a sessão não é um modo suportado.

Desligar apenas o monitor não é o mesmo estado que suspender o computador.
O Windows documenta estados específicos de energia do display, mas isso não
garante que este jogo e a captura continuem funcionando. **Funcionamento com
monitor apagado permanece não validado neste equipamento.** A correção do erro
MSS permite recuperar uma rodada, não oferece suporte a execução sem tela.

Fontes: [MSS: captura de regiões da tela](https://python-mss.readthedocs.io/stable/examples.html)
e [Microsoft: estados de energia do display](https://learn.microsoft.com/windows/win32/power/power-setting-guids).
