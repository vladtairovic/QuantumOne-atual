"""Fila de prioridade de LLMs via API compatível com OpenAI (NVIDIA NIM por padrão).

Regras da fila (pedido do projeto):
- Um modelo de cada vez, na ordem da fila. Cada um tem até ``LLM_TIMEOUT_SECONDS``
  (padrão 30 s, qualquer que seja o tamanho do prompt). Ao estourar, a requisição é
  cancelada e só então o próximo modelo é chamado, com mais 30 s próprios. Não há teto
  total (``LLM_TEMPO_TOTAL_SECONDS=0``).
- Streaming (``LLM_STREAMING=1``): o texto chega em pedaços e ``ao_parcial`` recebe o que o
  modelo já escreveu, para a interface mostrar a resposta sendo redigida. O prazo de 30 s
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
# Fila rápida para tarefas auxiliares curtas (planejar buscas): os mais rápidos primeiro.
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

    def __init__(self, tentativas):
        super().__init__("Nenhum modelo da fila respondeu.")
        self.tentativas = tentativas


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
              max_tokens=None, ao_parcial=None) -> dict:
    """Percorre a fila, um modelo por vez. Devolve {texto, modelo, tentativas}; lança FilaEsgotada.

    ``ao_parcial(modelo, texto)`` recebe o texto já escrito pelo modelo em uso (streaming)."""
    chave = os.getenv("NVIDIA_API_KEY", "").strip()
    if not chave:
        raise RuntimeError("NVIDIA_API_KEY não configurada.")
    modelos = list(modelos or fila())
    base = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    limite = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
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
            if restante <= 1:
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
                                    "Responda à pergunta usando a documentação e cite [Fonte N]."},
    ]
    return conversar(mensagens, tarefa="Resposta", ao_progresso=ao_progresso, transporte=transporte)
