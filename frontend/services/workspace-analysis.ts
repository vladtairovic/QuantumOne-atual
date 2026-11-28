import { AnalysisError, analisar, obterProgresso } from "./analysis-api";
import type {
  AnalysisResponse, CircuitoAnalisado, CodeFormat, EntradaAnalise, MetricasCircuito, OptimizationProfile,
  ProgressoAnalise, RespostaAnalise,
} from "./analysis-api";
import type {
  AnalysisResultRecord, AnalyzedCircuitRecord, AnalyzedFileRecord, AttachmentRecord, CircuitMetricsRecord, ConversationRecord,
} from "../components/layout/conversationTypes";
import { isConversationRecord, RESULT_LIMITS } from "../components/security/workspaceData";
import { readLocalProfile } from "../components/profile/localProfile";

/** Converte respostas antigas (conversas e /qiskit/optimize) guardadas no histórico. */
export function report(response: AnalysisResponse): AnalysisResultRecord {
  const q = response.quantum;
  return {
    summary: response.answer,
    requestId: response.request_id,
    insufficient: response.status === "insufficient_context",
    answerMode: response.answer_mode,
    llmModel: response.llm?.modelo || undefined,
    warnings: response.warnings,
    suggestions: q?.circuito_otimizado_qasm ? [{
      title: "Circuito otimizado em OpenQASM 2",
      description: "Proposta gerada pelo transpilador do Qiskit.",
      rationale: q.motivo || "Consulte as métricas antes e depois da transpilação.",
      code: q.circuito_otimizado_qasm,
    }] : [],
    sources: response.sources.map(source => ({
      title: source.title || source.document_id, origin: source.origin,
      excerpt: source.content, url: /^https?:\/\//i.test(source.source) ? source.source : undefined,
    })),
    metrics: q && {
      depthBefore: q.original.profundidade, depthAfter: q.proposta.profundidade,
      gatesBefore: q.original.total_operacoes, gatesAfter: q.proposta.total_operacoes,
    },
    circuit: q && {
      qubits: q.proposta.qubits,
      equivalence: q.equivalente === null ? "unknown" : q.equivalente ? "equivalent" : "different",
    },
    engine: q && {
      profile: q.perfil || "nivel_fixo", strategy: q.estrategia_aplicada,
      verificationMethod: q.verificacao?.metodo, guarantee: q.verificacao?.garantia,
      cxBefore: q.analise_original?.cx_equivalentes, cxAfter: q.analise_final?.cx_equivalentes,
      twoQubitBefore: q.analise_original?.operacoes_dois_qubits, twoQubitAfter: q.analise_final?.operacoes_dois_qubits,
      memoryBytes: q.simulacao?.memoria_statevector_bytes, simulationMethod: q.simulacao?.metodo,
      simulationReason: q.simulacao?.justificativa, tradeoffs: q.compromissos?.slice(0, 5),
    },
    diagnosis: response.diagnosis && {
      language: response.diagnosis.linguagem, confidence: response.diagnosis.confianca_global,
      problems: response.diagnosis.problemas.slice(0, 300).map(p => ({
        code: p.codigo, line: p.linha, message: p.mensagem.slice(0, 2000), cause: p.causa_raiz?.slice(0, 2000) ?? null,
        fix: (p.correcao?.descricao || "").slice(0, 2000), alternatives: p.alternativas.slice(0, 5).map(a => a.slice(0, 500)),
        confidence: p.confianca,
      })),
      correctedCode: response.diagnosis.codigo_corrigido, valid: response.diagnosis.validacao?.valido,
      limitations: response.diagnosis.limitacoes.slice(0, 30),
    },
  };
}

export function readLegacyHistory(storage: Storage) {
  const records: ConversationRecord[] = [];
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i);
    if (!key?.startsWith("quantumone.chat.v1.")) continue;
    try {
      const old = JSON.parse(storage.getItem(key) || "null");
      const last = old.turns.at(-1);
      if (!last) continue;
      const file = { id: "original", name: last.format === "qasm" ? "circuit.qasm" : "main.py", language: last.format, content: last.code };
      const entry = {
        id: old.id, title: old.title, remoteId: old.remote, prompt: last.input,
        activeLanguage: old.format, backend: "reference", topK: old.topK,
        qasmCode: old.format === "qasm" ? old.code : "", pythonCode: old.format === "python" ? old.code : "",
        createdAt: new Date(old.updated).toISOString(), analyzedFileId: file.id, codeFiles: [file],
        analysisResult: report(last.result),
        previous: old.turns.slice(0, -1).map((turn: {input: string; result: AnalysisResponse}) => ({ prompt: turn.input, result: report(turn.result) })),
      };
      if (isConversationRecord(entry)) records.push(entry);
    } catch { /* Other conversations can still be restored. Keep the original storage entry. */ }
  }
  return records.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
}

export function diagnostic(error: unknown) {
  if (error instanceof AnalysisError) return error.message + (error.requestId ? " Solicitação: " + error.requestId : "");
  if (error instanceof TypeError) return "Não foi possível conectar à API.";
  return error instanceof Error ? error.message : "Não foi possível concluir a análise.";
}

export type TurnAttempt = { messageId: string; remoteId?: string };

const UUID = /^[0-9a-f-]{36}$/i;
const FILE_TYPES: AnalyzedFileRecord["type"][] = ["circuito_qasm", "circuito_python", "python", "notebook", "documento", "dados", "ignorado"];
const PROGRESS_INTERVAL_MS = 500; // curto o bastante para o texto em streaming parecer contínuo

function text(value: unknown, max: number) {
  return typeof value === "string" ? value.slice(0, max) : undefined;
}

function nullableText(value: unknown, max: number) {
  return typeof value === "string" ? value.slice(0, max) : null;
}

function count(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
}

function textList(value: unknown, maxItems: number, maxLength: number) {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string").slice(0, maxItems).map((item) => item.slice(0, maxLength))
    : [];
}

function metrics(value: MetricasCircuito | null | undefined): CircuitMetricsRecord | undefined {
  if (!value || typeof value !== "object") return undefined;
  return {
    qubits: count(value.qubits), depth: count(value.profundidade),
    operations: count(value.total_operacoes), twoQubit: count(value.operacoes_dois_qubits),
  };
}

function circuitRecord(circuit: CircuitoAnalisado): AnalyzedCircuitRecord {
  const o = circuit.otimizacao && typeof circuit.otimizacao === "object" ? circuit.otimizacao : null;
  const correction = circuit.correcao && typeof circuit.correcao === "object" ? circuit.correcao : null;
  const equivalent = o ? (typeof o.equivalente === "boolean" ? o.equivalente
    : typeof o.verificacao?.equivalente === "boolean" ? o.verificacao.equivalente : null) : null;
  return {
    origin: text(circuit.origem, RESULT_LIMITS.path) || "editor",
    format: circuit.formato === "qasm" || circuit.formato === "python" ? circuit.formato : null,
    validOriginal: circuit.valido_original === true,
    problems: Number.isSafeInteger(circuit.problemas) && circuit.problemas > 0 ? circuit.problemas : 0,
    diagnosis: textList(circuit.diagnostico_resumo, RESULT_LIMITS.diagnosisLines, RESULT_LIMITS.text),
    correction: {
      method: correction && ["nenhuma", "deterministica", "llm"].includes(correction.metodo) ? correction.metodo : "nenhuma",
      model: nullableText(correction?.modelo, RESULT_LIMITS.label),
      code: nullableText(correction?.codigo, RESULT_LIMITS.code),
      valid: correction?.valido === true,
      explanation: nullableText(correction?.explicacao, RESULT_LIMITS.longText),
    },
    optimization: o ? {
      profile: text(o.perfil, RESULT_LIMITS.label),
      strategy: text(o.estrategia_aplicada, RESULT_LIMITS.text),
      equivalent,
      verificationMethod: text(o.verificacao?.metodo, RESULT_LIMITS.label),
      guarantee: text(o.verificacao?.garantia, RESULT_LIMITS.label),
      before: metrics(o.original),
      after: metrics(o.proposta),
      cxBefore: count(o.cx_equivalentes_antes),
      cxAfter: count(o.cx_equivalentes_depois),
      optimizedQasm: text(o.circuito_otimizado_qasm, RESULT_LIMITS.code),
      warnings: textList(o.warnings, RESULT_LIMITS.diagnosisLines, RESULT_LIMITS.text),
      simulationMethod: text(o.simulacao?.metodo, RESULT_LIMITS.label),
      simulationReason: text(o.simulacao?.justificativa, RESULT_LIMITS.text),
      memoryBytes: count(o.simulacao?.memoria_statevector_bytes),
    } : undefined,
    error: nullableText(circuit.erro, RESULT_LIMITS.text),
  };
}

/**
 * Converte a resposta de POST /analise no registro guardado no histórico,
 * já recortada nos limites do validador (workspaceData.ts).
 */
export function reportAnalysis(response: RespostaAnalise): AnalysisResultRecord {
  const circuitsRaw = Array.isArray(response.circuitos)
    ? response.circuitos.filter((item) => Boolean(item) && typeof item === "object") : [];
  const circuits = circuitsRaw.slice(0, RESULT_LIMITS.circuits).map(circuitRecord);
  // O primeiro circuito (preferindo o do editor) alimenta as ferramentas Diff e Qiskit do workspace.
  const primaryRaw = circuitsRaw.find((item) => item.origem === "editor") ?? circuitsRaw[0];
  const primary = primaryRaw ? circuitRecord(primaryRaw) : undefined;
  const o = primaryRaw?.otimizacao && typeof primaryRaw.otimizacao === "object" ? primaryRaw.otimizacao : null;
  const optimization = o ? primary?.optimization : undefined;
  const suggestions: AnalysisResultRecord["suggestions"] = [];
  if (optimization?.optimizedQasm) suggestions.push({
    title: "Circuito otimizado em OpenQASM 2",
    description: "Proposta gerada pelo transpilador do Qiskit.",
    rationale: optimization.strategy || "Consulte as métricas antes e depois da transpilação.",
    code: optimization.optimizedQasm,
  });
  const corrected = primary?.correction.code;
  if (primary && corrected && corrected.trim() && corrected.trim() !== optimization?.optimizedQasm?.trim()) suggestions.push({
    title: "Código corrigido",
    description: primary.correction.method === "llm"
      ? "Correção proposta por modelo de linguagem" + (primary.correction.model ? " (" + primary.correction.model + ")." : ".")
      : "Correção determinística do diagnóstico.",
    rationale: (primary.correction.explanation ||
      (primary.correction.valid ? "O código corrigido foi validado." : "O código corrigido não foi validado.")).slice(0, RESULT_LIMITS.longText),
    code: corrected,
  });
  const llm = response.llm && typeof response.llm === "object" ? response.llm : null;
  const files = Array.isArray(response.arquivos) ? response.arquivos : [];
  const steps = Array.isArray(response.etapas) ? response.etapas : [];
  const sources = Array.isArray(response.sources) ? response.sources : [];
  const attempts = llm && Array.isArray(llm.tentativas) ? llm.tentativas : [];
  return {
    summary: String(response.resposta ?? "").slice(0, 100_000),
    requestId: text(response.request_id, 160),
    insufficient: response.status === "insufficient_context",
    answerMode: ["llm", "local", "none"].includes(response.answer_mode) ? response.answer_mode : undefined,
    llmModel: text(llm?.modelo, RESULT_LIMITS.label) || undefined,
    warnings: textList(response.warnings, 100, RESULT_LIMITS.text),
    suggestions,
    sources: sources.filter((source) => Boolean(source) && typeof source === "object").slice(0, 100).map((source) => ({
      title: text(source.title, 500) || text(source.document_id, 500) || text(source.source, 500) || "Fonte",
      origin: source.origin === "web" ? "web" : "local",
      excerpt: text(source.content, 20_000),
      url: typeof source.source === "string" && /^https?:\/\//i.test(source.source) && source.source.length <= 2_000
        ? source.source : undefined,
    })),
    metrics: optimization ? {
      depthBefore: optimization.before?.depth, depthAfter: optimization.after?.depth,
      gatesBefore: optimization.before?.operations, gatesAfter: optimization.after?.operations,
    } : undefined,
    circuit: optimization ? {
      qubits: Math.min(4096, Math.floor(optimization.after?.qubits ?? optimization.before?.qubits ?? 0)),
      equivalence: optimization.equivalent === true ? "equivalent" : optimization.equivalent === false ? "different" : "unknown",
    } : undefined,
    engine: o && optimization ? {
      profile: optimization.profile || "nivel_fixo", strategy: optimization.strategy,
      verificationMethod: optimization.verificationMethod, guarantee: optimization.guarantee,
      cxBefore: optimization.cxBefore, cxAfter: optimization.cxAfter,
      twoQubitBefore: optimization.before?.twoQubit, twoQubitAfter: optimization.after?.twoQubit,
      memoryBytes: optimization.memoryBytes, simulationMethod: optimization.simulationMethod,
      simulationReason: optimization.simulationReason,
      tradeoffs: textList(o.compromissos, 5, RESULT_LIMITS.text),
    } : undefined,
    circuits,
    files: files.filter((file) => Boolean(file) && typeof file === "object" && typeof file.nome === "string" && file.nome.length > 0)
      .slice(0, RESULT_LIMITS.files).map((file) => ({
        name: file.nome.slice(0, RESULT_LIMITS.path),
        type: FILE_TYPES.includes(file.tipo) ? file.tipo : "ignorado",
        size: Number.isSafeInteger(file.tamanho) && file.tamanho > 0 ? file.tamanho : 0,
        reason: nullableText(file.motivo, RESULT_LIMITS.text),
      })),
    timeline: steps.filter((step) => Boolean(step) && typeof step.texto === "string").slice(0, RESULT_LIMITS.timeline)
      .map((step) => ({ text: step.texto.slice(0, RESULT_LIMITS.timelineText), seconds: count(step.segundos) ?? 0 })),
    llmAttempts: attempts.filter((item) => Boolean(item) && typeof item.modelo === "string").slice(0, RESULT_LIMITS.attempts)
      .map((item) => ({
        model: item.modelo.slice(0, RESULT_LIMITS.label),
        result: String(item.resultado ?? "").slice(0, RESULT_LIMITS.label),
        seconds: count(item.segundos) ?? 0,
      })),
    totalSeconds: count(response.tempo_total_segundos),
  };
}

function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
  });
}

/** Falhas de conexão ou do servidor que valem nova tentativa (entrada inválida, 4xx, não melhora repetindo). */
const STATUS_PASSAGEIROS = [0, 408, 429, 500, 502, 503, 504];
const INSISTIR_POR_MS = 30 * 60 * 1000; // mesmo teto da análise no servidor (QONE_ANALISE_TIMEOUT_SECONDS)

/**
 * Envia a análise e, se a conexão cair ou o servidor falhar, tenta de novo sozinho (2, 4, 8, 15 s...),
 * avisando no cartão ao vivo. Só desiste se o usuário cancelar, se o erro for da própria entrada
 * ou depois de 30 minutos tentando.
 */
async function analisarInsistindo(body: EntradaAnalise, signal: AbortSignal,
  onProgress?: (progress: ProgressoAnalise) => void): Promise<RespostaAnalise> {
  const inicio = Date.now();
  for (let tentativa = 1; ; tentativa++) {
    try {
      return await analisar(body, signal);
    } catch (error) {
      signal.throwIfAborted();
      const status = error instanceof AnalysisError ? error.status : 0;
      const passageiro = error instanceof TypeError || (error instanceof AnalysisError && STATUS_PASSAGEIROS.includes(status));
      if (!passageiro || Date.now() - inicio > INSISTIR_POR_MS) throw error;
      const espera = Math.min(2000 * 2 ** (tentativa - 1), 15_000);
      const motivo = status ? `o servidor respondeu ${status}` : "a conexão com o servidor caiu";
      try {
        onProgress?.({
          progress_id: body.progress_id, concluido: false, modelo: null, parcial: null, historico: [],
          etapa: `Falha passageira (${motivo}); tentando de novo em ${Math.round(espera / 1000)} s (tentativa ${tentativa + 1})`,
          segundos_na_etapa: 0, segundos_total: (Date.now() - inicio) / 1000,
        });
      } catch { /* O aviso é apenas informativo. */ }
      await wait(espera, signal);
      signal.throwIfAborted();
    }
  }
}

/** Nível, estilo e papel do perfil local, para a resposta se adaptar a quem pergunta. */
function perfilDoUsuario(): EntradaAnalise["usuario"] {
  const perfil = readLocalProfile();
  if (!perfil) return null;
  return { experiencia: perfil.experience, estilo: perfil.answerStyle, papel: perfil.role };
}

/** Consulta GET /analise/progresso/{id} a cada 500 ms até a função devolvida ser chamada ou o sinal abortar. */
function pollProgress(progressId: string, signal: AbortSignal, onProgress: (progress: ProgressoAnalise) => void) {
  const controller = new AbortController();
  const stop = () => controller.abort();
  signal.addEventListener("abort", stop, { once: true });
  if (signal.aborted) stop();
  void (async () => {
    while (!controller.signal.aborted) {
      await wait(PROGRESS_INTERVAL_MS, controller.signal);
      if (controller.signal.aborted) break;
      const progress = await obterProgresso(progressId, controller.signal);
      if (!progress || controller.signal.aborted) continue;
      try { onProgress(progress); } catch { /* O progresso é apenas informativo. */ }
    }
  })();
  return () => {
    signal.removeEventListener("abort", stop);
    controller.abort();
  };
}

/**
 * Análise unificada: uma única chamada a POST /analise com a pergunta, o código
 * do editor e todos os anexos (arquivos soltos ou pastas, com caminho relativo).
 */
export async function analyze(input: {
  code: string; format: CodeFormat; question: string; topK?: number; profile?: OptimizationProfile;
  attachments: AttachmentRecord[]; remoteId?: string;
  onProgress?: (progress: ProgressoAnalise) => void;
}, signal: AbortSignal, attempt: TurnAttempt) {
  const remoteId = attempt.remoteId ?? input.remoteId;
  const question = input.question.trim();
  const codigo = input.code.trim() ? input.code : null;
  // Anexos restaurados do histórico podem estar sem conteúdo (economia de espaço); só os completos são enviados.
  const arquivos = input.attachments
    .filter((file): file is AttachmentRecord & { content: string } => typeof file.content === "string" && file.content.length > 0)
    .map((file) => ({ nome: file.name, conteudo: file.content }));
  if (!question && !codigo && !arquivos.some((file) => file.conteudo.trim()))
    throw new Error("Informe um circuito, uma pergunta ou arquivos.");
  if (question.length > 30_000) throw new Error("Reduza a pergunta para até 30.000 caracteres.");
  const body: EntradaAnalise = {
    mensagem: question,
    codigo,
    formato: codigo ? input.format : "auto",
    arquivos,
    conversation_id: remoteId && UUID.test(remoteId) ? remoteId : null,
    progress_id: attempt.messageId,
    // O programa decide: otimização mais forte que segue verificada, até 15 fontes e internet sempre ativa.
    perfil: input.profile ?? "avancado",
    top_k: Math.min(15, Math.max(1, Math.round(Number(input.topK) || 15))),
    pesquisa_web: true,
    usuario: perfilDoUsuario(),
  };
  const stopPolling = input.onProgress ? pollProgress(attempt.messageId, signal, input.onProgress) : () => undefined;
  let response: RespostaAnalise;
  try {
    response = await analisarInsistindo(body, signal, input.onProgress);
  } finally {
    stopPolling();
  }
  // O histórico só aceita identificadores UUID; outro formato mantém a conversa anterior.
  const nextRemoteId = typeof response.conversation_id === "string" && UUID.test(response.conversation_id)
    ? response.conversation_id : remoteId;
  attempt.remoteId = nextRemoteId;
  return { analysisResult: reportAnalysis(response), remoteId: nextRemoteId };
}
