"use client";

import { useEffect } from "react";

/** Controles de vidro que recebem o brilho especular (--mx/--my). */
const SHEEN_TARGETS = [
  ".button-primary",
  ".button-secondary",
  ".example-chip",
  ".add-code-button",
  ".tool-action-button",
  ".workspace-tool-tabs button",
  ".editor-tab",
  ".sidebar-item",
  ".sidebar-create-account",
  ".settings-page-nav a",
  ".glass-dropdown-trigger",
].join(",");

/** Superfícies grandes que recebem um holofote suave sob o cursor. */
const SURFACE_TARGETS = ".chat-panel, .context-panel, .panel, .chat-composer, .analysis-live-status, .quantum-sidebar";

/** Cartões que inclinam em 3D na direção do cursor (spatial UI). */
const TILT_TARGETS = ".example-chip, .settings-page-card, .project-card, .history-card";

/** Botões "magnéticos": deslizam alguns pixels em direção ao cursor. */
const MAGNET_TARGETS = ".button-primary";

const MAX_TILT_DEG = 7;
const MAX_MAGNET_PX = 3;

function setLocalPoint(element: HTMLElement, x: number, y: number) {
  const rect = element.getBoundingClientRect();
  element.style.setProperty("--mx", `${(x - rect.left).toFixed(0)}px`);
  element.style.setProperty("--my", `${(y - rect.top).toFixed(0)}px`);
  return rect;
}

function clearMotion(element: HTMLElement | null) {
  if (!element) return;
  for (const name of ["--rx", "--ry", "--tx", "--ty"]) element.style.removeProperty(name);
}

/**
 * Equivalente web do modo "interactive" do expo-glass-effect, mais as
 * interações espaciais (inclinação, magnetismo, paralaxe do fundo).
 * Uma única escuta passiva, no máximo uma atualização por quadro; tudo fica
 * desligado com movimento reduzido ou em telas de toque.
 */
export default function GlassPointerEffects() {
  useEffect(() => {
    const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const background = () => document.getElementById("q1-spatial-bg");
    let frame = 0;
    let point: { target: Element | null; x: number; y: number } | null = null;
    let tilted: HTMLElement | null = null;
    let magnet: HTMLElement | null = null;

    const reducedMotion = () => motionQuery.matches || document.documentElement.dataset.reducedMotion === "true";

    const flush = () => {
      frame = 0;
      if (!point) return;
      const { target, x, y } = point;
      point = null;

      const bg = background();
      if (bg) {
        bg.style.setProperty("--cx", `${x.toFixed(0)}px`);
        bg.style.setProperty("--cy", `${y.toFixed(0)}px`);
        bg.style.setProperty("--px", ((x / window.innerWidth) * 2 - 1).toFixed(3));
        bg.style.setProperty("--py", ((y / window.innerHeight) * 2 - 1).toFixed(3));
      }

      const sheen = target?.closest<HTMLElement>(SHEEN_TARGETS);
      if (sheen) setLocalPoint(sheen, x, y);
      const surface = target?.closest<HTMLElement>(SURFACE_TARGETS);
      if (surface) setLocalPoint(surface, x, y);

      const tilt = target?.closest<HTMLElement>(TILT_TARGETS) ?? null;
      if (tilt !== tilted) { clearMotion(tilted); tilted = tilt; }
      if (tilt) {
        const rect = tilt.getBoundingClientRect();
        const nx = (x - rect.left) / rect.width - 0.5;
        const ny = (y - rect.top) / rect.height - 0.5;
        tilt.style.setProperty("--rx", `${(-ny * MAX_TILT_DEG).toFixed(2)}deg`);
        tilt.style.setProperty("--ry", `${(nx * MAX_TILT_DEG).toFixed(2)}deg`);
      }

      const pull = target?.closest<HTMLElement>(MAGNET_TARGETS) ?? null;
      if (pull !== magnet) { clearMotion(magnet); magnet = pull; }
      if (pull) {
        const rect = pull.getBoundingClientRect();
        const nx = (x - rect.left) / rect.width - 0.5;
        const ny = (y - rect.top) / rect.height - 0.5;
        pull.style.setProperty("--tx", `${(nx * 2 * MAX_MAGNET_PX).toFixed(1)}px`);
        pull.style.setProperty("--ty", `${(ny * 2 * MAX_MAGNET_PX).toFixed(1)}px`);
      }
    };

    const onPointerMove = (event: PointerEvent) => {
      if (event.pointerType === "touch" || reducedMotion()) return;
      point = { target: event.target as Element | null, x: event.clientX, y: event.clientY };
      if (!frame) frame = window.requestAnimationFrame(flush);
    };

    const onPointerLeaveWindow = () => {
      clearMotion(tilted); tilted = null;
      clearMotion(magnet); magnet = null;
    };

    document.addEventListener("pointermove", onPointerMove, { passive: true });
    document.documentElement.addEventListener("pointerleave", onPointerLeaveWindow);
    return () => {
      document.removeEventListener("pointermove", onPointerMove);
      document.documentElement.removeEventListener("pointerleave", onPointerLeaveWindow);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  return null;
}
