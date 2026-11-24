"use client";

import { useId, useState } from "react";

export type CompactSource = { title: string; origin?: string; excerpt?: string; url?: string };

function safeExternalUrl(value?: string) {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    const localHttp = url.protocol === "http:" && ["localhost", "127.0.0.1", "::1"].includes(url.hostname);
    return (url.protocol === "https:" || localHttp) && !url.username && !url.password ? url.href : undefined;
  } catch {
    return undefined;
  }
}

function originLabel(source: CompactSource) {
  if (source.url?.startsWith("qone://") || source.origin === "qone_interno") return "conhecimento interno";
  if (source.origin === "web") return "internet";
  return source.origin === "local" || !source.origin ? "base local" : source.origin;
}

/**
 * Caixa de fontes compacta: recolhida por padrão, com cabeçalho discreto
 * "Fontes consultadas (N)". Não abre sozinha, mesmo com muitas referências.
 */
export default function CompactSources({ sources }: { sources: CompactSource[] }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  if (!sources.length) return null;
  return (
    <section className={`compact-sources${open ? " is-open" : ""}`} aria-label="Fontes consultadas">
      <button
        type="button"
        className="compact-sources-toggle"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >
        <span>Fontes consultadas ({sources.length})</span>
        <span className="compact-sources-chevron" aria-hidden="true">⌄</span>
      </button>
      <div id={panelId} className="compact-sources-body" role="region" aria-hidden={!open} inert={!open}>
        <div className="compact-sources-inner">
          <ol className="compact-sources-list">
            {sources.map((source, index) => {
              const href = safeExternalUrl(source.url);
              return (
                <li key={`${source.title}-${index}`}>
                  <div className="compact-sources-title">
                    <span className="compact-sources-number">{index + 1}</span>
                    <strong>{source.title}</strong>
                    <span className={`compact-sources-origin${source.origin === "web" ? " is-web" : ""}`}>{originLabel(source)}</span>
                  </div>
                  {source.excerpt && <p>{source.excerpt.length > 420 ? source.excerpt.slice(0, 420) + "…" : source.excerpt}</p>}
                  {href && <a href={href} target="_blank" rel="noopener noreferrer">Abrir fonte <span aria-hidden="true">↗</span></a>}
                </li>
              );
            })}
          </ol>
        </div>
      </div>
    </section>
  );
}
