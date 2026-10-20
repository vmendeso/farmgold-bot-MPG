# Calibração da interface em 100%

Em 19/10/2026, a interface foi ampliada de 75% para 100%. A âncora do menu passou de 39 para 52 pixels de largura. Os deslocamentos do histórico, da legenda e do painel Mundo já acompanhavam essa escala; não foi necessário fixar coordenadas da tela.

As referências reduzidas de Inferno selecionado e Ato 2 selecionado não alcançaram o limiar de 0,9 na nova escala. Foram adicionados `inferno_selected_native.png` e `act2_selected_native.png`, recortados de uma captura real em 100%. As referências anteriores continuam disponíveis para a escala reduzida.

O campo opcional `template_variants` permite até três referências adicionais do mesmo controle, cada uma com `template`, `scales` e `resampling`. Todas herdam a região de busca, o limiar e o nome lógico do alvo original. Correspondências sobrepostas representam um único controle; controles em posições distintas continuam ambíguos. Uma variante não pode reduzir o limiar nem ampliar a região de busca.

As capturas de regressão `tests/fixtures/scale100_*.png` vêm de uma segunda passagem ao vivo, depois da calibração. Cobrem os três mapas configurados, listas de dificuldades e estados intermediários em que a dificuldade já mudou, mas o ato ainda não. Os testes também verificam deslocamento do painel e rejeição de atos/dificuldades incorretos.

Mantenha a escala utilizada no teste. Alterações posteriores no zoom, na fonte ou no desenho da interface precisam de nova validação. O histórico expandido continua sendo acompanhado pelas cinco linhas inferiores, com ordem e ocorrências preservadas.

## Validação em 19/10/2026

- Suíte completa: 438 testes aprovados, sem ignorados, em 50,4 segundos.
- Ao vivo: transições entre Inferno/Ato 2, Pesadelo/Ato 4 e Averno/Ato 1; descoberta de 26 marcadores confirmados (9 + 9 + 8).
- Coleta em 2-8/Inferno: uma batalha de 71,71 segundos, 2.282 Gold e 5.800 EXP.
- Repetição com o código e a configuração da cópia `github/farmgold-bot-MPG`: seleção de 2-7/Inferno e coleta de uma batalha de 70,96 segundos, 2.282 Gold e 5.690 EXP.
- Dados dos testes gravados em bancos isolados; nenhum resultado de calibração foi inserido no ranking habitual.

Uma primeira coleta exploratória em 2-7 expirou sem contabilizar as batalhas visíveis. Os registros daquela tentativa não permitem concluir a causa. A repetição instrumentada passou. Efeitos luminosos também atravessaram o histórico: capturas incompletas foram rejeitadas e o OCR se recuperou na tentativa seguinte. Estes testes confirmam os caminhos exercitados, não uma execução indefinida sem interrupções.
