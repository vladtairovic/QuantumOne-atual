const DURATION = 5;
const LOGO_SETTLED = 4.05;
// Seconds in the supplied recording, measured from the decoded MP3.
const SPEECH_END = 2.547;
const VOICE_START = LOGO_SETTLED - SPEECH_END;

function renderEffects() {
  const mix = new OfflineAudioContext(2, 32_000 * DURATION, 32_000);

  function tone(at: number, from: number, to: number, duration: number, volume: number, pan: number) {
    const oscillator = mix.createOscillator();
    const envelope = mix.createGain();
    const position = mix.createStereoPanner();
    position.pan.value = pan;
    oscillator.frequency.setValueAtTime(from, at);
    oscillator.frequency.exponentialRampToValueAtTime(to, at + duration);
    envelope.gain.setValueAtTime(0.0001, at);
    envelope.gain.exponentialRampToValueAtTime(volume, at + 0.008);
    envelope.gain.exponentialRampToValueAtTime(0.0001, at + duration);
    oscillator.connect(envelope).connect(position).connect(mix.destination);
    oscillator.start(at);
    oscillator.stop(at + duration);
  }

  // Short grains move across the stereo field as the code assembles.
  const grains = [0.10, 0.29, 0.46, 0.61, 0.75, 0.87, 0.98, 1.07, 1.16, 1.23, 1.30, 1.36];
  const notes = [370, 554, 740, 831];
  grains.forEach((at, index) => {
    const frequency = notes[index % notes.length];
    tone(at, frequency * 1.44, frequency, 0.077, 0.038, -0.8 + 1.45 * index / grains.length);
  });
  tone(0.06, 49, 97, 1.5, 0.12, -0.25);
  tone(1.34, 148, 74, 0.91, 0.085, 0.18);
  // A soft glass-and-metal chord lands on the last syllable.
  tone(LOGO_SETTLED, 740, 739, 0.55, 0.075, -0.20);
  tone(LOGO_SETTLED + 0.014, 1109, 1107, 0.39, 0.028, 0.29);

  // Band-limited stereo air follows the turn and the final passage through the logo.
  const noise = mix.createBuffer(1, mix.length, mix.sampleRate);
  const samples = noise.getChannelData(0);
  let seed = 91013;
  for (let i = 0; i < samples.length; i++) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    samples[i] = seed / 2147483648 - 1;
  }
  const air = mix.createBufferSource();
  const filter = mix.createBiquadFilter();
  const envelope = mix.createGain();
  const position = mix.createStereoPanner();
  air.buffer = noise;
  filter.type = "bandpass";
  filter.Q.value = 0.9;
  filter.frequency.setValueAtTime(200, 0);
  filter.frequency.exponentialRampToValueAtTime(2400, 1.35);
  filter.frequency.exponentialRampToValueAtTime(380, 2.25);
  filter.frequency.exponentialRampToValueAtTime(1100, 3.85);
  filter.frequency.exponentialRampToValueAtTime(3000, 4.42);
  filter.frequency.exponentialRampToValueAtTime(180, 4.99);
  envelope.gain.setValueAtTime(0, 0);
  envelope.gain.linearRampToValueAtTime(0.12, 1.22);
  envelope.gain.linearRampToValueAtTime(0.011, 1.54);
  envelope.gain.linearRampToValueAtTime(0.028, 3.68);
  envelope.gain.linearRampToValueAtTime(0.12, 4.41);
  envelope.gain.linearRampToValueAtTime(0, 4.96);
  position.pan.setValueAtTime(-0.7, 0);
  position.pan.linearRampToValueAtTime(0.65, 1.35);
  position.pan.linearRampToValueAtTime(0, LOGO_SETTLED);
  air.connect(filter).connect(envelope).connect(position).connect(mix.destination);
  air.start();
  return mix.startRendering();
}

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
  const bytes = fetch("/intro/quantumone-voz.mp3", { signal: loading.signal })
    .then(response => response.ok ? response.arrayBuffer() : null)
    .catch(() => null);
  let context: AudioContext | null = null;
  let output: GainNode | null = null;
  let voice: Promise<AudioBuffer | null> | null = null;
  let effects: Promise<AudioBuffer | null> | null = null;
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
          voice = bytes.then(data => data && !loading.signal.aborted ? audio.decodeAudioData(data) : null)
            .catch(() => null);
          effects = renderEffects().catch(() => null);
        }
        if (!await running || loading.signal.aborted || elapsedSeconds() >= DURATION) return false;
        if (scheduled) return true;
        scheduled = true;

        function schedule(buffer: AudioBuffer | null, cue: number, end: number) {
          if (!buffer || !output || loading.signal.aborted) return;
          // A late unlock or decode joins the live scene at its current sample.
          const elapsed = elapsedSeconds() + (audio.outputLatency || 0);
          const offset = Math.max(0, elapsed - cue);
          const duration = Math.min(buffer.duration - offset, end - cue - offset);
          if (duration <= 0) return;
          const track = audio.createBufferSource();
          track.buffer = buffer;
          track.connect(output);
          track.onended = () => track.disconnect();
          track.start(audio.currentTime + Math.max(0, cue - elapsed), offset, duration);
        }
        void effects?.then(buffer => schedule(buffer, 0, DURATION));
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
