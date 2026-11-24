export type EditorLanguage = "qasm" | "python";

export type ProjectRecord = {
  id: string;
  name: string;
  description: string;
  language: "OpenQASM 2" | "Python / Qiskit";
  createdAt: string;
  isPinned?: boolean;
};

export type AttachmentRecord = {
  id: string;
  name: string;
  content?: string;
  size: number;
  type: string;
};

export type EditorFileRecord = {
  id: string;
  name: string;
  language: EditorLanguage;
  content: string;
  isDirty?: boolean;
};

export type AnalysisResultRecord = {
  summary: string;
  searchSuggestions?: string;
  insufficient?: boolean;
  suggestions: Array<{
    title: string;
    description: string;
    rationale: string;
    code?: string;
  }>;
  sources: Array<{
    title: string;
    origin?: string;
    excerpt?: string;
    url?: string;
  }>;
  circuit?: { qubits: number; equivalence: "equivalent" | "different" | "unknown" };
  metrics?: {
    depthBefore?: number;
    depthAfter?: number;
    gatesBefore?: number;
    gatesAfter?: number;
  };
  warnings?: string[];
  requestId?: string;
  /** llm: redigida pela fila de LLMs com trechos locais; local: extrativa, sem LLM; none: sem resposta. */
  answerMode?: "llm" | "local" | "none";
  /** Modelo da fila que redigiu a resposta (quando answerMode = "llm"). */
  llmModel?: string;
  engine?: {
    profile: string;
    strategy?: string;
    verificationMethod?: string;
    guarantee?: string;
    cxBefore?: number;
    cxAfter?: number;
    twoQubitBefore?: number;
    twoQubitAfter?: number;
    memoryBytes?: number;
    simulationMethod?: string;
    simulationReason?: string;
    tradeoffs?: string[];
  };
  diagnosis?: {
    language?: string | null;
    confidence: string;
    problems: { code: string; line?: number | null; message: string; cause?: string | null; fix: string;
      alternatives: string[]; confidence: string }[];
    correctedCode?: string | null;
    valid?: boolean;
    limitations: string[];
  };
  /** Circuitos encontrados pela análise unificada (editor e arquivos). */
  circuits?: AnalyzedCircuitRecord[];
  /** Arquivos enviados e como a análise os classificou. */
  files?: AnalyzedFileRecord[];
  /** Etapas concluídas da análise, com a duração de cada uma. */
  timeline?: { text: string; seconds: number }[];
  /** Tentativas da fila de LLMs, na ordem em que foram feitas. */
  llmAttempts?: { model: string; result: string; seconds: number }[];
  /** Duração total da análise no servidor. */
  totalSeconds?: number;
};

export type CircuitMetricsRecord = {
  qubits?: number;
  depth?: number;
  operations?: number;
  twoQubit?: number;
};

export type AnalyzedCircuitRecord = {
  /** "editor" ou o caminho do arquivo. */
  origin: string;
  format: EditorLanguage | null;
  validOriginal: boolean;
  problems: number;
  diagnosis: string[];
  correction: {
    method: "nenhuma" | "deterministica" | "llm";
    model?: string | null;
    code?: string | null;
    valid: boolean;
    explanation?: string | null;
  };
  optimization?: {
    profile?: string;
    strategy?: string;
    equivalent?: boolean | null;
    verificationMethod?: string;
    guarantee?: string;
    before?: CircuitMetricsRecord;
    after?: CircuitMetricsRecord;
    cxBefore?: number;
    cxAfter?: number;
    optimizedQasm?: string;
    warnings: string[];
    simulationMethod?: string;
    simulationReason?: string;
    memoryBytes?: number;
  };
  error?: string | null;
};

export type AnalyzedFileRecord = {
  name: string;
  type: "circuito_qasm" | "circuito_python" | "python" | "notebook" | "documento" | "dados" | "ignorado";
  size: number;
  reason?: string | null;
};

export type ConversationRecord = {
  id: string;
  title: string;
  prompt: string;
  qasmCode: string;
  pythonCode: string;
  activeLanguage: EditorLanguage;
  backend: string;
  topK: string;
  createdAt?: string;
  isPinned?: boolean;
  projectId?: string;
  attachments?: AttachmentRecord[];
  codeFiles?: EditorFileRecord[];
  remoteId?: string;
  previous?: { prompt: string; result: AnalysisResultRecord }[];
  analyzedFileId?: string;
  analysisResult?: AnalysisResultRecord;
};
