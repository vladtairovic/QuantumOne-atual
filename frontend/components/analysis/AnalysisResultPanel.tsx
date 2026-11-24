"use client";

import { Fragment, useState, type ReactNode } from "react";
import type { AnalysisResultRecord, AnalyzedCircuitRecord, AnalyzedFileRecord } from "../layout/conversationTypes";
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
const CORRECOES: Record<AnalyzedCircuitRecord["correction"]["method"], string> = {
  nenhuma: "nenhuma", deterministica: "determinística", llm: "modelo de linguagem",
};
const TIPOS_ARQUIVO: Record<AnalyzedFileRecord["type"], string> = {
  circuito_qasm: "circuito OpenQASM", circuito_python: "circuito Python", python: "Python", notebook: "notebook",
  documento: "documento", dados: "dados", ignorado: "ignorado",
};
const MAX_DIAGNOSIS_LINES = 6;

function formatBytes(bytes?: number) {
  if (bytes === undefined) return "-";
  const unidades = ["B", "KiB", "MiB", "GiB", "TiB"];
  let valor = bytes, i = 0;
  while (valor >= 1024 && i < unidades.length - 1) { valor /= 1024; i++; }
  return `${valor >= 10 ? valor.toFixed(0) : valor.toFixed(1)} ${unidades[i]}`;
}

function formatSeconds(seconds: number) {
  return seconds >= 60 ? `${Math.floor(seconds / 60)} min ${String(Math.round(seconds % 60)).padStart(2, "0")} s` : `${seconds.toFixed(1)} s`;
}

function plural(count: number, singular: string, pluralForm: string) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

function attemptLabel(result: string) {
  if (result === "ok") return "respondeu";
  if (result === "pausado") return "pausado";
  if (result === "sem_tempo") return "sem tempo restante";
  if (result.startsWith("tempo_esgotado")) return "tempo esgotado";
  if (result === "resposta_vazia") return "resposta vazia";
  if (result.startsWith("http_")) return "HTTP " + result.slice(5);
  if (result.startsWith("erro_")) return "erro (" + result.slice(5).replace(/_/g, " ") + ")";
  return result.replace(/_/g, " ");
}

// ---------------------------------------------------------------------------
// Resposta em Markdown mínimo: quebras de linha, blocos ``` como <pre><code>,
// títulos (#), **negrito** e `código`. Tudo vira elementos React (sem HTML bruto).
// ---------------------------------------------------------------------------

type AnswerBlock = { kind: "text" | "code"; content: string; lang?: string };

function parseAnswer(text: string): AnswerBlock[] {
  const blocks: AnswerBlock[] = [];
  let buffer: string[] = [];
  let code: string[] | null = null;
  let lang = "";
  const flushText = () => {
    const content = buffer.join("\n").replace(/^\s*\n|\n\s*$/g, "");
    if (content.trim()) blocks.push({ kind: "text", content });
    buffer = [];
  };
  for (const line of text.split(/\r?\n/)) {
    if (code === null) {
      const fence = /^\s{0,3}```\s*([\w+#.-]*)/.exec(line);
      if (fence) {
        flushText();
        code = [];
        lang = fence[1] || "";
      } else buffer.push(line);
    } else if (/^\s{0,3}```\s*$/.test(line)) {
      blocks.push({ kind: "code", content: code.join("\n"), lang });
      code = null;
    } else code.push(line);
  }
  if (code !== null) blocks.push({ kind: "code", content: code.join("\n"), lang });
  flushText();
  return blocks;
}

function renderInline(line: string, key: string): ReactNode[] {
  return line.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean).map((part, index) => {
    if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) return <code key={`${key}-${index}`}>{part.slice(1, -1)}</code>;
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) return <strong key={`${key}-${index}`}>{part.slice(2, -2)}</strong>;
    return <Fragment key={`${key}-${index}`}>{part}</Fragment>;
  });
}

function AnswerText({ text }: { text: string }) {
  const blocks = parseAnswer(text);
  if (!blocks.length) return <p className="analysis-result-summary">{text}</p>;
  return (
    <div className="analysis-answer">
      {blocks.map((block, index) => block.kind === "code" ? (
        <pre key={index} className="analysis-answer-code" data-lang={block.lang || undefined}>
          {block.lang && <span className="analysis-answer-code-lang">{block.lang}</span>}
          <code>{block.content}</code>
        </pre>
      ) : (
        <p key={index} className="analysis-result-summary">
          {block.content.split("\n").map((line, lineIndex) => {
            const heading = /^#{1,6}\s+(.*)$/.exec(line);
            return (
              <Fragment key={lineIndex}>
                {lineIndex > 0 && "\n"}
                {heading
                  ? <strong className="analysis-answer-heading">{renderInline(heading[1], `${index}-${lineIndex}`)}</strong>
                  : renderInline(line, `${index}-${lineIndex}`)}
              </Fragment>
            );
          })}
        </p>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Circuitos analisados
// ---------------------------------------------------------------------------

function BeforeAfter({ label, before, after }: { label: string; before?: number; after?: number }) {
  if (before === undefined && after === undefined) return null;
  return <div><dt>{label}</dt><dd>{before ?? "-"} → {after ?? "-"}</dd></div>;
}

function CircuitCard({ circuit }: { circuit: AnalyzedCircuitRecord }) {
  const o = circuit.optimization;
  const correction = circuit.correction;
  const format = circuit.format === "qasm" ? "OpenQASM 2" : circuit.format === "python" ? "Python / Qiskit" : "formato não identificado";
  const diagnosis = circuit.diagnosis.slice(0, MAX_DIAGNOSIS_LINES);
  const hasCorrection = correction.method !== "nenhuma" || Boolean(correction.code);
  return (
    <article className="analysis-circuit">
      <header className="analysis-circuit-heading">
        <strong title={circuit.origin}>{circuit.origin === "editor" ? "Editor" : circuit.origin}</strong>
        <span className="analysis-badge">{format}</span>
        <span className={`analysis-badge ${circuit.validOriginal ? "is-ok" : "is-error"}`}>{circuit.validOriginal ? "válido" : "inválido"}</span>
        <span className="analysis-badge">{plural(circuit.problems, "problema", "problemas")}</span>
      </header>
      {diagnosis.length > 0 && (
        <ul className="analysis-circuit-diagnosis" aria-label="Diagnóstico">
          {diagnosis.map((line, index) => <li key={index}>{line}</li>)}
        </ul>
      )}
      {circuit.diagnosis.length > MAX_DIAGNOSIS_LINES && (
        <p className="analysis-circuit-more">+ {plural(circuit.diagnosis.length - MAX_DIAGNOSIS_LINES, "linha", "linhas")} de diagnóstico</p>
      )}
      {hasCorrection ? (
        <div className="analysis-circuit-block">
          <h4>Correção</h4>
          <p>
            Método: {CORRECOES[correction.method]}{correction.model ? ` · ${correction.model}` : ""}
            {" · "}<span className={correction.valid ? "analysis-text-ok" : "analysis-text-error"}>{correction.valid ? "validada" : "não validada"}</span>
          </p>
          {correction.explanation && <p>{correction.explanation}</p>}
          {correction.code && <pre><code>{correction.code}</code></pre>}
        </div>
      ) : !circuit.validOriginal && (
        <p className="analysis-circuit-more">Nenhuma correção automática foi proposta.</p>
      )}
      {o && (
        <div className="analysis-circuit-block">
          <h4>Otimização{o.profile ? ` · ${PERFIS[o.profile] || o.profile}` : ""}</h4>
          <p>
            Verificação: {METODOS[o.verificationMethod || "nenhum"] || o.verificationMethod}
            {o.guarantee ? ` · garantia ${o.guarantee}` : ""}
            {" · "}{o.equivalent === true ? "equivalente" : o.equivalent === false ? "não equivalente" : "equivalência não confirmada"}
          </p>
          <dl className="analysis-result-metrics">
            <BeforeAfter label="Portas" before={o.before?.operations} after={o.after?.operations} />
            <BeforeAfter label="Profundidade" before={o.before?.depth} after={o.after?.depth} />
            <BeforeAfter label="Portas de 2 qubits" before={o.before?.twoQubit} after={o.after?.twoQubit} />
            <BeforeAfter label="CX equivalentes" before={o.cxBefore} after={o.cxAfter} />
            {(o.after?.qubits ?? o.before?.qubits) !== undefined && <div><dt>Qubits</dt><dd>{o.after?.qubits ?? o.before?.qubits}</dd></div>}
            {o.simulationMethod && <div><dt>Simulação recomendada</dt><dd className="analysis-engine-text" title={o.simulationReason}>{o.simulationMethod}</dd></div>}
            {o.memoryBytes !== undefined && <div><dt>Statevector exigiria</dt><dd>{formatBytes(o.memoryBytes)}</dd></div>}
          </dl>
          {o.strategy && <p className="analysis-circuit-more">Estratégia: {o.strategy}</p>}
          {o.warnings.length > 0 && <ul className="analysis-result-warnings">{o.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
          {o.optimizedQasm && (
            <details className="analysis-collapsible">
              <summary>Circuito otimizado (OpenQASM 2)</summary>
              <pre><code>{o.optimizedQasm}</code></pre>
            </details>
          )}
        </div>
      )}
      {circuit.error && <p className="analysis-circuit-error">Erro: {circuit.error}</p>}
    </article>
  );
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
  // Resultados da análise unificada mostram métricas e propostas por circuito;
  // os campos agregados continuam no registro para as ferramentas Diff e Qiskit.
  const unified = Boolean(result.circuits?.length);
  const usedWeb = result.sources.some((source) => source.origin === "web");
  const attempts = result.llmAttempts ?? [];
  const files = result.files ?? [];
  const timeline = result.timeline ?? [];
  return (
    <section className="analysis-result-panel" aria-labelledby="analysis-result-title">
      <div className="analysis-result-heading">
        <span className="analysis-result-mark" aria-hidden="true">✦</span>
        <div><span className="analysis-result-eyebrow">QONE AGENT · ANÁLISE</span><h2 id="analysis-result-title">Resultado</h2></div>
        <div className="analysis-result-actions"><button type="button" onClick={copySummary}>Copiar resumo</button><button type="button" onClick={exportReport}>Exportar JSON</button></div>
      </div>
      {notice && <FeedbackMessage tone={notice.tone} message={notice.message} />}
      <section className="analysis-result-section"><h3>Resumo</h3><AnswerText text={result.summary} /></section>
      {result.insufficient && <FeedbackMessage tone="warning" message="A documentação recuperada foi insuficiente para fundamentar a resposta." />}
      {result.answerMode === "local" && <p className="analysis-answer-mode">Resposta extrativa da base local (sem modelo de linguagem).</p>}
      {result.answerMode === "llm" && result.llmModel && (
        <p className="analysis-answer-mode">Redigida por {result.llmModel} a partir da base local{usedWeb ? " e da internet" : ""}.</p>
      )}
      {attempts.length > 0 && (
        <details className="analysis-collapsible analysis-attempts">
          <summary>Tentativas dos modelos ({attempts.length})</summary>
          <ol>
            {attempts.map((attempt, index) => (
              <li key={`${attempt.model}-${index}`}>
                <code>{attempt.model}</code>
                <span className={attempt.result === "ok" ? "analysis-text-ok" : "analysis-text-muted"}>{attemptLabel(attempt.result)}</span>
                <span className="analysis-attempt-seconds">{formatSeconds(attempt.seconds)}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
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
      {unified && result.circuits && (
        <section className="analysis-result-section analysis-circuits">
          <h3>Circuitos analisados <span>{result.circuits.length}</span></h3>
          <div className="analysis-circuit-list">
            {result.circuits.map((circuit, index) => <CircuitCard key={`${circuit.origin}-${index}`} circuit={circuit} />)}
          </div>
        </section>
      )}
      {!unified && metrics && Object.values(metrics).some((value) => value !== undefined) && (
        <section className="analysis-result-section"><h3>Métricas</h3><dl className="analysis-result-metrics">
          {metrics.depthBefore !== undefined && <div><dt>Profundidade antes</dt><dd>{metrics.depthBefore}</dd></div>}
          {metrics.depthAfter !== undefined && <div><dt>Profundidade depois</dt><dd>{metrics.depthAfter}</dd></div>}
          {metrics.gatesBefore !== undefined && <div><dt>Portas antes</dt><dd>{metrics.gatesBefore}</dd></div>}
          {metrics.gatesAfter !== undefined && <div><dt>Portas depois</dt><dd>{metrics.gatesAfter}</dd></div>}
        </dl></section>
      )}
      {!unified && result.engine && <section className="analysis-result-section"><h3>Motor quântico</h3><dl className="analysis-result-metrics">
        <div><dt>Perfil</dt><dd>{PERFIS[result.engine.profile] || result.engine.profile}</dd></div>
        <div><dt>Verificação</dt><dd className="analysis-engine-text">{METODOS[result.engine.verificationMethod || "nenhum"] || result.engine.verificationMethod}{result.engine.guarantee === "aproximada" ? " · aproximada (diferença desprezível medida)" : ""}</dd></div>
        {result.engine.cxBefore !== undefined && <div><dt>CX equivalentes</dt><dd>{result.engine.cxBefore} → {result.engine.cxAfter}</dd></div>}
        {result.engine.twoQubitBefore !== undefined && <div><dt>Portas de 2 qubits</dt><dd>{result.engine.twoQubitBefore} → {result.engine.twoQubitAfter}</dd></div>}
        <div><dt>Simulação recomendada</dt><dd className="analysis-engine-text" title={result.engine.simulationReason}>{result.engine.simulationMethod || "-"}</dd></div>
        <div><dt>Statevector exigiria</dt><dd>{formatBytes(result.engine.memoryBytes)}</dd></div>
      </dl>{result.engine.tradeoffs?.length ? <ul className="analysis-result-warnings">{result.engine.tradeoffs.map((t, k) => <li key={k}>{t}</li>)}</ul> : null}</section>}
      {!unified && result.suggestions.length > 0 && <section className="analysis-result-section"><h3>Sugestões <span>{result.suggestions.length}</span></h3><div className="analysis-suggestions">
        {result.suggestions.map((item, index) => <article key={`${item.title}-${index}`} className="analysis-suggestion"><h4>{item.title}</h4><p>{item.description}</p><p><strong>Justificativa:</strong> {item.rationale}</p>{item.code && <pre><code>{item.code}</code></pre>}</article>)}
      </div></section>}
      {files.length > 0 && (
        <details className="analysis-collapsible analysis-files">
          <summary>Arquivos analisados ({files.length})</summary>
          <ul>
            {files.map((file, index) => (
              <li key={`${file.name}-${index}`}>
                <span className="analysis-file-name" title={file.name}>{file.name}</span>
                <span className={`analysis-badge${file.type === "ignorado" ? " is-muted" : ""}`}>{TIPOS_ARQUIVO[file.type] || file.type}</span>
                <span className="analysis-file-size">{formatBytes(file.size)}</span>
                {file.reason && <em className="analysis-file-reason">{file.reason}</em>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {result.warnings?.length ? <section className="analysis-result-section"><h3>Observações</h3><ul className="analysis-result-warnings">{result.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul></section> : null}
      <CompactSources sources={result.sources} />
      {timeline.length > 0 && (
        <details className="analysis-collapsible analysis-timeline">
          <summary>Etapas da análise ({timeline.length}){result.totalSeconds !== undefined ? ` · ${formatSeconds(result.totalSeconds)}` : ""}</summary>
          <ol>
            {timeline.map((step, index) => (
              <li key={`${step.text}-${index}`}><span>{step.text}</span><span className="analysis-attempt-seconds">{formatSeconds(step.seconds)}</span></li>
            ))}
          </ol>
        </details>
      )}
      {result.requestId && <p className="analysis-result-id">Referência: <code>{result.requestId}</code></p>}
    </section>
  );
}
