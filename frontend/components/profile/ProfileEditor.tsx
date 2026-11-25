"use client";

import { useState, type FormEvent } from "react";
import FeedbackMessage from "../ui/FeedbackMessage";
import {
  NAME_MIN, PROFILE_ANSWER_STYLES, PROFILE_EXPERIENCE, PROFILE_ROLES,
  saveLocalProfile, useLocalProfile, type LocalProfile,
} from "./localProfile";
import { AccentGroup, ChoiceGroup, InterestGroup, NameField, ProfileAvatar } from "./ProfileFields";

const LANGUAGES = [
  { value: "qasm", label: "OpenQASM 2", hint: "Circuitos em texto" },
  { value: "python", label: "Python / Qiskit", hint: "Código Qiskit" },
] as const;

function EditorForm({ profile }: { profile: LocalProfile }) {
  const [draft, setDraft] = useState(profile);
  const [status, setStatus] = useState<{ tone: "success" | "error"; message: string } | null>(null);
  const update = <K extends keyof LocalProfile>(key: K, value: LocalProfile[K]) => {
    setStatus(null);
    setDraft((current) => ({ ...current, [key]: value }));
  };
  const changed = JSON.stringify({ ...draft, updatedAt: "" }) !== JSON.stringify({ ...profile, updatedAt: "" });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (draft.name.trim().length < NAME_MIN) {
      setStatus({ tone: "error", message: `Informe um nome com pelo menos ${NAME_MIN} caracteres.` });
      return;
    }
    try {
      saveLocalProfile(draft);
      setStatus({ tone: "success", message: "Perfil atualizado neste dispositivo." });
    } catch (problem) {
      setStatus({ tone: "error", message: problem instanceof Error ? problem.message : "Não foi possível salvar o perfil." });
    }
  };

  return (
    <form className="profile-editor-grid" onSubmit={submit}>
      <section className="panel profile-editor-card profile-editor-identity">
        <ProfileAvatar name={draft.name || "Q"} accent={draft.accent} size={84} />
        <div>
          <strong>{draft.name || "Sem nome"}</strong>
          <span>Perfil local desde {new Date(profile.createdAt).toLocaleDateString("pt-BR")}</span>
        </div>
        <NameField value={draft.name} onChange={(value) => update("name", value)} />
        <AccentGroup value={draft.accent} onChange={(value) => update("accent", value)} name={draft.name} />
      </section>

      <section className="panel profile-editor-card">
        <h2>Sobre você</h2>
        <ChoiceGroup legend="Perfil" options={PROFILE_ROLES} value={draft.role} onChange={(value) => update("role", value as LocalProfile["role"])} />
        <ChoiceGroup legend="Experiência" options={PROFILE_EXPERIENCE} value={draft.experience} onChange={(value) => update("experience", value as LocalProfile["experience"])} />
      </section>

      <section className="panel profile-editor-card">
        <h2>Interesses e preferências</h2>
        <InterestGroup value={draft.interests} onChange={(value) => update("interests", value)} />
        <ChoiceGroup legend="Linguagem padrão do editor" options={LANGUAGES} value={draft.preferredLanguage} onChange={(value) => update("preferredLanguage", value as LocalProfile["preferredLanguage"])} columns={2} />
        <ChoiceGroup legend="Estilo das explicações" options={PROFILE_ANSWER_STYLES} value={draft.answerStyle} onChange={(value) => update("answerStyle", value as LocalProfile["answerStyle"])} />
      </section>

      <div className="profile-editor-actions">
        {status && <FeedbackMessage tone={status.tone} message={status.message} />}
        <button type="button" className="button-secondary" disabled={!changed} onClick={() => { setDraft(profile); setStatus(null); }}>Desfazer alterações</button>
        <button type="submit" className="button-primary" disabled={!changed}>Salvar perfil</button>
      </div>
    </form>
  );
}

/** "Meu perfil": edição do perfil local criado no onboarding. */
export default function ProfileEditor() {
  const profile = useLocalProfile();
  return (
    <main className="profile-editor-page">
      <header className="profile-editor-intro">
        <span className="section-kicker">QUANTUMONE / PERFIL</span>
        <h1>Meu perfil</h1>
        <p>Seu perfil fica salvo apenas neste dispositivo e personaliza sugestões, editor e explicações.</p>
      </header>
      {profile === "loading" ? null : profile ? (
        <EditorForm key={profile.createdAt} profile={profile} />
      ) : (
        <p className="profile-editor-empty">Conclua a personalização inicial para criar seu perfil.</p>
      )}
    </main>
  );
}
