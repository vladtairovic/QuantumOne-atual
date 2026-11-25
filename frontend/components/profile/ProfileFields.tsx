"use client";

import { useId } from "react";
import {
  NAME_MAX, PROFILE_ACCENTS, PROFILE_INTERESTS, profileInitials, type LocalProfile,
} from "./localProfile";

type Option = { value: string; label: string; hint?: string };

/** Avatar local: iniciais sobre um gradiente de neon escolhido pelo usuário. */
export function ProfileAvatar({ name, accent, size = 44 }: { name: string; accent: LocalProfile["accent"]; size?: number }) {
  return (
    <span className={`profile-avatar profile-accent-${accent}`} style={{ width: size, height: size, fontSize: size * 0.38 }} aria-hidden="true">
      {profileInitials(name)}
    </span>
  );
}

export function NameField({ value, onChange, autoFocus }: { value: string; onChange: (value: string) => void; autoFocus?: boolean }) {
  const id = useId();
  return (
    <div className="profile-name-field">
      <label htmlFor={id}>Como podemos te chamar?</label>
      <input
        id={id}
        type="text"
        autoComplete="nickname"
        maxLength={NAME_MAX}
        value={value}
        autoFocus={autoFocus}
        placeholder="Seu nome ou apelido"
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

/** Grupo de opções exclusivas em cartões de vidro (rádios nativos, acessíveis por teclado). */
export function ChoiceGroup({ legend, options, value, onChange, columns = 3 }: {
  legend: string;
  options: readonly Option[];
  value: string;
  onChange: (value: string) => void;
  columns?: number;
}) {
  const name = useId();
  return (
    <fieldset className="profile-choice-group">
      <legend>{legend}</legend>
      <div className="profile-choice-grid" style={{ ["--cols" as string]: columns }}>
        {options.map((option, index) => (
          <label key={option.value} className="profile-choice" style={{ ["--i" as string]: index }}>
            <input type="radio" name={name} value={option.value} checked={value === option.value} onChange={() => onChange(option.value)} />
            <span className="profile-choice-body">
              <strong>{option.label}</strong>
              {option.hint && <small>{option.hint}</small>}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function InterestGroup({ value, onChange }: { value: LocalProfile["interests"]; onChange: (value: LocalProfile["interests"]) => void }) {
  return (
    <fieldset className="profile-choice-group">
      <legend>O que você quer fazer aqui? <span className="profile-legend-hint">Escolha quantos quiser</span></legend>
      <div className="profile-chip-grid">
        {PROFILE_INTERESTS.map((option, index) => {
          const checked = value.includes(option.value);
          return (
            <label key={option.value} className="profile-chip" style={{ ["--i" as string]: index }}>
              <input
                type="checkbox"
                checked={checked}
                onChange={() => onChange(checked ? value.filter((item) => item !== option.value) : [...value, option.value])}
              />
              <span>{option.label}</span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

export function AccentGroup({ value, onChange, name }: { value: LocalProfile["accent"]; onChange: (value: LocalProfile["accent"]) => void; name: string }) {
  const group = useId();
  return (
    <fieldset className="profile-choice-group">
      <legend>Cor do seu avatar</legend>
      <div className="profile-accent-grid">
        {PROFILE_ACCENTS.map((option) => (
          <label key={option.value} className="profile-accent-option" title={option.label}>
            <input type="radio" name={group} checked={value === option.value} onChange={() => onChange(option.value)} />
            <ProfileAvatar name={name || "Q"} accent={option.value} size={40} />
            <span className="visually-hidden">{option.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
