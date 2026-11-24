# Origens da integração — 08/11/2026

- RAG/LLM: [GrowUp-Pessoa4](https://github.com/Cauarichard018/GrowUp-Pessoa4/tree/26ec508ac06f907770cb09f188692205a7804b0f), commit `26ec508ac06f907770cb09f188692205a7804b0f`.
  O repositório original continha pipeline, montagem de contexto, prompt e cliente de LLM, mas importava um módulo de busca que não acompanha o repositório. Nesta aplicação a busca é fornecida por `rag/busca_chromadb.py`, conectada ao ChromaDB remoto.
  `rag/contexto.py` preserva a montagem de contexto e `rag/prompts.py`, as instruções. `rag/pipeline.py` preserva o fluxo recuperar/contextualizar/gerar e converte as fontes para o contrato existente. A geração usa a fila de LLMs NVIDIA (`rag/cliente_llm.py`); a integração original com o Gemini foi removida em 23/11/2026.
- Qiskit: [GrowUP---Qiskit](https://github.com/Guilherme-Ximenes/GrowUP---Qiskit/tree/b148e235abca4c02cfc2c01468c7c6a35f642134), commit `b148e235abca4c02cfc2c01468c7c6a35f642134`.
  `quantum/qiskit_service.py` foi incorporado sem mudanças na lógica científica (apenas espaços finais foram removidos). Os testes originais foram incorporados com ajuste apenas do caminho de importação. `quantum/api.py` e `quantum/worker.py` acrescentam contrato HTTP, concorrência limitada e processo encerrável por timeout. O serviço usa transpiler e Operator reais do Qiskit 2.6.0; não precisa de Aer nem credenciais IBM.
- ChromaDB: código da pasta da workspace `GROWUP - ChromaDB` conferido com a implementação remota. Mesma coleção `qiskit_documentacao_multilingue`, modelo multilíngue MiniLM-L12-v2, embeddings normalizados e metadados documentais. O snapshot textual é idêntico byte a byte ao da pasta atual: SHA-256 `E72D0B83DC245E9E1FDF099852518F6A38B6DF2A16300DE9FE8DCD2A16B2A191`. Nenhum arquivo do banco local foi aberto ou copiado.

A integração não requer um terceiro serviço Docker: Qiskit, qiskit-aer, FastAPI e cliente ChromaDB coexistem na imagem Python 3.12. O servidor ChromaDB permanece separado e usa somente um volume vetorial.

A fila de LLMs NVIDIA é um provedor externo real e opcional. Sem credencial, o serviço continua respondendo com a base local (resposta extrativa), além de recuperação documental e Qiskit.
