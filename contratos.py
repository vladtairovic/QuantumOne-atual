"""Formatos de entrada e saída da API. Não há regras de Qiskit ou ChromaDB."""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Modelo(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class Consulta(Modelo):
    web_search: bool | None = Field(default=None, strict=True,
        description='Obsoleto: a busca web foi removida. Aceito por compatibilidade e sempre ignorado.')
    top_k: int = Field(default=3, ge=1, le=10, strict=True)
    input: Annotated[str, Field(min_length=1, max_length=30000)]

class Busca(Modelo):
    top_k: int = Field(default=3, ge=1, le=10, strict=True)
    query: Annotated[str, Field(min_length=1, max_length=2000)]

class Fonte(Modelo):
    document_id: str
    chunk_id: str
    source: str
    content: str

class FonteContexto(Fonte):
    model_config = ConfigDict(from_attributes=True)
    origin: Literal['local', 'web'] = 'local'
    title: str | None = None
    retrieved_at: str | None = None

class Interpretacao(Modelo):
    tipo_entrada: str
    componentes: list[str]
    versao_openqasm: str | None = None
    bibliotecas: list[str]
    biblioteca_principal: str | None = None
    intencao: str
    intencoes_secundarias: list[str]
    complexidade: Literal['basica', 'intermediaria', 'avancada']
    hipoteses: list[str]

class Evidencia(Modelo):
    suficiente: bool
    cobertura: float
    termos_sem_evidencia: list[str] = Field(default_factory=list)
    motivo: str | None = None

class TentativaLLM(Modelo):
    modelo: str
    resultado: str = Field(description='ok, pausado, sem_tempo, tempo_esgotado_10s, http_<código>, resposta_vazia ou erro_<tipo>.')
    segundos: float

class InfoLLM(Modelo):
    provedor: Literal['nvidia']
    modelo: str | None = Field(description='Modelo que gerou a resposta; null se nenhum respondeu.')
    tentativas: list[TentativaLLM]

class TracoRAG(Modelo):
    etapas: dict[str, float] = Field(description='Segundos gastos em cada etapa da recuperação.')
    consultas: list[str] = Field(description='Consultas realmente usadas na busca (só a original se a camada de consulta estiver desligada).')
    candidatos: int = Field(description='Quantidade de seções recuperadas.')
    camadas: dict[str, bool] = Field(description='Camadas opcionais do RAG (RAG_*) ligadas nesta resposta.')
    modelo_embeddings: str
    fallbacks: list[str] = Field(default_factory=list, description='Camadas ligadas que caíram no comportamento anterior, com o motivo.')

class ResultadoConsulta(Modelo):
    web_status: Literal['disabled'] = Field(default='disabled', description='A busca web foi removida; sempre disabled.')
    answer_mode: Literal['llm', 'local', 'none'] = Field(default='llm', description=
        'llm: redigida pela fila de LLMs NVIDIA com os trechos locais; local: extrativa, sem LLM; none: nenhuma resposta gerada.')
    interpretation: Interpretacao | None = None
    evidence: Evidencia | None = None
    llm: InfoLLM | None = None
    trace: TracoRAG | None = Field(default=None, description='Diagnóstico da recuperação; ausente em respostas antigas.')
    variantes_consulta: list[str] | None = Field(default=None, description='Presente só com RAG_CONSULTA=1.')
    verificacao: dict | None = Field(default=None, description='Presente só com RAG_VERIFICACAO=1: fidelidade e frases sem suporte.')
    warnings: list[str] = Field(default_factory=list)
    response: str
    sources: list[FonteContexto]
    status: Literal['ok', 'insufficient_context']

class RespostaConsulta(ResultadoConsulta):
    request_id: UUID
    mode: Literal['demo', 'integrated']

class ResultadoBusca(Modelo):
    chunks: list[Fonte]
    citations: list[str]
    status: Literal['ok', 'insufficient_context']

class RespostaBusca(ResultadoBusca):
    request_id: UUID
    mode: Literal['demo', 'integrated']

class Documento(Modelo):
    id: str
    filename: str

class RespostaDocumento(Documento):
    status: Literal['sucesso'] = 'sucesso'
    mode: Literal['demo', 'integrated']

class ListaDocumentos(Modelo):
    documents: list[Documento]
    mode: Literal['demo', 'integrated']

class Saude(Modelo):
    status: Literal['ok'] = 'ok'
    mode: Literal['demo', 'integrated']

class Nota(Modelo):
    criterion: Literal['pertinencia', 'coerencia', 'aderencia', 'clareza', 'falhas']
    score: int = Field(ge=0, le=5, strict=True)
    evidence: Annotated[str, Field(min_length=5, max_length=1000)]

class Avaliacao(Modelo):
    case_id: Annotated[str, Field(min_length=1, max_length=100)]
    evaluator: Annotated[str, Field(min_length=2, max_length=100)]
    ratings: list[Nota] = Field(min_length=5, max_length=5)
    critical_failure: bool = Field(default=False, strict=True)
    critical_evidence: Annotated[str, Field(min_length=5, max_length=1000)] | None = None

    @model_validator(mode='after')
    def validar(self):
        if len({n.criterion for n in self.ratings}) != 5:
            raise ValueError('Informe os cinco critérios sem repetir.')
        if self.critical_failure and not self.critical_evidence:
            raise ValueError('Descreva a evidência da falha crítica.')
        return self

class RespostaAvaliacao(Avaliacao):
    id: UUID
    created_at: str
    rubric_version: Literal['inicial-1.0'] = 'inicial-1.0'
    mean: float
    approved: bool
    result: Literal['aprovado', 'reprovado', 'revisao_obrigatoria']

class Erro(Modelo):
    request_id: UUID
    code: str
    message: str
    fields: list[str] = Field(default_factory=list)
