"use client";

import type { CSSProperties, ReactNode } from "react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { AttachmentRecord } from "../layout/conversationTypes";
import SourceDrawer, { type SourceDetails } from "../analysis/SourceDrawer";
import Icon, { type IconName } from "../ui/Icon";

export type WorkspaceToolId = "files" | "diff" | "qiskit" | "sources";

type WorkspaceToolsPanelProps = {
  activeTool: WorkspaceToolId;
  attachments: AttachmentRecord[];
  files: string[];
  onPickFiles: () => void;
  onRemoveAttachment: (id: string) => void;
  diffFiles?: DiffFilePreview[];
  onApproveDiffFile?: (id: string) => void;
  onRevertDiffFile?: (id: string) => void;
  qiskitData?: QiskitPreviewData;
  sources?: WorkspaceSource[];
};

export type DiffFilePreview = {
  id: string;
  path: string;
  originalLines: string[];
  proposedLines: string[];

};

export type QiskitGateRole = "control" | "target" | "swap" | "measure" | "reset" | "gate";

/** One entry per operand (qubit) of an operation; entries of the same operation share `op` and `column`. */
export type QiskitPreviewGate = {
  wire: number;
  column: number;
  /** Instruction name as written in the QASM source (e.g. "cx", "rz", "measure"). */
  label: string;
  /** Index of the operation this operand belongs to. */
  op?: number;
  /** How the operand is drawn (control dot, target, swap cross, box...). */
  role?: QiskitGateRole;
  /** Short text drawn inside the gate box (e.g. "RZ", "√X", "M"). */
  text?: string;
  /** Raw parameter list, without the parentheses. */
  params?: string;
  /** Lowest and highest wire touched by the operation (for the vertical connector). */
  span?: [number, number];
  /** Operation runs under a classical condition (`if`). */
  conditional?: boolean;
};

export type QiskitPreviewData = {
  depth?: number;
  operations?: number;
  qubits?: number;
  equivalence?: "equivalent" | "different" | "unknown";
  gates?: QiskitPreviewGate[];
};

export type WorkspaceSource = SourceDetails & { version?: string };

const SINGLE_CONTROL_GATES = new Set(["cx", "cy", "cz", "ch", "csx", "crx", "cry", "crz", "cp", "cphase", "cu", "cu1", "cu3", "cs", "csdg", "cnot", "cswap", "fredkin"]);
const DOUBLE_CONTROL_GATES = new Set(["ccx", "ccz", "toffoli", "rccx"]);
const SKIPPED_STATEMENTS = new Set([
  "openqasm", "include", "creg", "bit", "input", "output", "const", "let", "float", "int", "uint", "bool", "angle",
  "duration", "stretch", "complex", "array", "extern", "pragma", "return", "break", "continue", "end", "gphase",
  "if", "else", "for", "while", "box", "switch", "case", "default",
]);

function controlCount(name: string, operands: number) {
  if (operands < 2) return 0;
  if (SINGLE_CONTROL_GATES.has(name)) return 1;
  if (DOUBLE_CONTROL_GATES.has(name)) return Math.min(2, operands - 1);
  const numbered = /^c(\d+)(?:x|sqrtx|z)$/.exec(name);
  if (numbered) return Math.min(Number(numbered[1]), operands - 1);
  if (/^mc/.test(name)) return operands - 1;
  return 0;
}

function baseGateName(name: string) {
  if (name === "cnot" || name === "toffoli" || name === "rccx") return "x";
  if (name === "fredkin") return "swap";
  if (SINGLE_CONTROL_GATES.has(name)) return name.slice(1);
  return name.replace(/^(?:mc|c\d+|cc)/, "") || name;
}

function gateText(name: string) {
  switch (name) {
    case "sx": case "sqrtx": return "√X";
    case "sxdg": return "√X†";
    case "sdg": return "S†";
    case "tdg": return "T†";
    case "id": case "i": return "I";
    case "u": case "u3": return "U";
    case "phase": return "P";
    default: return name.toUpperCase();
  }
}

/** Index of the parenthesis closing the one at `start`, or -1. */
function closingParen(text: string, start: number) {
  let depth = 0;
  for (let index = start; index < text.length; index++) {
    if (text[index] === "(") depth++;
    else if (text[index] === ")" && --depth === 0) return index;
  }
  return -1;
}

/**
 * Turns an OpenQASM 2/3 program (as exported by Qiskit) into drawable gate entries.
 * Every operation is kept; columns are assigned as-soon-as-possible per wire, so operations on
 * disjoint qubits share a column and a multi-qubit gate blocks every wire between its operands.
 */
export function previewGates(qasm: string): QiskitPreviewGate[] {
  const registers = new Map<string, { offset: number; size: number }>();
  const gates: QiskitPreviewGate[] = [];
  const nextFree: number[] = [];
  let wires = 0;
  let opIndex = 0;

  // Split into statements, dropping comments and gate/def bodies; brace blocks of if/for are flattened.
  const source = qasm.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, "");
  const statements: { text: string; conditional: boolean }[] = [];
  const blocks: boolean[] = [];
  let buffer = "";
  let skipDepth = 0;
  const flush = () => {
    const text = buffer.trim();
    if (text) statements.push({ text, conditional: blocks.includes(true) });
    buffer = "";
  };
  for (let index = 0; index < source.length; index++) {
    const char = source[index];
    if (skipDepth) {
      if (char === "{") skipDepth++;
      else if (char === "}") skipDepth--;
      continue;
    }
    if (char === ";") flush();
    else if (char === "{") {
      if (/^\s*(?:gate|def|defcal|cal|opaque)\b/.test(buffer)) { skipDepth = 1; buffer = ""; continue; }
      blocks.push(/^\s*if\b/.test(buffer) || /^\s*else\b/.test(buffer));
      buffer = "";
    } else if (char === "}") { flush(); blocks.pop(); }
    else buffer += char;
  }
  flush();

  const resolve = (part: string): number[] => {
    const physical = /^\$(\d+)$/.exec(part);
    if (physical) {
      const wire = Number(physical[1]);
      wires = Math.max(wires, wire + 1);
      return [wire];
    }
    const whole = /^([A-Za-z_]\w*)$/.exec(part);
    if (whole) {
      const register = registers.get(whole[1]);
      return register ? Array.from({ length: register.size }, (_, index) => register.offset + index) : [];
    }
    const refs = [...part.matchAll(/([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]/g)];
    const ref = refs[refs.length - 1];
    const register = ref && registers.get(ref[1]);
    return register && Number(ref[2]) < register.size ? [register.offset + Number(ref[2])] : [];
  };

  const place = (min: number, max: number) => {
    let column = 0;
    for (let wire = min; wire <= max; wire++) column = Math.max(column, nextFree[wire] ?? 0);
    for (let wire = min; wire <= max; wire++) nextFree[wire] = column + 1;
    return column;
  };

  const emit = (label: string, params: string | undefined, operandWires: number[], conditional: boolean, extraControls: number, inverse: boolean) => {
    const min = Math.min(...operandWires);
    const max = Math.max(...operandWires);
    const column = place(min, max);
    const op = opIndex++;
    const name = label.toLowerCase();
    const span: [number, number] = [min, max];
    const common = { column, label, op, params, span, conditional: conditional || undefined };
    if (name === "measure") {
      operandWires.forEach((wire) => gates.push({ ...common, wire, role: "measure", text: "M" }));
      return;
    }
    if (name === "reset") {
      operandWires.forEach((wire) => gates.push({ ...common, wire, role: "reset", text: "|0⟩" }));
      return;
    }
    const nameControls = controlCount(name, operandWires.length);
    const controls = Math.min(operandWires.length - 1, nameControls + extraControls);
    const base = nameControls ? baseGateName(name) : name;
    operandWires.forEach((wire, index) => {
      if (index < controls) gates.push({ ...common, wire, role: "control" });
      else if (base === "swap") gates.push({ ...common, wire, role: "swap", text: "×" });
      else if (controls && (base === "x" || base === "cnot")) gates.push({ ...common, wire, role: "target", text: "X" });
      else if (controls && base === "z") gates.push({ ...common, wire, role: "control" });
      else gates.push({ ...common, wire, role: "gate", text: `${gateText(base)}${inverse ? "†" : ""}` });
    });
  };

  for (const statement of statements) {
    let text = statement.text;
    let conditional = statement.conditional;

    const qreg = /^qreg\s+([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]$/.exec(text);
    const qubitArray = /^qubit\s*\[\s*(\d+)\s*\]\s*([A-Za-z_]\w*)$/.exec(text);
    const qubitSingle = /^qubit\s+([A-Za-z_]\w*)$/.exec(text);
    if (qreg || qubitArray || qubitSingle) {
      const name = qreg?.[1] ?? qubitArray?.[2] ?? qubitSingle![1];
      const size = Number(qreg?.[2] ?? qubitArray?.[1] ?? 1);
      registers.set(name, { offset: wires, size });
      wires += size;
      continue;
    }

    // QASM 2 inline condition: `if(c==1) x q[0];`
    const inlineIf = /^if\s*\(/.exec(text);
    if (inlineIf) {
      const end = closingParen(text, inlineIf[0].length - 1);
      if (end < 0) continue;
      text = text.slice(end + 1).trim();
      conditional = true;
    }

    // QASM 3 measurement assignment: `c[0] = measure q[0];` / `bit[2] c = measure q;`
    const assignedMeasure = /=\s*measure\s+(.+)$/.exec(text);
    if (assignedMeasure) text = `measure ${assignedMeasure[1]}`;

    // QASM 3 gate modifiers: `ctrl @ x a, b;`, `inv @ s q[0];`
    let extraControls = 0;
    let inverse = false;
    for (let modifier = /^(ctrl|negctrl|inv|pow)\s*(?:\(([^)]*)\))?\s*@\s*/.exec(text); modifier; modifier = /^(ctrl|negctrl|inv|pow)\s*(?:\(([^)]*)\))?\s*@\s*/.exec(text)) {
      if (modifier[1] === "inv") inverse = !inverse;
      else if (modifier[1] !== "pow") extraControls += Number(modifier[2]) || 1;
      text = text.slice(modifier[0].length);
    }

    const nameMatch = /^[A-Za-z_]\w*/.exec(text);
    if (!nameMatch) continue;
    const label = nameMatch[0];
    if (SKIPPED_STATEMENTS.has(label.toLowerCase())) continue;
    let rest = text.slice(label.length).trimStart();
    let params: string | undefined;
    if (rest.startsWith("(")) {
      const end = closingParen(rest, 0);
      if (end < 0) continue;
      params = rest.slice(1, end).trim() || undefined;
      rest = rest.slice(end + 1).trimStart();
    }
    if (rest.startsWith("[")) rest = rest.slice(rest.indexOf("]") + 1).trimStart(); // QASM 3 designator, e.g. delay[100ns]

    const parts = rest.split("->")[0].split(",").map((part) => part.trim()).filter(Boolean);
    const operandWires = parts.map(resolve).filter((list) => list.length);

    if (label.toLowerCase() === "barrier") {
      const touched = parts.length ? operandWires.flat() : Array.from({ length: wires }, (_, wire) => wire);
      const sync = touched.reduce((column, wire) => Math.max(column, nextFree[wire] ?? 0), 0);
      touched.forEach((wire) => { nextFree[wire] = sync; });
      continue;
    }
    if (!operandWires.length || operandWires.length !== parts.length) continue;

    // Register broadcast (`h q;`, `cx a, b;` with equally sized registers) expands to one operation per index.
    const count = Math.max(...operandWires.map((list) => list.length));
    if (operandWires.some((list) => list.length !== 1 && list.length !== count)) continue;
    for (let index = 0; index < count; index++) {
      const instance = operandWires.map((list) => list[list.length === 1 ? 0 : index]);
      if (new Set(instance).size !== instance.length) continue;
      emit(label, params, instance, conditional, extraControls, inverse);
    }
  }
  return gates;
}

type DiffMarks = { removed: Uint8Array; added: Uint8Array; removedCount: number; addedCount: number };

const LCS_CELL_LIMIT = 2_000_000;

/** Marks lines removed from `original` and added in `proposed` (LCS on the changed middle, multiset fallback for huge files). */
function diffLineMarks(original: string[], proposed: string[]): DiffMarks {
  const a = original.map((line) => line.replace(/\r$/, ""));
  const b = proposed.map((line) => line.replace(/\r$/, ""));
  const removed = new Uint8Array(a.length);
  const added = new Uint8Array(b.length);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
  const n = endA - start;
  const m = endB - start;

  if (n && m && n * m <= LCS_CELL_LIMIT) {
    // table[i][j] = LCS length of a[start+i..endA) and b[start+j..endB)
    const width = m + 1;
    const table = new Uint32Array((n + 1) * width);
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        table[i * width + j] = a[start + i] === b[start + j]
          ? table[(i + 1) * width + j + 1] + 1
          : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (a[start + i] === b[start + j]) { i++; j++; }
      else if (table[(i + 1) * width + j] >= table[i * width + j + 1]) removed[start + i++] = 1;
      else added[start + j++] = 1;
    }
    for (; i < n; i++) removed[start + i] = 1;
    for (; j < m; j++) added[start + j] = 1;
  } else if (n && m) {
    const counts = new Map<string, number>();
    for (let j = start; j < endB; j++) counts.set(b[j], (counts.get(b[j]) ?? 0) + 1);
    for (let i = start; i < endA; i++) {
      const left = counts.get(a[i]) ?? 0;
      if (left) counts.set(a[i], left - 1);
      else removed[i] = 1;
    }
    const remaining = new Map<string, number>();
    for (let i = start; i < endA; i++) remaining.set(a[i], (remaining.get(a[i]) ?? 0) + 1);
    for (let j = start; j < endB; j++) {
      const left = remaining.get(b[j]) ?? 0;
      if (left) remaining.set(b[j], left - 1);
      else added[j] = 1;
    }
  } else {
    removed.fill(1, start, endA);
    added.fill(1, start, endB);
  }
  return { removed, added, removedCount: removed.reduce((sum, value) => sum + value, 0), addedCount: added.reduce((sum, value) => sum + value, 0) };
}

async function copyToClipboard(text: string) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Falls back to the legacy path below (e.g. permission denied).
  }
  const previousFocus = document.activeElement as HTMLElement | null;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "0";
  textarea.style.left = "0";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  document.body.removeChild(textarea);
  previousFocus?.focus();
  return copied;
}

type DiffSide = "original" | "proposed";
type CopyFeedback = { side: DiffSide; ok: boolean } | null;

function DiffColumn({ side, lines, marks, changed, feedback, onCopy }: { side: DiffSide; lines: string[]; marks: Uint8Array; changed: number; feedback: CopyFeedback; onCopy: (side: DiffSide) => void }) {
  const title = side === "original" ? "Original" : "Proposta";
  const changeKind = side === "original" ? "removed" : "added";
  const state = feedback?.side === side ? (feedback.ok ? "Copiado!" : "Falha ao copiar") : "Copiar tudo";
  return (
    <div className={`wt-diff-col wt-diff-col-${side}`}>
      <div className="wt-diff-col-head">
        <strong className="wt-diff-col-title">{title}</strong>
        <span className="wt-diff-col-meta">
          {lines.length} {lines.length === 1 ? "linha" : "linhas"}
          {changed > 0 && <span className={`wt-diff-count is-${changeKind}`}>{side === "original" ? "−" : "+"}{changed}</span>}
        </span>
        <button
          type="button"
          className={`wt-copy-button${feedback?.side === side ? (feedback.ok ? " is-copied" : " is-error") : ""}`}
          onClick={() => onCopy(side)}
          aria-label={`Copiar todo o código ${side === "original" ? "original" : "proposto"}`}
        >
          <Icon name="files" size={13} />
          <span>{state}</span>
        </button>
      </div>
      <div className="wt-diff-scroll" tabIndex={0} role="region" aria-label={`Código ${side === "original" ? "original" : "proposto"}`}>
        <div className="wt-diff-lines" style={{ "--wt-ln-digits": String(Math.max(2, String(lines.length).length)) } as CSSProperties}>
          {lines.map((line, index) => {
            const isChanged = marks[index] === 1;
            return (
              <div className={`wt-diff-line${isChanged ? ` is-${changeKind}` : ""}`} key={index}>
                <span className="wt-diff-ln" aria-hidden="true">{index + 1}</span>
                <span className="wt-diff-sign" aria-hidden="true">{isChanged ? (side === "original" ? "−" : "+") : ""}</span>
                <code>{isChanged && <span className="visually-hidden">{side === "original" ? "Removida: " : "Adicionada: "}</span>}{line.replace(/\r$/, "") || " "}</code>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

const QC_COLUMN = 46;
const QC_ROW = 40;
const QC_PAD = 16;

function CircuitDiagram({ gates, qubits }: { gates: QiskitPreviewGate[]; qubits: number }) {
  const layout = useMemo(() => {
    let lastWire = qubits - 1;
    let lastColumn = -1;
    const connectors = new Map<number, { column: number; min: number; max: number; conditional: boolean }>();
    const legacyColumns = new Set<number>();
    let hasOps = true;
    for (const gate of gates) {
      lastWire = Math.max(lastWire, gate.wire, gate.span?.[1] ?? gate.wire);
      lastColumn = Math.max(lastColumn, gate.column);
      if (gate.op === undefined) { hasOps = false; legacyColumns.add(gate.column); continue; }
      if (gate.span && gate.span[0] !== gate.span[1] && !connectors.has(gate.op)) {
        connectors.set(gate.op, { column: gate.column, min: gate.span[0], max: gate.span[1], conditional: Boolean(gate.conditional) });
      }
    }
    const operations = hasOps ? new Set(gates.map((gate) => gate.op)).size : legacyColumns.size;
    const wires = lastWire + 1;
    const columns = lastColumn + 1;
    return { wires, columns, operations, connectors: [...connectors.values()], width: QC_PAD * 2 + Math.max(columns, 4) * QC_COLUMN, height: wires * QC_ROW };
  }, [gates, qubits]);

  const x = (column: number) => QC_PAD + column * QC_COLUMN + QC_COLUMN / 2;
  const y = (wire: number) => wire * QC_ROW + QC_ROW / 2;
  const caption = layout.operations
    ? `${layout.operations} ${layout.operations === 1 ? "operação" : "operações"} em ${layout.columns} ${layout.columns === 1 ? "coluna" : "colunas"}`
    : "Nenhuma operação reconhecida no circuito proposto";

  return (
    <div className="qiskit-circuit-diagram wt-qc">
      <div className="wt-qc-caption">
        <small>Prévia completa do circuito proposto</small>
        <span className="wt-qc-count">{caption}</span>
      </div>
      <div
        className="wt-qc-scroll"
        tabIndex={0}
        role="img"
        aria-label={`Circuito proposto com ${layout.wires} qubits: ${caption}.`}
      >
        <div className="wt-qc-row-wrap">
          <div className="wt-qc-labels" style={{ height: layout.height }} aria-hidden="true">
            {Array.from({ length: layout.wires }, (_, wire) => <span key={wire} style={{ height: QC_ROW }}>q[{wire}]</span>)}
          </div>
          <div className="wt-qc-canvas" style={{ width: layout.width, height: layout.height }} aria-hidden="true">
            {Array.from({ length: layout.wires }, (_, wire) => <span key={`w${wire}`} className="wt-qc-wire" style={{ top: y(wire) }} />)}
            {layout.connectors.map((connector, index) => (
              <span
                key={`c${index}`}
                className={`wt-qc-link${connector.conditional ? " is-conditional" : ""}`}
                style={{ left: x(connector.column), top: y(connector.min), height: y(connector.max) - y(connector.min) }}
              />
            ))}
            {gates.map((gate, index) => {
              const role = gate.role ?? "gate";
              const text = gate.text ?? gate.label;
              const title = `${gate.label}${gate.params ? `(${gate.params})` : ""} q[${gate.wire}]${gate.conditional ? " · condicional" : ""}`;
              return (
                <span
                  key={index}
                  className={`wt-qc-node is-${role}${gate.conditional ? " is-conditional" : ""}`}
                  style={{ left: x(gate.column), top: y(gate.wire) }}
                  title={title}
                >
                  {role === "gate" || role === "measure" || role === "reset" ? text : role === "swap" ? "×" : null}
                </span>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

const tools:{ id: WorkspaceToolId; label: string; icon: IconName }[] = [
  { id: "files", label: "Arquivos", icon: "files" },
  { id: "diff", label: "Diff", icon: "diff" },
  { id: "qiskit", label: "Qiskit", icon: "circuit" },
  { id: "sources", label: "Fontes", icon: "search" },
];

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function WorkspaceToolTabs({
  activeTool,
  onSelect,
}: {
  activeTool: WorkspaceToolId | null;
  onSelect: (tool: WorkspaceToolId | null) => void;
}) {
  const navRef = useRef<HTMLElement>(null);
  const indicatorRef = useRef<HTMLSpanElement>(null);

  // Indicador de vidro que desliza até a aba ativa (posição medida no DOM, sem estado extra).
  useLayoutEffect(() => {
    const place = () => {
      const indicator = indicatorRef.current;
      const active = navRef.current?.querySelector<HTMLElement>("[role='tab'][aria-selected='true']");
      if (!indicator) return;
      if (!active) { indicator.style.opacity = "0"; return; }
      indicator.style.opacity = "1";
      indicator.style.width = `${active.offsetWidth}px`;
      indicator.style.transform = `translateX(${active.offsetLeft}px)`;
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [activeTool]);

  return (
    <nav className="workspace-tool-tabs" aria-label="Ferramentas do workspace" role="tablist" ref={navRef}>
      <span className="workspace-tool-tab-indicator" ref={indicatorRef} aria-hidden="true" />
      <span className="workspace-tool-tabs-label">FERRAMENTAS</span>
      {tools.map((tool) => (
        <button
          key={tool.id}
          type="button"
          role="tab"
          aria-selected={activeTool === tool.id}
          className={activeTool === tool.id ? "active" : ""}
          onClick={() => onSelect(activeTool === tool.id ? null : tool.id)}
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault();
            const tabs = Array.from(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("[role='tab']") ?? []);
            const current = tabs.indexOf(event.currentTarget);
            const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
            tabs[next]?.focus();
            tabs[next]?.click();
          }}
        >
          <Icon name={tool.icon} size={15} />
          {tool.label}
        </button>
      ))}
    </nav>
  );
}

function EmptyState({ icon, title, children }: { icon: string; title: string; children: ReactNode }) {
  return (
    <div className="workspace-tool-empty">
      <span className="workspace-tool-empty-icon" aria-hidden="true">{icon}</span>
      <strong>{title}</strong>
      {children}
    </div>
  );
}

export default function WorkspaceToolsPanel({
  activeTool,
  attachments,
  files,
  onPickFiles,
  onRemoveAttachment,
  diffFiles = [],
  onApproveDiffFile,
  onRevertDiffFile,
  qiskitData,
  sources = [],
}: WorkspaceToolsPanelProps) {
  const [selectedDiffId, setSelectedDiffId] = useState<string | null>(null);
  const [selectedSource, setSelectedSource] = useState<SourceDetails | null>(null);
  const heading = tools.find((tool) => tool.id === activeTool)?.label ?? "Ferramenta";
  const selectedDiff = diffFiles.find((file) => file.id === selectedDiffId) ?? diffFiles[0];
  const [copyFeedback, setCopyFeedback] = useState<CopyFeedback>(null);
  // The parent rebuilds diffFiles on every render, so memoize on the text itself, not on object identity.
  const hasDiff = Boolean(selectedDiff);
  const originalText = selectedDiff?.originalLines.join("\n") ?? "";
  const proposedText = selectedDiff?.proposedLines.join("\n") ?? "";
  const diffMarks = useMemo(
    () => (hasDiff ? diffLineMarks(originalText.split("\n"), proposedText.split("\n")) : null),
    [hasDiff, originalText, proposedText],
  );

  useEffect(() => {
    if (!copyFeedback) return;
    const timer = window.setTimeout(() => setCopyFeedback(null), 1600);
    return () => window.clearTimeout(timer);
  }, [copyFeedback]);

  const copySide = async (side: DiffSide) => {
    if (!hasDiff) return;
    const ok = await copyToClipboard(side === "original" ? originalText : proposedText);
    setCopyFeedback({ side, ok });
  };

  return (
    <section className={`workspace-tool-panel workspace-tool-panel-${activeTool}`} role="tabpanel" aria-label={heading}>
      <div className="workspace-tool-panel-heading">
        <div>
          <span className="section-kicker">WORKSPACE</span>
          <h3>{heading}</h3>
        </div>
        {activeTool === "files" && (
          <button type="button" className="button-secondary tool-action-button" onClick={onPickFiles}>
            <span aria-hidden="true">+</span> Anexar arquivos
          </button>
        )}
      </div>

      {activeTool === "files" && (
        <div className="workspace-files-layout">
          <section className="workspace-project-tree" aria-label="Explorador do projeto">
            <div className="workspace-subpanel-heading"><span>EXPLORADOR</span><span className="workspace-connection-state">ARQUIVOS DO PROJETO</span></div>
            <ul>{files.map((name, index) => <li key={index}>{name}</li>)}</ul>
          </section>

          <section className="workspace-attachments" aria-label="Arquivos anexados à conversa">
            <div className="workspace-subpanel-heading"><span>ANEXOS DA CONVERSA</span><span>{attachments.length.toString().padStart(2, "0")}</span></div>
            {attachments.length ? (
              <div className="workspace-file-list">
                {attachments.map((file) => (
                  <article className="workspace-file-row" key={file.id}>
                    <span className="workspace-file-icon" aria-hidden="true"><Icon name="files" size={15} /></span>
                    <div>
                      <strong>{file.name}</strong>
                      <span>{formatFileSize(file.size)}{file.type ? ` | ${file.type}` : ""}</span>
                    </div>
                    <button type="button" className="workspace-file-remove" onClick={() => onRemoveAttachment(file.id)} aria-label={`Remover ${file.name}`}><Icon name="close" size={13} /></button>
                  </article>
                ))}
                <p className="workspace-tool-hint">Arquivos selecionados para esta conversa.</p>
              </div>
            ) : (
              <EmptyState icon="+" title="Nenhum arquivo anexado">
                <p>Selecione arquivos para preparar o contexto desta conversa.</p>
                <button type="button" className="button-secondary tool-action-button" onClick={onPickFiles}>Selecionar arquivos</button>
              </EmptyState>
            )}
          </section>
        </div>
      )}

      {activeTool === "diff" && (
        diffFiles.length ? (
          <div className="workspace-diff-viewer wt-diff">
            <div className="workspace-diff-status"><span className="workspace-status-dot" /> {diffFiles.length} arquivo(s) com alterações propostas</div>
            <div className={`workspace-diff-browser wt-diff-browser${diffFiles.length === 1 ? " is-single" : ""}`}>
              <nav className="workspace-diff-file-list" aria-label="Arquivos com diff">
                {diffFiles.map((file) => (
                  <button key={file.id} type="button" className={selectedDiff?.id === file.id ? "active" : ""} onClick={() => setSelectedDiffId(file.id)}>
                    <span>{file.path}</span><small>{file.originalLines.length} → {file.proposedLines.length} linhas</small>
                  </button>
                ))}
              </nav>
              {selectedDiff && diffMarks && (
                <div className="workspace-diff-content wt-diff-content">
                  <div className="workspace-diff-file-heading">
                    <strong>{selectedDiff.path}</strong>
                    <span className="wt-diff-summary">
                      <span className="wt-diff-count is-added">+{diffMarks.addedCount}</span>
                      <span className="wt-diff-count is-removed">−{diffMarks.removedCount}</span>
                      <span>linhas alteradas</span>
                    </span>
                  </div>
                  <div className="workspace-diff-columns wt-diff-columns">
                    <DiffColumn side="original" lines={selectedDiff.originalLines} marks={diffMarks.removed} changed={diffMarks.removedCount} feedback={copyFeedback} onCopy={copySide} />
                    <DiffColumn side="proposed" lines={selectedDiff.proposedLines} marks={diffMarks.added} changed={diffMarks.addedCount} feedback={copyFeedback} onCopy={copySide} />
                  </div>
                  <span className="visually-hidden" aria-live="polite">
                    {copyFeedback ? (copyFeedback.ok ? `Código ${copyFeedback.side === "original" ? "original" : "proposto"} copiado.` : "Não foi possível copiar o código.") : ""}
                  </span>
                  {(onApproveDiffFile || onRevertDiffFile) && <div className="workspace-disabled-actions">
                    {onApproveDiffFile && <button type="button" className="button-secondary" onClick={() => onApproveDiffFile(selectedDiff.id)}>Aprovar este arquivo</button>}
                    {onRevertDiffFile && <button type="button" className="button-secondary" onClick={() => onRevertDiffFile(selectedDiff.id)}>Reverter este arquivo</button>}
                  </div>}
                </div>
              )}
            </div>
          </div>
        ) : (
          <EmptyState icon={"\u00B1"} title="Nenhuma alteração nesta conversa"><p>Quando houver uma proposta, a comparação entre o original e o resultado será exibida aqui.</p></EmptyState>
        )
      )}

      {activeTool === "qiskit" && (
        <div className="qiskit-preview">
          <div className="qiskit-metric-grid">
            {[["Profundidade", qiskitData?.depth === undefined ? "-" : String(qiskitData.depth)], ["Operações", qiskitData?.operations === undefined ? "-" : String(qiskitData.operations)], ["Qubits", qiskitData?.qubits === undefined ? "-" : String(qiskitData.qubits)]].map(([label, value]) => (
              <div className="qiskit-metric" key={label}><span>{label}</span><strong>{value}</strong></div>
            ))}
          </div>
          {qiskitData?.qubits ? (
            <CircuitDiagram gates={qiskitData.gates ?? []} qubits={qiskitData.qubits} />
          ) : (
              <div className="qiskit-circuit-empty"><span className="qiskit-circuit-wire" /><span className="qiskit-circuit-label">Nenhum circuito nesta conversa</span><span className="qiskit-circuit-wire" /></div>
          )}
          <div className={`qiskit-equivalence-state qiskit-equivalence-${qiskitData?.equivalence ?? "unknown"}`}><span className="qiskit-state-dot" /><div><strong>{qiskitData?.equivalence === "equivalent" ? "Circuitos equivalentes" : qiskitData?.equivalence === "different" ? "Circuitos diferentes" : "Equivalência não verificada"}</strong><span>{qiskitData?.equivalence === "unknown" || !qiskitData?.equivalence ? "Nenhuma verificação foi executada." : "Resultado recebido da verificação."}</span></div></div>
        </div>
      )}

      {activeTool === "sources" && (
        sources.length ? (
          <div className="workspace-source-browser">
            <div className="workspace-source-list">
              {sources.map((source) => (
                <button type="button" className="workspace-source-card" key={source.id} onClick={() => setSelectedSource(source)}>
                  <span className="workspace-source-type">{source.category} / {source.type}</span>
                  <strong>{source.title}</strong>
                  <span className="workspace-source-meta">{source.origin}{source.version ? ` | ${source.version}` : ""}</span>
                  <p>{source.excerpt}</p>
                  <span className="workspace-source-open">Ver evidencia</span>
                </button>
              ))}
            </div>
            <SourceDrawer source={selectedSource} onClose={() => setSelectedSource(null)} />
          </div>
        ) : (
          <EmptyState icon={"\u2315"} title="Fontes e evidências">
          <p>Nenhuma fonte associada a esta conversa.</p>
          </EmptyState>
        )
      )}
    </section>
  );
}
