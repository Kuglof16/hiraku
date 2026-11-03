// Signal processing for the narration recorder.
//
// buildChain() makes the voice filter out of Web Audio nodes, the same way in
// a live AudioContext (to listen while tuning) and in an OfflineAudioContext
// (to render the export):
//
//   input -> highpass -> low cut (2 highpass + mains notches)
//         -> mud cut (peaking) -> presence (peaking)
//         -> de-esser (worklet) -> noise gate (worklet)
//         -> compressor -> makeup gain -> output
//   input -> (dry) ---------------------------------------> output
//
// A stage that is off is made an identity: biquads become a 0 dB peaking
// filter, the worklets pass the signal through, the compressor gets ratio 1.
// Web Audio reads Q of a highpass biquad in dB (checked with
// getFrequencyResponse in Chromium: Q -3.01 gives -3.01 dB at the cutoff),
// so the Butterworth Q values below are given in dB.
// exportTake() renders a take through the chain at 44.1 kHz, trims silence at
// the head and tail, fades the edges (EDGE below), sets the loudness and
// encodes 16-bit mono WAV.

"use strict";

const WORKLET_SRC = `
const db2lin = (d) => Math.pow(10, d / 20);

class HirakuGate extends AudioWorkletProcessor {
  static get parameterDescriptors() {
    return [
      { name: "enabled", defaultValue: 1, automationRate: "k-rate" },
      { name: "threshold", defaultValue: -50, automationRate: "k-rate" },
      { name: "range", defaultValue: -25, automationRate: "k-rate" },
      { name: "attack", defaultValue: 3, automationRate: "k-rate" },
      { name: "hold", defaultValue: 120, automationRate: "k-rate" },
      { name: "release", defaultValue: 150, automationRate: "k-rate" },
    ];
  }
  constructor() {
    super();
    this.env = 0; this.gain = 1; this.holdLeft = 0; this.open = true; this.tick = 0;
  }
  process(inputs, outputs, p) {
    const inp = inputs[0], out = outputs[0];
    if (!inp || inp.length === 0) { for (const ch of out) ch.fill(0); return true; }
    const x = inp[0], n = x.length, sr = sampleRate;
    const enabled = p.enabled[0] >= 0.5;
    const thr = db2lin(p.threshold[0]);
    const closeThr = thr * 0.6;              // about 4.4 dB of hysteresis
    const floor = db2lin(p.range[0]);
    const aA = Math.exp(-1 / (sr * Math.max(0.1, p.attack[0]) / 1000));
    const aR = Math.exp(-1 / (sr * Math.max(1, p.release[0]) / 1000));
    const envR = Math.exp(-1 / (sr * 0.010)); // 10 ms peak detector release
    const holdN = Math.round(sr * p.hold[0] / 1000);
    for (let i = 0; i < n; i++) {
      const a = Math.abs(x[i]);
      this.env = a > this.env ? a : this.env * envR;
      if (this.env > thr) { this.open = true; this.holdLeft = holdN; }
      else if (this.env < closeThr) { if (this.holdLeft > 0) this.holdLeft--; else this.open = false; }
      if (!enabled) this.gain = 1;
      else {
        const target = this.open ? 1 : floor;
        const c = target > this.gain ? aA : aR;
        this.gain = target + (this.gain - target) * c;
      }
      for (let ch = 0; ch < out.length; ch++) out[ch][i] = (inp[ch] || x)[i] * this.gain;
    }
    if ((this.tick += n) >= 2048) { this.tick = 0; this.port.postMessage({ gainDb: 20 * Math.log10(Math.max(1e-6, this.gain)), open: this.open }); }
    return true;
  }
}

// De-esser: a band-pass at the sibilance frequency is both the detector and
// the part that is taken away. out = x - bp(x) * (1 - g) is a peaking cut
// whose depth follows how far the band's level is over the threshold.
class HirakuDeesser extends AudioWorkletProcessor {
  static get parameterDescriptors() {
    return [
      { name: "enabled", defaultValue: 1, automationRate: "k-rate" },
      { name: "freq", defaultValue: 7000, automationRate: "k-rate" },
      { name: "threshold", defaultValue: -32, automationRate: "k-rate" },
      { name: "ratio", defaultValue: 4, automationRate: "k-rate" },
      { name: "maxCut", defaultValue: 10, automationRate: "k-rate" },
    ];
  }
  constructor() {
    super();
    this.x1 = 0; this.x2 = 0; this.y1 = 0; this.y2 = 0;
    this.env = 0; this.g = 1; this.f = -1; this.tick = 0; this.maxRed = 0;
  }
  coeffs(f) {
    const q = 1.4, w0 = 2 * Math.PI * Math.min(f, sampleRate * 0.45) / sampleRate;
    const alpha = Math.sin(w0) / (2 * q), a0 = 1 + alpha;
    this.b0 = alpha / a0; this.b2 = -alpha / a0;
    this.a1 = -2 * Math.cos(w0) / a0; this.a2 = (1 - alpha) / a0;
    this.f = f;
  }
  process(inputs, outputs, p) {
    const inp = inputs[0], out = outputs[0];
    if (!inp || inp.length === 0) { for (const ch of out) ch.fill(0); return true; }
    const x = inp[0], n = x.length, sr = sampleRate;
    if (p.freq[0] !== this.f) this.coeffs(p.freq[0]);
    const enabled = p.enabled[0] >= 0.5;
    const thr = p.threshold[0], slope = 1 - 1 / Math.max(1, p.ratio[0]), maxCut = p.maxCut[0];
    const eA = Math.exp(-1 / (sr * 0.002)), eR = Math.exp(-1 / (sr * 0.060));
    const gS = Math.exp(-1 / (sr * 0.003));
    for (let i = 0; i < n; i++) {
      const xi = x[i];
      const bp = this.b0 * xi + this.b2 * this.x2 - this.a1 * this.y1 - this.a2 * this.y2;
      this.x2 = this.x1; this.x1 = xi; this.y2 = this.y1; this.y1 = bp;
      const sq = bp * bp;
      this.env = sq > this.env ? eA * this.env + (1 - eA) * sq : eR * this.env + (1 - eR) * sq;
      const lvl = 10 * Math.log10(this.env * 2 + 1e-12); // mean square of a sine * 2 = its peak squared
      const red = enabled ? Math.min(maxCut, Math.max(0, lvl - thr) * slope) : 0;
      const target = Math.pow(10, -red / 20);
      this.g = target + (this.g - target) * gS;
      if (red > this.maxRed) this.maxRed = red;
      const y = xi - bp * (1 - this.g);
      for (let ch = 0; ch < out.length; ch++) out[ch][i] = ch === 0 ? y : (inp[ch] || x)[i] - bp * (1 - this.g);
    }
    if ((this.tick += n) >= 2048) { this.tick = 0; this.port.postMessage({ reductionDb: this.maxRed }); this.maxRed = 0; }
    return true;
  }
}

// Recording tap: hands the raw input to the page in blocks of 4096 frames.
class HirakuTap extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(4096); this.k = 0; this.peak = 0; }
  process(inputs, outputs) {
    const inp = inputs[0];
    if (inp && inp.length) {
      const x = inp[0];
      for (let i = 0; i < x.length; i++) {
        let v = x[i];
        if (inp.length > 1) { v = 0; for (let c = 0; c < inp.length; c++) v += inp[c][i]; v /= inp.length; }
        this.buf[this.k++] = v;
        const a = Math.abs(v); if (a > this.peak) this.peak = a;
        if (this.k === this.buf.length) {
          this.port.postMessage({ pcm: this.buf, peak: this.peak }, [this.buf.buffer]);
          this.buf = new Float32Array(4096); this.k = 0; this.peak = 0;
        }
      }
    }
    if (outputs[0]) for (const ch of outputs[0]) ch.fill(0);
    return true;
  }
}

registerProcessor("hiraku-gate", HirakuGate);
registerProcessor("hiraku-deesser", HirakuDeesser);
registerProcessor("hiraku-tap", HirakuTap);
`;

let workletUrl = null;
function workletURL() {
  if (!workletUrl) workletUrl = URL.createObjectURL(new Blob([WORKLET_SRC], { type: "application/javascript" }));
  return workletUrl;
}

// Chromium refuses a Blob URL module on a page opened from file:// but takes
// a data: URL there, so the data: URL is the fallback.
const loadedContexts = new WeakSet();
async function ensureWorklet(ctx) {
  if (loadedContexts.has(ctx)) return;
  try {
    await ctx.audioWorklet.addModule(workletURL());
  } catch (e) {
    await ctx.audioWorklet.addModule("data:application/javascript;charset=utf-8," + encodeURIComponent(WORKLET_SRC));
  }
  loadedContexts.add(ctx);
}

// --- settings ---------------------------------------------------------------

const PRESETS = {
  clear: {
    hp: { on: true, freq: 80 },
    hum: { on: true, cut: 100, mains: 50, harmonics: 10 },
    mud: { on: true, freq: 300, gain: -4, q: 1.0 },
    presence: { on: true, freq: 3500, gain: 4, q: 0.9 },
    deess: { on: true, freq: 7000, threshold: -32, ratio: 4, maxCut: 10 },
    gate: { on: true, threshold: -48, range: -24, attack: 3, hold: 120, release: 160 },
    comp: { on: true, threshold: -22, ratio: 3, attack: 8, release: 160, knee: 6, makeup: 3 },
    out: { normalize: true, lufs: -16, ceiling: -1, trim: true, pad: 0.05 },
  },
  soft: {
    hp: { on: true, freq: 70 },
    hum: { on: true, cut: 100, mains: 50, harmonics: 10 },
    mud: { on: true, freq: 280, gain: -1.5, q: 0.8 },
    presence: { on: true, freq: 3000, gain: 1.5, q: 0.7 },
    deess: { on: true, freq: 6500, threshold: -30, ratio: 3, maxCut: 8 },
    gate: { on: true, threshold: -52, range: -14, attack: 5, hold: 150, release: 250 },
    comp: { on: true, threshold: -20, ratio: 2, attack: 15, release: 250, knee: 10, makeup: 2 },
    out: { normalize: true, lufs: -16, ceiling: -1, trim: true, pad: 0.05 },
  },
  raw: {
    hp: { on: false, freq: 80 },
    hum: { on: false, cut: 100, mains: 50, harmonics: 10 },
    mud: { on: false, freq: 300, gain: 0, q: 1.0 },
    presence: { on: false, freq: 3500, gain: 0, q: 0.9 },
    deess: { on: false, freq: 7000, threshold: -32, ratio: 4, maxCut: 10 },
    gate: { on: false, threshold: -48, range: -24, attack: 3, hold: 120, release: 160 },
    comp: { on: false, threshold: -22, ratio: 3, attack: 8, release: 160, knee: 6, makeup: 0 },
    out: { normalize: true, lufs: -16, ceiling: -1, trim: true, pad: 0.05 },
  },
};

function cloneSettings(s) { return JSON.parse(JSON.stringify(s)); }

// Fill missing fields from the "clear" preset so an old or partial JSON still loads.
function completeSettings(s) {
  const base = cloneSettings(PRESETS.clear);
  if (!s || typeof s !== "object") return base;
  for (const k of Object.keys(base)) {
    if (s[k] && typeof s[k] === "object") {
      for (const f of Object.keys(base[k])) {
        if (typeof s[k][f] === typeof base[k][f]) base[k][f] = s[k][f];
      }
    }
  }
  return base;
}

// --- the chain --------------------------------------------------------------

function mono(node) {
  node.channelCount = 1;
  node.channelCountMode = "explicit";
  node.channelInterpretation = "speakers";
  return node;
}

// The low cut: a 4th-order Butterworth highpass as two biquads (Q 0.5412 and
// 1.3066, as dB), then a narrow notch on the mains frequency and each of its
// harmonics up to MAX_HARMONICS.
const BUTTER4_Q_DB = [20 * Math.log10(0.5412), 20 * Math.log10(1.3066)];
const BUTTER2_Q_DB = 20 * Math.log10(Math.SQRT1_2);
const NOTCH_Q = 30;
const MAX_HARMONICS = 12;

function buildChain(ctx, settings) {
  const input = mono(ctx.createGain());
  const hp = mono(ctx.createBiquadFilter());
  const low = BUTTER4_Q_DB.map(() => mono(ctx.createBiquadFilter()));
  const notches = Array.from({ length: MAX_HARMONICS }, () => mono(ctx.createBiquadFilter()));
  const mud = mono(ctx.createBiquadFilter());
  const pres = mono(ctx.createBiquadFilter());
  const deess = new AudioWorkletNode(ctx, "hiraku-deesser", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
  const gate = new AudioWorkletNode(ctx, "hiraku-gate", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
  const comp = mono(ctx.createDynamicsCompressor());
  const makeup = mono(ctx.createGain());
  const wet = mono(ctx.createGain());
  const dry = mono(ctx.createGain());
  const output = mono(ctx.createGain());
  input.connect(hp);
  [hp, ...low, ...notches, mud].reduce((a, b) => { a.connect(b); return b; });
  mud.connect(pres); pres.connect(deess);
  deess.connect(gate); gate.connect(comp); comp.connect(makeup); makeup.connect(wet);
  wet.connect(output);
  input.connect(dry); dry.connect(output);

  const live = !(ctx instanceof OfflineAudioContext);
  const set = (param, v) => {
    if (live) param.setTargetAtTime(v, ctx.currentTime, 0.02);
    else param.value = v;
  };
  const setBiquad = (node, type, on, freq, gain, q) => {
    if (!on) { node.type = "peaking"; set(node.gain, 0); return; }
    node.type = type;
    set(node.frequency, freq);
    if (q !== undefined) set(node.Q, q);
    if (gain !== undefined) set(node.gain, gain);
  };

  function update(s) {
    setBiquad(hp, "highpass", s.hp.on, s.hp.freq, undefined, BUTTER2_Q_DB);
    low.forEach((n, i) => setBiquad(n, "highpass", s.hum.on, s.hum.cut, undefined, BUTTER4_Q_DB[i]));
    notches.forEach((n, i) => setBiquad(n, "notch", s.hum.on && i < s.hum.harmonics, s.hum.mains * (i + 1), undefined, NOTCH_Q));
    setBiquad(mud, "peaking", s.mud.on, s.mud.freq, s.mud.gain, s.mud.q);
    setBiquad(pres, "peaking", s.presence.on, s.presence.freq, s.presence.gain, s.presence.q);
    const dp = deess.parameters;
    set(dp.get("enabled"), s.deess.on ? 1 : 0);
    set(dp.get("freq"), s.deess.freq);
    set(dp.get("threshold"), s.deess.threshold);
    set(dp.get("ratio"), s.deess.ratio);
    set(dp.get("maxCut"), s.deess.maxCut);
    const gp = gate.parameters;
    set(gp.get("enabled"), s.gate.on ? 1 : 0);
    set(gp.get("threshold"), s.gate.threshold);
    set(gp.get("range"), s.gate.range);
    set(gp.get("attack"), s.gate.attack);
    set(gp.get("hold"), s.gate.hold);
    set(gp.get("release"), s.gate.release);
    if (s.comp.on) {
      set(comp.threshold, s.comp.threshold);
      set(comp.ratio, s.comp.ratio);
      set(comp.knee, s.comp.knee);
      set(comp.attack, s.comp.attack / 1000);
      set(comp.release, s.comp.release / 1000);
      set(makeup.gain, Math.pow(10, s.comp.makeup / 20));
    } else {
      set(comp.threshold, 0); set(comp.ratio, 1); set(comp.knee, 0);
      set(makeup.gain, 1);
    }
  }

  function setBypass(bypass) {
    set(wet.gain, bypass ? 0 : 1);
    set(dry.gain, bypass ? 1 : 0);
  }

  update(settings);
  setBypass(false);
  return { input, output, update, setBypass, deess, gate, comp };
}

// --- analysis ---------------------------------------------------------------

// K-weighting at any sample rate, in the form libebur128 uses to make the
// ITU-R BS.1770 filters for rates other than 48 kHz (high shelf about +4 dB
// above about 1.7 kHz, then a high-pass at about 38 Hz). The constants are
// as recalled from libebur128 and were not checked against its source; the
// result is compared with ffmpeg's ebur128 filter in the README.
function kWeightCoeffs(fs) {
  let f0 = 1681.974450955533, G = 3.999843853973347, Q = 0.7071752369554196;
  let K = Math.tan(Math.PI * f0 / fs);
  const Vh = Math.pow(10, G / 20), Vb = Math.pow(Vh, 0.4996667741545416);
  let a0 = 1 + K / Q + K * K;
  const s1 = {
    b: [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0],
    a: [2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0],
  };
  f0 = 38.13547087602444; Q = 0.5003270373238773;
  K = Math.tan(Math.PI * f0 / fs);
  a0 = 1 + K / Q + K * K;
  const s2 = { b: [1, -2, 1], a: [2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0] };
  return [s1, s2];
}

function biquadRun(x, { b, a }) {
  const y = new Float64Array(x.length);
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  for (let i = 0; i < x.length; i++) {
    const v = b[0] * x[i] + b[1] * x1 + b[2] * x2 - a[0] * y1 - a[1] * y2;
    x2 = x1; x1 = x[i]; y2 = y1; y1 = v; y[i] = v;
  }
  return y;
}

// Gated loudness of a mono signal, BS.1770 style: 400 ms blocks every 100 ms,
// absolute gate -70, relative gate -10. Mono, so no channel weights. A
// signal shorter than one block is measured as one block.
function integratedLoudness(x, fs) {
  const [s1, s2] = kWeightCoeffs(fs);
  const y = biquadRun(biquadRun(x, s1), s2);
  const block = Math.round(0.4 * fs), step = Math.round(0.1 * fs);
  const z = [];
  if (y.length < block) {
    let s = 0; for (let i = 0; i < y.length; i++) s += y[i] * y[i];
    z.push(s / Math.max(1, y.length));
  } else {
    // running sum of squares
    const cum = new Float64Array(y.length + 1);
    for (let i = 0; i < y.length; i++) cum[i + 1] = cum[i] + y[i] * y[i];
    for (let s = 0; s + block <= y.length; s += step) z.push((cum[s + block] - cum[s]) / block);
  }
  const L = (ms) => -0.691 + 10 * Math.log10(ms);
  const abs = z.filter((v) => v > 0 && L(v) > -70);
  if (!abs.length) return -Infinity;
  const rel = L(abs.reduce((p, v) => p + v, 0) / abs.length) - 10;
  const gated = abs.filter((v) => L(v) > rel);
  return L(gated.reduce((p, v) => p + v, 0) / gated.length);
}

function peakOf(x) {
  let p = 0;
  for (let i = 0; i < x.length; i++) { const a = Math.abs(x[i]); if (a > p) p = a; }
  return p;
}

// Head and tail silence: 10 ms frames, a frame is voice when its RMS is
// within 35 dB of the loudest frame and above -60 dBFS.
function trimBounds(x, fs, pad = 0.05) {
  const frame = Math.max(1, Math.round(0.01 * fs));
  const n = Math.floor(x.length / frame);
  if (n === 0) return { start: 0, end: x.length };
  const db = new Float64Array(n);
  let top = -Infinity;
  for (let k = 0; k < n; k++) {
    let s = 0;
    for (let i = k * frame; i < (k + 1) * frame; i++) s += x[i] * x[i];
    db[k] = 10 * Math.log10(s / frame + 1e-12);
    if (db[k] > top) top = db[k];
  }
  const thr = Math.max(-60, top - 35);
  let first = -1, last = -1;
  for (let k = 0; k < n; k++) if (db[k] > thr) { if (first < 0) first = k; last = k; }
  if (first < 0) return { start: 0, end: x.length, voiceStart: 0, voiceEnd: x.length };
  const p = Math.round(pad * fs);
  return {
    start: Math.max(0, first * frame - p), end: Math.min(x.length, (last + 1) * frame + p),
    voiceStart: first * frame, voiceEnd: (last + 1) * frame,
  };
}

// Edges of an exported take. Measured on recorded takes: the room tone
// (about -60 dBFS, mostly above 2 kHz) started at the first sample and
// stopped at the last one, and where the take was stopped there was often a
// click; over the music both edges stood out as hiss starting / stopping at
// once. So the room tone is faded in at the head and faded out after the last
// voiced frame (raised cosine), and the export ends before the take's stop.
const EDGE = {
  fadeIn: 0.03,     // s, from the first exported sample
  fadeOut: 0.15,    // s, room tone and breath after the last word
  hold: 0.02,       // s, after the last voiced frame before the fade-out starts
  stopGuard: 0.01,  // s, left out before the end of the take (the stop's click)
};

// Fade x (in place) over [a, b): gain from 0 at a to 1 when rising, from 1
// to 0 at b - 1 when not.
function rampInPlace(x, a, b, rising) {
  const n = Math.max(1, b - a);
  for (let i = a; i < b; i++) {
    const c = Math.cos(0.5 * Math.PI * (i - a + (rising ? 0 : 1)) / n);
    x[i] *= rising ? 1 - c * c : c * c;
  }
}

// Where the export of a take ends and its fade-out lies, in samples of the
// rendered take. srcEnd: where the take's own samples end in the render
// (after it only the filters' decay of the render's tail is left).
function edgeSpan(b, srcEnd, fs) {
  const fo = Math.round(EDGE.fadeOut * fs), hold = Math.round(EDGE.hold * fs);
  // at least hold + fade-out after the last word (longer when the pad is),
  // never past the take's stop
  const limit = Math.max(b.start + 1, srcEnd - Math.round(EDGE.stopGuard * fs));
  const end = Math.min(Math.max(b.end, b.voiceEnd + hold + fo), limit);
  const outStart = Math.max(b.start, Math.min(b.voiceEnd + hold, end - fo));
  return { end, outStart };
}

// --- render and export -------------------------------------------------------

const OUT_RATE = 44100;

async function renderChain(pcm, rate, settings, bypass) {
  const tail = Math.ceil(0.1 * OUT_RATE); // room for the compressor's look-ahead
  const len = Math.ceil(pcm.length * OUT_RATE / rate) + tail;
  const ctx = new OfflineAudioContext(1, len, OUT_RATE);
  await ensureWorklet(ctx);
  const buf = ctx.createBuffer(1, pcm.length, rate);
  buf.copyToChannel(pcm, 0);
  const src = ctx.createBufferSource();
  src.buffer = buf;
  const chain = buildChain(ctx, settings);
  chain.setBypass(bypass);
  src.connect(chain.input);
  chain.output.connect(ctx.destination);
  src.start(0);
  const out = await ctx.startRendering();
  return out.getChannelData(0);
}

// Render, trim, set the loudness. Returns the samples at 44.1 kHz and what was done.
async function processTake(pcm, rate, settings, { bypass = false } = {}) {
  const y = await renderChain(pcm, rate, settings, bypass);
  const o = settings.out;
  const srcEnd = Math.min(y.length, Math.ceil(pcm.length * OUT_RATE / rate));
  const b = o.trim ? trimBounds(y.subarray(0, srcEnd), OUT_RATE, o.pad)
    : { start: 0, end: srcEnd, voiceStart: 0, voiceEnd: srcEnd };
  const span = edgeSpan(b, srcEnd, OUT_RATE);
  b.end = span.end;
  const seg = y.slice(b.start, b.end);
  rampInPlace(seg, 0, Math.min(seg.length, Math.round(EDGE.fadeIn * OUT_RATE)), true);
  rampInPlace(seg, span.outStart - b.start, seg.length, false);
  const before = integratedLoudness(seg, OUT_RATE);
  let gainDb = 0, limited = false;
  if (o.normalize && isFinite(before)) {
    gainDb = o.lufs - before;
    const peak = peakOf(seg);
    const ceil = Math.pow(10, o.ceiling / 20);
    if (peak > 0 && peak * Math.pow(10, gainDb / 20) > ceil) {
      gainDb = 20 * Math.log10(ceil / peak);
      limited = true;
    }
    const g = Math.pow(10, gainDb / 20);
    for (let i = 0; i < seg.length; i++) seg[i] *= g;
  }
  return {
    samples: seg, rate: OUT_RATE, seconds: seg.length / OUT_RATE,
    trim: { start: b.start / OUT_RATE, end: b.end / OUT_RATE },
    loudnessBefore: before, gainDb, limited,
    loudnessAfter: isFinite(before) ? before + gainDb : before,
    peakDb: 20 * Math.log10(peakOf(seg) + 1e-12),
  };
}

function encodeWav16(samples, rate) {
  const n = samples.length;
  const buf = new ArrayBuffer(44 + 2 * n);
  const v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + 2 * n, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, 2 * n, true);
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + 2 * i, Math.round(s < 0 ? s * 32768 : s * 32767), true);
  }
  return buf;
}

// Read a 16-bit or 32-bit float PCM WAV header for its length (s) without decoding.
function wavSeconds(arrayBuffer) {
  const v = new DataView(arrayBuffer);
  let o = 12, rate = 0, blockAlign = 0;
  while (o + 8 <= v.byteLength) {
    const id = String.fromCharCode(v.getUint8(o), v.getUint8(o + 1), v.getUint8(o + 2), v.getUint8(o + 3));
    const size = v.getUint32(o + 4, true);
    if (id === "fmt ") { rate = v.getUint32(o + 12, true); blockAlign = v.getUint16(o + 20, true); }
    if (id === "data" && rate && blockAlign) return size / blockAlign / rate;
    o += 8 + size + (size & 1);
  }
  return NaN;
}

window.HirakuDSP = {
  PRESETS, cloneSettings, completeSettings, ensureWorklet, buildChain, MAX_HARMONICS,
  integratedLoudness, kWeightCoeffs, trimBounds, peakOf, renderChain, processTake,
  encodeWav16, wavSeconds, OUT_RATE, EDGE, edgeSpan,
};
