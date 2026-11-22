"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createIntroAudio } from "./introAudio";
import { createProjectedIntro } from "./introProjection";

const DURATION = 5;
const ARRIVAL = 4.05;
type Scene = { render: (seconds: number) => void; dispose: () => void };

function Intro({ close }: { close: () => void }) {
  const viewport = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLElement>(null);

  useEffect(() => {
    const host = viewport.current;
    const overlay = stage.current;
    if (!host || !overlay) return;
    const lifetime = new AbortController();
    const webglLoad = new AbortController();
    let scene: Scene | null = null;
    let audio: ReturnType<typeof createIntroAudio> | null = createIntroAudio();
    let soundStarted = false;
    let frame = 0;
    let played = 0;
    let started: number | null = null;
    let timer = 0;
    let fallbackTimer = 0;
    let software = false;
    let finished = false;
    const elapsed = () => played + (started === null ? 0 : (performance.now() - started) / 1000);
    const finish = () => {
      if (!finished) { finished = true; close(); }
    };
    async function startSound() {
      if (!audio || soundStarted || started === null || finished) return;
      soundStarted = await audio.start(elapsed);
    }
    function newCanvas() {
      const canvas = document.createElement("canvas");
      canvas.setAttribute("aria-hidden", "true");
      host!.replaceChildren(canvas);
      return canvas;
    }
    function draw() {
      if (!scene || finished || started === null) return;
      const time = elapsed();
      if (time >= DURATION) { scene.render(DURATION); finish(); return; }
      try {
        scene.render(time);
        if (time >= ARRIVAL) overlay!.dataset.phase = "opening";
      } catch {
        if (!software) { startProjection(); return; }
        finish(); return;
      }
      frame = requestAnimationFrame(draw);
    }
    function resume() {
      if (!scene || finished || document.hidden || started !== null) return;
      audio ??= createIntroAudio();
      started = performance.now();
      timer = window.setTimeout(finish, Math.max(0, (DURATION - played) * 1000));
      void startSound();
      draw();
    }
    function visibilityChanged() {
      if (!document.hidden) { resume(); return; }
      if (started === null) return;
      played = elapsed(); started = null;
      clearTimeout(timer); cancelAnimationFrame(frame);
      audio?.dispose(); audio = null; soundStarted = false;
    }
    function startProjection() {
      if (finished || software) return;
      software = true;
      clearTimeout(fallbackTimer);
      webglLoad.abort();
      cancelAnimationFrame(frame);
      scene?.dispose();
      try {
        scene = createProjectedIntro(newCanvas());
        scene.render(elapsed());
        overlay!.dataset.renderer = "software";
        overlay!.dataset.phase = "playing";
        if (started === null) resume(); else draw();
      } catch { finish(); }
    }
    async function load() {
      try {
        const { createIntroScene } = await import("./introScene");
        if (finished || software) return;
        const canvas = newCanvas();
        const next = await createIntroScene(canvas, webglLoad.signal);
        if (finished || software) { next.dispose(); return; }
        scene = next;
        clearTimeout(fallbackTimer);
        canvas.addEventListener("webglcontextlost", startProjection, { once: true, signal: lifetime.signal });
        overlay!.dataset.renderer = "webgl";
        overlay!.dataset.phase = "playing";
        resume();
      } catch { if (!finished && !software) startProjection(); }
    }
    document.addEventListener("visibilitychange", visibilityChanged, { signal: lifetime.signal });
    window.addEventListener("pointerdown", startSound, { signal: lifetime.signal });
    window.addEventListener("keydown", startSound, { signal: lifetime.signal });
    // Count all five seconds from the first rendered scene, including on a cold load.
    fallbackTimer = window.setTimeout(startProjection, 4000);
    void load();
    return () => {
      finished = true;
      lifetime.abort(); webglLoad.abort();
      cancelAnimationFrame(frame); clearTimeout(timer); clearTimeout(fallbackTimer);
      audio?.dispose(); scene?.dispose();
      host.replaceChildren();
    };
  }, [close]);

  return <section className="startup-splash" ref={stage} aria-label="Abertura 3D do QuantumOne">
    <div className="startup-viewport" ref={viewport} />
  </section>;
}

export default function StartupSplash({ children }: { children: ReactNode }) {
  const [visible, setVisible] = useState(true);
  const close = useCallback(() => setVisible(false), []);
  useEffect(() => {
    const replay = () => setVisible(true);
    window.addEventListener("quantumone:replay-intro", replay);
    return () => window.removeEventListener("quantumone:replay-intro", replay);
  }, []);
  return <>
    {visible && <Intro close={close} />}
    <div className="startup-app" inert={visible}>{children}</div>
  </>;
}

