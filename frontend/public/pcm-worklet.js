// Downsamples microphone audio to 16 kHz mono 16-bit PCM and posts 100 ms frames (1,600 samples).
class PcmWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.buffer = new Int16Array(1600);
    this.filled = 0;
    this.carry = 0;
    this.acc = 0;
    this.count = 0;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      this.acc += channel[i];
      this.count++;
      this.carry += 1;
      if (this.carry >= this.ratio) {
        this.carry -= this.ratio;
        const sample = Math.max(-1, Math.min(1, this.acc / this.count));
        this.acc = 0;
        this.count = 0;
        this.buffer[this.filled++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        if (this.filled === this.buffer.length) {
          let energy = 0;
          for (let j = 0; j < this.buffer.length; j++) energy += (this.buffer[j] / 32768) ** 2;
          this.port.postMessage({ pcm: this.buffer.buffer.slice(0), rms: Math.sqrt(energy / this.buffer.length) });
          this.filled = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('pcm-worklet', PcmWorklet);
