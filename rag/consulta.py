"""Entendimento da consulta: reescrita autônoma e multi-query com fallback local.

``expandir_consulta`` devolve a pergunta original seguida de até 3 variantes. Sem
chave, com a fila esgotada ou com resposta inválida, a saída é a pergunta mais uma
variante determinística (termos do glossário). Nunca lança exceção.
"""
import hashlib
import json
import logging
import math
import os
import re
import threading
from collections import OrderedDict

from . import cliente_llm, traco
from .glossario import expandir
from .texto import termos_unicos

log = logging.getLogger(__name__)

MAXIMO = 4
PRAZO_PADRAO = 4.0  # segundos, por modelo e no total (RAG_CONSULTA_TIMEOUT)
PRAZO_MINIMO, PRAZO_MAXIMO = 0.5, 30.0
_CONVERSA_LONGA = 200
_CACHE_TAMANHO = 128
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_BLOCO_CODIGO = re.compile(r"```.*?(?:```|$)", re.DOTALL)
_PROMPT = (
    "Reescreva a pergunta do usuário como uma consulta de busca autônoma para uma base de "
    "documentação de computação quântica (Qiskit). Use a conversa, se houver, para resolver "
    "referências como 'isso' ou 'ele'. Mantenha identificadores técnicos exatamente como estão, "
    "acrescente uma formulação técnica em inglês e, se a pergunta for composta, divida-a em no "
    "máximo 3 consultas. Responda SOMENTE com JSON no formato {\"consultas\": [\"...\"]}."
)


def limpar_cache():
    with _CACHE_LOCK:
        _CACHE.clear()


def _prazo():
    """``RAG_CONSULTA_TIMEOUT`` em segundos: inválido vale 4,0; limitado a [0,5, 30]."""
    try:
        valor = float(os.getenv("RAG_CONSULTA_TIMEOUT", "").strip() or PRAZO_PADRAO)
    except ValueError:
        return PRAZO_PADRAO
    if not math.isfinite(valor):
        return PRAZO_PADRAO
    return min(PRAZO_MAXIMO, max(PRAZO_MINIMO, valor))


def _chave_conversa(conversa):
    """Conversas longas entram no cache pelo hash (a chave não guarda o texto inteiro)."""
    if isinstance(conversa, str) and len(conversa) > _CONVERSA_LONGA:
        return "sha1:" + hashlib.sha1(conversa.encode("utf-8")).hexdigest()
    return conversa


def _so_codigo(texto):
    """True quando, sem os blocos de código, não sobra nenhum caractere alfanumérico."""
    return not any(c.isalnum() for c in _BLOCO_CODIGO.sub("", texto))


def _unicas(pergunta, candidatas):
    saida, vistos = [pergunta], {pergunta.strip().lower()}
    for item in candidatas:
        chave = item.strip().lower()
        if chave and chave not in vistos:
            vistos.add(chave)
            saida.append(item.strip())
        if len(saida) == MAXIMO:
            break
    return saida


def _variante_local(pergunta):
    originais = termos_unicos(pergunta)
    extras = expandir(originais)
    return [" ".join(originais + extras)] if extras else []


def variantes_locais(pergunta: str) -> list[str]:
    """Saída determinística do fallback (sem LLM): a pergunta mais, se houver, uma variante do glossário."""
    return _unicas(pergunta, _variante_local(pergunta))


def _ler_json(texto):
    """Lista de consultas do JSON da resposta; ``None`` se o formato for inválido."""
    texto = re.sub(r"```(?:json)?", "", texto or "")
    inicio = texto.find("{")
    if inicio < 0:
        return None
    try:
        objeto, _ = json.JSONDecoder().raw_decode(texto[inicio:])
    except ValueError:
        return None
    lista = objeto.get("consultas") if isinstance(objeto, dict) else None
    if not isinstance(lista, list) or not lista or not all(isinstance(i, str) for i in lista):
        return None
    return lista


def _pelo_llm(pergunta, conversa, transporte):
    """Variantes devolvidas pela LLM ou ``None`` (falha ou formato inválido)."""
    usuario = f"Pergunta: {pergunta}"
    if conversa:
        usuario = f"Conversa:\n{conversa}\n\n{usuario}"
    prazo = _prazo()
    try:
        resposta = cliente_llm.conversar(
            [{"role": "system", "content": _PROMPT}, {"role": "user", "content": usuario}],
            modelos=cliente_llm.fila_rapida(), tarefa="Reescrita da consulta", transporte=transporte,
            temperatura=0.0, max_tokens=300, prazo_por_modelo=prazo, orcamento_total=prazo)
        lista = _ler_json(resposta["texto"])
        if lista is None:
            traco.registrar_fallback("consulta: resposta da LLM malformada")
        return lista
    except Exception as erro:  # FilaEsgotada, rede, formato inesperado: cai no fallback local
        log.warning("Reescrita da consulta indisponível: %s", type(erro).__name__)
        traco.registrar_fallback(f"consulta: reescrita pela LLM indisponível ({type(erro).__name__})")
        return None


def expandir_consulta(pergunta: str, conversa: str | None = None, transporte=None) -> list[str]:
    try:
        if not isinstance(pergunta, str) or not pergunta.strip() or _so_codigo(pergunta):
            return [pergunta]
        chave = (pergunta, _chave_conversa(conversa))  # o transporte nunca entra na chave
        with _CACHE_LOCK:
            if chave in _CACHE:
                _CACHE.move_to_end(chave)
                return list(_CACHE[chave])
        if cliente_llm.configurada():
            variantes = _pelo_llm(pergunta, conversa, transporte)
        else:
            variantes = None
            traco.registrar_fallback("consulta: sem LLM configurada")
        if variantes is not None:  # só resultados válidos da LLM vão para o cache
            resultado = _unicas(pergunta, variantes)
            with _CACHE_LOCK:
                _CACHE[chave] = tuple(resultado)
                while len(_CACHE) > _CACHE_TAMANHO:
                    _CACHE.popitem(last=False)
            return resultado
        return variantes_locais(pergunta)
    except Exception:  # garantia: a busca nunca falha por causa da reescrita
        log.exception("Falha inesperada na expansão da consulta")
        traco.registrar_fallback("consulta: falha inesperada na expansão")
        return [pergunta]
