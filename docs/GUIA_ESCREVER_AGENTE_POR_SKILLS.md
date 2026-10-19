# Como escrever um agente por skills

Guia para quem administra a empresa e quer montar um agente de IA sem programar. Tudo o que está aqui foi conferido contra o código do serviço; o que ainda não existe aparece marcado como **não implementado**.

## 1. A ideia em um minuto

Um agente por skills é uma **pasta de textos**. Você escreve em português comum:

- quem o agente é e como ele fala (`AGENTE.md`);
- o que ele sabe fazer em cada situação (uma **skill** por situação);
- o que ele **pode usar** e até onde (o **manifesto**).

O texto orienta o agente. **Quem decide o que ele pode fazer de verdade é o manifesto.** Se você escrever "pode enviar e-mail" no texto, mas não ligar a ferramenta de e-mail no manifesto, o agente não envia nada. O contrário também vale: ferramenta ligada e texto silencioso significa que o agente pode usar a ferramenta quando achar adequado.

## 2. Anatomia do pacote

```
AGENTE.md                         obrigatório: a instrução-mestra
skills/<nome>/SKILL.md            uma skill por situação
skills/<nome>/references/*.md     material de apoio (políticas, tabelas, FAQ)
skills/<nome>/scripts/*           scripts (.py, .sh, .js) que a skill pode mandar rodar
skills/<nome>/templates/*         modelos de mensagem (.md .txt .html .json .csv .tmpl .j2)
manifesto                         ferramentas, ambiente, modelo e limites (gerado pela tela)
```

Outros tipos de arquivo ou outras pastas são recusados na hora de publicar.

### 2.1 AGENTE.md

É a instrução-mestra. Escreva: quem é o agente, para quem ele trabalha, o tom de voz, o que fazer e o que **não** fazer, e **quando usar cada skill**. Texto curto e direto funciona melhor que texto longo.

### 2.2 SKILL.md

Cada skill começa com um cabeçalho entre duas linhas de `---` e depois o texto:

```markdown
---
name: consultar-pedido
description: Quando o cliente pergunta onde está o pedido ou qual o status.
---

# Passos
1. ...
```

Regras do cabeçalho (conferidas pela validação de publicação):

- `name`: só letras minúsculas, números, ponto, hífen e sublinhado; começa com letra ou número; até 64 caracteres. Use o **mesmo nome da pasta** (se for diferente o sistema avisa).
- `description`: obrigatória, até 1.024 caracteres. É o que o agente lê para decidir se usa a skill. Diga **quando** usar.
- Dentro do texto, links para material de apoio devem apontar para arquivos que existem, no formato `(references/arquivo.md)`. Link quebrado impede a publicação.

### 2.3 Limites do pacote

| Item | Limite |
|---|---|
| Arquivos no pacote | 100 |
| Tamanho do pacote | 2 MiB |
| Cada arquivo | 200 KiB |
| Cada SKILL.md | 100.000 caracteres |
| Codificação | UTF-8, sem bytes nulos |
| Caminhos | sem `..`, sem começar com `/`, sem pastas ou arquivos ocultos (nome começando com ponto) |

**Segredos nunca entram no texto.** O sistema varre o pacote e **recusa** chaves de acesso (AWS, GitHub, Google, Slack, Mercado Pago), chaves privadas, e linhas do tipo `senha: valor`, `token = valor` ou `api_key: valor` com um valor de 8 ou mais caracteres. As chaves e senhas da empresa ficam cadastradas na tela de credenciais; o agente as usa pelas ferramentas, sem nunca ver o valor.

### 2.4 O manifesto

É um quadro de permissões. Em formato de arquivo fica assim (a tela monta isso para você):

```json
{
  "tools": ["rt_search_docs", "rt_sql_read"],
  "environment": {"enabled": false},
  "model": {"alias": "agente-padrao"},
  "limits": {"max_turns": 30}
}
```

| Campo | Para que serve |
|---|---|
| `tools` | As ferramentas que o agente pode usar (tabela da seção 3). Nome desconhecido impede a publicação. |
| `environment.enabled` | `true` dá ao agente o **ambiente da empresa** (um servidor próprio com terminal, onde ele instala e roda programas). `false` (padrão) não dá. |
| `model.alias` | O modelo de IA. Sem este campo vale `agente-padrao`. Use outro apelido só se a plataforma tiver cadastrado. |
| `limits.max_turns` | Quantos passos o agente pode dar em uma resposta. De 1 a 200 na tela de publicação. |
| `limits.confirm_actions` | `true` liga o **cartão de confirmação** (seção 4). Padrão: desligado. |
| `limits.write_tables` | Lista de tabelas onde o agente pode gravar (só vale com `rt_sql_write`). **Se a lista não existir, ele pode gravar em qualquer tabela da fonte liberada.** Sempre preencha. |
| `limits.http_allowed_hosts` | Lista de endereços de internet que o agente pode chamar (só vale com `rt_http`). Aceita `*.exemplo.com.br`. Se a lista não existir, qualquer endereço público é aceito (endereços internos ficam sempre bloqueados). Sempre preencha. |

**Importante sobre limites.** O serviço de execução entende mais limites do que a tela de publicação guarda hoje (`pix_max_amount`, `max_recipients`, `max_tool_calls_per_turn`, `external_approval`). A tela descarta esses campos ao salvar o manifesto, então **não os use no pacote**. Valem os padrões do serviço: teto de cobrança Pix de R$ 10.000,00, até 5 destinatários por e-mail e 40 chamadas de ferramenta por resposta. Alterar esses padrões é tarefa da equipe técnica.

## 3. Ferramentas que você pode ligar

Estes são os nomes aceitos no campo `tools` da tela de publicação:

| Ferramenta | O que o agente faz com ela | Pede confirmação (se o cartão estiver ligado)? |
|---|---|---|
| `rt_data_describe` | Ver quais fontes de dados e tabelas existem | não |
| `rt_sql_read` | Consultar dados (somente leitura) | não |
| `rt_mongo_find` | Consultar coleções de dados | não |
| `rt_sql_write` | Gravar dados (um comando por vez, com `WHERE` obrigatório em alterar/apagar, só nas tabelas de `write_tables`) | sim |
| `rt_pix_create` | Criar cobrança Pix (Mercado Pago) com QR Code e copia-e-cola | sim |
| `rt_payment_status` | Ver se uma cobrança foi paga | não |
| `rt_email_list` | Ler e-mails de uma caixa da empresa | não |
| `rt_email_send` | Enviar e-mail | sim |
| `rt_google_calendar`, `rt_google_sheets`, `rt_ms_calendar` | Agenda e planilhas conectadas. **Não implementado:** o nome aparece no catálogo da tela, mas o serviço ainda não executa essas ferramentas; o agente não consegue usá-las. | - |
| `rt_http` | Chamar serviços de terceiros com as chaves cadastradas | só chamadas que alteram algo (POST, PUT, PATCH, DELETE) |
| `rt_search_docs` | Buscar nos arquivos da empresa | não |
| `rt_memory_recall` | Lembrar o que o usuário já informou | não |
| `rt_artifact_put` | Entregar arquivo, imagem ou tabela para o usuário baixar | não |
| `rt_executor_run` | Calcular e gerar gráficos, planilhas e PDFs num espaço isolado sem internet | não |
| `rt_image_generate` | Gerar imagens. **Não implementado** (mesma situação das agendas). | - |
| `rt_template_plan` | Montar agentes novos conversando. **Não implementado** (mesma situação das agendas). | - |
| `rt_env_status` | Ver o estado do ambiente e dos apps publicados | não |
| `rt_env_manage` | Operar o ambiente: ligar, rodar comandos no terminal, enviar e baixar arquivos, parar, backup, publicar app na internet, tirar do ar, ligar domínio próprio | parar, publicar, tirar do ar e ligar domínio |
| `rt_job_schedule` | Criar, listar, pausar e apagar rotinas agendadas | criar e apagar |

Não liste ferramentas marcadas como não implementadas: a publicação aceita o nome, mas em uso o serviço nega a chamada.

**Regra de ouro: liste só o que o agente precisa.** Uma skill nunca amplia o que o agente pode fazer; só o manifesto liga ferramentas. Do mesmo modo, as instruções internas da plataforma para cada grupo de ferramentas (Pix, dados, e-mail, ambiente, rotinas e outros) só chegam ao agente quando você liga alguma ferramenta do grupo.

Observação técnica: `rt_env_manage` e `rt_job_schedule` são "grupos". Ao rodar, o serviço os expande em ferramentas finas (`rt_env_start`, `rt_env_exec`, `rt_env_expose`, `rt_schedule_create` e outras). Você só escreve o nome do grupo.

## 4. Por padrão o agente age direto

Quando o pedido é claro, o agente executa: consulta, grava, cria a cobrança, envia o e-mail. Ninguém precisa clicar em nada. O agente conta o que fez ao final. A instrução interna da plataforma (`boas-praticas-de-confirmacao`) já ensina o agente a **perguntar na própria conversa**, em uma frase, quando há dúvida real ou risco alto (valor que o usuário não citou, envio para muita gente, apagar dados, publicar para o público).

### Quando ligar o cartão de confirmação (`confirm_actions`)

Com `"confirm_actions": true`, as ações sensíveis ficam **paradas até uma pessoa clicar em "Confirmar"** num cartão na tela. A confirmação usa a sessão da pessoa logada; não é texto digitado no chat e o agente não consegue "se autoconfirmar". Se ninguém decidir em 2 minutos (padrão), a ação expira e **nada é executado**. Só o dono da conversa pode decidir.

Ligue quando:

- o agente mexe com **dinheiro** (Pix), **grava dados**, **envia e-mail** em nome da empresa ou **publica** algo na internet;
- há uma **pessoa da equipe** na conversa para clicar.

Deixe desligado quando:

- o agente só **lê** e responde (como o exemplo de atendimento);
- o agente conversa com o **cliente final** sem ninguém da equipe presente.

Duas situações que surpreendem:

1. **Cliente externo com o cartão ligado.** Se o autor da conversa é um contato externo (por exemplo, um cliente no WhatsApp), não há ninguém para clicar. A ação sensível é **negada** ("Este canal não permite executar esta ação"). A opção de enviar o cartão a um humano da equipe (`external_approval`) existe no serviço, mas **a tela de publicação não a guarda hoje: não implementado no pacote**.
2. **Rotina agendada com o cartão ligado.** Quando o agente roda sozinho em horário marcado, só o **envio de e-mail** segue (a pessoa autorizou ao criar a rotina). Dinheiro, gravação de dados e publicação são **negados**. Rotinas de manutenção devem usar apenas ferramentas que não pedem confirmação, mais o e-mail.

## 5. Quem pode usar o agente

A permissão de **usar** um agente vem do perfil de IA de cada pessoa na plataforma (escopos `ai.chat.run`, `ai.agents.*`, `ai.environment.manage` e outros). Quem pode **criar, editar e publicar** agentes também é decidido por esses escopos.

Restringir **um agente específico** a quem tem certa permissão (por exemplo, o agente administrador só para quem tem `ai.environment.manage`) está previsto nas especificações como "escopo por template", com o nome `allowed_scopes` no manifesto. **Não implementado:** o campo `allowed_scopes` não existe no serviço de execução nem na tela de publicação, e é **descartado ao salvar** o manifesto. Enquanto isso:

- trate o agente administrador como acessível a **quem pode conversar com agentes** da empresa;
- reduza o risco ligando o cartão de confirmação (`confirm_actions`) nesse agente, o que exige um clique autenticado para parar o ambiente, publicar apps e ligar domínios;
- não ofereça agentes com ferramentas sensíveis nos canais abertos ao público.

## 6. Como o agente aprende (camada do usuário)

Além das skills da empresa, cada pessoa pode ter skills próprias que o agente propõe a partir do uso. Por padrão elas **ficam pendentes** até a pessoa aprovar. Skills aprendidas nunca ampliam ferramentas, não podem ter o mesmo nome de uma skill da empresa ou do sistema, e passam pela mesma varredura de segredos (limite de 32 KiB cada).

## 7. Antes de publicar

1. Rode a validação da tela de publicação. Ela confere tudo o que a seção 2 descreve.
2. Teste no chat de teste com 3 perguntas típicas e 1 pedido que o agente deve **recusar**.
3. Publique. Publicar cria uma **versão** que passa a valer; editar só mexe no rascunho. Para voltar atrás, restaure a versão anterior.

---

# Três exemplos completos

Os arquivos abaixo podem ser copiados como estão. Todos foram validados contra as regras reais de publicação (nomes de ferramenta, cabeçalhos, links, varredura de segredos, limites) e contra o gerador de configuração do agente. Troque os nomes de empresa, tabelas e endereços pelos seus.

## Exemplo A: atendimento ao cliente

**O que o agente faz.** Atende clientes da loja "Casa Verde Móveis": responde dúvidas com base na política da empresa, consulta o andamento de pedidos em somente leitura, acompanha a entrega na transportadora e encaminha para um humano quando não resolve.

**Limites.** Só lê: não grava dados, não cobra, não envia e-mail. Só chama o endereço da transportadora. Máximo de 30 passos por resposta. Cartão de confirmação desligado, porque não há nada sensível para confirmar e o cliente final não tem como clicar.

<!-- arquivo: a/AGENTE.md -->
```markdown
# Atendente da Casa Verde Móveis

Você atende clientes da Casa Verde Móveis, uma loja de móveis, pelo chat. Fale em português do Brasil, com educação e frases curtas. Trate o cliente por "você".

## Objetivo
Resolver a dúvida do cliente na primeira conversa: andamento de pedido, prazo de entrega, trocas e devoluções, garantia.

## Como trabalhar
- Para saber onde está um pedido, use a skill `consultar-pedido`.
- Para trocas, devoluções e garantia, use a skill `trocas-e-devolucoes`.
- Para perguntas gerais (horário, endereço, formas de pagamento), busque nos arquivos da empresa antes de responder.
- Se o cliente já contou algo em outra conversa, aproveite o que a memória trouxer. Não peça de novo.

## O que você não faz
- Não prometa prazo, desconto ou exceção que não estejam na política.
- Não peça nem aceite senha, número de cartão ou código de segurança.
- Não altere pedidos e não faça cobranças. Se o cliente pedir isso, diga que um atendente humano vai ajudar.
- Se não souber ou se o cliente estiver irritado, diga que vai encaminhar para um atendente humano e resuma o caso em duas frases para facilitar.
```

<!-- arquivo: a/skills/consultar-pedido/SKILL.md -->
```markdown
---
name: consultar-pedido
description: Quando o cliente pergunta onde está o pedido, o status ou a data de entrega. Consulta o pedido e a transportadora.
---

# Consultar pedido

1. Peça o número do pedido ou o e-mail usado na compra. Sem um dos dois, não consulte.
2. Veja as tabelas disponíveis com `rt_data_describe` e consulte o pedido com `rt_sql_read`. Faça `SELECT` só das colunas necessárias e confira que o pedido pertence ao e-mail ou documento informado pelo cliente.
3. Se o pedido já foi despachado, consulte o rastreio na transportadora com `rt_http` (método GET) no endereço `https://api.transportadora-exemplo.com.br/rastreio/<codigo>`. A chave de acesso é o segredo cadastrado na empresa com o nome `TRANSPORTADORA_KEY`: use-a no cabeçalho `Authorization` (formato `Bearer` seguido da chave), pelo nome, como a ferramenta explica. Nunca escreva o valor da chave.
4. Responda com: situação atual, última movimentação e previsão. Se a previsão passou, peça desculpas e encaminhe para um atendente humano.
5. Se não achar o pedido, não invente. Peça para conferir o número.
```

<!-- arquivo: a/skills/trocas-e-devolucoes/SKILL.md -->
```markdown
---
name: trocas-e-devolucoes
description: Quando o cliente quer trocar, devolver ou acionar a garantia de um produto.
---

# Trocas, devoluções e garantia

A política completa está em [política da loja](references/politica.md). Resumo para a conversa:

1. Pergunte o produto, a data da compra e o motivo.
2. Confira o prazo e as condições na política. Explique ao cliente o que vale para o caso dele.
3. Se estiver dentro das regras, explique o passo a passo. Se estiver fora, explique o motivo com gentileza e ofereça encaminhar a um atendente humano para avaliar exceção.
4. Nunca prometa reembolso ou data de coleta: quem confirma isso é a equipe.
```

<!-- arquivo: a/skills/trocas-e-devolucoes/references/politica.md -->
```markdown
# Política de trocas, devoluções e garantia

- Arrependimento de compra online: até 7 dias corridos após o recebimento, produto sem uso e na embalagem original.
- Defeito de fabricação: até 90 dias para reclamar de defeito aparente; garantia de 12 meses contra defeitos de fabricação.
- Troca por outro modelo: até 30 dias, sujeita a estoque e diferença de valor.
- Não cobertos: danos por mau uso, desgaste normal e montagem feita por terceiros sem orientação.
- Quem decide exceções e confirma coleta ou reembolso: equipe de atendimento humano.
```

<!-- arquivo: a/manifest.json -->
```json
{
  "tools": ["rt_search_docs", "rt_memory_recall", "rt_data_describe", "rt_sql_read", "rt_http"],
  "environment": {"enabled": false},
  "model": {"alias": "agente-padrao"},
  "limits": {
    "max_turns": 30,
    "http_allowed_hosts": ["api.transportadora-exemplo.com.br"]
  }
}
```

Observações:

- `rt_http` está ligado para o rastreio. Como o agente só faz consultas (GET), nada pede confirmação.
- A chave `TRANSPORTADORA_KEY` deve estar cadastrada como segredo da empresa e liberada para o agente. O texto cita apenas o **nome**; na hora da chamada a plataforma troca o nome pelo valor e o agente nunca vê o valor. Não escreva no pacote o marcador técnico de segredo (chaves duplas com a palavra `secret` e dois-pontos): a varredura de segredos o confunde com uma senha e recusa a publicação.
- A fonte de dados com a tabela de pedidos precisa estar cadastrada na empresa e liberada para o agente.

## Exemplo B: vendas com cobrança Pix

**O que o agente faz.** Ajuda a pessoa de vendas da empresa "Estúdio Bela Forma" a fechar pedidos: confirma com ela o valor combinado, registra o pedido, gera a cobrança Pix pelo Mercado Pago da empresa e entrega o QR Code e o código copia-e-cola. Depois confere se o pagamento caiu e só então marca o pedido como pago.

**Limites.** Cobra **somente o valor que a pessoa de vendas informar** (o agente não calcula nem sugere desconto). A plataforma recusa valor acima de R$ 10.000,00, valor com mais de 2 casas decimais e referência repetida com valor diferente. Grava dados só na tabela `pedidos`. Cartão de confirmação **ligado**: criar cobrança, gravar no pedido e enviar e-mail exigem clique de confirmação da pessoa logada. Como depende de alguém para clicar, este agente é para uso **interno** (equipe de vendas no chat). Para vender direto a um cliente final num canal aberto, veja a seção 4 (ação sensível é negada com o cartão ligado).

<!-- arquivo: b/AGENTE.md -->
```markdown
# Assistente de Vendas do Estúdio Bela Forma

Você trabalha com a equipe de vendas do Estúdio Bela Forma (aulas e pacotes de pilates). Quem conversa com você é uma pessoa da equipe, não o cliente final. Fale em português do Brasil, de forma objetiva.

## Objetivo
Ajudar a fechar pedidos: registrar o pedido, gerar a cobrança Pix, entregar o QR Code e o copia-e-cola à pessoa de vendas e conferir se o pagamento foi feito.

## Regras de dinheiro
- Cobre **apenas o valor combinado** que a pessoa de vendas informou. Nunca calcule desconto, acréscimo ou parcela por conta própria. Se o valor não foi dito, pergunte.
- Repita o resumo antes de cobrar: cliente, produto e valor em reais. A plataforma também mostra um cartão de confirmação.
- Nunca diga que um pedido está pago sem conferir o pagamento (skill `conferir-pagamento`).
- Se a ferramenta recusar o valor ou a referência, explique o motivo em uma frase. Não tente dividir a cobrança para contornar o limite.

## Como trabalhar
- Para gerar uma cobrança, use a skill `cobrar-pix`.
- Para saber se o pagamento caiu, use a skill `conferir-pagamento`.
- Para tirar dúvidas de preço de tabela e pacotes, busque nos dados da empresa antes de responder.

## O que você não faz
- Não envie código Pix, chave ou dados de pagamento para ninguém fora da conversa. A pessoa de vendas repassa ao cliente.
- Não peça nem guarde número de cartão.
- Não grave em nenhuma tabela além de `pedidos`.
```

<!-- arquivo: b/skills/cobrar-pix/SKILL.md -->
```markdown
---
name: cobrar-pix
description: Quando a pessoa de vendas pede para cobrar um cliente por Pix com um valor já combinado.
---

# Cobrar por Pix

1. Reúna três coisas, sem inventar nenhuma: **nome do cliente**, **o que foi vendido** e o **valor combinado** em reais (ex.: 350.00). Falta algo, pergunte.
2. Crie a **referência** do pedido no formato `BF-AAAAMMDD-NNN` (data e número do dia). A mesma referência no mesmo chat nunca gera cobrança duplicada.
3. Registre o pedido com `rt_sql_write` na tabela `pedidos` (um `INSERT` com cliente, produto, valor, referência e situação `aguardando_pagamento`).
4. Gere a cobrança com `rt_pix_create`, informando `amount` (exatamente o valor combinado, com ponto decimal), `description` (o que foi vendido) e `reference`.
5. Entregue à pessoa de vendas: o **QR Code** (ele aparece como imagem na conversa) e o **código copia-e-cola** que a ferramenta devolveu. Diga o valor e a referência.
6. Avise que, depois do pagamento, ela pode pedir "conferir o pedido BF-..." para você confirmar.

Se a ferramenta devolver que a cobrança já existe, apresente a mesma cobrança; não crie outra.
```

<!-- arquivo: b/skills/conferir-pagamento/SKILL.md -->
```markdown
---
name: conferir-pagamento
description: Quando a pessoa de vendas quer saber se um pedido já foi pago, ou pede para conferir uma cobrança Pix.
---

# Conferir pagamento

1. Pegue a **referência** do pedido (ou o número do pagamento).
2. Consulte com `rt_payment_status`. Os resultados possíveis são: pendente, pago, recusado, cancelado ou devolvido.
3. Só diga que o pedido está **pago** quando a situação for **pago**. Qualquer outra situação significa que o pedido não pode ser liberado.
4. Quando estiver pago, atualize o pedido com `rt_sql_write`: `UPDATE pedidos SET situacao = 'pago' WHERE referencia = '<referencia>'`.
5. Se estiver pendente, diga isso e sugira aguardar. Se for recusado ou cancelado, avise a pessoa de vendas para gerar uma cobrança nova.
6. Se a pessoa pedir um comprovante por e-mail, redija a mensagem, mostre o texto e envie com `rt_email_send` para o endereço que ela indicar.
```

<!-- arquivo: b/manifest.json -->
```json
{
  "tools": [
    "rt_pix_create",
    "rt_payment_status",
    "rt_data_describe",
    "rt_sql_read",
    "rt_sql_write",
    "rt_email_send",
    "rt_memory_recall"
  ],
  "environment": {"enabled": false},
  "model": {"alias": "agente-padrao"},
  "limits": {
    "max_turns": 40,
    "confirm_actions": true,
    "write_tables": ["pedidos"]
  }
}
```

Observações:

- O Mercado Pago precisa estar cadastrado nas credenciais da empresa, com o agente autorizado a usá-lo. Sem isso a ferramenta responde que o Mercado Pago não está configurado.
- A cobrança devolve o código copia-e-cola e, quando o Mercado Pago fornece, o QR Code como imagem na conversa.
- O teto de R$ 10.000,00 é padrão do serviço e não pode ser mudado pelo pacote (seção 2.4).
- Quem libera produto ou serviço é o aviso oficial do provedor de pagamento; a consulta do agente é informativa.

## Exemplo C: administrador de ambiente

**O que o agente faz.** Cuida do ambiente da empresa (o servidor próprio): liga o ambiente, instala e atualiza programas, sobe um serviço (por exemplo, um painel de BI), publica o app na internet com endereço HTTPS, liga um domínio da empresa e roda a rotina diária de conferência com backup e resumo por e-mail.

**Limites.** Não toca em dados de clientes, não cobra e não grava em bases. O ambiente é isolado de outras empresas e **não tem acesso aos bancos da plataforma**. Parar o ambiente, publicar app, tirar do ar e ligar domínio exigem **clique de confirmação** (cartão ligado). A rotina agendada só usa conferência de estado, backup e e-mail, porque numa rotina sem pessoa na tela qualquer outra ação sensível seria negada. Como o campo de restrição por permissão (`allowed_scopes`) **não está implementado**, use este agente só com quem administra a empresa (seção 5).

<!-- arquivo: c/AGENTE.md -->
```markdown
# Administrador do ambiente

Você cuida do ambiente da empresa: um servidor Linux só dela, onde se instalam programas e se mantêm serviços no ar. Quem conversa com você é quem administra a empresa. Fale em português do Brasil, simples e direto, sem jargão. Conte o que fez no final de cada tarefa.

## Objetivo
Manter o ambiente saudável e entregar o que a empresa pedir: programas instalados, serviços funcionando, apps publicados com endereço seguro e backup em dia.

## Como trabalhar
- Antes de qualquer coisa, veja o estado do ambiente. Se estiver parado, ligue.
- Instalações demoradas rodam em segundo plano com registro (log). Acompanhe o log e conte o andamento.
- Todo programa que deve ficar no ar vira um serviço supervisionado, para voltar sozinho depois de um reinício.
- Para publicar na internet, use a skill `publicar-servico`.
- Para a conferência diária, use a skill `rotina-diaria`.

## Cuidados
- Nunca escreva chaves, senhas ou tokens em arquivos ou na conversa. Se um programa precisar de uma chave, use o nome do segredo cadastrado na empresa.
- Publicar deixa o app acessível a qualquer pessoa com o endereço. Avise isso e recomende login no próprio app quando houver dados da empresa.
- Não pare o ambiente se houver apps publicados em uso sem avisar o administrador.
- Se algo falhar, explique em linguagem simples, tente corrigir no máximo duas vezes e diga o que falta.
```

<!-- arquivo: c/skills/publicar-servico/SKILL.md -->
```markdown
---
name: publicar-servico
description: Quando pedirem para colocar um programa do ambiente na internet, com endereço próprio ou domínio da empresa.
---

# Publicar um serviço

1. Confirme que o serviço já está rodando no ambiente e anote a porta em que ele escuta (de 1024 a 65535).
2. Escolha um nome curto para o endereço: letras minúsculas, números e hífen (ex.: `painel`).
3. Publique o app. O endereço fica no formato `https://<nome>-<empresa>.apps.rangeltech.net`, com HTTPS. Conte ao administrador que o app fica aberto a quem tiver o link.
4. Para usar um domínio da própria empresa (ex.: `painel.suaempresa.com.br`), peça o domínio, ligue-o ao app e entregue ao administrador os **dois registros de DNS** que a ferramenta devolver (um TXT e um CNAME). Depois que ele criar os registros no provedor do domínio, peça para avisar e verifique o domínio.
5. Para tirar um app do ar, use a opção de despublicar; o serviço continua rodando no ambiente.
```

<!-- arquivo: c/skills/rotina-diaria/SKILL.md -->
```markdown
---
name: rotina-diaria
description: Conferência diária do ambiente com backup e resumo por e-mail. Também usada para criar ou ajustar essa rotina.
---

# Rotina diária

## O que fazer a cada disparo
1. Veja o estado do ambiente e dos apps publicados.
2. Se o ambiente estiver parado e houver apps publicados, ligue-o.
3. Faça um backup do ambiente.
4. Envie por e-mail um resumo curto ao administrador: estado do ambiente, apps no ar, se o backup deu certo e qualquer problema encontrado. Não inclua chaves nem dados sensíveis.

## Criar a rotina
1. Pergunte o horário e o e-mail de destino do resumo.
2. Mostre ao administrador a instrução completa que será salva e o horário.
3. Crie a rotina com horário no fuso de São Paulo. Exemplo: todo dia às 8h usa `0 8 * * *`.
4. Confirme com a lista de rotinas. Para pausar ou apagar, use as opções correspondentes.

Na rotina, não tente publicar apps, ligar domínios ou parar o ambiente: sem ninguém na tela essas ações são negadas.
```

<!-- arquivo: c/manifest.json -->
```json
{
  "tools": [
    "rt_env_status",
    "rt_env_manage",
    "rt_job_schedule",
    "rt_email_send",
    "rt_search_docs"
  ],
  "environment": {"enabled": true},
  "model": {"alias": "agente-padrao"},
  "limits": {
    "max_turns": 60,
    "confirm_actions": true
  }
}
```

Observações:

- O endereço e a chave de acesso do ambiente são preenchidos pela plataforma; você só liga `environment.enabled`.
- Com `confirm_actions` ligado, parar o ambiente, publicar app, tirar do ar e ligar domínio mostram o cartão "Confirmar ação". Backup e conferência de estado não pedem confirmação.
- O ambiente dorme sozinho depois de 4 horas sem uso, exceto quando há app publicado.
- Criar a rotina também pede confirmação, e o envio de e-mail da rotina já nasce autorizado por quem a criou.

---

## Dúvidas comuns

**O agente não obedeceu ao texto.** O texto orienta, o manifesto limita. Se algo realmente não pode acontecer, tire a ferramenta do manifesto (ou, no caso de gravação e internet, restrinja `write_tables` e `http_allowed_hosts`).

**A publicação foi recusada.** A tela mostra uma lista de problemas com o arquivo e o motivo. Os mais comuns: `AGENTE.md` ausente, cabeçalho da skill sem `description`, link `references/...` para arquivo que não existe, ferramenta com nome errado, e texto parecido com chave ou senha.

**O agente disse que não tem permissão.** Confira se a ferramenta está no manifesto, se a credencial (Mercado Pago, e-mail, fonte de dados) está cadastrada e liberada para o agente, e se o cartão de confirmação está ligado em um canal sem ninguém para clicar.

**Quero impedir que o agente fale de certo assunto.** Escreva a regra em `O que você não faz` no `AGENTE.md`. Para assuntos que envolvem dados sensíveis, não dê a ferramenta que acessa esses dados.
