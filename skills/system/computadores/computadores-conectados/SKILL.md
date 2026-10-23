---
name: computadores-conectados
description: Como trabalhar nos computadores que o cliente conectou à empresa (Windows e Linux): listar pastas, ler e editar arquivos como planilhas, rodar comandos e usar o Chrome, sem baixar o arquivo para a nuvem.
---

# Computadores conectados

Skill global do sistema. Não aparece nas skills da empresa e não é editável pelo cliente.

## Quando usar
O usuário pediu algo que está **em um computador dele** (arquivo na Área de Trabalho, programa, pasta, navegador). Os computadores liberados para você aparecem na lista **Computadores** do início da conversa (nome, sistema, online ou offline). Use **só** o nome que está nessa lista. Se o computador estiver **offline**, diga ao usuário e peça para ele ligar o computador ou verificar a internet; não tente de novo em seguida.

## Como chamar (obrigatório, nesta ordem)
As credenciais dos computadores chegam como variáveis de ambiente **só quando você as lista** no parâmetro `secrets` do `rt_env_exec` (o valor nunca aparece para você). No catálogo de credenciais da conversa há os nomes `PC_HOSTS` e, para cada computador, `PC_<NOME>_KEY`, `PC_<NOME>_HUB`, `PC_<NOME>_PORT`, `PC_<NOME>_TOKEN` e `PC_<NOME>_OS`. **Liste todos eles em `secrets` em toda chamada de `luar-pc`.**

1. **Instalar a ferramenta (uma vez por ambiente).** Rode no `rt_env_exec`:
   `mkdir -p ~/bin && curl -fsSL https://conector.rangeltech.net/dl/luar-pc -o ~/bin/luar-pc && curl -fsSL https://conector.rangeltech.net/dl/luar-relay.py -o ~/bin/luar-relay.py && chmod +x ~/bin/luar-pc ~/bin/luar-relay.py`
   Depois use sempre `python3 ~/bin/luar-pc ...`.
2. **Listar o que existe:** `python3 ~/bin/luar-pc hosts` (com `secrets` = todos os `PC_*`).
3. Se o computador estiver **offline**, avise o usuário e pare.

## Ferramenta
Use sempre a CLI `luar-pc` (`python3 ~/bin/luar-pc`) (ela cuida das aspas, dos acentos e das barras do Windows):

| Comando | Para que serve |
|---|---|
| `luar-pc ls NOME "<pasta>"` | listar uma pasta (aceita `Desktop`, `Área de Trabalho`, `Documentos`, `Downloads`) |
| `luar-pc find NOME "<padrão>"` | procurar arquivo por nome |
| `luar-pc py NOME script.py` | **rodar um script Python no computador** (o arquivo `script.py` fica na nuvem; só o texto do script vai ao computador) |
| `luar-pc run NOME "<comando>"` | rodar um comando do sistema (PowerShell no Windows, bash no Linux) |
| `luar-pc pull NOME <caminho> [destino]` | copiar um arquivo do computador para o ambiente da empresa (só quando for realmente necessário) |
| `luar-pc push NOME <arquivo> <caminho>` | enviar um arquivo do ambiente para o computador |
| `luar-pc pip NOME <pacote>` | instalar uma biblioteca Python extra no ambiente isolado da LUAR (nunca no Python do cliente) |
| `luar-pc kit NOME status` | ver a versão do Python do computador |

## Regra de ouro: o arquivo não sai do computador
1. **Prefira executar Python no próprio computador** (`luar-pc py`). O arquivo é lido e editado lá. O que volta para você é **texto curto**: a estrutura, um resumo, um erro.
2. **Nunca imprima o arquivo inteiro.** Mostre cabeçalho, nomes de colunas, contagem de linhas e, no máximo, 3 linhas de exemplo. Resultados grandes viram resumo (contagens, somas, valores distintos).
3. **Não instale nada** no computador do cliente além do que o kit da LUAR já traz (`luar-pc pip` instala só no ambiente da LUAR).
4. Use `luar-pc pull` somente se o computador for lento, faltar biblioteca pesada ou o usuário pedir. Depois **apague a cópia** do ambiente.

## Ciclo para editar um arquivo (exemplo: planilha Excel)
Pedido: "no meu computador tem um Excel na Área de Trabalho; tire a coluna S e adicione uma coluna 'Valor Total' no final".

1. **Achar o arquivo:** `luar-pc ls NOME Desktop` (ou `find`). Se houver mais de um candidato, **pergunte** qual.
2. **Inspecionar (só leitura):** escreva um script que abre o arquivo em modo leitura e **imprime só a estrutura**: nomes das abas, dimensões, a linha de cabeçalho com a **letra e o nome de cada coluna**, quantidade de linhas, quantidade de fórmulas e até 3 linhas de exemplo (cortadas em 40 caracteres por célula). Limite a saída a ~8 KB.
3. **Entender e confirmar:** compare o que o usuário pediu com a estrutura. Se o pedido for ambíguo (por exemplo, "valor total" de quê?), **pergunte antes de alterar**. Diga em uma frase o que vai fazer.
4. **Escrever o script de edição**, que:
   - faz **primeiro** uma **cópia de segurança** ao lado do arquivo (`nome.bak-AAAAMMDD-HHMM.xlsx`) e nunca sobrescreve uma cópia anterior;
   - faz a alteração (remover a coluna, acrescentar o cabeçalho e a fórmula ou os valores);
   - **salva** e **reabre** o arquivo para conferir (cabeçalhos finais, número de linhas, ausência de erros);
   - imprime um **resumo curto** do que fez.
5. **Executar:** `luar-pc py NOME edit.py`. Se der erro, leia o **texto do erro** que voltou, **corrija o script** e execute de novo (até 5 tentativas). Como a cópia de segurança já existe, repetir é seguro; o script deve ser **idempotente** (se a coluna já foi removida, não remove outra).
6. **Verificar:** rode de novo o script de inspeção e confira o resultado.
7. **Responder** ao usuário em português simples: o que mudou, quantas linhas, **onde ficou a cópia de segurança** e como desfazer.

## Cuidados com Excel
- **Arquivo aberto no Excel** fica travado para escrita: se aparecer erro de permissão, peça ao usuário para **fechar o arquivo** e tente de novo. No Windows, se ele quiser trabalhar com o arquivo aberto, use o Excel por COM (`pywin32`).
- `openpyxl` **não preserva** tabelas dinâmicas, alguns gráficos e imagens complexas; macros só com `keep_vba=True`. Se o arquivo tiver esses recursos, **avise** e prefira o Excel por COM ou trabalhe em cópia.
- Fórmulas escritas com `openpyxl` **não têm valor calculado** até o Excel abrir o arquivo. Se precisar mostrar o resultado, calcule também em Python e diga isso.
- **OneDrive "sob demanda":** o arquivo pode aparecer sem estar no disco; abrir força o download e pode demorar. Avise e espere.
- CSV brasileiro costuma ser `;` com codificação Windows-1252: confirme antes de ler.

## Windows e Linux
- **Windows:** caminhos com `\`, acentos e espaços são normais; use sempre `luar-pc` (não monte aspas à mão). Pastas como `AppData` e `Windows` são protegidas: se negar, **informe** e não tente contornar.
- **Linux:** respeite maiúsculas e minúsculas nos nomes.
- Comando longo: rode **desanexado** e acompanhe o registro; não segure a conversa esperando.

## Chrome do cliente (CDP)
- Abra o Chrome com **o perfil da LUAR** (`--user-data-dir`), nunca com o perfil padrão (o Chrome ignora a depuração nele). Use a porta de depuração por dentro do computador (`luar-pc py`) ou por túnel.
- Se o usuário quiser **ver** o Chrome trabalhando, avise que ele precisa estar **conectado ao Windows** (sessão aberta); sem isso o Chrome roda em segundo plano, sem janela.
- O usuário entra uma vez nos sites no perfil da LUAR; a sessão fica salva.

## O que dizer ao usuário
Português simples, sem jargão. Conte o que fez, onde estão as cópias de segurança e o que ele pode pedir em seguida. Nunca mostre chaves, senhas nem o conteúdo completo de arquivos.
