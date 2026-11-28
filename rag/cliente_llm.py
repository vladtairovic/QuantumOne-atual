"""Fila de prioridade de LLMs via API compatível com OpenAI (NVIDIA NIM por padrão).

Regras da fila (pedido do projeto):
- Um modelo de cada vez, na ordem da fila. Cada um tem até ``LLM_TIMEOUT_SECONDS``
  (padrão 45 s, qualquer que seja o tamanho do prompt). Ao estourar, a requisição é
  cancelada e só então o próximo modelo é chamado, com mais 45 s próprios. Não há teto
  total (``LLM_TEMPO_TOTAL_SECONDS=0``).
- Streaming (``LLM_STREAMING=1``): o texto chega em pedaços e ``ao_parcial`` recebe o que o
  modelo já escreveu, para a interface mostrar a resposta sendo redigida. O prazo de 45 s
  vale para a resposta inteira.
- Sem limite artificial de tokens: ``LLM_MAX_TOKENS`` padrão 16384 (aceito pelos 7 modelos).
- Quem estoura o tempo NÃO é pausado: a próxima pergunta tenta de novo o melhor modelo.
  Só erros permanentes pausam o modelo (404/401/403 por 1 h, 429 por 60 s).
- Raciocínio interno desligado nos Nemotron (``LLM_RACIOCINIO=0``): medido em 23/11/2026,
  Super 6,4 s -> 2-3 s e Lightning 9,2 s -> 2,4 s, sem perda de correção nas respostas testadas.
- Cada modelo solicitado é anunciado por ``ao_progresso`` (texto ao vivo na interface).
Nenhuma ferramenta de busca é enviada aos modelos: o contexto (base local e resultados
da pesquisa na internet feita pelo QuantumOne) vai no próprio prompt.
"""
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturoTimeout

import httpx

from .prompts import INSTRUCOES

log = logging.getLogger(__name__)

# Fila principal: do mais capaz ao mais fraco (ver docs/LLM.md).
MODELOS_PADRAO = (
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3.5-super-vl-120b-a12b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "poolside/laguna-xs-2.1",
    "nvidia/nemotron-3.5-bolt-e4b",
    "nvidia/ising-calibration-1.5-31b",
)
# Fila rápida para tarefas auxiliares curtas (planejar buscas e reescrever a consulta): os mais rápidos primeiro.
# Só geram consultas de busca em JSON; a resposta e as correções continuam na fila principal de 7 modelos.
MODELOS_RAPIDOS = (
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3.5-bolt-e4b",
    "poolside/laguna-xs-2.1",
    "nvidia/nemotron-3.5-super-vl-120b-a12b",
)
_PAUSADOS = {}
_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="qone-llm")
_PENSAMENTO = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_PENSAMENTO_ABERTO = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL | re.IGNORECASE)
_INTERVALO_PARCIAL = 0.25  # segundos entre avisos de texto parcial


class FilaEsgotada(RuntimeError):
    """Nenhum modelo da fila respondeu; ``tentativas`` explica cada falha."""

    def __init__(self, tentativas, rodadas=1):
        super().__init__("Nenhum modelo da fila respondeu.")
        self.tentativas = tentativas
        self.rodadas = rodadas


def configurada() -> bool:
    return bool(os.getenv("NVIDIA_API_KEY", "").strip())


def _lista(variavel, padrao):
    bruto = os.getenv(variavel, "").strip()
    modelos = [m.strip() for m in bruto.split(",") if m.strip()] if bruto else list(padrao)
    return list(dict.fromkeys(modelos))


def fila() -> list[str]:
    return _lista("NVIDIA_MODELS", MODELOS_PADRAO)


def fila_rapida() -> list[str]:
    return _lista("NVIDIA_MODELS_RAPIDOS", MODELOS_RAPIDOS)


def _pausar(modelo, segundos):
    with _LOCK:
        _PAUSADOS[modelo] = time.monotonic() + segundos


def _pausado(modelo):
    with _LOCK:
        ate = _PAUSADOS.get(modelo)
        if ate is None:
            return False
        if time.monotonic() >= ate:
            _PAUSADOS.pop(modelo, None)
            return False
        return True


def limpar_pausas():
    with _LOCK:
        _PAUSADOS.clear()


def _corpo(modelo, mensagens, max_tokens, temperatura):
    corpo = {"model": modelo, "messages": mensagens, "max_tokens": max_tokens, "temperature": temperatura}
    if os.getenv("LLM_RACIOCINIO", "0") != "1" and "nemotron" in modelo:
        corpo["chat_template_kwargs"] = {"enable_thinking": False}
    return corpo


def visivel(texto):
    """Texto parcial sem blocos de raciocínio (inclusive um <think> ainda não fechado)."""
    return _PENSAMENTO_ABERTO.sub("", texto or "").lstrip()


def _ler_stream(resposta, ao_parcial):
    """Lê o SSE do chat/completions acumulando ``delta.content``."""
    partes, ultimo = [], 0.0
    for linha in resposta.iter_lines():
        if not linha.startswith("data:"):
            continue
        dado = linha[5:].strip()
        if dado == "[DONE]":
            break
        try:
            escolhas = json.loads(dado).get("choices") or []
        except ValueError:
            continue
        conteudo = (escolhas[0].get("delta") or {}).get("content") if escolhas else None
        if conteudo:
            partes.append(conteudo)
            agora = time.monotonic()
            if ao_parcial and agora - ultimo >= _INTERVALO_PARCIAL:
                ultimo = agora
                ao_parcial("".join(partes))
    return "".join(partes)


def _chamar(cliente, url, chave, corpo, ao_parcial=None):
    streaming = os.getenv("LLM_STREAMING", "1") == "1"
    if streaming:
        corpo = {**corpo, "stream": True}
    cabecalhos = {"Authorization": f"Bearer {chave}", "Accept": "text/event-stream" if streaming else "application/json"}
    with cliente.stream("POST", url, headers=cabecalhos, json=corpo) as resposta:
        if resposta.status_code >= 400:
            resposta.read()
            resposta.raise_for_status()
        if "text/event-stream" in resposta.headers.get("content-type", ""):
            texto = _ler_stream(resposta, ao_parcial)
        else:  # servidor (ou transporte de teste) que responde JSON inteiro
            resposta.read()
            texto = resposta.json()["choices"][0]["message"].get("content") or ""
    return _PENSAMENTO.sub("", texto).strip()


def conversar(mensagens, modelos=None, tarefa="Resposta", ao_progresso=None, transporte=None, temperatura=0.2,
              max_tokens=None, ao_parcial=None, prazo_por_modelo=None, orcamento_total=None) -> dict:
    """Percorre a fila, um modelo por vez. Devolve {texto, modelo, tentativas}; lança FilaEsgotada.

    ``ao_parcial(modelo, texto)`` recebe o texto já escrito pelo modelo em uso (streaming).
    ``prazo_por_modelo`` e ``orcamento_total`` (segundos) são opcionais: ``None`` mantém o
    comportamento guiado pelas variáveis de ambiente; um valor limita o prazo de cada modelo
    (``min(LLM_TIMEOUT_SECONDS, prazo_por_modelo)``) e substitui o teto total de ``LLM_TEMPO_TOTAL_SECONDS``."""
    chave = os.getenv("NVIDIA_API_KEY", "").strip()
    if not chave:
        raise RuntimeError("NVIDIA_API_KEY não configurada.")
    modelos = list(modelos or fila())
    base = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    limite = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
    if prazo_por_modelo is not None:
        limite = min(limite, prazo_por_modelo)
    # sobra mínima para ainda tentar um modelo: 1 s com o teto do ambiente (inalterado), 50 ms com teto explícito
    sobra_minima = 1 if orcamento_total is None else 0.05
    if orcamento_total is None:
        orcamento_total = float(os.getenv("LLM_TEMPO_TOTAL_SECONDS", "0"))  # 0 = sem teto
    max_tokens = max_tokens or int(os.getenv("LLM_MAX_TOKENS", "16384"))
    tentativas = []
    inicio_total = time.monotonic()
    for posicao, modelo in enumerate(modelos, start=1):
        if _pausado(modelo):
            tentativas.append({"modelo": modelo, "resultado": "pausado", "segundos": 0.0})
            continue
        prazo = limite
        if orcamento_total > 0:
            restante = orcamento_total - (time.monotonic() - inicio_total)
            if restante <= sobra_minima:
                tentativas.append({"modelo": modelo, "resultado": "sem_tempo", "segundos": 0.0})
                continue
            prazo = min(limite, restante)
        if ao_progresso:
            ao_progresso(modelo, posicao, len(modelos), tarefa)
        inicio = time.monotonic()
        # Um cliente por tentativa: ao estourar o prazo ele é fechado, cancelando a
        # requisição antes de o próximo modelo começar (um modelo de cada vez).
        cliente = httpx.Client(timeout=httpx.Timeout(prazo + 1, connect=min(5.0, prazo)), transport=transporte)
        ativo = threading.Event()
        ativo.set()

        def parcial(texto, modelo=modelo, ativo=ativo):
            # depois do prazo a thread antiga ainda pode ler um pedaço: não deixa vazar para o próximo modelo
            if ativo.is_set():
                ao_parcial(modelo, visivel(texto))

        futuro = _EXECUTOR.submit(_chamar, cliente, f"{base}/chat/completions", chave,
                                  _corpo(modelo, mensagens, max_tokens, temperatura), parcial if ao_parcial else None)
        pausar_por = 0
        try:
            texto = futuro.result(timeout=prazo)
        except FuturoTimeout:
            resultado = f"tempo_esgotado_{prazo:g}s"
        except httpx.HTTPStatusError as erro:
            codigo = erro.response.status_code
            resultado = f"http_{codigo}"
            pausar_por = 3600 if codigo in (401, 403, 404) else 60 if codigo == 429 else 0
        except Exception as erro:  # rede, JSON inesperado etc.
            resultado = f"erro_{type(erro).__name__}"
        else:
            duracao = round(time.monotonic() - inicio, 2)
            cliente.close()
            if texto:
                tentativas.append({"modelo": modelo, "resultado": "ok", "segundos": duracao})
                return {"texto": texto, "modelo": modelo, "tentativas": tentativas}
            resultado = "resposta_vazia"
        finally:
            ativo.clear()
            cliente.close()
        duracao = round(time.monotonic() - inicio, 2)
        tentativas.append({"modelo": modelo, "resultado": resultado, "segundos": duracao})
        log.warning("LLM %s: %s em %.1fs; próximo da fila.", modelo, resultado, duracao)
        if pausar_por:
            _pausar(modelo, pausar_por)
    raise FilaEsgotada(tentativas)


def gerar_resposta(pergunta: str, contexto: str, transporte=None, ao_progresso=None) -> dict:
    """Compatibilidade com /query: resposta a partir dos trechos da base local."""
    mensagens = [
        {"role": "system", "content": INSTRUCOES.strip()},
        {"role": "user", "content": f"<documentacao>\n{contexto}\n</documentacao>\n\n<pergunta>\n{pergunta}\n</pergunta>\n\n"
                                    "Responda à pergunta usando a documentação."},
    ]
    return conversar(mensagens, tarefa="Resposta", ao_progresso=ao_progresso, transporte=transporte)


# ---------------------------------------------------------------- insistência: nova rodada até conseguir

_ERROS_PERMANENTES = ("http_401", "http_403", "http_404")
_PAUSAS_ENTRE_RODADAS = (5, 10, 20, 30)  # segundos; a partir da 4ª rodada, sempre 30


def _rodada_sem_saida(tentativas):
    """True quando nenhuma nova rodada pode dar certo: todos os modelos falharam por erro permanente
    (chave inválida, modelo não liberado) ou estão pausados por um desses erros."""
    agora = time.monotonic()
    for t in tentativas:
        if t["resultado"] in _ERROS_PERMANENTES:
            continue
        if t["resultado"] == "pausado":
            with _LOCK:
                ate = _PAUSADOS.get(t["modelo"], 0)
            if ate - agora > 120:  # pausa longa = erro permanente; a de 429 (60 s) passa logo
                continue
        return False
    return bool(tentativas)


def conversar_insistente(mensagens, modelos=None, tarefa="Resposta", ao_progresso=None, transporte=None,
                         temperatura=0.2, max_tokens=None, ao_parcial=None, prazo_final=None, ao_aviso=None,
                         espera=time.sleep) -> dict:
    """Como ``conversar``, mas não desiste quando a fila inteira falha: espera um pouco e percorre a fila de
    novo (5, 10, 20 e depois 30 s entre rodadas), avisando por ``ao_aviso``, até um modelo responder.

    Para só em dois casos, lançando ``FilaEsgotada`` com todas as tentativas: o ``prazo_final``
    (``time.monotonic()``; padrão ``LLM_INSISTIR_SEGUNDOS``, 1800 s) acabaria antes da próxima rodada,
    ou todos os modelos falharam por erro permanente (nova rodada não mudaria nada).
    Cada rodada segue as regras da fila: um modelo de cada vez, cada um com o seu prazo."""
    if prazo_final is None:
        prazo_final = time.monotonic() + float(os.getenv("LLM_INSISTIR_SEGUNDOS", "1800"))
    rodada, todas = 1, []
    while True:
        restante = prazo_final - time.monotonic()
        try:
            gerado = conversar(mensagens, modelos, tarefa, ao_progresso, transporte, temperatura, max_tokens,
                               ao_parcial=ao_parcial, orcamento_total=max(1.0, restante))
        except FilaEsgotada as erro:
            todas += erro.tentativas
            pausa = _PAUSAS_ENTRE_RODADAS[min(rodada - 1, len(_PAUSAS_ENTRE_RODADAS) - 1)]
            if _rodada_sem_saida(erro.tentativas) or time.monotonic() + pausa + 5 >= prazo_final:
                raise FilaEsgotada(todas, rodadas=rodada) from erro
            log.warning("Fila de LLMs sem resposta na rodada %d; nova rodada em %d s.", rodada, pausa)
            if ao_aviso:
                ao_aviso(f"{tarefa}: nenhum modelo respondeu na rodada {rodada}; "
                         f"tentando de novo em {pausa} s (rodada {rodada + 1})")
            espera(pausa)
            rodada += 1
            continue
        gerado["tentativas"] = todas + gerado["tentativas"]
        gerado["rodadas"] = rodada
        return gerado
