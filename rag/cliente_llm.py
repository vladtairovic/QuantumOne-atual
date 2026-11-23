"""Fila de prioridade de LLMs via API compatível com OpenAI (NVIDIA NIM por padrão).

Cada modelo da fila recebe a mesma pergunta com os trechos locais e tem um
tempo máximo rígido (``LLM_TIMEOUT_SECONDS``, padrão 10 s). Se não responder a
tempo, responder vazio ou falhar, a fila passa para o próximo. Um modelo que
falha é pausado por ``LLM_PAUSA_SEGUNDOS`` (disjuntor), para que as próximas
perguntas não esperem por ele de novo. Nenhuma ferramenta de busca é enviada:
o modelo só vê a pergunta e os trechos da base local.

Ordem padrão (do mais capaz ao mais fraco), medida em 23/11/2026 com um
prompt real do RAG e limite de 10 s (ver docs/LLM.md):
1. nvidia/nemotron-3-ultra-550b-a55b
2. nvidia/nemotron-3-nano-omni-30b-a3b-reasoning
3. poolside/laguna-xs-2.1
4. nvidia/nemotron-3.5-bolt-e4b
5. nvidia/ising-calibration-1.5-31b
"""
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturoTimeout

import httpx

from .prompts_gemini import INSTRUCOES

log = logging.getLogger(__name__)

MODELOS_PADRAO = (
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "poolside/laguna-xs-2.1",
    "nvidia/nemotron-3.5-bolt-e4b",
    "nvidia/ising-calibration-1.5-31b",
)
_PAUSADOS = {}          # modelo -> instante (monotonic) até quando fica pausado
_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="qone-llm")
_PENSAMENTO = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class FilaEsgotada(RuntimeError):
    """Nenhum modelo da fila respondeu; ``tentativas`` explica cada falha."""

    def __init__(self, tentativas):
        super().__init__("Nenhum modelo da fila respondeu.")
        self.tentativas = tentativas


def configurada() -> bool:
    return bool(os.getenv("NVIDIA_API_KEY", "").strip())


def fila() -> list[str]:
    bruto = os.getenv("NVIDIA_MODELS", "").strip()
    modelos = [m.strip() for m in bruto.split(",") if m.strip()] if bruto else list(MODELOS_PADRAO)
    return list(dict.fromkeys(modelos))


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


def _chamar(cliente, url, chave, modelo, mensagens, max_tokens):
    resposta = cliente.post(url, headers={"Authorization": f"Bearer {chave}", "Accept": "application/json"},
                            json={"model": modelo, "messages": mensagens, "max_tokens": max_tokens,
                                  "temperature": 0.2})
    resposta.raise_for_status()
    mensagem = resposta.json()["choices"][0]["message"]
    texto = _PENSAMENTO.sub("", mensagem.get("content") or "").strip()
    return texto


def gerar_resposta(pergunta: str, contexto: str, transporte=None) -> dict:
    """Percorre a fila e devolve {texto, modelo, tentativas}. Lança FilaEsgotada."""
    chave = os.getenv("NVIDIA_API_KEY", "").strip()
    if not chave:
        raise RuntimeError("NVIDIA_API_KEY não configurada.")
    base = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    limite = float(os.getenv("LLM_TIMEOUT_SECONDS", "10"))
    pausa = float(os.getenv("LLM_PAUSA_SEGUNDOS", "300"))
    orcamento_total = float(os.getenv("LLM_TEMPO_TOTAL_SECONDS", "45"))
    max_tokens = int(os.getenv("LLM_MAX_TOKENS", "2000"))
    mensagens = [
        {"role": "system", "content": INSTRUCOES.strip()},
        {"role": "user", "content": f"<documentacao>\n{contexto}\n</documentacao>\n\n<pergunta>\n{pergunta}\n</pergunta>\n\n"
                                    "Responda à pergunta usando a documentação e cite [Fonte N]."},
    ]
    tentativas = []
    inicio_total = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(limite + 2, connect=min(5.0, limite)), transport=transporte) as cliente:
        for modelo in fila():
            if _pausado(modelo):
                tentativas.append({"modelo": modelo, "resultado": "pausado", "segundos": 0.0})
                continue
            restante = orcamento_total - (time.monotonic() - inicio_total)
            if restante <= 1:
                tentativas.append({"modelo": modelo, "resultado": "sem_tempo", "segundos": 0.0})
                continue
            prazo = min(limite, restante)
            inicio = time.monotonic()
            futuro = _EXECUTOR.submit(_chamar, cliente, f"{base}/chat/completions", chave, modelo, mensagens, max_tokens)
            try:
                texto = futuro.result(timeout=prazo)
            except FuturoTimeout:
                resultado, pausar_por = f"tempo_esgotado_{prazo:g}s", pausa
            except httpx.HTTPStatusError as erro:
                codigo = erro.response.status_code
                # 404: o modelo não está liberado para a conta; pausa longa.
                resultado, pausar_por = f"http_{codigo}", pausa * 12 if codigo == 404 else pausa
            except Exception as erro:  # rede, JSON inesperado etc.
                resultado, pausar_por = f"erro_{type(erro).__name__}", pausa
            else:
                duracao = round(time.monotonic() - inicio, 2)
                if texto:
                    tentativas.append({"modelo": modelo, "resultado": "ok", "segundos": duracao})
                    return {"texto": texto, "modelo": modelo, "tentativas": tentativas}
                resultado, pausar_por = "resposta_vazia", 0
            duracao = round(time.monotonic() - inicio, 2)
            tentativas.append({"modelo": modelo, "resultado": resultado, "segundos": duracao})
            log.warning("LLM %s falhou (%s em %.1fs); próximo da fila.", modelo, resultado, duracao)
            if pausar_por:
                _pausar(modelo, pausar_por)
    raise FilaEsgotada(tentativas)
