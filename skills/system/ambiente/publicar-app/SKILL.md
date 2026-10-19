---
name: publicar-app
description: Publicar um serviço do ambiente na internet com endereço próprio da empresa (subdomínio ou domínio do cliente), com HTTPS.
---

# Publicar um app

Pré-requisito: o serviço já roda no ambiente (skill `servicos-no-ambiente`) escutando em `0.0.0.0:<porta>`.

## Subdomínio automático
1. Escolha um nome curto (minúsculas e números, ex.: `metabase`).
2. Publique direto (o usuário já pediu) e **avise** que o link fica acessível a qualquer pessoa, sugerindo login próprio se o app mostra dados da empresa.
3. Chame `rt_env_expose` com `app` (nome) e `port` (porta do serviço).
4. O endereço fica `https://<app>-<empresa>.apps.rangeltech.net` com certificado automático. Entregue o link ao usuário.
5. Se o app está desligado quando alguém abre o link, a plataforma o acorda sozinha na primeira visita (pode levar alguns segundos).

## Domínio próprio do cliente
1. `rt_env_domain_add` com `host` (ex.: `vendas.minhaempresa.com.br`) e `app`.
2. A ferramenta devolve **dois registros DNS** para o cliente criar no provedor: um `TXT` de verificação e um `CNAME` apontando para o endereço da plataforma.
3. Depois que o cliente criar, chame `rt_env_domain_verify`. Só então o domínio fica ativo. Nunca diga que está no ar antes de verificar.

## Despublicar
- `rt_env_unexpose` com o nome do app. O serviço continua rodando internamente.

## Segurança
- Apps que mostram dados da empresa (dashboards, Metabase, Airflow) devem ter **login próprio**. Avise o usuário e configure senha forte antes de publicar.
- Nunca publique painéis administrativos com a senha padrão.
