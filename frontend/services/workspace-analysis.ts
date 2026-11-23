import { AnalysisError, circuitContext, createConversation, getCircuitLimits, optimizeCircuit, sendTurn } from "./analysis-api";
import type { AnalysisResponse, CodeFormat } from "./analysis-api";
import type { AnalysisResultRecord, AttachmentRecord, ConversationRecord } from "../components/layout/conversationTypes";
import { isConversationRecord } from "../components/security/workspaceData";

export function report(response: AnalysisResponse): AnalysisResultRecord {
  const q = response.quantum;
  return {
    summary: response.answer,
    requestId: response.request_id,
    searchSuggestions: response.search_suggestions_html || undefined,
    insufficient: response.status === "insufficient_context",
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

export async function analyze(input: {
  code: string; format: CodeFormat; question: string; topK: number;
  attachments: AttachmentRecord[]; remoteId?: string;
}, signal: AbortSignal, attempt: TurnAttempt) {
  const { code, format, question, attachments } = input;
  let result: AnalysisResponse | undefined;
  let remoteId = attempt.remoteId ?? input.remoteId;
  const documents = attachments.filter(file => file.content).map(file => "\n\nArquivo: " + file.name + "\n" + file.content).join("");
  const prompt = question || (documents ? "Analise os arquivos anexados." : "");
  if ((prompt + documents + (code ? circuitContext(code, format) : "")).length > 29500)
    throw new Error("Reduza a pergunta ou os anexos para caber no contexto de 30.000 caracteres da API.");
  if (code.trim()) {
    const limits = await getCircuitLimits(signal);
    if (code.split(/\r?\n/).length > limits.max_circuit_lines || new Blob([code]).size > limits.max_code_size_bytes)
      throw new Error("O circuito ultrapassa o limite de " + limits.max_circuit_lines + " linhas ou " + limits.max_code_size_bytes + " bytes.");
    result = await optimizeCircuit(code, signal, format);
  }
  if (prompt) {
    try {
      remoteId ??= await createConversation(signal);
      attempt.remoteId = remoteId;
      const text = prompt + documents + (code ? circuitContext(code, format, result?.quantum) : "");
      const messageId = attempt.messageId;
      const chat = await sendTurn(remoteId, text, messageId, { top_k: input.topK }, signal);
      result = { ...chat, quantum: result?.quantum, warnings: [...(result?.warnings || []), ...(chat.warnings || [])] };
    } catch (error) {
      if (!result || signal.aborted) throw error;
      result.warnings.push("A explicação do agente ficou indisponível. " + diagnostic(error));
    }
  }
  if (!result) throw new Error("Informe um circuito ou uma pergunta.");
  return { analysisResult: report(result), remoteId };
}
