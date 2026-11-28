const DURATION = 5;
const LOGO_SETTLED = 4.05;
// Seconds in the supplied recording, measured from the decoded MP3.
const SPEECH_END = 2.547;
const VOICE_START = LOGO_SETTLED - SPEECH_END;

async function resumeDuringGesture(context: AudioContext) {
  if (context.state === "running") return true;
  // Browsers can leave resume() pending until the next user gesture.
  let timer = 0;
  try {
    return await Promise.race([
      context.resume().then(() => true, () => false),
      new Promise<boolean>(resolve => { timer = window.setTimeout(() => resolve(false), 150); }),
    ]);
  } finally {
    window.clearTimeout(timer);
  }
}

export function createIntroAudio() {
  const loading = new AbortController();
  const load = (url: string) => fetch(url, { signal: loading.signal })
    .then(response => response.ok ? response.arrayBuffer() : null)
    .catch(() => null);
  const voiceBytes = load("/intro/quantumone-voz.mp3");
  let context: AudioContext | null = null;
  let output: GainNode | null = null;
  let voice: Promise<AudioBuffer | null> | null = null;
  let scheduled = false;
  let muted = false;

  return {
    async start(elapsedSeconds: () => number): Promise<boolean> {
      if (loading.signal.aborted || elapsedSeconds() >= DURATION) return false;
      try {
        const audio = context ??= new AudioContext({ latencyHint: "interactive" });
        const running = resumeDuringGesture(audio);
        if (!output) {
          output = audio.createGain();
          output.gain.value = muted ? 0 : 0.9;
          output.connect(audio.destination);
          voice = voiceBytes.then(data => data && !loading.signal.aborted ? audio.decodeAudioData(data) : null)
            .catch(() => null);
        }
        if (!await running || loading.signal.aborted || elapsedSeconds() >= DURATION) return false;
        if (scheduled) return true;
        scheduled = true;

        function schedule(buffer: AudioBuffer | null, cue: number, end: number, volume = 1) {
          if (!buffer || !output || loading.signal.aborted) return;
          // A late unlock or decode joins the live scene at its current sample.
          const elapsed = elapsedSeconds() + (audio.outputLatency || 0);
          const offset = Math.max(0, elapsed - cue);
          const duration = Math.min(buffer.duration - offset, end - cue - offset);
          if (duration <= 0) return;
          const track = audio.createBufferSource();
          track.buffer = buffer;
          const gain = audio.createGain();
          gain.gain.value = volume;
          track.connect(gain).connect(output);
          track.onended = () => { track.disconnect(); gain.disconnect(); };
          track.start(audio.currentTime + Math.max(0, cue - elapsed), offset, duration);
        }
        void voice?.then(buffer => schedule(buffer, VOICE_START, LOGO_SETTLED));
        return true;
      } catch {
        return false;
      }
    },
    setMuted(value: boolean) {
      muted = value;
      if (output && context) output.gain.setTargetAtTime(value ? 0 : 0.9, context.currentTime, 0.005);
    },
    dispose() {
      loading.abort();
      output?.disconnect();
      if (context && context.state !== "closed") void context.close().catch(() => {});
      output = null;
      context = null;
    },
  };
}
