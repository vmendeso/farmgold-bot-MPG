# OCR híbrido com Groq

O Tesseract continua sendo a primeira tentativa. Quando uma leitura de histórico é incompleta, ou a legenda não obtém consenso local, o bot pode consultar a Groq. Só o recorte do histórico ou da legenda é enviado. Botões, mapas e cliques continuam dependendo das referências locais.

O modelo inicial é `qwen/qwen3.8-27b`, com entrada de imagem e JSON conforme a [documentação oficial de visão](https://console.groq.com/docs/vision). A integração usa HTTPS pela biblioteca padrão do Python; não requer novo pacote em `requirements.txt`.

## Configurar a chave no Windows

Na pasta do projeto, execute no PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts/configure_groq.py
```

Cole a chave no campo oculto e pressione Enter. O configurador grava a variável do usuário e a lê novamente para verificar a gravação. Não grava a chave em arquivos do projeto nem faz chamadas à API. A mensagem de sucesso comprova a gravação local; a validade da chave será verificada pelo benchmark.

Para diagnosticar sem exibir a chave:

```powershell
.\.venv\Scripts\python.exe scripts/configure_groq.py --check
```

Se houver uma chave antiga no terminal diferente da salva no Windows, remova somente a variável desse terminal e execute o diagnóstico novamente:

```powershell
Remove-Item Env:GROQ_API_KEY -ErrorAction SilentlyContinue
```

A chave salva nas variáveis do usuário permanece disponível ao bot. Caso `FARMBOT_DISABLE_REMOTE_OCR` esteja definida como `1`, o diagnóstico indicará o bloqueio. A saída do diagnóstico não contém credenciais e pode ser compartilhada para investigação.

Não coloque a chave no `config.json`. O bot lê `GROQ_API_KEY` do processo ou, no Windows, das variáveis de ambiente do usuário. A chave não é exibida nos logs. Arquivos `.env` não são carregados automaticamente.

```powershell
.\.venv\Scripts\python.exe -m farmbot check-config
```

Essa verificação informa se a chave está disponível, sem chamar a API. Sem chave, o bot permanece com OCR local. Depois de configurar, reinicie o bot e execute seu comando habitual.

## Configuração

O `config.json` já inclui:

```json
"groq": {
  "enabled": true,
  "model": "qwen/qwen3.8-27b",
  "timeout_seconds": 4.0,
  "min_interval_seconds": 1.0,
  "max_requests_per_hour": 60
}
```

Esse bloco fica dentro de `vision`. Para desativar, use `enabled: false`. O limite de chamadas é compartilhado entre as instâncias de OCR do mesmo processo, inclusive rodadas do loop; reiniciar o processo reinicia o contador. Não é um limite monetário da conta. Cada chamada tem saída limitada a 2.048 tokens. Erros e timeouts também contam para o limite. Uma falha HTTP impõe 60 segundos de pausa; timeout impõe 15 segundos. Não há retries ocultos do cliente HTTP.

ESC e a parada do painel interrompem a espera. Uma requisição já enviada pode terminar no servidor e ser cobrada; sua resposta tardia não será usada. A chave ativa amplia o prazo de confirmação da legenda para acomodar duas capturas com consulta remota; uma legenda local válida continua sendo retornada assim que confirmada.

## Validação e limites

- Uma resposta remota deve conter JSON completo e exatamente uma transcrição por linha visual, incluindo notificações e linhas repetidas.
- O mesmo parser local valida fase, tempo, Gold, EXP e derrota. Uma linha remota não pode contrariar números de uma linha local já confirmada.
- Capturas incompletas, respostas truncadas, ambiguidades e falhas de rede não autorizam cliques nem registram recompensa.
- Antes do envio, recortes menores que 32 pixels de altura ou largura recebem bordas pretas até esse tamanho. Isso corrige o HTTP 400 reproduzido na legenda de 23 pixels e também prepara a legenda de 31 pixels, preservando o texto e sem incluir outras áreas da tela.
- O coletor continua exigindo duas capturas consecutivas concordantes. As respostas remotas não são reutilizadas como confirmação de uma segunda captura.
- O histórico e as decisões de rentabilidade continuam locais, incluindo os cinco segundos de reinício por batalha.

A integração não corrige uma leitura local errada que já passou em todos os consensos, nem os problemas de comparação de templates. Modelos de visão também podem errar números. O benchmark abaixo precisa ser executado com sua chave antes de concluir que a Groq é mais precisa neste jogo.

## Comparar nas capturas existentes

Somente OCR local, sem custo de API:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_groq.py
```

Comparar com a Groq, enviando quatro recortes de teste (duas legendas e dois históricos):

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_groq.py --execute
```

O resultado fica em `artifacts/groq-benchmark.json`, com acertos exatos, latência e uso de tokens informado pela API. O teste não abre o jogo, não clica e não altera seu ranking. As anotações esperadas são usadas apenas para comparar o resultado, nunca são enviadas ao modelo. A suíte `scripts/check_tests.py` bloqueia chamadas externas para não consumir créditos; os testes da integração simulam o transporte.

## HTTP 403 no benchmark

HTTP 403 significa que a requisição foi recusada por acesso; não mede a qualidade da transcrição. A Groq documenta bloqueios de modelo por organização e por projeto em [Model Permissions](https://console.groq.com/docs/model-permissions). Confira se `vision.groq.model` está permitido em Settings → Organization → Limits e Settings → Projects → Limits. Uma permissão do projeto não libera um bloqueio da organização.

O cliente envia `User-Agent: farmgold-bot-MPG/1.0`. No diagnóstico deste ambiente, a consulta à lista de modelos retornou 403 com a identificação padrão do `urllib`, e 200 com essa identificação explícita do aplicativo. Depois dessa correção, a chamada de OCR recebeu o código `model_permission_blocked_org`: ainda é necessário liberar o modelo na organização. Acesso à lista de modelos não comprova permissão para executar cada modelo.

O diagnóstico identifica esses dois códigos quando fornecidos pela API. Outros retornos 403 permanecem como causa não identificada; não é possível concluir só pelo status se o bloqueio está na conta ou na rede. Mensagens e cabeçalhos recebidos não são exibidos. Após um erro, casos executados durante a pausa são marcados como “não executado: pausa após erro anterior”, em vez de serem interpretados como novas falhas de leitura.
