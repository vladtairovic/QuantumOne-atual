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
  /** llm: redigida pelo Gemini com trechos locais; local: extrativa, sem LLM; none: sem resposta. */
  answerMode?: "llm" | "local" | "none";
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
