---
name: documentos-rag
description: Buscar trechos nos documentos da empresa e recuperar memórias relevantes antes de responder.
---

# Documentos e memória

Ferramentas: `rt_search_docs`, `rt_memory_recall`, `rt_artifact_put`.

1. Para perguntas sobre políticas, contratos, manuais ou histórico, chame `rt_search_docs` com uma consulta curta e específica antes de responder.
2. Cite de qual documento veio cada informação. Se nada relevante for encontrado, diga isso; não invente.
3. `rt_memory_recall` traz preferências e fatos que o usuário já informou; use para personalizar a resposta.
4. Para entregar um arquivo gerado (relatório, planilha), use `rt_artifact_put` e informe o nome ao usuário.
