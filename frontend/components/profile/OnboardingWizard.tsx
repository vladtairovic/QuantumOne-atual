"use client";

import { useState } from "react";
import useModalFocus from "../ui/useModalFocus";
import Icon from "../ui/Icon";
import {
  NAME_MIN, PROFILE_ANSWER_STYLES, PROFILE_EXPERIENCE, PROFILE_INTERESTS, PROFILE_ROLES,
  defaultProfile, saveLocalProfile, useLocalProfile, type LocalProfile,
} from "./localProfile";
import { AccentGroup, ChoiceGroup, InterestGroup, NameField, ProfileAvatar } from "./ProfileFields";

const STEPS = ["Boas-vindas", "Sobre você", "Interesses", "Preferências", "Pronto"] as const;
const LANGUAGES = [
  { value: "qasm", label: "OpenQASM 2", hint: "Circuitos em texto" },
  { value: "python", label: "Python / Qiskit", hint: "Código Qiskit" },
] as const;

const labelOf = (options: readonly { value: string; label: string }[], value: string) =>
  options.find((option) => option.value === value)?.label ?? value;

/**
 * Personalização por etapas exibida só na primeira abertura (enquanto não existe perfil local).
 * Ao concluir ou pular, o perfil é salvo neste dispositivo e o assistente não volta a aparecer.
 */
export default function OnboardingWizard() {
  const stored = useLocalProfile();
  const open = stored === null;
  const [step, setStep] = useState(0);
  const [direction, setDirection] = useState<"forward" | "back">("forward");
  const [draft, setDraft] = useState<LocalProfile>(defaultProfile);
  const [error, setError] = useState("");
  const dialogRef = useModalFocus<HTMLDivElement>(open);

  if (!open) return null;

  const update = <K extends keyof LocalProfile>(key: K, value: LocalProfile[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const nameValid = draft.name.trim().length >= NAME_MIN;
  const last = STEPS.length - 1;

  const go = (next: number) => {
    setDirection(next > step ? "forward" : "back");
    setError("");
    setStep(next);
  };

  const finish = (profile: LocalProfile) => {
    try {
      saveLocalProfile(profile);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Não foi possível salvar o perfil neste navegador.");
    }
  };

  return (
    <div className="onboarding-overlay">
      <div
        className="onboarding-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="onboarding-title"
        aria-describedby="onboarding-step"
        ref={dialogRef}
        tabIndex={-1}
      >
        <header className="onboarding-header">
          <span className="section-kicker">PERSONALIZE SEU QUANTUMONE</span>
          <p id="onboarding-step" className="onboarding-step-label">Etapa {step + 1} de {STEPS.length} · {STEPS[step]}</p>
          <div className="onboarding-progress" aria-hidden="true">
            {STEPS.map((label, index) => (
              <span key={label} className={index <= step ? "is-done" : undefined} />
            ))}
          </div>
        </header>

        <form
          className="onboarding-body"
          onSubmit={(event) => {
            event.preventDefault();
            if (step === 0 && !nameValid) { setError(`Informe um nome com pelo menos ${NAME_MIN} caracteres.`); return; }
            if (step < last) go(step + 1); else finish(draft);
          }}
        >
          <div key={step} className={`onboarding-step onboarding-step-${direction}`}>
            {step === 0 && (
              <>
                <h2 id="onboarding-title">Bem-vindo ao seu laboratório quântico</h2>
                <p className="onboarding-lead">Em quatro passos rápidos, o QuantumOne se ajusta a você. Tudo fica salvo só neste dispositivo e pode ser alterado depois em <strong>Meu perfil</strong>.</p>
                <div className="onboarding-identity">
                  <ProfileAvatar name={draft.name || "Q"} accent={draft.accent} size={64} />
                  <NameField value={draft.name} onChange={(value) => update("name", value)} autoFocus />
                </div>
                <AccentGroup value={draft.accent} onChange={(value) => update("accent", value)} name={draft.name} />
              </>
            )}
            {step === 1 && (
              <>
                <h2 id="onboarding-title">Conte um pouco sobre você, {draft.name.trim().split(/\s+/)[0]}</h2>
                <ChoiceGroup legend="Qual é o seu perfil?" options={PROFILE_ROLES} value={draft.role} onChange={(value) => update("role", value as LocalProfile["role"])} />
                <ChoiceGroup legend="Sua experiência com computação quântica" options={PROFILE_EXPERIENCE} value={draft.experience} onChange={(value) => update("experience", value as LocalProfile["experience"])} />
              </>
            )}
            {step === 2 && (
              <>
                <h2 id="onboarding-title">Seus interesses</h2>
                <p className="onboarding-lead">Usamos isto para sugerir os primeiros pedidos na conversa.</p>
                <InterestGroup value={draft.interests} onChange={(value) => update("interests", value)} />
              </>
            )}
            {step === 3 && (
              <>
                <h2 id="onboarding-title">Como você prefere trabalhar?</h2>
                <ChoiceGroup legend="Linguagem padrão do editor" options={LANGUAGES} value={draft.preferredLanguage} onChange={(value) => update("preferredLanguage", value as LocalProfile["preferredLanguage"])} columns={2} />
                <ChoiceGroup legend="Estilo das explicações" options={PROFILE_ANSWER_STYLES} value={draft.answerStyle} onChange={(value) => update("answerStyle", value as LocalProfile["answerStyle"])} />
              </>
            )}
            {step === 4 && (
              <>
                <h2 id="onboarding-title">Tudo pronto, {draft.name.trim().split(/\s+/)[0]}!</h2>
                <div className="onboarding-summary">
                  <ProfileAvatar name={draft.name} accent={draft.accent} size={56} />
                  <dl>
                    <div><dt>Perfil</dt><dd>{labelOf(PROFILE_ROLES, draft.role)} · {labelOf(PROFILE_EXPERIENCE, draft.experience)}</dd></div>
                    <div><dt>Interesses</dt><dd>{draft.interests.length ? draft.interests.map((value) => labelOf(PROFILE_INTERESTS, value)).join(", ") : "Nenhum escolhido"}</dd></div>
                    <div><dt>Editor</dt><dd>{labelOf(LANGUAGES, draft.preferredLanguage)}</dd></div>
                    <div><dt>Explicações</dt><dd>{labelOf(PROFILE_ANSWER_STYLES, draft.answerStyle)}</dd></div>
                  </dl>
                </div>
                <p className="onboarding-lead">Você pode mudar qualquer escolha depois em <strong>Meu perfil</strong>, na barra lateral.</p>
              </>
            )}
          </div>

          {error && <p className="onboarding-error" role="alert">{error}</p>}

          <footer className="onboarding-actions">
            {step === 0 ? (
              <button type="button" className="button-quiet onboarding-skip" onClick={() => finish({ ...draft, name: draft.name.trim() || "Explorador(a)" })}>
                Pular por agora
              </button>
            ) : (
              <button type="button" className="button-secondary" onClick={() => go(step - 1)}>Voltar</button>
            )}
            <button type="submit" className="button-primary" disabled={step === 0 && !nameValid} aria-disabled={step === 0 && !nameValid}>
              {step === last ? "Começar a explorar" : "Continuar"}
              <Icon name={step === last ? "sparkle" : "arrowUp"} size={15} className={step === last ? undefined : "onboarding-next-icon"} />
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
