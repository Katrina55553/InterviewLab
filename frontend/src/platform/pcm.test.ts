import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

interface CaptureProcessor { process: (inputs: Float32Array[][]) => boolean; offset: number }
function capture(rate: number) {
  const frames: ArrayBuffer[] = [];
  let Processor: { new(): CaptureProcessor } | undefined;
  runInNewContext(readFileSync(resolve(process.cwd(), 'public/pcm-worklet.js'), 'utf8'), {
    sampleRate: rate,
    AudioWorkletProcessor: class { port = { postMessage: (buffer: ArrayBuffer) => frames.push(buffer) } },
    registerProcessor: (_name: string, processor: typeof Processor) => { Processor = processor },
    Int16Array,
  });
  return { processor: new Processor!(), frames };
}
describe('microphone PCM transport', () => {
  it.each([48000, 44100, 16000])('resamples a %i Hz microphone into bounded 40ms mono PCM16 frames', rate => {
    const { processor, frames } = capture(rate);
    for (let offset = 0; offset < rate; offset += 128) {
      processor.process([[new Float32Array(Math.min(128, rate - offset)).fill(0.5)]]);
    }
    expect(frames.length).toBeGreaterThanOrEqual(24);
    expect(frames.length).toBeLessThanOrEqual(25);
    expect(frames.length * 640 + processor.offset).toBe(rate === 16000 ? 15999 : 16000);
    expect(frames.every(frame => frame.byteLength === 1280)).toBe(true);
    for (const frame of frames) expect(new Int16Array(frame)[0]).toBe(16383);
  });
  it.each([48000, 44100])('preserves the resampling phase across 128-sample blocks at %i Hz', rate => {
    const { processor, frames } = capture(rate);
    for (let offset = 0; offset < rate; offset += 128) {
      processor.process([[Float32Array.from({ length: Math.min(128, rate - offset) }, (_, index) => (offset + index) / rate)]]);
    }
    const output = frames.flatMap(frame => [...new Int16Array(frame)]);
    for (const index of [640, 5000, 15000]) expect(Math.abs(output[index] - Math.trunc(index / 16000 * 32767))).toBeLessThanOrEqual(1);
  });
  it('clamps amplified input instead of wrapping signed samples', () => {
    const { processor, frames } = capture(16000);
    processor.process([[Float32Array.from({ length: 650 }, (_, index) => index % 2 ? 2 : -2)]]);
    const samples = new Int16Array(frames[0]);
    expect(samples[0]).toBe(-32768); expect(samples[1]).toBe(32767);
  });
});
