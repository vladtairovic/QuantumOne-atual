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
  origin?: "local";
  title?: string;
  retrieved_at?: string;
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
