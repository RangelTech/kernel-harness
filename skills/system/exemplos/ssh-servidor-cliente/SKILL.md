---
name: exemplo-ssh-servidor-cliente
description: Exemplos para executar comandos e copiar arquivos em um servidor do cliente por SSH, com a chave privada vinda de variável de ambiente e apagada no fim.
---

# Exemplo: SSH no servidor do cliente

Referência, não obrigação. Segredo (em `secrets`): `SSH_KEY`, com o conteúdo da chave privada. Host e usuário o usuário informa (não são segredo).

## Padrão
1. Gravar `$SSH_KEY` em arquivo temporário com permissão `0600` (nunca em `/workspace`, que persiste).
2. Usar `ssh -i ARQUIVO -o StrictHostKeyChecking=accept-new -o BatchMode=yes`.
3. Apagar a chave ao terminar, mesmo com erro (`trap`/`finally`).

## Arquivos
- `ssh_exec.sh USUARIO@HOST "comando" [porta]`: executa um comando remoto.
- `scp_enviar.sh ORIGEM DESTINO`: copia arquivo; um dos lados é `usuario@host:caminho` (para baixar, inverta).
- `ssh_paramiko.py USUARIO HOST "comando"`: alternativa em Python, chave só em memória (`pip install --user paramiko`).

## Cuidados
- Comandos que apagam, reiniciam ou alteram o servidor do cliente: confirme na conversa antes.
- Comece por leitura (`uname -a`, `df -h`, `systemctl status`), depois altere.
- Nunca imprima a chave nem o conteúdo de arquivos de segredo do servidor.
