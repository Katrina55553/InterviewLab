/* Microphone capture: resample the browser's actual sample rate to PCM16/16kHz.
 * Fixed 640-sample frames are 40ms; no microphone audio is played locally. */
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = [];
    this.position = 0;
    this.frame = new Int16Array(640);
    this.offset = 0;
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const value of input) this.samples.push(value);
    const step = sampleRate / 16000;
    while (this.position + 1 < this.samples.length) {
      const index = Math.floor(this.position);
      const fraction = this.position - index;
      const value = Math.max(-1, Math.min(1, this.samples[index] * (1 - fraction) + this.samples[index + 1] * fraction));
      this.frame[this.offset++] = value < 0 ? value * 32768 : value * 32767;
      this.position += step;
      if (this.offset === 640) {
        this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
        this.frame = new Int16Array(640);
        this.offset = 0;
      }
    }
    // A downsampling step can jump past this block. Preserve that overshoot
    // relative to the next block instead of silently increasing the sample rate.
    const consumed = Math.min(this.samples.length, Math.floor(this.position));
    this.samples.splice(0, consumed);
    this.position -= consumed;
    return true;
  }
}
registerProcessor('pcm-capture', PcmCapture);
