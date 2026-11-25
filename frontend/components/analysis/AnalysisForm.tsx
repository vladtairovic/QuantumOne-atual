"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react";
import dynamic from "next/dynamic";
import Image from "next/image";

import { analyze, diagnostic, readLegacyHistory, type TurnAttempt } from "../../services/workspace-analysis";
import AnalysisLiveStatus, { type LiveProgress } from "./AnalysisLiveStatus";
import AnalysisResultPanel from "./AnalysisResultPanel";
import AppFrame from "../layout/AppFrame";
import type { AttachmentRecord, ConversationRecord, EditorFileRecord, EditorLanguage, ProjectRecord } from "../layout/conversationTypes";
import {
  isConversationRecord, isProjectRecord, MAX_ATTACHMENT_CONTENT, MAX_ATTACHMENT_NAME, MAX_ATTACHMENTS, MAX_CONVERSATIONS, MAX_PROJECTS,
  parseUniqueRecords,
} from "../security/workspaceData";
import FeedbackMessage from "../ui/FeedbackMessage";
import useModalFocus from "../ui/useModalFocus";
import WorkspaceToolsPanel, { previewGates, WorkspaceToolTabs, type WorkspaceToolId } from "../workspace/WorkspaceToolsPanel";
import Icon from "../ui/Icon";
import { suggestionsFor, useLocalProfile } from "../profile/localProfile";

const CodeEditor = dynamic(() => import("./CodeEditor"), {
  ssr: false,
  loading: () => (
    <div className="editor-loading" role="status" aria-live="polite" aria-busy="true">Carregando editor...</div>
  ),
});

/** Extensões de texto aceitas em anexos soltos e em pastas de projeto. */
const ATTACHMENT_EXTENSIONS = ["py", "qasm", "ipynb", "txt", "md", "json", "toml", "cfg", "ini", "yaml", "yml", "csv", "log", "rst"];
const ATTACHMENT_ACCEPT = ATTACHMENT_EXTENSIONS.map((extension) => "." + extension).join(",");
const ATTACHMENT_PATTERN = new RegExp("\\.(" + ATTACHMENT_EXTENSIONS.join("|") + ")$", "i");
/** Pastas de dependências, ambientes e artefatos que nunca entram na análise. */
const IGNORED_FOLDERS = new Set(["node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", ".next", ".ipynb_checkpoints"]);
const MAX_ATTACHMENT_BYTES = Math.min(300_000, MAX_ATTACHMENT_CONTENT);
const MAX_ATTACHMENTS_TOTAL_BYTES = 3_000_000;
/** Acima disso, o histórico guarda só nome, tamanho e tipo dos anexos (cota do localStorage). */
const STORED_ATTACHMENT_CONTENT_LIMIT = 1_000_000;
const CONVERSATIONS_STORAGE_KEY = "quantumone.conversations.v1";
/** Máximo de fontes por análise; o mínimo fica a critério do backend (evidências fracas são descartadas). */
const MAX_SOURCES = 15;

function useProfileForChat() {
  const stored = useLocalProfile();
  return stored && stored !== "loading" ? stored : null;
}
/** Relógio usado apenas em manipuladores de evento (início da análise e chegada do progresso). */
const currentTime = () => Date.now();

type AttachmentNotice = { tone: "success" | "warning" | "info"; message: string };
type SkipReasons = {
  ignoredFolder: number; unsupported: number; tooLarge: number; empty: number;
  duplicate: number; countLimit: number; totalLimit: number; unreadable: number;
};

function describeAttachmentResult(added: number, skipped: SkipReasons, folder?: string): AttachmentNotice {
  const reasons = [
    skipped.ignoredFolder && `${skipped.ignoredFolder} em pastas ignoradas (node_modules, .git, venv, build…)`,
    skipped.unsupported && `${skipped.unsupported} com tipo não suportado`,
    skipped.tooLarge && `${skipped.tooLarge} acima de 300 KB`,
    skipped.empty && `${skipped.empty} vazio(s)`,
    skipped.duplicate && `${skipped.duplicate} já anexado(s)`,
    skipped.countLimit && `${skipped.countLimit} além do limite de ${MAX_ATTACHMENTS} arquivos`,
    skipped.totalLimit && `${skipped.totalLimit} além do limite total de 3 MB`,
    skipped.unreadable && `${skipped.unreadable} que não puderam ser lidos`,
  ].filter(Boolean);
  const skippedCount = Object.values(skipped).reduce((sum, value) => sum + value, 0);
  const origin = folder ? ` da pasta ${folder}` : "";
  const head = added
    ? `${added} arquivo(s) adicionado(s)${origin}.`
    : `Nenhum arquivo foi adicionado${origin}.`;
  return {
    tone: added && !skippedCount ? "success" : "warning",
    message: skippedCount ? `${head} ${skippedCount} ignorado(s): ${reasons.join("; ")}.` : head,
  };
}

/** Remove o conteúdo dos anexos grandes antes de gravar o histórico (o envio usa o estado em memória). */
function conversationsForStorage(conversations: ConversationRecord[], dropAllContent = false) {
  return conversations.map((conversation) => {
    const attachments = conversation.attachments;
    if (!attachments?.length) return conversation;
    const total = attachments.reduce((sum, attachment) => sum + (attachment.content?.length ?? 0), 0);
    if (!total || (!dropAllContent && total <= STORED_ATTACHMENT_CONTENT_LIMIT)) return conversation;
    return {
      ...conversation,
      attachments: attachments.map((attachment) => ({ id: attachment.id, name: attachment.name, size: attachment.size, type: attachment.type })),
    };
  });
}

function conversationCodeSummary(conversation: ConversationRecord) {
  const files = conversation.codeFiles?.filter((file) => file.language === conversation.activeLanguage && file.content.trim()) ?? [];
  if (files.length) return files.map((file) => file.name).join(", ");
  return conversation.activeLanguage === "qasm" ? "OpenQASM 2" : "Python / Qiskit";
}

function parseConversations(value: unknown): ConversationRecord[] {
  return Array.isArray(value) ? parseUniqueRecords(value, isConversationRecord, MAX_CONVERSATIONS).records : [];
}

function readStoredConversations(): { conversations: ConversationRecord[]; issue: string } {
  if (typeof window === "undefined") return { conversations: [], issue: "" };
  try {
    const raw = window.localStorage.getItem("quantumone.conversations.v1");
    if (raw === null) return { conversations: readLegacyHistory(window.localStorage), issue: "" };
    if (raw.length > 10 * 1024 * 1024) throw new Error("workspace-data-too-large");
    const stored: unknown = JSON.parse(raw);
    if (!Array.isArray(stored)) return { conversations: [], issue: "O histórico salvo está em um formato inválido. Restaure uma cópia pelas configurações; os dados antigos foram preservados." };
    const parsed = parseUniqueRecords(stored, isConversationRecord, MAX_CONVERSATIONS);
    return {
      conversations: parsed.records,
      issue: parsed.invalid ? "O histórico contém registros inválidos ou ultrapassa o limite permitido. Restaure uma cópia pelas configurações; os dados antigos foram preservados." : "",
    };
  } catch {
    return { conversations: [], issue: "Não foi possível ler o histórico salvo. Restaure uma cópia pelas configurações; os dados antigos foram preservados." };
  }
}

function readStoredProjects(): { projects: ProjectRecord[]; issue: string } {
  if (typeof window === "undefined") return { projects: [], issue: "" };
  try {
    const raw = window.localStorage.getItem("quantumone.projects.v1");
    if (raw === null) return { projects: [], issue: "" };
    if (raw.length > 2 * 1024 * 1024) throw new Error("project-data-too-large");
    const stored: unknown = JSON.parse(raw);
    if (!Array.isArray(stored)) return { projects: [], issue: "Os dados dos projetos estão em um formato inválido. Confira a tela Projetos ou restaure um backup." };
    const parsed = parseUniqueRecords(stored, isProjectRecord, MAX_PROJECTS);
    return {
      projects: parsed.records,
      issue: parsed.invalid ? "Os projetos contêm registros inválidos ou ultrapassam o limite permitido. Confira a tela Projetos ou restaure um backup." : "",
    };
  } catch {
    return { projects: [], issue: "Não foi possível ler os projetos salvos. Confira a tela Projetos ou restaure um backup." };
  }
}

const subscribeToHydration = () => () => undefined;
const getClientHydrationSnapshot = () => true;
const getServerHydrationSnapshot = () => false;

export default function AnalysisForm() {
  const localProfile = useProfileForChat();
  const [qasmCode, setQasmCode] = useState("");
  const [pythonCode, setPythonCode] = useState("");
  const [activeLanguage, setActiveLanguage] =
    useState<EditorLanguage>("qasm");
  const [defaultLanguage, setDefaultLanguage] = useState<EditorLanguage>("qasm");
  const [sendWithEnter, setSendWithEnter] = useState(false);
  const [openCodePanelOnNewConversation, setOpenCodePanelOnNewConversation] = useState(false);
  const [question, setQuestion] = useState("");
  const [backend, setBackend] = useState("reference");
  const [progress, setProgress] = useState<LiveProgress | null>(null);
  const [startedAt, setStartedAt] = useState(0);
  const [attachmentNotice, setAttachmentNotice] = useState<AttachmentNotice | null>(null);
  const [showInputErrors, setShowInputErrors] = useState(false);
  const [isCodePanelOpen, setIsCodePanelOpen] = useState(false);
  const codeDialogRef = useModalFocus<HTMLElement>(isCodePanelOpen);
  const [activeTool, setActiveTool] = useState<WorkspaceToolId | null>(null);
  const [attachments, setAttachments] = useState<AttachmentRecord[]>([]);
  const [codeFiles, setCodeFiles] = useState<EditorFileRecord[]>([
    { id: "main-qasm", name: "circuit.qasm", language: "qasm", content: "" },
    { id: "main-python", name: "main.py", language: "python", content: "" },
  ]);
  const [activeCodeFileId, setActiveCodeFileId] = useState("main-qasm");
  const [renamingCodeFileId, setRenamingCodeFileId] = useState<string | null>(null);
  const [renamingCodeFileName, setRenamingCodeFileName] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const codeDialogSnapshot = useRef<{
    qasmCode: string;
    pythonCode: string;
    activeLanguage: EditorLanguage;
    codeFiles: EditorFileRecord[];
    activeCodeFileId: string;
  } | null>(null);
  const [initialConversationState] = useState(readStoredConversations);
  const [conversations, setConversations] = useState<ConversationRecord[]>(initialConversationState.conversations);
  const [historyStorageIssue, setHistoryStorageIssue] = useState(initialConversationState.issue);
  const blockConversationWrites = useRef(Boolean(initialConversationState.issue));
  const [initialProjectState] = useState(readStoredProjects);
  const [projects, setProjects] = useState<ProjectRecord[]>(initialProjectState.projects);
  const [projectStorageIssue, setProjectStorageIssue] = useState(initialProjectState.issue);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const isHydrated = useSyncExternalStore(subscribeToHydration, getClientHydrationSnapshot, getServerHydrationSnapshot);

  const [feedback, setFeedback] = useState<{
    type: "error";
    message: string;
  } | null>(null);

  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const lastAttempt = useRef<(TurnAttempt & { key: string }) | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  const selected = conversations.find(item => item.id === activeConversationId);
  const result = selected?.analysisResult;
  const originalFile = selected?.codeFiles?.find(file => file.id === selected.analyzedFileId);
  const proposal = result?.suggestions.find(item => item.code)?.code;
  function cancelPending() {
    pending.current?.abort();
    pending.current = null;
    lastAttempt.current = null;
    setBusy(false);
    setProgress(null);
  }
  const [editorMessage, setEditorMessage] = useState("");

  useEffect(() => {
    function handleConversationsChanged(event: Event) {
      const nextValue = (event as CustomEvent<unknown>).detail;
      const nextConversations = parseConversations(nextValue);
      if (Array.isArray(nextValue) && nextConversations.length !== nextValue.length) {
        blockConversationWrites.current = true;
        setHistoryStorageIssue("A cópia restaurada contém registros incompletos. Alguns itens foram ignorados.");
      } else {
        setHistoryStorageIssue("");
        blockConversationWrites.current = false;
      }
      setConversations(nextConversations);
      setActiveConversationId(null);
    }

    window.addEventListener("quantumone:conversations-changed", handleConversationsChanged);
    return () => window.removeEventListener("quantumone:conversations-changed", handleConversationsChanged);
  }, []);
  useEffect(() => {
    function readDefaultLanguage() {
      try {
        const stored = window.localStorage.getItem("quantumone.preferences.v1");
        if (!stored) return;
        const storedPreferences = JSON.parse(stored);
        applyPreferences(storedPreferences);
        const language = storedPreferences.defaultLanguage === "python" ? "python" : "qasm";
        setActiveLanguage(language);
        setActiveCodeFileId(language === "python" ? "main-python" : "main-qasm");
      } catch {
        applyPreferences({});
      }
    }

    function handlePreferencesChanged(event: Event) {
      applyPreferences((event as CustomEvent<Record<string, unknown>>).detail ?? {});
    }

    function applyPreferences(preferences: Record<string, unknown>) {
      setDefaultLanguage(preferences.defaultLanguage === "python" ? "python" : "qasm");
      setSendWithEnter(preferences.sendWithEnter === true);
      setOpenCodePanelOnNewConversation(preferences.openCodePanelOnNewConversation === true);
      document.documentElement.dataset.reducedMotion = String(preferences.reduceMotion === true);
      document.documentElement.dataset.highContrast = String(preferences.highContrast === true);
      const scale = preferences.interfaceScale === "90" || preferences.interfaceScale === "110"
        ? preferences.interfaceScale
        : "100";
      document.documentElement.style.setProperty("zoom", `${Number(scale) / 100}`);
    }

    readDefaultLanguage();
    window.addEventListener("quantumone:preferences-changed", handlePreferencesChanged);
    const syncPreferences = (event: StorageEvent) => {
      if (event.key === "quantumone.preferences.v1" && event.newValue) {
        try { applyPreferences(JSON.parse(event.newValue)); } catch { /* Keep current preferences. */ }
      }
    };
    window.addEventListener("storage", syncPreferences);
    return () => {
      window.removeEventListener("quantumone:preferences-changed", handlePreferencesChanged);
      window.removeEventListener("storage", syncPreferences);
    };
  }, []);

  useEffect(() => {
    function applyProjects(nextProjects: ProjectRecord[], issue = "") {
      setProjects(nextProjects);
      setProjectStorageIssue(issue);
      const projectIds = new Set(nextProjects.map((project) => project.id));
      setActiveProjectId((current) => current && !projectIds.has(current) ? null : current);
      if (!issue) {
        setConversations((current) => current.map((conversation) => {
          if (!conversation.projectId || projectIds.has(conversation.projectId)) return conversation;
          const updated = { ...conversation };
          delete updated.projectId;
          return updated;
        }));
      }
    }

    function readProjects() {
      try {
        const stored = window.localStorage.getItem("quantumone.projects.v1");
        if (!stored) {
          applyProjects([]);
          return;
        }
        if (stored.length > 2 * 1024 * 1024) throw new Error("project-data-too-large");
        const parsed: unknown = JSON.parse(stored);
        if (Array.isArray(parsed)) {
          const result = parseUniqueRecords(parsed, isProjectRecord, MAX_PROJECTS);
          applyProjects(result.records, result.invalid ? "Os dados de projeto são inválidos ou excedem o limite permitido." : "");
        } else {
          applyProjects([], "Os dados dos projetos estão em um formato inválido. Confira a tela Projetos ou restaure um backup.");
        }
      } catch {
        applyProjects([], "Não foi possível ler os projetos salvos. Confira a tela Projetos ou restaure um backup.");
      }
    }

    function handleProjectsChanged(event: Event) {
      const changedProjects: unknown = (event as CustomEvent<unknown>).detail;
      if (Array.isArray(changedProjects)) {
        const result = parseUniqueRecords(changedProjects, isProjectRecord, MAX_PROJECTS);
        applyProjects(result.records, result.invalid ? "Os dados de projeto recebidos são inválidos." : "");
      }
    }

    window.addEventListener("quantumone:projects-changed", handleProjectsChanged);
    window.addEventListener("storage", readProjects);
    return () => {
      window.removeEventListener("quantumone:projects-changed", handleProjectsChanged);
      window.removeEventListener("storage", readProjects);
    };
  }, []);

  useEffect(() => {
    if (!isHydrated || blockConversationWrites.current) return;
    const write = (dropAllContent: boolean) => window.localStorage.setItem(
      CONVERSATIONS_STORAGE_KEY, JSON.stringify(conversationsForStorage(conversations, dropAllContent)));
    try {
      // Se a cota estourar, tenta de novo guardando os anexos só com nome, tamanho e tipo.
      try { write(false); } catch { write(true); }
      window.setTimeout(() => setHistoryStorageIssue(""), 0);
    } catch {
      window.setTimeout(() => setFeedback({
        type: "error",
        message: "Não foi possível salvar o histórico neste dispositivo.",
      }), 0);
      window.setTimeout(() => setHistoryStorageIssue("O histórico não pôde ser salvo neste dispositivo. Libere espaço ou exporte uma cópia pelas configurações."), 0);
    }
  }, [conversations, isHydrated]);

  const code = activeLanguage === "qasm" ? qasmCode : pythonCode;
  const visibleProjects = isHydrated ? projects : [];
  const visibleConversations = isHydrated ? conversations : [];
  const activeProject = visibleProjects.find((project) => project.id === activeProjectId) ?? null;
  const activeCodeFile = codeFiles.find((file) => file.id === activeCodeFileId);
  const editorCode = activeCodeFile?.content ?? code;
  const visibleCodeFiles = codeFiles.filter((file) => file.language === activeLanguage);

  function setCode(value: string) {
    if (value.length > 500_000) {
      setEditorMessage("O código ultrapassa o limite de 500 mil caracteres.");
      setFeedback({ type: "error", message: "Reduza o código para continuar; o limite é de 500 mil caracteres." });
      return false;
    }
    if (activeCodeFile) {
      setCodeFiles((current) => current.map((file) => file.id === activeCodeFile.id ? { ...file, content: value, isDirty: true } : file));
      if (activeCodeFile.id === "main-qasm") setQasmCode(value);
      if (activeCodeFile.id === "main-python") setPythonCode(value);
    } else if (activeLanguage === "qasm") setQasmCode(value);
    else setPythonCode(value);
    return true;
  }

  function selectCodeFile(file: EditorFileRecord) {
    setActiveCodeFileId(file.id);
    setActiveLanguage(file.language);
    setActiveConversationId(null);
    setEditorMessage("");
  }

  function selectLanguage(language: EditorLanguage) {
    let languageFiles = codeFiles.filter((file) => file.language === language);
    if (!languageFiles.length) {
      const file: EditorFileRecord = language === "qasm"
        ? { id: "main-qasm", name: "circuit.qasm", language, content: qasmCode }
        : { id: "main-python", name: "main.py", language, content: pythonCode };
      setCodeFiles((current) => [...current, file]);
      languageFiles = [file];
    }
    const primaryFile = languageFiles.find((file) => file.id === (language === "qasm" ? "main-qasm" : "main-python")) ?? languageFiles[0];
    setActiveCodeFileId(primaryFile.id);
    setActiveLanguage(language);
    setActiveConversationId(null);
    setEditorMessage("");
  }

  function addCodeFile() {
    if (codeFiles.length >= 30) { setEditorMessage("O limite é de 30 arquivos por conversa."); return; }
    const language = activeLanguage;
    const index = visibleCodeFiles.length + 1;
    const file: EditorFileRecord = {
      id: `local-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      name: language === "qasm" ? `circuit-${index}.qasm` : `module-${index}.py`,
      language,
      content: "",
      isDirty: true,
    };
    setCodeFiles((current) => [...current, file]);
    setActiveCodeFileId(file.id);
    setActiveConversationId(null);
    setEditorMessage("");
  }

  function closeCodeFile(file: EditorFileRecord) {
    if (file.id === "main-qasm" || file.id === "main-python") return;
    if (file.isDirty && !window.confirm(`Fechar ${file.name} e descartar as alterações ainda não salvas na conversa?`)) return;
    const remaining = codeFiles.filter((item) => item.id !== file.id);
    setCodeFiles(remaining);
    if (activeCodeFileId === file.id) {
      const nextFile = remaining.find((item) => item.language === file.language) ?? remaining[0];
      if (nextFile) selectCodeFile(nextFile);
    }
  }

  function startRenameCodeFile(file: EditorFileRecord) {
    setRenamingCodeFileId(file.id);
    setRenamingCodeFileName(file.name);
  }

  function finishRenameCodeFile() {
    const normalizedName = renamingCodeFileName.trim();
    if (normalizedName) {
      setCodeFiles((current) => current.map((file) => file.id === renamingCodeFileId
        ? { ...file, name: normalizedName, isDirty: true }
        : file));
    }
    setRenamingCodeFileId(null);
    setRenamingCodeFileName("");
  }

  // Sugestões e saudação personalizadas pelo perfil local (onboarding).
  const examples = suggestionsFor(localProfile);
  const firstName = localProfile?.name.trim().split(/\s+/)[0];

  function selectExample(example: string) {
    setActiveConversationId(null);
    setQuestion(example);
    setFeedback(null);
  }

  function openCodeEditor() {
    codeDialogSnapshot.current = { qasmCode, pythonCode, activeLanguage, codeFiles, activeCodeFileId };
    setIsCodePanelOpen(true);
    setEditorMessage("");
  }

  const cancelCodeEditor = useCallback(() => {
    if (codeDialogSnapshot.current) {
      setQasmCode(codeDialogSnapshot.current.qasmCode);
      setPythonCode(codeDialogSnapshot.current.pythonCode);
      setActiveLanguage(codeDialogSnapshot.current.activeLanguage);
      setCodeFiles(codeDialogSnapshot.current.codeFiles);
      setActiveCodeFileId(codeDialogSnapshot.current.activeCodeFileId);
    }
    codeDialogSnapshot.current = null;
    setEditorMessage("");
    setRenamingCodeFileId(null);
    setRenamingCodeFileName("");
    setIsCodePanelOpen(false);
  }, []);

  function applyCodeEditor() {
    codeDialogSnapshot.current = null;
    setEditorMessage("");
    setIsCodePanelOpen(false);
    setRenamingCodeFileId(null);
    setRenamingCodeFileName("");
  }

  function clearCurrentCode() {
    setCode("");
    setCodeFiles((current) => current.map((file) => file.id === activeCodeFileId ? { ...file, content: "" } : file));
    setActiveConversationId(null);
    setShowInputErrors(false);
    setFeedback(null);
    setEditorMessage("Código removido.");
  }

  /**
   * Anexa arquivos soltos ou uma pasta inteira (webkitdirectory). Em pastas, o
   * nome guardado é o caminho relativo ("projeto/src/circuito.py"); dependências,
   * ambientes e artefatos de build são ignorados.
   */
  async function handleSelectFiles(files: FileList | null, source: "files" | "folder" = "files") {
    const picked = files ? Array.from(files) : [];
    const input = source === "folder" ? folderInputRef.current : fileInputRef.current;
    if (input) input.value = "";
    if (!picked.length) return;
    const skipped: SkipReasons = {
      ignoredFolder: 0, unsupported: 0, tooLarge: 0, empty: 0, duplicate: 0, countLimit: 0, totalLimit: 0, unreadable: 0,
    };
    const names = new Set(attachments.map((attachment) => attachment.name));
    let slots = Math.max(0, MAX_ATTACHMENTS - attachments.length);
    let totalBytes = attachments.reduce((sum, attachment) => sum + (attachment.content ? attachment.size : 0), 0);
    const entries = picked
      .map((file) => ({ file, path: (file.webkitRelativePath || file.name).replace(/\\/g, "/") }))
      .sort((a, b) => a.path.localeCompare(b.path));
    const accepted: typeof entries = [];
    for (const entry of entries) {
      const segments = entry.path.split("/");
      // A pasta escolhida pelo usuário (primeiro segmento) nunca é descartada pelo nome.
      const folders = entry.file.webkitRelativePath ? segments.slice(1, -1) : segments.slice(0, -1);
      if (folders.some((folder) => IGNORED_FOLDERS.has(folder))) skipped.ignoredFolder++;
      else if (!ATTACHMENT_PATTERN.test(entry.path) || entry.path.length > MAX_ATTACHMENT_NAME) skipped.unsupported++;
      else if (entry.file.size === 0) skipped.empty++;
      else if (entry.file.size > MAX_ATTACHMENT_BYTES) skipped.tooLarge++;
      else if (names.has(entry.path)) skipped.duplicate++;
      else if (slots <= 0) skipped.countLimit++;
      else if (totalBytes + entry.file.size > MAX_ATTACHMENTS_TOTAL_BYTES) skipped.totalLimit++;
      else {
        accepted.push(entry);
        names.add(entry.path);
        slots--;
        totalBytes += entry.file.size;
      }
    }
    const read = await Promise.allSettled(accepted.map(async ({ file, path }): Promise<AttachmentRecord> => ({
      content: await file.text(),
      id: globalThis.crypto?.randomUUID?.() ?? `${path}-${file.lastModified}-${Math.random().toString(16).slice(2)}`,
      name: path,
      size: file.size,
      type: file.type || "Arquivo local",
    })));
    const selected: AttachmentRecord[] = [];
    for (const item of read) {
      if (item.status === "fulfilled" && (item.value.content?.length ?? 0) <= MAX_ATTACHMENT_CONTENT) selected.push(item.value);
      else skipped.unreadable++;
    }
    const folder = source === "folder" ? entries[0]?.file.webkitRelativePath.split("/")[0] || undefined : undefined;
    setAttachmentNotice(describeAttachmentResult(selected.length, skipped, folder));
    if (selected.length) {
      setAttachments((current) => [...current, ...selected].slice(0, MAX_ATTACHMENTS));
      setActiveTool("files");
      setActiveConversationId(null);
    }
  }

  function removeAttachment(id: string) {
    setAttachments((current) => current.filter((attachment) => attachment.id !== id));
    if (activeConversationId) {
      setConversations((current) => current.map((conversation) =>
        conversation.id === activeConversationId
          ? { ...conversation, attachments: conversation.attachments?.filter((attachment) => attachment.id !== id) }
          : conversation
      ));
    }
  }

  function createConversationTitle(prompt: string, language: EditorLanguage) {
    const normalizedPrompt = prompt.trim().replace(/\s+/g, " ");
    if (normalizedPrompt) {
      return normalizedPrompt.length > 38
        ? `${normalizedPrompt.slice(0, 38).trimEnd()}…`
        : normalizedPrompt;
    }
    return language === "qasm" ? "Análise OpenQASM 2" : "Análise Python / Qiskit";
  }

  function handleSelectConversation(id: string) {
    cancelPending();
    const conversation = conversations.find((item) => item.id === id);
    if (!conversation) return;
    setActiveConversationId(id);
    setActiveProjectId(conversation.projectId ?? null);
    setQuestion("");
    setQasmCode(conversation.qasmCode);
    setPythonCode(conversation.pythonCode);
    setActiveLanguage(conversation.activeLanguage);
    setBackend(conversation.backend);
    setAttachments(conversation.attachments ?? []);
    setAttachmentNotice(null);
    const restoredFiles = conversation.codeFiles?.slice() ?? [];
    if (!restoredFiles.length) {
      restoredFiles.push(conversation.activeLanguage === "qasm"
        ? { id: "main-qasm", name: "circuit.qasm", language: "qasm", content: conversation.qasmCode }
        : { id: "main-python", name: "main.py", language: "python", content: conversation.pythonCode });
    }
    setCodeFiles(restoredFiles);
    setActiveCodeFileId(conversation.analyzedFileId ?? (restoredFiles.find((file) => file.language === conversation.activeLanguage) ?? restoredFiles[0]).id);
    setFeedback(null);
    setShowInputErrors(false);
    setEditorMessage("");
    setIsCodePanelOpen(false);
  }

  function handleRenameConversation(id: string, title: string) {
    const normalizedTitle = title.trim();
    if (!normalizedTitle) return;
    setConversations((current) => current.map((item) =>
      item.id === id ? { ...item, title: normalizedTitle } : item
    ));
  }

  function handleToggleConversationPin(id: string) {
    setConversations((current) => current.map((item) =>
      item.id === id ? { ...item, isPinned: !item.isPinned } : item
    ));
  }

  function handleDeleteConversation(id: string) {
    setConversations((current) => current.filter((item) => item.id !== id));
    if (activeConversationId === id) handleClear();
  }

  function handleAssignConversationToProject(id: string, projectId: string | null) {
    if (activeConversationId === id) setActiveProjectId(projectId);
    setConversations((current) => current.map((item) => {
      if (item.id !== id) return item;
      const updated = { ...item };
      if (projectId) updated.projectId = projectId;
      else delete updated.projectId;
      return updated;
    }));
  }

  function openProjectWorkspace(projectId: string) {
    handleClear();
    setActiveProjectId(projectId);
    const projectLanguage = projects.find((project) => project.id === projectId)?.language;
    const language: EditorLanguage = projectLanguage === "Python / Qiskit" ? "python" : "qasm";
    setActiveLanguage(language);
    setActiveCodeFileId(language === "python" ? "main-python" : "main-qasm");
    if (codeDialogSnapshot.current) {
      codeDialogSnapshot.current = {
        ...codeDialogSnapshot.current,
        activeLanguage: language,
        activeCodeFileId: language === "python" ? "main-python" : "main-qasm",
      };
    }
  }

  async function handleCopy() {
    if (!editorCode.trim()) {
      setEditorMessage("Não existe código para copiar.");
      return;
    }

    try {
      await navigator.clipboard.writeText(editorCode);
      setEditorMessage("Código copiado!");
    } catch {
      setEditorMessage(
        "Não foi possível copiar automaticamente. Utilize Ctrl+C."
      );
    }
  }

  async function handlePaste() {
    try {
      const clipboardText = await navigator.clipboard.readText();

      if (!clipboardText) {
        setEditorMessage("A área de transferência está vazia.");
        return;
      }

      if (!setCode(clipboardText)) return;
      setActiveConversationId(null);
      setEditorMessage("Código inserido!");
      setFeedback(null);
      setShowInputErrors(false);
    } catch {
      setEditorMessage(
        "Não foi possível acessar a área de transferência. Utilize Ctrl+V."
      );
    }
  }

  function handleClear() {
    cancelPending();
    setActiveConversationId(null);
    setQasmCode("");
    setPythonCode("");
    setCodeFiles([
      { id: "main-qasm", name: "circuit.qasm", language: "qasm", content: "" },
      { id: "main-python", name: "main.py", language: "python", content: "" },
    ]);
    setActiveCodeFileId(defaultLanguage === "python" ? "main-python" : "main-qasm");
    setRenamingCodeFileId(null);
    setRenamingCodeFileName("");
    setAttachments([]);
    setAttachmentNotice(null);
    setQuestion("");
    setBackend("reference");
    setFeedback(null);
    setEditorMessage("");
    setShowInputErrors(false);
    setActiveLanguage(defaultLanguage);
    codeDialogSnapshot.current = openCodePanelOnNewConversation
      ? {
          qasmCode: "", pythonCode: "", activeLanguage: defaultLanguage,
          codeFiles: [
            { id: "main-qasm", name: "circuit.qasm", language: "qasm", content: "" },
            { id: "main-python", name: "main.py", language: "python", content: "" },
          ],
          activeCodeFileId: defaultLanguage === "python" ? "main-python" : "main-qasm",
        }
      : null;
    setIsCodePanelOpen(openCodePanelOnNewConversation);
    setActiveTool(null);
  }

  async function handleAnalyze(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (pending.current) return;
    const file = activeCodeFile?.content.trim() ? activeCodeFile : visibleCodeFiles.find(item => item.content.trim());
    const activeLanguageCode = file?.content || "";
    if (!selected && conversations.length >= MAX_CONVERSATIONS) {
      setFeedback({ type: "error", message: "O histórico está cheio. Exporte ou remova uma conversa antes de continuar." });
      return;
    }
    if (question.length > 20_000 || activeLanguageCode.length > 500_000 || attachments.length > MAX_ATTACHMENTS) {
      setFeedback({ type: "error", message: "A pergunta, o código ou os anexos excedem os limites permitidos. Revise o conteúdo antes de continuar." });
      return;
    }
    const hasActiveLanguageFile = visibleCodeFiles.some((file) => file.content.trim());
    if (!activeLanguageCode.trim() && !hasActiveLanguageFile && !question.trim() && !attachments.some(file => file.content)) {
      setShowInputErrors(true);
      setFeedback({
        type: "error",
        message:
          "Preencha o código, a pergunta ou os dois para continuar.",
      });
      return;
    }

    setShowInputErrors(false);
    setFeedback(null);
    const withoutContent = attachments.filter((attachment) => !attachment.content).length;
    setAttachmentNotice(withoutContent ? {
      tone: "warning",
      message: `${withoutContent} anexo(s) restaurado(s) do histórico sem conteúdo salvo não serão enviados. Anexe-os novamente para incluí-los.`,
    } : null);
    const id = selected?.id ?? crypto.randomUUID();
    const savedCodeFiles = codeFiles.map((file) => ({ ...file, isDirty: false }));
    const controller = new AbortController();
    const key = JSON.stringify([activeConversationId, activeLanguageCode, activeLanguage, question, attachments]);
    if (lastAttempt.current?.key !== key) lastAttempt.current = { key, messageId: crypto.randomUUID() };
    const attempt = lastAttempt.current;
    pending.current = controller;
    setProgress(null);
    setStartedAt(currentTime());
    setBusy(true);
    try {
      const response = await analyze({
        code: activeLanguageCode, format: activeLanguage, question: question.trim(),
        attachments, remoteId: selected?.remoteId,
        onProgress: (data) => {
          if (pending.current === controller) setProgress({ data, receivedAt: currentTime() });
        },
      }, controller.signal, attempt);
      if (controller.signal.aborted) return;
      lastAttempt.current = null;
      const conversation: ConversationRecord = {
        ...selected, ...response,
        analyzedFileId: file?.id,
        previous: selected?.analysisResult ? [...(selected.previous ?? []), { prompt: selected.prompt, result: selected.analysisResult }] : selected?.previous,
        id,
        title: selected?.title || createConversationTitle(question, activeLanguage),
        prompt: question.trim(),
        qasmCode, pythonCode,
        activeLanguage,
        backend,
        topK: String(MAX_SOURCES),
        createdAt: new Date().toISOString(),
        isPinned: selected?.isPinned ?? false,
        attachments,
        codeFiles: savedCodeFiles,
        ...(activeProjectId ? { projectId: activeProjectId } : {}),
      };
      setConversations((current) => [conversation, ...current.filter(item => item.id !== id)]);
      setCodeFiles(savedCodeFiles);
      setActiveConversationId(id);
      setQuestion("");
    } catch (error) {
      if (!controller.signal.aborted) setFeedback({ type: "error", message: diagnostic(error) });
    } finally {
      if (pending.current === controller) { pending.current = null; setBusy(false); setProgress(null); }
    }
  }

  useEffect(() => {
    if (!isCodePanelOpen) return;
    function handleEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      cancelCodeEditor();
    }
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [isCodePanelOpen, cancelCodeEditor]);

  return (
    <AppFrame
      activeItem="new-analysis"
      conversations={visibleConversations}
      activeConversationId={activeConversationId}
      onSelectConversation={handleSelectConversation}
      onStartNewConversation={handleClear}
      onRenameConversation={handleRenameConversation}
      onToggleConversationPin={handleToggleConversationPin}
      onDeleteConversation={handleDeleteConversation}
      projects={visibleProjects}
      onAssignConversationToProject={handleAssignConversationToProject}
      onOpenProjectWorkspace={openProjectWorkspace}
    >
      <>
        <main className="workspace chat-workspace">
          <section className="page-intro chat-page-intro">
            <div>
              <span className="eyebrow">QUANTUMONE / WORKSPACE</span>
              <h1>Nova conversa</h1>
              <p>
                Explore seu circuito, faça perguntas e prepare uma análise
                no mesmo espaço.
              </p>
            </div>
          </section>

          <form
            className={
              "chat-layout " + (!isCodePanelOpen ? "chat-layout-expanded" : "")
            }
            onSubmit={handleAnalyze}
            noValidate
          >
            <section className="panel chat-panel">
              <div className="chat-context-bar">
                <div className="chat-breadcrumb">
                  <span className="workspace-dot" aria-hidden="true" />
                  <span>quantumone</span>
                  <span className="breadcrumb-separator">/</span>
                  <strong>{activeProject?.name ?? "workspace"}</strong>
                </div>
                <span className="chat-mode-label">QONE · AGENTE</span>
              </div>

              {activeProject && (
                <div className="chat-project-context">
                  <span className="chat-project-context-mark" aria-hidden="true">P</span>
                  <div><span>PROJETO ATIVO</span><strong>{activeProject.name}</strong></div>
                  <p>Novas conversas serão relacionadas a este projeto.</p>
                  <button type="button" onClick={() => setActiveProjectId(null)}>Sair do projeto</button>
                </div>
              )}

              {historyStorageIssue && <FeedbackMessage tone="warning" className="chat-storage-warning" message={historyStorageIssue} />}
              {projectStorageIssue && <FeedbackMessage tone="warning" className="chat-storage-warning" message={projectStorageIssue} />}

              <p className="composer-auto-note">
                <Icon name="sparkle" size={14} />
                <span>Análise automática: até {MAX_SOURCES} fontes da base local e da internet, com a otimização mais forte que continua verificada pelo Qiskit.</span>
              </p>
              <WorkspaceToolTabs activeTool={activeTool} onSelect={setActiveTool} />
              {activeTool && (
                <WorkspaceToolsPanel
                  activeTool={activeTool}
                  attachments={attachments}
                  onPickFiles={() => fileInputRef.current?.click()}
                  key={activeConversationId || "draft"}
                  onRemoveAttachment={removeAttachment}
                  files={codeFiles.map(file => file.name)}
                  qiskitData={result?.circuit && { ...result.circuit, gates: previewGates(proposal || ""), depth: result.metrics?.depthAfter, operations: result.metrics?.gatesAfter }}
                  diffFiles={originalFile && proposal ? [{ id: originalFile.id, path: originalFile.name,
                    originalLines: originalFile.content.split("\n"), proposedLines: proposal.split("\n") }] : []}
                  onApproveDiffFile={() => {
                    if (!proposal) return;
                    const file = { id: "optimized-qasm", name: "otimizado.qasm", language: "qasm" as const, content: proposal };
                    setCodeFiles(current => [...current.filter(item => item.id !== file.id), file]);
                    setActiveLanguage("qasm"); setActiveCodeFileId(file.id); setIsCodePanelOpen(true);
                  }}
                  onRevertDiffFile={() => {
                    if (!originalFile) return;
                    setCodeFiles(current => [...current.filter(item => item.id !== "optimized-qasm" && item.id !== originalFile.id), originalFile]);
                    setActiveCodeFileId(originalFile.id); setActiveLanguage(originalFile.language); setIsCodePanelOpen(true);
                  }}
                  sources={result?.sources.map((source, index) => ({ ...source, id: String(index),
                    origin: source.origin === "web" ? "internet" : source.origin || "local",
                    category: "RAG", type: "Documento", excerpt: source.excerpt || "", relationship: "Trecho recuperado para esta resposta." }))}
                />
              )}

              <div className={"chat-thread" + (busy ? " is-busy" : "")}>
                {activeConversationId ? (
                  <div className="chat-conversation-thread">
                    {(() => {
                      const activeConversation = conversations.find(
                        (item) => item.id === activeConversationId,
                      );
                      if (!activeConversation) return null;
                      return (
                        <>
                          {activeConversation.previous?.map((turn, index) => <div key={index}>
                            <p className="chat-user-message">{turn.prompt || "Análise de circuito"}</p>
                            <AnalysisResultPanel result={turn.result} />
                          </div>)}
                          <div className="chat-user-message">
                            <p>
                              {activeConversation.prompt ||
                                `Analisei um circuito ${activeConversation.activeLanguage === "qasm" ? "OpenQASM 2" : "Python / Qiskit"}.`}
                            </p>
                            {(activeConversation.qasmCode.trim() || activeConversation.pythonCode.trim() || activeConversation.codeFiles?.some((file) => file.content.trim())) && (
                              <span className="chat-code-attachment">
                                Arquivo analisado: {originalFile?.name || conversationCodeSummary(activeConversation)}
                              </span>
                            )}
                            {activeConversation.attachments?.length ? (
                              <div className="chat-message-attachments">
                                {activeConversation.attachments.map((attachment) => (
                                  <span key={attachment.id}><Icon name="files" size={13} />{attachment.name}</span>
                                ))}
                              </div>
                            ) : null}
                          </div>
                          <AnalysisResultPanel result={activeConversation.analysisResult} />
                          <p className="chat-local-note">
                            A QOne Agent é uma IA e pode cometer erros.
                          </p>
                        </>
                      );
                    })()}
                  </div>
                ) : busy ? null : <div className="chat-empty-state">
                  <div className="chat-mark" aria-hidden="true">
                    <Image
                      src="/images/quantumone-icon.png"
                      alt=""
                      width={42}
                      height={42}
                      className="chat-mark-icon"
                    />
                  </div>
                  <span className="chat-empty-kicker">
                    SEU WORKSPACE QUÂNTICO
                  </span>
                  <h2>{firstName ? `O que vamos explorar, ${firstName}?` : "O que vamos explorar?"}</h2>
                  <p>
                    Faça uma pergunta sobre seu circuito ou escolha uma
                    sugestão para começar.
                  </p>

                  <div className="example-list chat-suggestions">
                    {examples.map((example) => (
                      <button
                        key={example}
                        type="button"
                        className="example-chip"
                        onClick={() => selectExample(example)}
                      >
                        {example}
                      </button>
                    ))}
                  </div>
                </div>}
                {busy && (
                  <>
                    <div className="chat-user-message chat-pending-message">
                      <p>{question.trim() || (attachments.length ? "Analisar os arquivos anexados." : "Analisar o código do editor.")}</p>
                      {attachments.length > 0 && <span className="chat-code-attachment">{attachments.length} anexo(s)</span>}
                    </div>
                    <AnalysisLiveStatus progress={progress} startedAt={startedAt} onCancel={cancelPending} />
                  </>
                )}
              </div>

              <div className="chat-composer">
                <input
                  ref={fileInputRef}
                  className="visually-hidden"
                  type="file"
                  accept={ATTACHMENT_ACCEPT}
                  multiple
                  onChange={(event) => handleSelectFiles(event.target.files, "files")}
                  aria-label="Selecionar arquivos para a conversa"
                />
                <input
                  ref={folderInputRef}
                  className="visually-hidden"
                  type="file"
                  multiple
                  {...{ webkitdirectory: "", directory: "" }}
                  onChange={(event) => handleSelectFiles(event.target.files, "folder")}
                  aria-label="Selecionar pasta de projeto para a conversa"
                />
                <label
                  className="visually-hidden"
                  htmlFor="analysis-question"
                >
                  Pergunta ou objetivo da análise
                </label>
                <textarea
                  id="analysis-question"
                  className={
                    "question-input chat-question-input " +
                    (showInputErrors ? "input-invalid-field" : "")
                  }
                  aria-invalid={showInputErrors}
                  disabled={busy}
                  value={question}
                  maxLength={20_000}
                  onChange={(event) => {
                    setQuestion(event.target.value);
                    setFeedback(null);

                    if (event.target.value.trim()) {
                      setShowInputErrors(false);
                    }
                  }}
                  onKeyDown={(event) => {
                    if (
                      sendWithEnter &&
                      event.key === "Enter" &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      event.currentTarget.form?.requestSubmit();
                    }
                  }}
                  placeholder="Pergunte, descreva o que deseja otimizar ou peça uma explicação..."
                />

                <div className="composer-meta">
                  {activeCodeFile?.content.trim() && <span>Arquivo para análise: {activeCodeFile.name}</span>}
                  <span>
                    {sendWithEnter
                      ? "Enter envia · Shift + Enter cria uma linha."
                      : "Use uma pergunta com ou sem código."}
                  </span>
                  <span>{question.length} caracteres</span>
                </div>

                <div className="composer-actions">
                  <div className="composer-tools">
                    <span className="composer-hint">
                      <span className="composer-hint-dot" />
                      {activeLanguage === "qasm" ? "OpenQASM 2" : "Python / Qiskit"}
                    </span>
                    <button
                      type="button"
                      className="button-secondary add-code-button"
                      onClick={openCodeEditor}
                      aria-haspopup="dialog"
                      aria-expanded={isCodePanelOpen}
                    >
                      <Icon name="plus" size={15} />
                      {qasmCode.trim() || pythonCode.trim() || codeFiles.some((file) => file.content.trim())
                        ? "Editar código"
                        : "Adicionar código"}
                    </button>
                    <button
                      type="button"
                      className="button-secondary add-code-button"
                      onClick={() => fileInputRef.current?.click()}
                      aria-label="Anexar arquivos à conversa"
                      title="Anexar arquivos à conversa"
                    >
                      <Icon name="attach" size={15} />
                      Anexar
                    </button>
                    <button
                      type="button"
                      className="button-secondary add-code-button"
                      onClick={() => folderInputRef.current?.click()}
                      aria-label="Anexar pasta à conversa"
                      title="Anexar uma pasta de projeto (até 300 arquivos de texto e código, 3 MB no total)"
                    >
                      <Icon name="folder" size={15} />
                      Anexar pasta
                    </button>
                  </div>

                  <div className="form-actions">
                    <button
                      type="button"
                      className="button-secondary"
                      onClick={handleClear}
                    >
                      Limpar
                    </button>

                    <button type="submit" disabled={busy} className="button-primary">
                      {busy ? "Analisando…" : "Analisar"}
                      <Icon name="arrowUp" size={15} />
                    </button>
                  </div>
                </div>
              </div>

              {attachmentNotice && (
                <FeedbackMessage tone={attachmentNotice.tone} className="attachment-feedback" message={attachmentNotice.message}
                  action={{ label: "Fechar aviso", onClick: () => setAttachmentNotice(null) }} />
              )}
              {feedback && (
                <FeedbackMessage tone="error" className="form-feedback error" message={feedback.message} />
              )}
            </section>

            {isCodePanelOpen && (
              <div className="code-dialog-layer">
                <button
                  type="button"
                  className="code-dialog-backdrop"
                  aria-label="Fechar janela de código"
                  onClick={cancelCodeEditor}
                />
                <aside
                  className="panel context-panel code-dialog"
                  role="dialog"
                  aria-modal="true"
                  aria-labelledby="code-dialog-title"
                  tabIndex={-1}
                  ref={codeDialogRef}
                >
              <div className="context-panel-heading">
                <div>
                  <span className="section-kicker">CONTEXTO</span>
                  <h2 id="code-dialog-title">Código do projeto</h2>
                  <p>Adicione o circuito que você quer explorar.</p>
                </div>
                <button
                  type="button"
                  className="button-quiet code-dialog-close"
                  aria-label="Fechar janela de código"
                  onClick={cancelCodeEditor}
                >
                  ×
                </button>
              </div>

              <div
                className="editor-file-tabs"
                role="tablist"
                aria-label="Arquivos de código abertos"
              >
                {visibleCodeFiles.map((file, index) => (
                  <div className={`editor-file-tab ${activeCodeFileId === file.id ? "active" : ""}`} key={file.id}>
                    {renamingCodeFileId === file.id ? (
                      <input
                        autoFocus
                        className="editor-file-rename-input"
                        aria-label="Rename code file"
                        value={renamingCodeFileName}
                        onChange={(event) => setRenamingCodeFileName(event.target.value)}
                        onBlur={finishRenameCodeFile}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") finishRenameCodeFile();
                          if (event.key === "Escape") {
                            setRenamingCodeFileId(null);
                            setRenamingCodeFileName("");
                          }
                        }}
                      />
                    ) : (
                      <button
                        type="button"
                        role="tab"
                        data-file-tab={file.id}
                        aria-selected={activeCodeFileId === file.id}
                        className="editor-file-tab-select"
                        title="Clique duas vezes para renomear"
                        onClick={() => selectCodeFile(file)}
                        onDoubleClick={() => startRenameCodeFile(file)}
                        onKeyDown={(event) => {
                          if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                          event.preventDefault();
                          const tabs = Array.from(event.currentTarget.closest(".editor-file-tabs")?.querySelectorAll<HTMLButtonElement>("[role='tab']") ?? []);
                          const nextIndex = (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
                          tabs[nextIndex]?.focus();
                          tabs[nextIndex]?.click();
                        }}
                      >
                        <span className={file.language === "qasm" ? "file-type-qasm" : "file-type-python"} aria-hidden="true" />
                        <span>{file.name}</span>
                        {file.isDirty && <i className="editor-file-dirty" aria-label="Alterações não salvas" title="Alterações não salvas" />}
                      </button>
                    )}
                    {file.id !== "main-qasm" && file.id !== "main-python" && (
                      <button type="button" className="editor-file-close" onClick={() => closeCodeFile(file)} aria-label={`Fechar ${file.name}`} title="Fechar aba">×</button>
                    )}
                  </div>
                ))}
                <button type="button" className="editor-file-add" onClick={addCodeFile} aria-label="Criar nova aba de código" title="Nova aba de código">+</button>
              </div>

              <div
                className="editor-tabs"
                role="group"
                aria-label="Linguagem do código"
              >
                <button
                  type="button"
                  className={
                    "editor-tab " +
                    (activeLanguage === "qasm" ? "active" : "")
                  }
                  onClick={() => {
                    selectLanguage("qasm");
                  }}
                  aria-pressed={activeLanguage === "qasm"}
                >
                  OpenQASM 2
                </button>

                <button
                  type="button"
                  className={
                    "editor-tab " +
                    (activeLanguage === "python" ? "active" : "")
                  }
                  onClick={() => {
                    selectLanguage("python");
                  }}
                  aria-pressed={activeLanguage === "python"}
                >
                  Python
                </button>
              </div>

              <div
                className={
                  "editor-shell context-editor-shell " +
                  (showInputErrors ? "input-invalid" : "")
                }
              >
                <div className="editor-toolbar">
                  <span className="language-label">
                    {activeLanguage === "qasm" ? "OPENQASM 2" : "PYTHON"}
                  </span>

                  <div className="toolbar-actions">
                    <button
                      type="button"
                      className="button-quiet"
                      onClick={handlePaste}
                      aria-label="Colar código"
                      title="Colar código"
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      className="button-quiet"
                      onClick={handleCopy}
                      aria-label="Copiar código"
                      title="Copiar código"
                    >
                      ⧉
                    </button>
                  </div>
                </div>

                <CodeEditor
                  language={activeLanguage}
                  value={editorCode}
                  onChange={(value) => {
                    setActiveConversationId(null);
                    setCode(value);
                    setEditorMessage("");

                    if (value.trim()) {
                      setShowInputErrors(false);
                      setFeedback(null);
                    }
                  }}
                />
              </div>

              <div className="editor-footer context-editor-footer">
                <span>
                  {activeLanguage === "qasm" ? "OpenQASM 2" : "Python"}
                </span>
                {activeCodeFile?.isDirty && <span className="editor-unsaved-status"><i /> Alterações não salvas</span>}
                <span>{editorCode.length} caracteres</span>
              </div>

              {editorMessage && (
                <FeedbackMessage tone="info" className="editor-message" message={editorMessage} />
              )}

              <div className="code-dialog-actions">
                <button
                  type="button"
                  className="button-quiet code-dialog-clear"
                  onClick={clearCurrentCode}
                  disabled={!editorCode.trim()}
                >
                  Limpar
                </button>
                <div>
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={cancelCodeEditor}
                  >
                    Cancelar
                  </button>
                  <button
                    type="button"
                    className="button-primary"
                    onClick={applyCodeEditor}
                  >
                    Adicionar código
                  </button>
                </div>
              </div>
                </aside>
              </div>
            )}
          </form>

          <footer className="page-footer">
            QuantumOne — Seu laboratório quântico.
          </footer>
        </main>
      </>
    </AppFrame>
  );
}
