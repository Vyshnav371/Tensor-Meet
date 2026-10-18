/**
 * Tensor's voice, played while it is still being generated: 24 kHz 16-bit PCM chunks are scheduled back to back,
 * so the first words play about a second after Tensor decides to speak instead of after the whole clip is ready.
 */
const RATE = 24000;
const LEAD = 0.08; // small buffer against network jitter

export class VoicePlayer {
  private ctx?: AudioContext;
  private next = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private current = -1;

  constructor(private onBlocked: () => void) {}

  /** Queue one chunk of utterance `id`; a new id replaces whatever was playing. */
  chunk(id: number, pcmBase64: string) {
    if (id !== this.current) {
      this.stop();
      this.current = id;
    }
    const ctx = this.context();
    const bytes = Uint8Array.from(atob(pcmBase64), (c) => c.charCodeAt(0));
    const samples = new Int16Array(bytes.buffer, 0, bytes.length >> 1);
    if (!samples.length) return;
    const buffer = ctx.createBuffer(1, samples.length, RATE);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    const start = Math.max(ctx.currentTime + LEAD, this.next);
    source.start(start);
    this.next = start + buffer.duration;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
  }

  /** A short rising two-note chime: "I heard my name". */
  chime() {
    const ctx = this.context();
    const now = ctx.currentTime;
    [660, 880].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, now + i * 0.11);
      gain.gain.linearRampToValueAtTime(0.08, now + i * 0.11 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.11 + 0.22);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + i * 0.11);
      osc.stop(now + i * 0.11 + 0.25);
    });
  }

  stop() {
    for (const s of this.sources) {
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources.clear();
    this.next = 0;
  }

  resume() {
    void this.ctx?.resume();
  }

  close() {
    this.stop();
    void this.ctx?.close().catch(() => undefined);
    this.ctx = undefined;
  }

  private context(): AudioContext {
    if (!this.ctx) this.ctx = new AudioContext();
    if (this.ctx.state === 'suspended') {
      void this.ctx.resume().then(() => {
        if (this.ctx?.state === 'suspended') this.onBlocked();
      });
    }
    return this.ctx;
  }
}
