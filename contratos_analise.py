"""Contrato da análise unificada (POST /analise) e do progresso ao vivo.

Uma única chamada aceita texto, código, arquivos soltos e pastas de projeto
(arquivos com caminho relativo), em qualquer combinação.
"""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from contratos import Evidencia, FonteContexto, InfoLLM, Interpretacao, Modelo

MAX_ARQUIVOS = 300
MAX_BYTES_ARQUIVO = 300_000
MAX_BYTES_TOTAL = 3_000_000


class ArquivoEnviado(Modelo):
    nome: Annotated[str, Field(min_length=1, max_length=500,
                               description="Nome ou caminho relativo (pastas: 'projeto/src/circuito.py').")]
    conteudo: str = Field(max_length=MAX_BYTES_ARQUIVO, description="Conteúdo em texto (UTF-8).")


class EntradaAnalise(Modelo):
    mensagem: str = Field(default="", max_length=30_000, description="Pedido em linguagem natural (opcional).")
    codigo: str | None = Field(default=None, max_length=524_288, description="Código do editor (opcional).")
    formato: Literal["auto", "qasm", "python"] = "auto"
    arquivos: list[ArquivoEnviado] = Field(default_factory=list, max_length=MAX_ARQUIVOS)
    conversation_id: UUID | None = None
    progress_id: UUID | None = Field(default=None, description="Identificador para GET /analise/progresso/{id}.")
    perfil: Literal["rapido", "balanceado", "avancado", "experimental"] = Field(
        default="avancado",
        description="Padrão: o perfil mais forte que segue exigindo verificação de equivalência e cabe no tempo da análise.")
    top_k: int = Field(default=15, ge=1, le=15, strict=True,
                       description="Máximo de fontes da base local; as fracas são descartadas pela avaliação de evidência.")
    pesquisa_web: bool = Field(default=True, strict=True,
                               description="Mantido por compatibilidade: a pesquisa na internet é sempre usada como complemento.")

    @model_validator(mode="after")
    def validar(self):
        if not self.mensagem.strip() and not (self.codigo or "").strip() and not any(a.conteudo.strip() for a in self.arquivos):
            raise ValueError("Envie uma mensagem, um código ou arquivos.")
        total = sum(len(a.conteudo.encode("utf-8")) for a in self.arquivos)
        if total > MAX_BYTES_TOTAL:
            raise ValueError(f"Os arquivos somam mais de {MAX_BYTES_TOTAL // 1_000_000} MB.")
        return self


class CorrecaoCircuito(Modelo):
    metodo: Literal["nenhuma", "deterministica", "llm"]
    modelo: str | None = None
    codigo: str | None = None
    valido: bool = False
    explicacao: str | None = None
    falhas: list[str] = Field(default_factory=list, description="Por que uma correção da LLM foi recusada pelo Qiskit.")


class CircuitoAnalisado(Modelo):
    origem: str = Field(description="'editor' ou o caminho do arquivo.")
    formato: Literal["qasm", "python"] | None = None
    valido_original: bool
    problemas: int = 0
    diagnostico_resumo: list[str] = Field(default_factory=list)
    correcao: CorrecaoCircuito
    otimizacao: dict | None = Field(default=None, description="Resumo do /qiskit/optimize sobre o código válido.")
    erro: str | None = None


class ArquivoClassificado(Modelo):
    nome: str
    tipo: Literal["circuito_qasm", "circuito_python", "python", "notebook", "documento", "dados", "ignorado"]
    tamanho: int
    motivo: str | None = None


class EtapaAnalise(Modelo):
    texto: str
    segundos: float


class RespostaAnalise(Modelo):
    request_id: UUID
    progress_id: UUID | None = None
    conversation_id: str | None = None
    resposta: str
    status: Literal["ok", "insufficient_context"]
    answer_mode: Literal["llm", "local", "none"]
    llm: InfoLLM | None = None
    sources: list[FonteContexto] = Field(description="Base local (origin=local) e internet (origin=web).")
    circuitos: list[CircuitoAnalisado] = Field(default_factory=list)
    arquivos: list[ArquivoClassificado] = Field(default_factory=list)
    interpretation: Interpretacao | None = None
    evidence: Evidencia | None = None
    pesquisa_web: dict | None = Field(default=None, description="Consultas feitas, fontes consultadas e falhas.")
    warnings: list[str] = Field(default_factory=list)
    etapas: list[EtapaAnalise] = Field(default_factory=list)
    tempo_total_segundos: float


class ProgressoAnalise(Modelo):
    progress_id: UUID
    concluido: bool
    etapa: str = Field(description="Texto ao vivo, por exemplo 'Consultando nvidia/nemotron-3-ultra-550b-a55b (1/7)'.")
    modelo: str | None = Field(default=None, description="Modelo solicitado neste momento, se houver.")
    segundos_na_etapa: float
    segundos_total: float
    historico: list[EtapaAnalise]
    parcial: str | None = Field(default=None, description="Texto que o modelo em uso já escreveu (streaming); "
                                                          "some quando o modelo muda ou a análise termina.")


class EntradaProposta(Modelo):
    """Pedido de proposta de edição da CLI: os arquivos vão numerados por linha."""
    pedido: str = Field(min_length=1, max_length=4000)
    arquivos: dict[str, str] = Field(description="Caminho relativo -> conteúdo com as linhas numeradas ('1: ...').")
    resultado_testes: str = Field(default="", max_length=8000, description="Saída dos testes locais (dado, não instrução).")
    progress_id: UUID | None = Field(default=None, description="Identificador para GET /analise/progresso/{id}.")

    @model_validator(mode="after")
    def validar(self):
        if not self.arquivos:
            raise ValueError("Envie ao menos um arquivo.")
        if len(self.arquivos) > 20:
            raise ValueError("No máximo 20 arquivos por proposta.")
        if sum(len(n) + len(c) for n, c in self.arquivos.items()) > 60_000:
            raise ValueError("Os arquivos somam mais de 60.000 caracteres; reduza a seleção.")
        return self


class RespostaProposta(Modelo):
    request_id: UUID
    response: str = Field(description="Objeto JSON da proposta: plano, riscos, verificacao e arquivos (intervalos de linhas).")
    llm: InfoLLM | None = None
    sources: list[FonteContexto] = Field(default_factory=list, description="Trechos da base local usados como referência.")
