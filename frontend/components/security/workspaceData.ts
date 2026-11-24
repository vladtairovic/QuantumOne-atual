import type { AnalysisResultRecord, ConversationRecord, EditorFileRecord, ProjectRecord } from "../layout/conversationTypes";

export const MAX_WORKSPACE_BACKUP_BYTES = 10 * 1024 * 1024;
export const MAX_CONVERSATIONS = 500;
export const MAX_PROJECTS = 200;
/** Anexos por conversa (arquivos soltos ou uma pasta de projeto). */
export const MAX_ATTACHMENTS = 300;
/** Caracteres por anexo; o contrato aceita até 300 000 por arquivo. */
export const MAX_ATTACHMENT_CONTENT = 300_000;
/** Nome ou caminho relativo do anexo (por exemplo, "projeto/src/circuito.py"). */
export const MAX_ATTACHMENT_NAME = 500;
/** Limites dos campos da análise unificada guardados no histórico. */
export const RESULT_LIMITS = {
  circuits: 100,
  diagnosisLines: 50,
  text: 2_000,
  longText: 20_000,
  label: 200,
  code: 500_000,
  files: 300,
  path: 500,
  timeline: 100,
  timelineText: 500,
  attempts: 50,
} as const;
const ANALYZED_FILE_TYPES = ["circuito_qasm", "circuito_python", "python", "notebook", "documento", "dados", "ignorado"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

export function isProjectRecord(value: unknown): value is ProjectRecord {
  if (!isRecord(value)) return false;
  return typeof value.id === "string" && value.id.length > 0 && value.id.length <= 160 &&
    typeof value.name === "string" && value.name.trim().length > 0 && value.name.length <= 80 &&
    typeof value.description === "string" && value.description.length <= 500 &&
    (value.language === "OpenQASM 2" || value.language === "Python / Qiskit") &&
    typeof value.createdAt === "string" && !Number.isNaN(Date.parse(value.createdAt)) &&
    (value.isPinned === undefined || typeof value.isPinned === "boolean");
}

function isEditorFile(value: unknown): value is EditorFileRecord {
  if (!isRecord(value)) return false;
  return typeof value.id === "string" && value.id.length > 0 && value.id.length <= 160 &&
    typeof value.name === "string" && value.name.trim().length > 0 && value.name.length <= 120 &&
    (value.language === "qasm" || value.language === "python") &&
    typeof value.content === "string" && value.content.length <= 500_000 &&
    (value.isDirty === undefined || typeof value.isDirty === "boolean");
}

function isAttachment(value: unknown) {
  if (!isRecord(value)) return false;
  return typeof value.id === "string" && value.id.length > 0 && value.id.length <= 160 &&
    typeof value.name === "string" && value.name.length > 0 && value.name.length <= MAX_ATTACHMENT_NAME &&
    typeof value.size === "number" && Number.isSafeInteger(value.size) && value.size >= 0 && value.size <= 25 * 1024 * 1024 &&
    (value.content === undefined || (typeof value.content === "string" && value.content.length <= MAX_ATTACHMENT_CONTENT)) &&
    typeof value.type === "string" && value.type.length <= 120;
}

function isOptionalText(value: unknown, max: number, nullable = false) {
  return value === undefined || (nullable && value === null) || (typeof value === "string" && value.length <= max);
}

function isSeconds(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function isOptionalCount(value: unknown) {
  return value === undefined || isSeconds(value);
}

function isTextList(value: unknown, maxItems: number, maxLength: number) {
  return Array.isArray(value) && value.length <= maxItems &&
    value.every((item) => typeof item === "string" && item.length <= maxLength);
}

function isCircuitMetrics(value: unknown) {
  return value === undefined || (isRecord(value) &&
    [value.qubits, value.depth, value.operations, value.twoQubit].every(isOptionalCount));
}

function isCircuitOptimization(value: unknown) {
  if (value === undefined) return true;
  if (!isRecord(value)) return false;
  return isOptionalText(value.profile, RESULT_LIMITS.label) && isOptionalText(value.strategy, RESULT_LIMITS.text) &&
    (value.equivalent === undefined || value.equivalent === null || typeof value.equivalent === "boolean") &&
    isOptionalText(value.verificationMethod, RESULT_LIMITS.label) && isOptionalText(value.guarantee, RESULT_LIMITS.label) &&
    isCircuitMetrics(value.before) && isCircuitMetrics(value.after) &&
    isOptionalCount(value.cxBefore) && isOptionalCount(value.cxAfter) &&
    isOptionalText(value.optimizedQasm, RESULT_LIMITS.code) &&
    isTextList(value.warnings, RESULT_LIMITS.diagnosisLines, RESULT_LIMITS.text) &&
    isOptionalText(value.simulationMethod, RESULT_LIMITS.label) && isOptionalText(value.simulationReason, RESULT_LIMITS.text) &&
    isOptionalCount(value.memoryBytes);
}

function isAnalyzedCircuit(value: unknown) {
  if (!isRecord(value) || !isRecord(value.correction)) return false;
  const correction = value.correction;
  return typeof value.origin === "string" && value.origin.length > 0 && value.origin.length <= RESULT_LIMITS.path &&
    (value.format === undefined || value.format === null || value.format === "qasm" || value.format === "python") &&
    typeof value.validOriginal === "boolean" &&
    Number.isSafeInteger(value.problems) && Number(value.problems) >= 0 &&
    isTextList(value.diagnosis, RESULT_LIMITS.diagnosisLines, RESULT_LIMITS.text) &&
    ["nenhuma", "deterministica", "llm"].includes(String(correction.method)) &&
    isOptionalText(correction.model, RESULT_LIMITS.label, true) &&
    isOptionalText(correction.code, RESULT_LIMITS.code, true) &&
    typeof correction.valid === "boolean" &&
    isOptionalText(correction.explanation, RESULT_LIMITS.longText, true) &&
    isCircuitOptimization(value.optimization) &&
    isOptionalText(value.error, RESULT_LIMITS.text, true);
}

function isAnalyzedFile(value: unknown) {
  return isRecord(value) && typeof value.name === "string" && value.name.length > 0 && value.name.length <= RESULT_LIMITS.path &&
    ANALYZED_FILE_TYPES.includes(String(value.type)) &&
    Number.isSafeInteger(value.size) && Number(value.size) >= 0 &&
    isOptionalText(value.reason, RESULT_LIMITS.text, true);
}

function isAnalysisResult(value: unknown): value is AnalysisResultRecord {
  if (!isRecord(value) || typeof value.summary !== "string" || value.summary.length > 100_000) return false;
  if (value.searchSuggestions !== undefined && (typeof value.searchSuggestions !== "string" || value.searchSuggestions.length > 100000)) return false;
  if (value.insufficient !== undefined && typeof value.insufficient !== "boolean") return false;
  if (!Array.isArray(value.suggestions) || value.suggestions.length > 30 || !value.suggestions.every((item) =>
    isRecord(item) && typeof item.title === "string" && item.title.length <= 300 &&
    typeof item.description === "string" && item.description.length <= 20_000 &&
    typeof item.rationale === "string" && item.rationale.length <= 20_000 &&
    (item.code === undefined || (typeof item.code === "string" && item.code.length <= 500_000)))) return false;
  if (!Array.isArray(value.sources) || value.sources.length > 100 || !value.sources.every((item) =>
    isRecord(item) && typeof item.title === "string" && item.title.length <= 500 &&
    (item.origin === undefined || (typeof item.origin === "string" && item.origin.length <= 500)) &&
    (item.excerpt === undefined || (typeof item.excerpt === "string" && item.excerpt.length <= 20_000)) &&
    (item.url === undefined || (typeof item.url === "string" && item.url.length <= 2_000)))) return false;
  if (value.warnings !== undefined && (!Array.isArray(value.warnings) || value.warnings.length > 100 ||
    !value.warnings.every((warning) => typeof warning === "string" && warning.length <= 2_000))) return false;
  if (value.requestId !== undefined && (typeof value.requestId !== "string" || value.requestId.length > 160)) return false;
  if (value.circuit !== undefined && (!isRecord(value.circuit) || !Number.isSafeInteger(value.circuit.qubits) || Number(value.circuit.qubits) < 0 || Number(value.circuit.qubits) > 4096 || !["equivalent","different","unknown"].includes(String(value.circuit.equivalence)))) return false;
  if (value.answerMode !== undefined && !["llm", "local", "none"].includes(String(value.answerMode))) return false;
  if (value.llmModel !== undefined && (typeof value.llmModel !== "string" || value.llmModel.length > 200)) return false;
  if (value.engine !== undefined && (!isRecord(value.engine) || typeof value.engine.profile !== "string" ||
    Object.values(value.engine).some((item) => !(item === undefined || item === null || typeof item === "number" ||
      (typeof item === "string" && item.length <= 2_000) ||
      (Array.isArray(item) && item.length <= 20 && item.every((text) => typeof text === "string" && text.length <= 2_000))))))
    return false;
  if (value.diagnosis !== undefined) {
    const d = value.diagnosis;
    if (!isRecord(d) || typeof d.confidence !== "string" || !Array.isArray(d.problems) || d.problems.length > 300 ||
      !Array.isArray(d.limitations) || d.limitations.length > 30 ||
      (d.correctedCode !== undefined && d.correctedCode !== null && (typeof d.correctedCode !== "string" || d.correctedCode.length > 500_000)) ||
      !d.problems.every((p) => isRecord(p) && typeof p.code === "string" && typeof p.message === "string" &&
        p.message.length <= 2_000 && typeof p.fix === "string" && p.fix.length <= 2_000 && Array.isArray(p.alternatives))) return false;
  }
  if (value.circuits !== undefined && (!Array.isArray(value.circuits) || value.circuits.length > RESULT_LIMITS.circuits ||
    !value.circuits.every(isAnalyzedCircuit))) return false;
  if (value.files !== undefined && (!Array.isArray(value.files) || value.files.length > RESULT_LIMITS.files ||
    !value.files.every(isAnalyzedFile))) return false;
  if (value.timeline !== undefined && (!Array.isArray(value.timeline) || value.timeline.length > RESULT_LIMITS.timeline ||
    !value.timeline.every((step) => isRecord(step) && typeof step.text === "string" &&
      step.text.length <= RESULT_LIMITS.timelineText && isSeconds(step.seconds)))) return false;
  if (value.llmAttempts !== undefined && (!Array.isArray(value.llmAttempts) || value.llmAttempts.length > RESULT_LIMITS.attempts ||
    !value.llmAttempts.every((item) => isRecord(item) && typeof item.model === "string" && item.model.length <= RESULT_LIMITS.label &&
      typeof item.result === "string" && item.result.length <= RESULT_LIMITS.label && isSeconds(item.seconds)))) return false;
  if (value.totalSeconds !== undefined && !isSeconds(value.totalSeconds)) return false;
  if (value.metrics !== undefined) {
    if (!isRecord(value.metrics)) return false;
    const numbers = [value.metrics.depthBefore, value.metrics.depthAfter, value.metrics.gatesBefore, value.metrics.gatesAfter];
    if (numbers.some((number) => number !== undefined && (typeof number !== "number" || !Number.isFinite(number) || number < 0))) return false;
  }
  return true;
}

export function isConversationRecord(value: unknown): value is ConversationRecord {
  if (!isRecord(value)) return false;
  const attachmentsValid = value.attachments === undefined ||
    (Array.isArray(value.attachments) && value.attachments.length <= MAX_ATTACHMENTS && value.attachments.every(isAttachment));
  const codeFilesValid = value.codeFiles === undefined ||
    (Array.isArray(value.codeFiles) && value.codeFiles.length <= 30 && value.codeFiles.every(isEditorFile));
  const analysisResultValid = value.analysisResult === undefined || isAnalysisResult(value.analysisResult);
  return typeof value.id === "string" && value.id.length > 0 && value.id.length <= 160 &&
    typeof value.title === "string" && value.title.trim().length > 0 && value.title.length <= 200 &&
    typeof value.prompt === "string" && value.prompt.length <= 20_000 &&
    typeof value.qasmCode === "string" && value.qasmCode.length <= 500_000 &&
    typeof value.pythonCode === "string" && value.pythonCode.length <= 500_000 &&
    (value.activeLanguage === "qasm" || value.activeLanguage === "python") &&
    (value.backend === undefined || (typeof value.backend === "string" && value.backend.length <= 120)) &&
    (value.topK === undefined || ["3", "5", "8", "10"].includes(String(value.topK))) &&
    (value.createdAt === undefined || (typeof value.createdAt === "string" && !Number.isNaN(Date.parse(value.createdAt)))) &&
    (value.isPinned === undefined || typeof value.isPinned === "boolean") &&
    (value.projectId === undefined || (typeof value.projectId === "string" && value.projectId.length <= 160)) &&
    (value.remoteId === undefined || (typeof value.remoteId === "string" && /^[0-9a-f-]{36}$/i.test(value.remoteId))) &&
    (value.analyzedFileId === undefined || typeof value.analyzedFileId === "string") &&
    (value.previous === undefined || (Array.isArray(value.previous) && value.previous.length <= 500 && value.previous.every(turn => isRecord(turn) && typeof turn.prompt === "string" && turn.prompt.length <= 20000 && isAnalysisResult(turn.result)))) &&
    attachmentsValid && codeFilesValid && analysisResultValid;
}

export function parseUniqueRecords<T extends { id: string }>(
  values: unknown[], isValid: (value: unknown) => value is T, limit: number,
) {
  const records: T[] = [];
  const ids = new Set<string>();
  let invalid = values.length > limit;
  for (const value of values.slice(0, limit)) {
    if (!isValid(value) || ids.has(value.id)) {
      invalid = true;
      continue;
    }
    ids.add(value.id);
    records.push(value);
  }
  return { records, invalid };
}
