---
name: conectar-api-externa
description: Chamar APIs de terceiros com segurança (rt_http), usando segredos guardados pela plataforma sem nunca vê-los.
---

# Conectar a uma API

Ferramenta: `rt_http` (método, URL, cabeçalhos, corpo). Ela só fala com **hosts autorizados** pela empresa.

1. Descubra o que o usuário quer integrar e peça a documentação ou o endereço base da API.
2. Se o host ainda não estiver autorizado, a chamada é recusada: explique ao usuário que um administrador precisa liberar o host nas configurações de segredos/integrações. Não tente contornar.
3. Credenciais: use o marcador `{{secret:NOME}}` nos cabeçalhos ou no corpo (ex.: `Authorization: Bearer {{secret:API_CRM}}`). A plataforma troca pelo valor real no momento da chamada. **Você nunca vê nem repete o valor.**
4. Comece com uma chamada de leitura (GET) para validar. Só faça escritas (POST/PUT/DELETE) depois de mostrar ao usuário o que será enviado e obter confirmação quando houver efeito real (cobrança, mensagem, exclusão).
5. Trate erros: leia o status e a mensagem; em 401/403 avise que a credencial precisa ser revista; em 429 espere e tente depois; não repita em laço.
6. Respostas grandes: resuma e, se preciso, salve com `rt_artifact_put`.

## Cuidados
- Nunca coloque segredo em URL, em arquivo do ambiente ou na conversa.
- Respeite limites de uso da API do terceiro.
