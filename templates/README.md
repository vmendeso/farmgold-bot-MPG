# Referências visuais

`world_footer.png` é uma referência alternativa do Mundo, recortada da captura real da falha de 14:24 em 16/10/2026. Ela contém os dois botões exclusivos do rodapé e permite localizar o painel quando a legenda da fase cobre seu título. Usa escalas 1 e 4/3 e deslocamento `world_footer_anchor`, relativo à mesma origem do painel. A regressão cobre o título oculto, a recuperação após capturas sem âncora, timeout e cancelamento; inventário sem referências do Mundo continua rejeitado.

Alguns templates usam `resampling: "linear"` ou `"nearest"` para preservar a escala exata e os detalhes da fonte pequena. O padrão continua sendo a redução por área. A configuração foi conferida com a captura da lista aberta que falhou em 16/10/2026; o limiar de 0,90 foi preservado. Os testes também verificam que a lista fechada não confirma a opção Normal e que os atos selecionados são distintos.

Os PNGs desta pasta foram recortados das telas reais do jogo durante a calibração. O `config.json` ativo usa `navigation.mode: "icon_map"` e contém três páginas: `ato2_inferno` (ato 2/Inferno), `ato4_nightmare` (ato 4/Pesadelo) e `ato1_averno` (ato 1/Averno). As coletas ao vivo de cinco batalhas em 4-7/Pesadelo e cinco em 4-8/Pesadelo já foram conferidas no SQLite. A busca selecionou 4-8; o retorno a 4-7 foi confirmado ao vivo pelo comando `select`, após corrigir o reconhecimento do tooltip. Uma execução completa de `run` ainda não foi repetida depois dessa correção.

Uma descoberta posterior terminou com sucesso e confirmou 16 nós dourados: sete em ato 4/Pesadelo (4-1, 4-3, 4-4, 4-5, 4-6, 4-8, 4-9) e nove em ato 1/Averno (1-1 a 1-9). O Mundo foi fechado e a fase inicial 4-7 foi confirmada ao final. O título encobria o nó 4-2 e os personagens encobriam o marcador atual 4-7; nós ocultos não foram inferidos. Identificação no mapa não equivale a medição de rendimento: somente duas fases tiveram lotes de batalhas coletados.

| Referências | Finalidade |
|---|---|
| `menu.png`, `menu.target.json` | Âncora visual do menu e alvo para diagnóstico |
| `world_tab.png`, `world_ready.png`, `close_world.png` | Abrir o Mundo, confirmar a tela e voltar à batalha |
| `difficulty_arrow.png`, `normal_option.png` | Reconhecer a abertura do seletor de dificuldade |
| `nightmare_option.png`, `nightmare_selected.png`, `act4_selected.png` | Página de ato 4/Pesadelo |
| `averno_option.png`, `averno_selected.png`, `act1_selected.png` | Página de ato 1/Averno |
| `node_gold.png` | Evidência positiva de nó dourado disponível |
| `locked_marker.png` | Referência do cadeado preto observado |

Outros PNGs de atos e dificuldades servem como referências adicionais; sua presença não significa que todas essas páginas estejam configuradas. O navegador atual reconhece apenas nós dourados, confirma a cor e lê o ID no tooltip antes de selecionar. Chefes vermelhos e outros tipos de nó não fazem parte do catálogo automático. A ausência de cadeado não comprova disponibilidade.

Os controles seguem o cabeçalho Mundo na captura atual, com `world_header_anchor` e `panel_search_offset`, mesmo quando o painel aparece acima da batalha. Os deslocamentos usam a largura do menu com referência de 52 pixels. Os templates originais usam escalas 0,75 e 1; os nós incluem 0,74 para arredondamento. Os novos `inferno_selected.png` e `act2_selected.png` usam escalas 1 e 4/3, pois foram recortados na interface reduzida.

O rodapé da batalha só fica visível com o Mundo fechado e o ponteiro sobre a área de batalha. Ele é reconhecido com filtro de texto branco e confirmação em duas capturas. `observe --hover` pode mover o ponteiro para revelá-lo, mas não fecha o Mundo nem clica. O OCR dos logs usa `logs_linewise: true` para ler cada faixa individualmente; linhas de caixa e itens permanecem como marcadores da sequência, sem contar como batalhas.

Comandos a partir da raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m farmbot locate templates/menu.target.json --image artifacts/battle.png
.\.venv\Scripts\python.exe -m farmbot --focus navigate --flow open_world --execute
.\.venv\Scripts\python.exe -m farmbot --focus navigate --flow return_to_battle --execute
.\.venv\Scripts\python.exe -m farmbot --focus discover --page ato4_nightmare --execute
.\.venv\Scripts\python.exe -m farmbot --focus select --stage 4-7 --difficulty Pesadelo --execute
.\.venv\Scripts\python.exe -m farmbot --focus observe --hover
```

`navigate` suporta tanto o mapa de ícones quanto as rotas do modo genérico. `--focus` solicita foco somente no início. Para criar novas referências, consulte o [README principal](../README.md) e `python -m farmbot crop --help`. Use recortes pequenos e distintivos, sem fundos uniformes. Cada lista `ready` deve confirmar tanto o ato quanto a dificuldade selecionados.

A descoberta ao vivo em Ato 2/Inferno confirmou oito marcadores, fechou o Mundo e confirmou a batalha em 2-6/Inferno. A entrada nessa página a partir de outra combinação ainda não foi validada ao vivo.
