"use client";

import { useSyncExternalStore } from "react";

/**
 * Perfil local do usuário: criado na primeira abertura (onboarding por etapas),
 * salvo só neste dispositivo e editável depois em "Meu perfil". Não há conta nem servidor.
 */
export const PROFILE_ROLES = [
  { value: "estudante", label: "Estudante", hint: "Aprendendo computação quântica" },
  { value: "pesquisador", label: "Pesquisador(a)", hint: "Experimentos e artigos" },
  { value: "desenvolvedor", label: "Desenvolvedor(a)", hint: "Código e integração" },
  { value: "educador", label: "Educador(a)", hint: "Aulas e materiais" },
  { value: "curioso", label: "Curioso(a)", hint: "Explorando o assunto" },
] as const;

export const PROFILE_EXPERIENCE = [
  { value: "iniciante", label: "Iniciante", hint: "Primeiros circuitos" },
  { value: "intermediario", label: "Intermediário", hint: "Já uso Qiskit" },
  { value: "avancado", label: "Avançado", hint: "Otimização e hardware real" },
] as const;

export const PROFILE_INTERESTS = [
  { value: "otimizacao", label: "Otimizar circuitos", prompt: "Reduzir profundidade e portas de dois qubits" },
  { value: "depuracao", label: "Corrigir erros", prompt: "Encontrar e corrigir erros no meu circuito" },
  { value: "algoritmos", label: "Algoritmos", prompt: "Explicar o algoritmo deste circuito" },
  { value: "hardware", label: "Hardware real", prompt: "Adaptar o circuito ao backend da IBM" },
  { value: "aprendizado", label: "Aprender conceitos", prompt: "Explicar o circuito passo a passo" },
  { value: "migracao", label: "Migrar para Qiskit 2", prompt: "Migrar este código para o Qiskit 2" },
] as const;

export const PROFILE_ANSWER_STYLES = [
  { value: "direta", label: "Direta", hint: "Só o essencial" },
  { value: "didatica", label: "Didática", hint: "Com explicações e analogias" },
  { value: "detalhada", label: "Detalhada", hint: "Com métricas e justificativas" },
] as const;

export const PROFILE_ACCENTS = [
  { value: "violeta", label: "Violeta" },
  { value: "magenta", label: "Magenta" },
  { value: "ciano", label: "Ciano" },
  { value: "aurora", label: "Aurora" },
] as const;

type ValueOf<T extends readonly { value: string }[]> = T[number]["value"];

export type LocalProfile = {
  name: string;
  role: ValueOf<typeof PROFILE_ROLES>;
  experience: ValueOf<typeof PROFILE_EXPERIENCE>;
  interests: ValueOf<typeof PROFILE_INTERESTS>[];
  preferredLanguage: "qasm" | "python";
  answerStyle: ValueOf<typeof PROFILE_ANSWER_STYLES>;
  accent: ValueOf<typeof PROFILE_ACCENTS>;
  createdAt: string;
  updatedAt: string;
};

const PROFILE_KEY = "quantumone.localProfile.v1";
const LEGACY_PROFILE_KEY = "quantumone.profile.v1";
const PREFERENCES_KEY = "quantumone.preferences.v1";
const CHANGE_EVENT = "quantumone:profile-changed";
export const NAME_MIN = 2;
export const NAME_MAX = 40;

export function defaultProfile(): LocalProfile {
  const now = new Date().toISOString();
  return {
    name: "", role: "estudante", experience: "iniciante", interests: ["otimizacao", "aprendizado"],
    preferredLanguage: "qasm", answerStyle: "didatica", accent: "violeta", createdAt: now, updatedAt: now,
  };
}

function pick<T extends readonly { value: string }[]>(options: T, value: unknown, fallback: ValueOf<T>): ValueOf<T> {
  return options.some((option) => option.value === value) ? (value as ValueOf<T>) : fallback;
}

export function parseLocalProfile(value: unknown): LocalProfile | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const name = typeof raw.name === "string" ? raw.name.trim().slice(0, NAME_MAX) : "";
  if (name.length < NAME_MIN) return null;
  const base = defaultProfile();
  const date = (input: unknown) => (typeof input === "string" && !Number.isNaN(Date.parse(input)) ? input : base.createdAt);
  const interests = Array.isArray(raw.interests)
    ? [...new Set(raw.interests.filter((item): item is LocalProfile["interests"][number] =>
      PROFILE_INTERESTS.some((option) => option.value === item)))]
    : base.interests;
  return {
    name,
    role: pick(PROFILE_ROLES, raw.role, base.role),
    experience: pick(PROFILE_EXPERIENCE, raw.experience, base.experience),
    interests,
    preferredLanguage: raw.preferredLanguage === "python" ? "python" : "qasm",
    answerStyle: pick(PROFILE_ANSWER_STYLES, raw.answerStyle, base.answerStyle),
    accent: pick(PROFILE_ACCENTS, raw.accent, base.accent),
    createdAt: date(raw.createdAt),
    updatedAt: date(raw.updatedAt),
  };
}

function readRaw(): string | null {
  try {
    return window.localStorage.getItem(PROFILE_KEY);
  } catch {
    return null;
  }
}

export function readLocalProfile(): LocalProfile | null {
  if (typeof window === "undefined") return null;
  try {
    return parseLocalProfile(JSON.parse(readRaw() ?? "null"));
  } catch {
    return null;
  }
}

/** Salva o perfil e aplica as preferências derivadas (linguagem padrão do editor). */
export function saveLocalProfile(profile: LocalProfile) {
  const normalized = parseLocalProfile({ ...profile, updatedAt: new Date().toISOString() });
  if (!normalized) throw new Error("Informe um nome com pelo menos 2 caracteres.");
  window.localStorage.setItem(PROFILE_KEY, JSON.stringify(normalized));
  // Restos do antigo cadastro com e-mail não são mais usados.
  window.localStorage.removeItem(LEGACY_PROFILE_KEY);
  try {
    const preferences = JSON.parse(window.localStorage.getItem(PREFERENCES_KEY) ?? "{}");
    const next = { ...(preferences && typeof preferences === "object" ? preferences : {}), defaultLanguage: normalized.preferredLanguage };
    window.localStorage.setItem(PREFERENCES_KEY, JSON.stringify(next));
    window.dispatchEvent(new CustomEvent("quantumone:preferences-changed", { detail: next }));
  } catch {
    // Preferências corrompidas: o perfil continua salvo.
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
  return normalized;
}

function subscribe(onChange: () => void) {
  const onStorage = (event: StorageEvent) => { if (event.key === PROFILE_KEY) onChange(); };
  window.addEventListener("storage", onStorage);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

/**
 * Estado do perfil para componentes cliente.
 * "loading" no servidor/primeira hidratação, null quando ainda não existe perfil.
 */
export function useLocalProfile(): LocalProfile | null | "loading" {
  const raw = useSyncExternalStore(subscribe, () => readRaw() ?? "", () => "__server__");
  if (raw === "__server__") return "loading";
  if (!raw) return null;
  try {
    return parseLocalProfile(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function profileInitials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "Q";
}

/** Sugestões iniciais da conversa conforme os interesses escolhidos. */
export function suggestionsFor(profile: LocalProfile | null): string[] {
  const chosen = profile?.interests.length ? profile.interests : ["otimizacao", "aprendizado", "hardware"];
  return PROFILE_INTERESTS.filter((option) => chosen.includes(option.value)).slice(0, 3).map((option) => option.prompt);
}
