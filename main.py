"""API inicial. Executar: python -m uvicorn main:app"""
import asyncio
import json
import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from pydantic import ValidationError
from quantum.errors import CircuitError
from quantum.http import CircuitBodyLimit, error_response
from contratos import (Consulta, Busca, ResultadoConsulta, ResultadoBusca, Documento,
    RespostaConsulta, RespostaBusca, RespostaDocumento, ListaDocumentos, Saude,
    Avaliacao, RespostaAvaliacao, Erro)
from servicos import ServicosIntegrados, FalhaServico, LLMNaoConfigurada, DocumentoInvalido

servicos = ServicosIntegrados()
TEMPO_LIMITE = float(os.getenv('API_TIMEOUT_SECONDS', '60'))
ARQUIVO_AVALIACOES = Path(os.getenv('AVALIACOES_PATH', 'data/avaliacoes.jsonl'))
LIMITE_ARQUIVO = 256 * 1024


@asynccontextmanager
async def lifespan(app):
    await asyncio.to_thread(servicos.inicializar)
    from rag.sincronizar_conhecimento import agendar
    tarefa = asyncio.create_task(agendar()) if os.getenv("KNOWLEDGE_AUTO_SYNC", "0") == "1" else None
    try:
        yield
    finally:
        if tarefa:
            tarefa.cancel()
            from contextlib import suppress
            with suppress(asyncio.CancelledError):
                await tarefa

ERROS = {n: {'model': Erro, 'description': descricao} for n, descricao in {
    400:'Requisição malformada', 404:'Rota inexistente', 405:'Método incorreto',
    413:'Arquivo acima do limite', 415:'Tipo de conteúdo não aceito',
    422:'Entrada inválida', 500:'Falha interna', 502:'Resposta externa inválida',
    503:'Serviço externo indisponível', 504:'Tempo limite excedido'}.items()}
app = FastAPI(title='GrowUp - API integrada', version='0.4.0', responses=ERROS,
    lifespan=lifespan,
    description='API do QuantumOne: RAG híbrido local (ChromaDB + BM25), resposta por fila de LLMs NVIDIA (opcional) ou extrativa local, '
                'diagnóstico de entradas e otimização Qiskit verificada. Sem busca web.')
app.openapi_version = '3.1.0'

def erro(request, status, code, message, fields=None):
    return JSONResponse(status_code=status, content={
        'request_id': request.state.request_id, 'code': code,
        'message': message, 'fields': fields or []})

@app.middleware('http')
async def identificar_chamada(request: Request, call_next):
    request.state.request_id = str(uuid4())
    esperado = 'multipart/form-data' if request.url.path == '/documents' else 'application/json'
    recebido = request.headers.get('content-type', '').split(';')[0].strip().lower()
    if request.method == 'POST' and recebido != esperado:
        if request.url.path in ('/qiskit/optimize', '/qiskit/diagnose'):
            resposta = error_response(request.state.request_id,
                CircuitError('CONTENT_TYPE', f'Use Content-Type: {esperado}.', {'stage': 'transport'}, 415))
        else:
            resposta = erro(request,415,'CONTENT_TYPE',f'Use Content-Type: {esperado}.')
    else:
        try:
            resposta = await call_next(request)
        except Exception:
            resposta = erro(request,500,'INTERNAL_ERROR','Não foi possível concluir a operação.')
    resposta.headers['X-Request-ID'] = request.state.request_id
    return resposta

@app.exception_handler(RequestValidationError)
async def entrada_invalida(request, exc):
    fields = ['.'.join(map(str,e['loc'])) for e in exc.errors()]
    if request.url.path in ('/qiskit/optimize', '/qiskit/diagnose'):
        malformed = any(e['type'] == 'json_invalid' for e in exc.errors())
        failure = CircuitError('INVALID_JSON' if malformed else 'INVALID_REQUEST',
            'JSON malformado.' if malformed else 'Confira os campos e tipos da requisição.',
            {'stage': 'request', 'fields': fields,
             'issues': [{'field': '.'.join(map(str, e['loc'])), 'reason': e['msg'], 'type': e['type']} for e in exc.errors()]},
            400 if malformed else 422)
        logging.getLogger(__name__).warning('qiskit request_id=%s stage=request code=%s fields=%s', request.state.request_id, failure.code, fields)
        return error_response(request.state.request_id, failure, fields)
    return erro(request,422,'INVALID_INPUT','Confira os campos, tipos e limites.',fields)

@app.exception_handler(CircuitError)
async def circuito_invalido(request, exc):
    logging.getLogger(__name__).warning('qiskit request_id=%s stage=domain code=%s', request.state.request_id, exc.code)
    return error_response(request.state.request_id, exc)

@app.exception_handler(HTTPException)
async def erro_http(request, exc):
    mensagens = {400:'Requisição ou formulário inválido.',404:'Rota não encontrada.',405:'Método não permitido.',
                 413:'Arquivo maior que 256 KiB.',415:'Use arquivo .txt, .md, .py ou .qasm em UTF-8.',
                 422:'Arquivo vazio, binário ou fora da codificação UTF-8.'}
    if request.url.path in ('/qiskit/optimize', '/qiskit/diagnose'):
        return error_response(request.state.request_id,
            CircuitError('INVALID_REQUEST', 'Requisição inválida.', {'stage': 'request'}, exc.status_code))
    return erro(request,exc.status_code,'HTTP_ERROR',exc.detail if request.url.path.startswith('/conversations') and isinstance(exc.detail, str)
                 else mensagens.get(exc.status_code,'Falha na requisição.'))

@app.exception_handler(FalhaServico)
async def servico_indisponivel(request, exc):
    return erro(request,exc.status,'SERVICE_ERROR','O serviço externo não conseguiu atender à solicitação.')

@app.exception_handler(LLMNaoConfigurada)
async def llm_ausente(request, exc):
    return erro(request,503,'LLM_NOT_CONFIGURED',
                'A LLM ainda não foi configurada e a resposta local está desativada (QONE_LOCAL_FALLBACK=0). Use POST /search para recuperar trechos e fontes; nenhuma resposta de IA foi gerada.')

@app.exception_handler(DocumentoInvalido)
async def documento_invalido(request, exc):
    return erro(request,422,'INVALID_DOCUMENT',
                'Não foi possível dividir o documento no limite de tokens. Revise blocos ou identificadores muito longos.')

@app.exception_handler(asyncio.TimeoutError)
async def tempo_excedido(request, exc):
    return erro(request,504,'TIMEOUT','O serviço externo excedeu o tempo limite.')

@app.exception_handler(ValidationError)
async def contrato_externo_invalido(request, exc):
    return erro(request,502,'INVALID_SERVICE_RESPONSE','O serviço externo retornou dados fora do contrato.')

async def chamar(corrotina):
    return await asyncio.wait_for(corrotina, timeout=TEMPO_LIMITE)

@app.get('/health', response_model=Saude)
async def health():
    """Confirma que a API responde; não verifica os serviços externos."""
    return Saude(mode=servicos.mode)

@app.post('/query', response_model=RespostaConsulta)
async def query(entrada: Consulta, request: Request):
    """Consulta somente a base local (RAG híbrido). Com NVIDIA_API_KEY, a fila de LLMs redige a resposta a partir
    dos trechos; sem chave, ou se nenhum modelo responder a tempo, a resposta é extrativa local (answer_mode=local).
    Nenhuma busca web é executada."""
    if entrada.web_search is None:
        chamada = servicos.consultar(entrada.input) if entrada.top_k == 3 else servicos.consultar(entrada.input, entrada.top_k)
    else:
        chamada = servicos.consultar(entrada.input, entrada.top_k, entrada.web_search)
    resultado = ResultadoConsulta.model_validate(await chamar(chamada))
    return RespostaConsulta(**resultado.model_dump(), request_id=request.state.request_id, mode=servicos.mode)

@app.post('/search', response_model=RespostaBusca)
async def search(entrada: Busca, request: Request):
    """Recupera seções e fontes no ChromaDB. Resultados não garantem suficiência do contexto."""
    resultado = ResultadoBusca.model_validate(await chamar(servicos.buscar(entrada.query) if entrada.top_k == 3 else servicos.buscar(entrada.query, entrada.top_k)))
    return RespostaBusca(**resultado.model_dump(), request_id=request.state.request_id, mode=servicos.mode)

@app.post('/documents', response_model=RespostaDocumento, status_code=201)
async def enviar_documento(file: UploadFile = File(...)):
    """Valida texto UTF-8 e indexa os trechos na mesma coleção usada pela busca."""
    try:
        conteudo = await file.read(LIMITE_ARQUIVO+1)
        nome = (file.filename or '').replace('\\','/').split('/')[-1]
        if len(conteudo) > LIMITE_ARQUIVO:
            raise HTTPException(413)
        if Path(nome).suffix.lower() not in ('.txt','.md','.py','.qasm'):
            raise HTTPException(415)
        try:
            texto = conteudo.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise HTTPException(422)
        if not texto.strip() or '\x00' in texto:
            raise HTTPException(422)
        resultado = Documento.model_validate(await chamar(servicos.adicionar_documento(nome,conteudo)))
        return RespostaDocumento(**resultado.model_dump(),mode=servicos.mode)
    finally:
        await file.close()

@app.get('/documents', response_model=ListaDocumentos)
async def listar_documentos():
    """Solicita ao serviço documental a lista de documentos disponíveis."""
    documentos = await chamar(servicos.listar_documentos())
    if not isinstance(documentos,list):
        raise FalhaServico(status=502)
    return ListaDocumentos(documents=[Documento.model_validate(d) for d in documentos],mode=servicos.mode)

@app.post('/evaluate', response_model=RespostaAvaliacao, status_code=201)
async def avaliar(entrada: Avaliacao):
    """Registra notas humanas e calcula média; não avalia automaticamente a IA."""
    media = sum(n.score for n in entrada.ratings) / 5
    # Proposta operacional: zero em critério técnico/crítico exige revisão.
    critica = entrada.critical_failure or any(n.score == 0 and n.criterion in
                                             ('coerencia','aderencia','falhas') for n in entrada.ratings)
    resultado = RespostaAvaliacao(**{**entrada.model_dump(), 'critical_failure': critica,
        'critical_evidence': entrada.critical_evidence or ('Regra automática: nota zero em critério técnico/crítico.' if critica else None)},
        id=uuid4(), created_at=datetime.now(timezone.utc).isoformat(), mean=media,
        approved=media >= 4 and not critica,
        result='revisao_obrigatoria' if critica else ('aprovado' if media >= 4 else 'reprovado'))
    # Registro simples exigido pela avaliação: uma linha JSON por avaliação, sem banco adicional.
    ARQUIVO_AVALIACOES.parent.mkdir(parents=True, exist_ok=True)
    with ARQUIVO_AVALIACOES.open('a',encoding='utf-8') as arquivo:
        arquivo.write(resultado.model_dump_json()+'\n')
    return resultado

from quantum.api import router as quantum_router
app.include_router(quantum_router)
app.add_middleware(CircuitBodyLimit)


# Sessões usadas pelo chat web e pelo futuro cliente CLI.
from uuid import UUID
from rag import conversas


class MensagemConversa(Consulta):
    message_id: UUID


@app.post("/conversations", status_code=201)
async def criar_conversa():
    return {"conversation_id": conversas.criar()}


@app.get("/conversations/{conversation_id}")
async def obter_conversa(conversation_id: UUID):
    _, mensagens = conversas.ler(str(conversation_id))
    return {"conversation_id": conversation_id, "messages": mensagens}


@app.post("/conversations/{conversation_id}/messages")
async def enviar_mensagem(
    conversation_id: UUID, entrada: MensagemConversa, request: Request
):
    identificador = str(conversation_id)
    versao, mensagens = conversas.ler(identificador)
    # Uma repetição após perda de conexão devolve a resposta já persistida.
    for indice, mensagem in enumerate(mensagens):
        if mensagem.get("id") == str(entrada.message_id):
            if mensagem["content"] != entrada.input:
                raise HTTPException(409, "Identificador de mensagem já utilizado.")
            return mensagens[indice + 1]["result"]

    pergunta = conversas.preparar(mensagens, entrada.input)
    resultado = ResultadoConsulta.model_validate(await chamar(
        servicos.consultar(pergunta, entrada.top_k, entrada.web_search, consulta=entrada.input)
    ))
    resposta = {
        **resultado.model_dump(mode="json"),
        "conversation_id": identificador,
        "request_id": request.state.request_id,
        "mode": servicos.mode,
    }
    atualizadas = mensagens + [
        {"id": str(entrada.message_id), "role": "user", "content": entrada.input},
        {"role": "assistant", "content": resultado.response, "result": resposta},
    ]
    conversas.registrar(identificador, versao, atualizadas)
    return resposta


# Análise unificada: texto, código, arquivos e pastas em uma única chamada.
from contratos_analise import EntradaAnalise, EntradaProposta, ProgressoAnalise, RespostaAnalise, RespostaProposta
from rag import progresso as progresso_analise

TEMPO_ANALISE = float(os.getenv('QONE_ANALISE_TIMEOUT_SECONDS', '900'))


@app.post('/analise', response_model=RespostaAnalise)
async def analisar_envio(entrada: EntradaAnalise, request: Request):
    """Interpreta e analisa qualquer combinação de texto, código e arquivos (inclusive pastas de projeto):
    Qiskit diagnostica e otimiza os circuitos, a fila de LLMs corrige o código inválido (revalidado pelo Qiskit),
    planeja as buscas, consulta a base local primeiro e a internet como complemento, e redige a resposta.
    Acompanhe a etapa e o modelo em uso por GET /analise/progresso/{progress_id}."""
    import orquestrador
    resultado = await asyncio.wait_for(orquestrador.analisar(entrada, request.state.request_id), TEMPO_ANALISE)
    return RespostaAnalise.model_validate(resultado)


@app.get('/analise/progresso/{progress_id}', response_model=ProgressoAnalise)
async def progresso_do_envio(progress_id: UUID):
    """Etapa atual e modelo de LLM solicitado no momento (texto ao vivo da interface)."""
    estado = progresso_analise.obter(progress_id)
    if estado is None:
        raise HTTPException(404, 'Progresso não encontrado.')
    return ProgressoAnalise.model_validate(estado)


@app.post('/assistente/proposta', response_model=RespostaProposta)
async def propor_edicao(entrada: EntradaProposta, request: Request):
    """Proposta de edição para a CLI (`qone /propor`): devolve um JSON com plano, riscos, verificação e
    edições por intervalo de linhas. Nada é gravado aqui; a CLI mostra o diff e pede autorização.
    O andamento (modelo em uso) aparece em GET /analise/progresso/{progress_id}."""
    import orquestrador
    from rag import cliente_llm
    if not cliente_llm.configurada() or os.getenv('QONE_LLM', 'auto').lower() == 'local':
        return erro(request, 503, 'LLM_NOT_CONFIGURED',
                    'Propostas de edição precisam da fila de LLMs (NVIDIA_API_KEY) e QONE_LLM diferente de local.')
    try:
        resultado = await asyncio.wait_for(orquestrador.propor_edicao(entrada, request.state.request_id), TEMPO_ANALISE)
    except cliente_llm.FilaEsgotada:
        return erro(request, 503, 'LLM_UNAVAILABLE', 'Nenhum modelo da fila respondeu; tente novamente em instantes.')
    return RespostaProposta.model_validate(resultado)
