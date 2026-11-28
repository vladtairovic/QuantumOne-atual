"""Coletor de fallbacks por requisição (ContextVar): as camadas registram o motivo quando
caem no comportamento anterior. Fora de um traço ativo, tudo aqui é um no-op que nunca lança."""
from contextvars import ContextVar, Token

_FALLBACKS: ContextVar[list | None] = ContextVar("rag_traco_fallbacks", default=None)


def iniciar() -> Token:
    """Abre um traço novo (lista vazia) no contexto atual."""
    return _FALLBACKS.set([])


def registrar_fallback(motivo: str) -> None:
    try:
        lista = _FALLBACKS.get()
        if lista is not None:
            lista.append(str(motivo))
    except Exception:  # noqa: BLE001 - observabilidade nunca pode quebrar a busca
        pass


def coletar() -> list[str]:
    lista = _FALLBACKS.get()
    return list(lista) if lista is not None else []
