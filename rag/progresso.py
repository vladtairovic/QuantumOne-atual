"""Progresso ao vivo das análises (consultado por GET /analise/progresso/{id}).

Armazenamento em memória do processo da API (uvicorn com um worker). Cada
análise registra a etapa atual e o modelo solicitado no momento; o frontend
consulta periodicamente para mostrar o texto ao vivo.
"""
import threading
import time

_TTL_SEGUNDOS = 900
_MAX_PARCIAL = 12_000  # caracteres finais do texto parcial devolvidos ao frontend
_LOCK = threading.Lock()
_ESTADOS = {}


def _limpar(agora):
    vencidos = [pid for pid, e in _ESTADOS.items() if agora - e["atualizado"] > _TTL_SEGUNDOS]
    for pid in vencidos:
        _ESTADOS.pop(pid, None)


class Rastreador:
    """Registra etapas de uma análise. ``progress_id`` None vira um rastreador mudo."""

    def __init__(self, progress_id):
        self.pid = str(progress_id) if progress_id else None
        self.inicio = time.monotonic()
        self.historico = []
        if self.pid:
            with _LOCK:
                _limpar(self.inicio)
                _ESTADOS[self.pid] = {"inicio": self.inicio, "etapa": "Recebendo o envio", "modelo": None,
                                      "inicio_etapa": self.inicio, "historico": [], "concluido": False,
                                      "atualizado": self.inicio, "parcial": None}

    def etapa(self, texto, modelo=None):
        agora = time.monotonic()
        self.historico.append({"texto": texto, "segundos": round(agora - self.inicio, 2)})
        if not self.pid:
            return
        with _LOCK:
            estado = _ESTADOS.get(self.pid)
            if estado is None:
                return
            estado.update(etapa=texto, modelo=modelo, inicio_etapa=agora, atualizado=agora, parcial=None)
            estado["historico"] = self.historico[-30:]

    def registrar(self, texto):
        """Acrescenta ao histórico sem trocar a etapa atual (tarefas que rodam em paralelo)."""
        agora = time.monotonic()
        self.historico.append({"texto": texto, "segundos": round(agora - self.inicio, 2)})
        if not self.pid:
            return
        with _LOCK:
            estado = _ESTADOS.get(self.pid)
            if estado is not None:
                estado["historico"] = self.historico[-30:]
                estado["atualizado"] = agora

    def parcial(self, modelo, texto):
        """Callback de streaming da fila: o que o modelo em uso já escreveu."""
        if not self.pid:
            return
        with _LOCK:
            estado = _ESTADOS.get(self.pid)
            if estado is not None and estado["modelo"] == modelo:
                estado["parcial"] = texto[-_MAX_PARCIAL:] if texto else None
                estado["atualizado"] = time.monotonic()

    def modelo(self, modelo, posicao, total, tarefa):
        """Callback da fila de LLMs: anuncia o modelo solicitado agora."""
        self.etapa(f"{tarefa}: consultando {modelo} ({posicao}/{total})", modelo=modelo)

    def concluir(self, texto="Análise concluída"):
        self.etapa(texto)
        if self.pid:
            with _LOCK:
                if self.pid in _ESTADOS:
                    _ESTADOS[self.pid].update(concluido=True, modelo=None, parcial=None)


def obter(progress_id):
    agora = time.monotonic()
    with _LOCK:
        estado = _ESTADOS.get(str(progress_id))
        if estado is None:
            return None
        return {"progress_id": str(progress_id), "concluido": estado["concluido"], "etapa": estado["etapa"],
                "modelo": estado["modelo"], "segundos_na_etapa": round(agora - estado["inicio_etapa"], 1),
                "segundos_total": round(agora - estado["inicio"], 1), "historico": list(estado["historico"]),
                "parcial": estado.get("parcial")}
