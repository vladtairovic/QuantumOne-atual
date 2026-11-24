export type CodeFormat = "python" | "qasm";
export type AnalysisRequest = {
  question: string;
  code: string;
  format?: CodeFormat;
  backend: string;
  top_k?: number;
};
export type Source = {
  document_id: string;
  chunk_id: string;
  source: string;
  content: string;
  origin?: "local" | "web";
  title?: string | null;
  retrieved_at?: string | null;
};
export type QuantumResult = {
  original: { qubits: number; profundidade: number; total_operacoes: number };
  proposta: { qubits: number; profundidade: number; total_operacoes: number };
  equivalente: boolean | null;
  motivo: string | null;
  circuito_otimizado_qasm: string;
  formato_saida: "qasm" | null;
  formato_detectado: CodeFormat;
  tempo_processamento_segundos: number;
  warnings: string[];
  perfil?: string;
  estrategia_aplicada?: string;
  verificacao?: { metodo: string; garantia: string; equivalente: boolean | null } | null;
  analise_original?: { cx_equivalentes: number; operacoes_dois_qubits: number } | null;
  analise_final?: { cx_equivalentes: number; operacoes_dois_qubits: number } | null;
  simulacao?: { metodo: string; justificativa: string; memoria_statevector_bytes: number } | null;
  compromissos?: string[];
};
export type OptimizationProfile = "rapido" | "balanceado" | "avancado" | "experimental";
export type DiagnosisResult = {
  linguagem: CodeFormat | null;
  confianca_global: "alta" | "media" | "baixa";
  problemas: { codigo: string; linha: number | null; mensagem: string; causa_raiz: string | null;
    correcao: { descricao?: string }; alternativas: string[]; confianca: string }[];
  codigo_corrigido: string | null;
  validacao: { valido?: boolean };
  limitacoes: string[];
};
export type CircuitLimits = {
  max_circuit_lines: number;
  max_code_size_bytes: number;
  optimization_timeout_seconds: number;
};
export async function getCircuitLimits(signal: AbortSignal): Promise<CircuitLimits> {
  // Compose may restart the frontend before the API has finished loading.
  // Retry only this read; an optimization POST must never be duplicated.
  for (let attempt = 0; ; attempt++) {
    signal.throwIfAborted();
    try {
      const response = await fetch("/api/qiskit/limits", { signal, cache: "no-store" });
      if (!response.ok) throw new AnalysisError(
        "O serviço de análise está indisponível ou iniciando. Aguarde e tente novamente.", response.status);
      return await response.json();
    } catch (error) {
      signal.throwIfAborted();
      const transient = error instanceof TypeError ||
        (error instanceof AnalysisError && [500, 502, 503, 504].includes(error.status));
      if (!transient || attempt >= 6) {
        if (error instanceof TypeError) throw new AnalysisError(
          "Não foi possível conectar ao serviço de análise. Verifique a conexão e tente novamente.", 503);
        throw error;
      }
    }
    await new Promise<void>((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer);
        reject(signal.reason);
      };
      const timer = setTimeout(() => {
        signal.removeEventListener("abort", abort);
        resolve();
      }, Math.min(1000 * 2 ** attempt, 8000));
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
    });
  }
}

export function circuitContext(code: string, format: CodeFormat, metrics?: QuantumResult) {
  if (!code) return "\n\nO editor está vazio.";
  const excerpt = code.length <= 8000 ? code : code.slice(0, 4000) +
    "\n[Trecho central omitido; a análise Qiskit usou o circuito completo.]\n" + code.slice(-4000);
  return "\n\nCódigo atual (" + format + "):\n" + excerpt +
    (metrics ? "\nMétricas calculadas pelo Qiskit: " + JSON.stringify({
      original: { qubits: metrics.original.qubits, operacoes: metrics.original.total_operacoes, profundidade: metrics.original.profundidade },
      proposta: { operacoes: metrics.proposta.total_operacoes, profundidade: metrics.proposta.profundidade },
    }) : "");
}
export type AnalysisResponse = {
  conversation_id?: string;
  request_id: string;
  status: string;
  answer: string;
  sources: Source[];
  quantum?: QuantumResult;
  warnings: string[];
  web_status?: string;
  answer_mode?: "llm" | "local" | "none";
  llm?: { provedor: string; modelo: string | null; tentativas: { modelo: string; resultado: string; segundos: number }[] } | null;
  diagnosis?: DiagnosisResult;
};
export class AnalysisError extends Error {
  constructor(
    message: string,
    public status: number,
    public requestId?: string,
    public code?: string,
  ) {
    super(message);
  }
}
async function post<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<{ data: T; id: string }> {
  const res = await fetch("/api" + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok)
    throw new AnalysisError(
      (data?.error?.message || data?.message || "Serviço indisponível.") +
        (data?.error?.details?.line ? ` Linha ${data.error.details.line}.` : "") +
        (data?.error?.details?.reason && data.error.details.reason !== data.error.message
          ? ` ${data.error.details.reason}` : ""),
      res.status,
      data?.request_id,
      data?.code,
    );
  if (!data) throw new AnalysisError("Resposta inválida do serviço.", 502);
  return { data, id: res.headers.get("x-request-id") || "" };
}
export async function createConversation(signal: AbortSignal) {
  const session = await post<{ conversation_id: string }>(
    "/conversations",
    {},
    signal,
  );
  return session.data.conversation_id;
}

export async function sendTurn(
  conversationId: string,
  input: string,
  messageId: string,
  settings: { top_k: number },
  signal: AbortSignal,
): Promise<AnalysisResponse> {
  const { data, id } = await post<
    Omit<AnalysisResponse, "answer"> & { response: string }
  >(
    "/conversations/" + conversationId + "/messages",
    { input, message_id: messageId, ...settings },
    signal,
  );
  return { ...data, answer: data.response, request_id: data.request_id || id };
}

export async function diagnoseCode(code: string, signal: AbortSignal, format: CodeFormat | "auto" = "auto") {
  const { data } = await post<DiagnosisResult>("/qiskit/diagnose", { code, format }, signal);
  return data;
}

export async function optimizeCircuit(
  code: string,
  signal: AbortSignal,
  format: CodeFormat = "qasm",
  perfil: OptimizationProfile = "balanceado",
): Promise<AnalysisResponse> {
  const { data, id } = await post<QuantumResult>(
    "/qiskit/optimize",
    { code, format, optimization_level: 3, perfil },
    signal,
  );
  return {
    request_id: id,
    status: "ok",
    sources: [],
    warnings: data.warnings || [],
    quantum: data,
    answer: "O Qiskit calculou as métricas e gerou a proposta abaixo.",
  };
}

// ---------------------------------------------------------------------------
// Análise unificada: POST /analise e GET /analise/progresso/{id}
// (contrato em contratos_analise.py).
// ---------------------------------------------------------------------------

export type ArquivoEnviado = { nome: string; conteudo: string };
export type EntradaAnalise = {
  mensagem: string;
  codigo: string | null;
  formato: "auto" | CodeFormat;
  arquivos: ArquivoEnviado[];
  conversation_id: string | null;
  progress_id: string;
  perfil: OptimizationProfile;
  top_k: number;
  pesquisa_web: boolean;
};
export type TentativaLLM = { modelo: string; resultado: string; segundos: number };
export type InfoLLM = { provedor: string; modelo: string | null; tentativas: TentativaLLM[] };
export type MetricasCircuito = {
  qubits?: number;
  profundidade?: number;
  total_operacoes?: number;
  operacoes_dois_qubits?: number;
};
/** Resumo do /qiskit/optimize que a análise devolve para cada circuito válido. */
export type OtimizacaoCircuito = {
  perfil?: string;
  estrategia_aplicada?: string;
  equivalente?: boolean | null;
  verificacao?: { metodo?: string; garantia?: string; equivalente?: boolean | null } | null;
  original?: MetricasCircuito | null;
  proposta?: MetricasCircuito | null;
  cx_equivalentes_antes?: number | null;
  cx_equivalentes_depois?: number | null;
  circuito_otimizado_qasm?: string | null;
  warnings?: string[];
  simulacao?: { metodo?: string; justificativa?: string; memoria_statevector_bytes?: number } | null;
  compromissos?: string[];
};
export type CorrecaoCircuito = {
  metodo: "nenhuma" | "deterministica" | "llm";
  modelo: string | null;
  codigo: string | null;
  valido: boolean;
  explicacao: string | null;
};
export type CircuitoAnalisado = {
  origem: string;
  formato: CodeFormat | null;
  valido_original: boolean;
  problemas: number;
  diagnostico_resumo: string[];
  correcao: CorrecaoCircuito;
  otimizacao: OtimizacaoCircuito | null;
  erro: string | null;
};
export type ArquivoClassificado = {
  nome: string;
  tipo: "circuito_qasm" | "circuito_python" | "python" | "notebook" | "documento" | "dados" | "ignorado";
  tamanho: number;
  motivo: string | null;
};
export type EtapaAnalise = { texto: string; segundos: number };
export type RespostaAnalise = {
  request_id: string;
  progress_id: string | null;
  conversation_id: string | null;
  resposta: string;
  status: "ok" | "insufficient_context";
  answer_mode: "llm" | "local" | "none";
  llm: InfoLLM | null;
  sources: Source[];
  circuitos: CircuitoAnalisado[];
  arquivos: ArquivoClassificado[];
  interpretation: unknown;
  evidence: unknown;
  pesquisa_web: Record<string, unknown> | null;
  warnings: string[];
  etapas: EtapaAnalise[];
  tempo_total_segundos: number;
};
export type ProgressoAnalise = {
  progress_id: string;
  concluido: boolean;
  etapa: string;
  modelo: string | null;
  segundos_na_etapa: number;
  segundos_total: number;
  historico: EtapaAnalise[];
  /** Texto que o modelo em uso já escreveu (streaming); null fora da redação. */
  parcial: string | null;
};

/**
 * Envia texto, código e arquivos numa única chamada. Não há tempo limite no
 * cliente: a fila de LLMs pode levar minutos; só o cancelamento do usuário
 * (signal) interrompe a requisição.
 */
export async function analisar(body: EntradaAnalise, signal?: AbortSignal): Promise<RespostaAnalise> {
  const { data, id } = await post<RespostaAnalise>("/analise", body, signal);
  return { ...data, request_id: data.request_id || id };
}

/** Progresso ao vivo. Retorna null em 404 (ainda não registrado ou expirado) e em falhas de rede. */
export async function obterProgresso(progressId: string, signal?: AbortSignal): Promise<ProgressoAnalise | null> {
  try {
    const res = await fetch("/api/analise/progresso/" + encodeURIComponent(progressId), { signal, cache: "no-store" });
    if (!res.ok) return null;
    const data: unknown = await res.json().catch(() => null);
    if (!data || typeof data !== "object" || Array.isArray(data)) return null;
    const progresso = data as Partial<ProgressoAnalise>;
    if (typeof progresso.etapa !== "string") return null;
    return {
      progress_id: typeof progresso.progress_id === "string" ? progresso.progress_id : progressId,
      concluido: progresso.concluido === true,
      etapa: progresso.etapa,
      modelo: typeof progresso.modelo === "string" && progresso.modelo ? progresso.modelo : null,
      segundos_na_etapa: Number.isFinite(progresso.segundos_na_etapa) ? Number(progresso.segundos_na_etapa) : 0,
      segundos_total: Number.isFinite(progresso.segundos_total) ? Number(progresso.segundos_total) : 0,
      historico: Array.isArray(progresso.historico)
        ? progresso.historico.filter((item): item is EtapaAnalise =>
          Boolean(item) && typeof item.texto === "string" && Number.isFinite(item.segundos))
        : [],
      parcial: typeof progresso.parcial === "string" && progresso.parcial ? progresso.parcial : null,
    };
  } catch {
    return null;
  }
}
