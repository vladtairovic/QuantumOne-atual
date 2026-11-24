"use client";

import { useEffect, useRef, useState } from "react";
import type { ProgressoAnalise } from "../../services/analysis-api";

/** Último progresso recebido e o instante local em que chegou (base do contador ao vivo). */
export type LiveProgress = { data: ProgressoAnalise; receivedAt: number };

function formatElapsed(seconds: number) {
  const total = Math.max(0, Math.floor(seconds));
  if (total < 60) return `${total} s`;
  return `${Math.floor(total / 60)} min ${String(total % 60).padStart(2, "0")} s`;
}

/**
 * Cartão de andamento exibido na conversa enquanto a análise está em curso.
 * O contador avança localmente a cada segundo a partir de segundos_na_etapa;
 * os contadores ficam fora da leitura do leitor de tela para não repetir a cada segundo.
 */
export default function AnalysisLiveStatus({ progress, startedAt, onCancel }: {
  progress: LiveProgress | null;
  startedAt: number;
  onCancel?: () => void;
}) {
  const [now, setNow] = useState(startedAt);
  const draftRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const draft = progress?.data.parcial ?? null;
  useEffect(() => {
    // Acompanha o fim do texto enquanto o modelo escreve.
    if (draftRef.current) draftRef.current.scrollTop = draftRef.current.scrollHeight;
  }, [draft]);

  const data = progress?.data;
  const sinceUpdate = progress ? Math.max(0, now - progress.receivedAt) / 1000 : 0;
  const localElapsed = Math.max(0, now - startedAt) / 1000;
  const stepSeconds = data ? data.segundos_na_etapa + sinceUpdate : localElapsed;
  const totalSeconds = data ? data.segundos_total + sinceUpdate : localElapsed;
  const history = data?.historico.slice(-4) ?? [];

  return (
    <section className="analysis-live-status" role="status" aria-live="polite" aria-label="Andamento da análise">
      <div className="analysis-live-status-heading">
        <span className="analysis-live-status-pulse" aria-hidden="true" />
        <strong>{data?.etapa.trim() || "Enviando para análise…"}</strong>
        <span className="analysis-live-status-timer" aria-hidden="true">{formatElapsed(stepSeconds)}</span>
      </div>
      {data?.modelo && (
        <p className="analysis-live-status-model">Modelo em uso: <code>{data.modelo}</code></p>
      )}
      {draft && (
        <div className="analysis-live-status-draft">
          <span className="analysis-live-status-draft-label">Escrevendo agora</span>
          {/* Fora do leitor de tela: o texto muda várias vezes por segundo; a resposta final é anunciada inteira. */}
          <div ref={draftRef} className="analysis-live-status-draft-text" aria-hidden="true">{draft}</div>
        </div>
      )}
      {history.length > 0 && (
        <ol className="analysis-live-status-history" aria-label="Etapas recentes">
          {history.map((step, index) => (
            <li key={`${step.texto}-${index}`}>
              <span>{step.texto}</span>
              <span className="analysis-live-status-seconds">{step.segundos.toFixed(1)} s</span>
            </li>
          ))}
        </ol>
      )}
      <div className="analysis-live-status-footer">
        <span aria-hidden="true">Tempo total: {formatElapsed(totalSeconds)}</span>
        {onCancel && <button type="button" onClick={onCancel}>Cancelar análise</button>}
      </div>
    </section>
  );
}
