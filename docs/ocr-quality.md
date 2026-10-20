# Qualidade do reconhecimento visual

O erro de 19/10 às 20:32 foi uma leitura válida na sintaxe, mas incompatível com
a fase esperada: `2-58|Inferno` em vez de `2-8|Inferno`. Repetir o mesmo OCR em
dois frames não elimina erros sistemáticos. As capturas `measurement-1792452731909947700.png`
e `error-1792452731979000600.png` foram feitas depois da rejeição: nelas, a
variante antiga leu `2-B` e outras variantes leram `2-8`. O `2-58` original não
foi reproduzido nesses pixels; foi usado como entrada simulada nos testes da
política de confirmação.

## Correção aplicada

- A legenda inteira é lida com quatro tratamentos: máscara de texto claro em
  duas escalas e máscara que preserva mais bordas em duas escalas. Pelo menos
  duas leituras completas devem concordar, com um vencedor único. Empate ou
  ambiguidade de fase/dificuldade impede confirmação. Campos de leituras
  parciais não são combinados.
- O resultado ainda precisa se repetir em dois frames consecutivos. Todas as
  variantes compartilham o prazo de leitura da legenda; ESC é verificado entre
  elas. Isso usa mais OCR que a política anterior de aceitar a primeira leitura.
- Durante a busca, uma leitura diferente da fase esperada, na mesma dificuldade,
  e ausente do catálogo visual confirmado é tratada como OCR não confirmado.
  Nenhuma batalha é contabilizada nessa tentativa. Os limites existentes de
  falhas OCR e tempo encerram a coleta se ela não se recuperar.
- Uma mudança confirmada para outra dificuldade ou fase superior conhecida
  continua interrompendo a medição. O recuo por derrota ainda exige evidência
  nova no histórico. Não há conversão de `2-58` para `2-8` nem limite inventado
  de fases por ato.
- A primeira rejeição de legenda e uma mudança fatal agora podem salvar
  `artifacts/ocr-stage-*.png` e `.json`, com o recorte exato que foi lido e as
  interpretações de cada variante. Isso evita depender somente de screenshots
  feitos depois da animação que causou o erro.

Validação: 432 testes passaram sem skips no ambiente de desenvolvimento.
As duas capturas completas também passaram por `current_stage()` com OCR real
e retornaram `2-8|Inferno`, em aproximadamente 2,0–2,4 s, dentro do prazo de 4 s.
Isso é replay de capturas; não substitui uma nova coleta ao vivo.

## Como melhorar a imagem de entrada

1. Aumentar o tamanho do texto na origem é um experimento prioritário: testar a
   interface do jogo a 100%, se essa opção estiver disponível. A captura atual
   usa menu de 39 px contra a referência de 52 px, compatível com 75%. Manter a
   escala estável e conferir/recalibrar os templates depois da mudança.
2. Usar regiões pequenas para cada tarefa: legenda, histórico e título do
   tooltip. Excluir o portal da próxima fase e efeitos de combate sempre que
   possível. Cada região precisa de tratamento próprio.
3. Medir qualquer mudança com as capturas de regressão, conferindo o valor
   completo da fase, tempo, Gold e EXP. Uma confiança OCR alta sozinha não
   comprova que um dígito está correto.
4. Para falhas novas, conservar o par `ocr-stage-*.png/.json` ou o diagnóstico
   `ocr-log-*`. Melhorar a leitura com esse material e adicionar o caso à suíte,
   incluindo imagens negativas que não devem ser aceitas.

Escala, qualidade da binarização, bordas e segmentação de linhas afetam o
Tesseract. A [documentação oficial](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)
descreve esses fatores. Substituir o motor OCR deve ser avaliado com as mesmas
capturas e os mesmos critérios de acerto/latência.

Erros em `averno_option`, `act2_selected` ou âncoras do Mundo usam correspondência
de templates. Eles pedem revisão de escala, posição e estados selecionados; não
são corrigidos apenas trocando o motor que lê texto.
