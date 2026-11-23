"use client";

import { useState } from "react";
import type { AnalysisResultRecord } from "../layout/conversationTypes";
import FeedbackMessage from "../ui/FeedbackMessage";
import CompactSources from "./CompactSources";

const METODOS: Record<string, string> = {
  operador: "unitária comparada (exata)", clifford: "tableau de Clifford (exata)", reescrita: "reescrita U·V† (exata)",
  estados_aleatorios: "estados aleatórios (amostral)", amostragem: "amostragem de medições (estatística)",
  identidade: "circuito original mantido", nenhum: "não verificada",
};
const PERFIS: Record<string, string> = {
  rapido: "Rápido", balanceado: "Balanceado", avancado: "Avançado", experimental: "Experimental", nivel_fixo: "Nível fixo",
};

function formatBytes(bytes?: number) {
  if (bytes === undefined) return "-";
  const unidades = ["B", "KiB", "MiB", "GiB", "TiB"];
  let valor = bytes, i = 0;
  while (valor >= 1024 && i < unidades.length - 1) { valor /= 1024; i++; }
  return `${valor >= 10 ? valor.toFixed(0) : valor.toFixed(1)} ${unidades[i]}`;
}

export default function AnalysisResultPanel({ result }: { result?: AnalysisResultRecord }) {
  const [notice, setNotice] = useState<{ tone: "success" | "error"; message: string } | null>(null);

  async function copySummary() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.summary);
      setNotice({ tone: "success", message: "Resumo copiado para a área de transferência." });
    } catch {
      setNotice({ tone: "error", message: "Não foi possível copiar o resumo. Verifique a permissão da área de transferência." });
    }
  }

  function exportReport() {
    if (!result) return;
    try {
      const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "relatorio-analise-quantumone.json";
      anchor.click();
      URL.revokeObjectURL(url);
      setNotice({ tone: "success", message: "Relatório exportado." });
    } catch {
      setNotice({ tone: "error", message: "Não foi possível exportar o relatório." });
    }
  }

  if (!result) return (
    <section className="analysis-result-panel analysis-result-empty" aria-labelledby="analysis-result-title">
      <div className="analysis-result-heading">
        <span className="analysis-result-mark" aria-hidden="true">✦</span>
        <div><span className="analysis-result-eyebrow">QONE AGENT</span><h2 id="analysis-result-title">Solicitação registrada</h2></div>
      </div>
      <p>A solicitação foi adicionada à conversa. O espaço abaixo organiza o resumo, as sugestões, as justificativas e as fontes da análise.</p>
      <div className="analysis-result-sections" aria-label="Seções do relatório">
        <span>Resumo</span><span>Sugestões</span><span>Justificativas</span><span>Fontes</span>
      </div>
    </section>
  );

  const metrics = result.metrics;
  return (
    <section className="analysis-result-panel" aria-labelledby="analysis-result-title">
      <div className="analysis-result-heading">
        <span className="analysis-result-mark" aria-hidden="true">✦</span>
        <div><span className="analysis-result-eyebrow">QONE AGENT · ANÁLISE</span><h2 id="analysis-result-title">Resultado</h2></div>
        <div className="analysis-result-actions"><button type="button" onClick={copySummary}>Copiar resumo</button><button type="button" onClick={exportReport}>Exportar JSON</button></div>
      </div>
      {notice && <FeedbackMessage tone={notice.tone} message={notice.message} />}
      <section className="analysis-result-section"><h3>Resumo</h3><p className="analysis-result-summary">{result.summary}</p></section>
      {result.insufficient && <FeedbackMessage tone="warning" message="A documentação recuperada foi insuficiente para fundamentar a resposta." />}
      {result.answerMode === "local" && <p className="analysis-answer-mode">Resposta extrativa da base local (sem modelo de linguagem).</p>}
      {result.diagnosis && <section className="analysis-result-section analysis-diagnosis"><h3>Diagnóstico <span>{result.diagnosis.problems.length}</span>
        <em className={`analysis-confidence analysis-confidence-${result.diagnosis.confidence}`}>confiança {result.diagnosis.confidence}</em></h3>
        <ol className="analysis-diagnosis-list">{result.diagnosis.problems.map((p, index) => <li key={`${p.code}-${index}`}>
          <div><code>{p.code}</code>{p.line ? <span>linha {p.line}</span> : null}<span className={`analysis-confidence analysis-confidence-${p.confidence}`}>{p.confidence}</span></div>
          <p>{p.message}</p>{p.cause && <p className="analysis-diagnosis-cause">Causa: {p.cause}</p>}
          {p.fix && <p className="analysis-diagnosis-fix">Correção: {p.fix}</p>}
          {p.alternatives.length > 0 && <ul>{p.alternatives.map((a, k) => <li key={k}>{a}</li>)}</ul>}
        </li>)}</ol>
        {result.diagnosis.correctedCode && <><h4 className="analysis-diagnosis-subtitle">Código corrigido {result.diagnosis.valid ? "(validado)" : "(não validado)"}</h4>
          <pre><code>{result.diagnosis.correctedCode}</code></pre></>}
        {result.diagnosis.limitations.length > 0 && <ul className="analysis-result-warnings">{result.diagnosis.limitations.map((l, k) => <li key={k}>{l}</li>)}</ul>}
      </section>}
      {metrics && Object.values(metrics).some((value) => value !== undefined) && (
        <section className="analysis-result-section"><h3>Métricas</h3><dl className="analysis-result-metrics">
          {metrics.depthBefore !== undefined && <div><dt>Profundidade antes</dt><dd>{metrics.depthBefore}</dd></div>}
          {metrics.depthAfter !== undefined && <div><dt>Profundidade depois</dt><dd>{metrics.depthAfter}</dd></div>}
          {metrics.gatesBefore !== undefined && <div><dt>Portas antes</dt><dd>{metrics.gatesBefore}</dd></div>}
          {metrics.gatesAfter !== undefined && <div><dt>Portas depois</dt><dd>{metrics.gatesAfter}</dd></div>}
        </dl></section>
      )}
      {result.engine && <section className="analysis-result-section"><h3>Motor quântico</h3><dl className="analysis-result-metrics">
        <div><dt>Perfil</dt><dd>{PERFIS[result.engine.profile] || result.engine.profile}</dd></div>
        <div><dt>Verificação</dt><dd className="analysis-engine-text">{METODOS[result.engine.verificationMethod || "nenhum"] || result.engine.verificationMethod}{result.engine.guarantee === "aproximada" ? " · aproximada (diferença desprezível medida)" : ""}</dd></div>
        {result.engine.cxBefore !== undefined && <div><dt>CX equivalentes</dt><dd>{result.engine.cxBefore} → {result.engine.cxAfter}</dd></div>}
        {result.engine.twoQubitBefore !== undefined && <div><dt>Portas de 2 qubits</dt><dd>{result.engine.twoQubitBefore} → {result.engine.twoQubitAfter}</dd></div>}
        <div><dt>Simulação recomendada</dt><dd className="analysis-engine-text" title={result.engine.simulationReason}>{result.engine.simulationMethod || "-"}</dd></div>
        <div><dt>Statevector exigiria</dt><dd>{formatBytes(result.engine.memoryBytes)}</dd></div>
      </dl>{result.engine.tradeoffs?.length ? <ul className="analysis-result-warnings">{result.engine.tradeoffs.map((t, k) => <li key={k}>{t}</li>)}</ul> : null}</section>}
      {result.suggestions.length > 0 && <section className="analysis-result-section"><h3>Sugestões <span>{result.suggestions.length}</span></h3><div className="analysis-suggestions">
        {result.suggestions.map((item, index) => <article key={`${item.title}-${index}`} className="analysis-suggestion"><h4>{item.title}</h4><p>{item.description}</p><p><strong>Justificativa:</strong> {item.rationale}</p>{item.code && <pre><code>{item.code}</code></pre>}</article>)}
      </div></section>}
      {result.warnings?.length ? <section className="analysis-result-section"><h3>Observações</h3><ul className="analysis-result-warnings">{result.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul></section> : null}
      <CompactSources sources={result.sources} />
      {result.requestId && <p className="analysis-result-id">Referência: <code>{result.requestId}</code></p>}
    </section>
  );
}
