// Narration recorder: script, prompter, takes, filter knobs, export.
// Takes are kept raw (the mic signal before the filter); the filter is applied
// when listening and when exporting, so it can be changed after recording.

"use strict";

const D = window.HirakuDSP;

// Timings shared with promo/subs.py and promo/voice.py: a cut's first
// sentence starts LEAD into its slot, sentences are GAP apart, and TAIL is
// left at the end of the slot (as in the script's notes).
const LEAD_S = 0.3, GAP_S = 0.35, TAIL_S = 0.2;

const LS = { settings: "hiraku-rec-settings", script: "hiraku-rec-script", pos: "hiraku-rec-pos",
  device: "hiraku-rec-device", chosen: "hiraku-rec-chosen", countdown: "hiraku-rec-countdown",
  autoSave: "hiraku-rec-autosave" };
const lsGet = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* storage off */ } };

const $ = (id) => document.getElementById(id);

const state = {
  lines: [],           // {cut, start, end, ja, en, key, file}
  pos: 0,
  synth: {},           // key -> {seconds, buffer (AudioBuffer)}
  takes: {},           // key -> [{id, pcm, rate, seconds, trimmed, peak, clipped, ja}]
  chosen: {},          // key -> take id
  settings: D.completeSettings(JSON.parse(lsGet(LS.settings) || "null")),
  preset: null,
  bypass: false,
  recording: false,
  countingDown: false,
  scriptName: "",
  server: null,        // /api/status of promo/recorder/serve.py, or null when opened as a file
};

// --- script ------------------------------------------------------------------

function parseClock(t) { const [m, s] = t.trim().split(":"); return parseInt(m, 10) * 60 + parseFloat(s); }

// Same rules as parse_script in promo/voice.py: '#' lines are notes, four tab-separated columns.
function parseScript(text) {
  const lines = [], count = {};
  text.split(/\r?\n/).forEach((raw, n) => {
    if (!raw.trim() || raw.trimStart().startsWith("#")) return;
    const cols = raw.split("\t");
    if (cols.length !== 4) throw new Error(`${n + 1} 行目: タブで区切った 4 列のはずが ${cols.length} 列`);
    const [cut, slot, ja, en] = cols.map((c) => c.trim());
    const [a, b] = slot.split("-");
    const start = parseClock(a), end = parseClock(b);
    if (!(end > start)) throw new Error(`${n + 1} 行目: 枠の終わりが始まりより前 (${slot})`);
    count[cut] = (count[cut] || 0) + 1;
    const key = `${cut}_${count[cut]}`;
    lines.push({ cut, start, end, ja, en, key, file: `${key}.wav` });
  });
  return lines;
}

function cutsOf(lines) {
  const out = [];
  for (const ln of lines) {
    let c = out[out.length - 1];
    if (!c || c.cut !== ln.cut) { c = { cut: ln.cut, start: ln.start, end: ln.end, lines: [] }; out.push(c); }
    c.lines.push(ln);
  }
  return out;
}

// The voice a cut can hold: its slot minus the lead, the tail and the gaps.
function cutAvailable(c) { return c.end - c.start - LEAD_S - TAIL_S - GAP_S * (c.lines.length - 1); }

// Share of the cut for one sentence: by the synthesized length when known, else by characters.
function weight(ln) {
  if (state.synth[ln.key]) return state.synth[ln.key].seconds;
  return Math.max(1, ln.ja.replace(/[\s、。「」，．・]/g, "").length);
}
function sentenceBudget(ln) {
  const c = cutsOf(state.lines).find((x) => x.cut === ln.cut);
  const total = c.lines.reduce((s, x) => s + weight(x), 0);
  return cutAvailable(c) * weight(ln) / total;
}

function loadScriptText(text, name) {
  const lines = parseScript(text);
  if (!lines.length) throw new Error("文がひとつもありません");
  state.lines = lines;
  lsSet(LS.script, text);
  const saved = parseInt(lsGet(LS.pos) || "0", 10);
  state.pos = Math.min(isNaN(saved) ? 0 : saved, lines.length - 1);
  state.scriptName = name || "前回の原稿";
  $("scriptInfo").textContent = `${state.scriptName} — ${cutsOf(lines).length} カット・${lines.length} 文`;
  $("dropHint").classList.add("hidden");
  renderList();
  renderPrompter();
  renderStatus();
}

// --- synthesized voices --------------------------------------------------------

// files: File objects, or {name, arrayBuffer()} for voices fetched from the server.
// Decoded in an offline context so nothing waits for the page's first click.
async function loadSynthFiles(files) {
  const dec = new OfflineAudioContext(1, 1, 44100);
  const got = await Promise.all(files.map(async (f) => {
    const m = f.name.match(/^(.+_\d+)\.wav$/i);
    if (!m) return null;
    let ab;
    try { ab = await f.arrayBuffer(); } catch (e) { return null; }
    const seconds = D.wavSeconds(ab);
    let buffer = null;
    try { buffer = await dec.decodeAudioData(ab.slice(0)); } catch (e) { /* length only */ }
    return [m[1], { seconds: isNaN(seconds) && buffer ? buffer.duration : seconds, buffer }];
  }));
  let n = 0;
  for (const g of got) if (g) { state.synth[g[0]] = g[1]; n++; }
  renderList(); renderPrompter(); renderStatus();
  return n;
}

// --- the local server (promo/recorder/serve.py) -----------------------------------

// Opened from serve.py: read the script and the synthesized voices by themselves.
async function connectServer() {
  if (!/^https?:$/.test(location.protocol)) return false;
  let st;
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    if (!r.ok) return false;
    st = await r.json();
  } catch (e) { return false; }
  if (!st || st.app !== "hiraku-recorder") return false;
  state.server = st;
  document.body.classList.add("served");
  $("exportOne").textContent = "この文を保存";
  $("exportAll").textContent = "選んだ録音を全部保存";
  $("exportDir").hidden = true;
  $("autoSaveBox").hidden = false;
  $("dropHint").innerHTML = "原稿を読んでいます…";
  if (st.script.error) {
    $("scriptInfo").textContent = `作業フォルダに原稿がありません (${st.script.label})`;
    $("dropHint").textContent = `作業フォルダの ${st.script.label} が読めません: ${st.script.error}`;
  } else {
    try {
      const r = await fetch("/api/script", { cache: "no-store" });
      loadScriptText(await r.text(), st.script.name);
    } catch (e) { $("scriptInfo").textContent = `読めませんでした: ${e.message}`; $("dropHint").textContent = e.message; }
  }
  await loadSynthFiles(st.synth.files.map((name) => ({
    name, arrayBuffer: () => fetch(`/api/synth/${encodeURIComponent(name)}`).then((r) => {
      if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
      return r.arrayBuffer();
    }),
  })));
  renderStatus();
  return true;
}

const savedOnDisk = (file) => !!state.server && state.server.out.have.includes(file);

// One save at a time, in the order asked; voice.json is rewritten after each.
let saveChain = Promise.resolve();
function saveKey(key) {
  const p = saveChain.then(() => putTake(key));
  saveChain = p.catch(() => {});
  return p;
}

async function putTake(key) {
  const ln = state.lines.find((l) => l.key === key);
  if (!ln) throw new Error(`${key}: 原稿にない文`);
  const res = await renderExport(key);
  const r = await fetch(`/api/take/${encodeURIComponent(ln.file)}`, {
    method: "PUT", headers: { "Content-Type": "audio/wav" }, body: res.wav });
  let j = {};
  try { j = await r.json(); } catch (e) { /* empty body */ }
  if (!r.ok) throw new Error(`${ln.file} を保存できませんでした: ${j.error || "HTTP " + r.status}`);
  state.server.out.have = j.have;
  state.server.out.missing = j.missing;
  const left = j.missing ? `・残り ${j.missing} 文` : "・全部の文がそろった";
  $("exportInfo").textContent = `${ln.file} を ${j.folder} に保存 (${describe(res)}${left})`;
  renderList(); renderStatus();
  return j;
}

function maybeAutoSave(key) {
  if (!state.server || !$("autoSave").checked || !chosenTake(key)) return;
  saveKey(key).catch((e) => { $("exportInfo").textContent = e.message; });
}

// One line under the header: script, synthesized voices, where takes go.
function renderStatus() {
  const put = (id, html) => { $(id).querySelector("span").innerHTML = html; };
  const s = state.server, n = state.lines.length;
  if (n) put("stScript", `${escapeHtml(state.scriptName)}<span class="count">(${n} 文)</span>`);
  else if (s && s.script.error) put("stScript", `<span class="bad">見つかりません (${escapeHtml(s.script.label)})</span>`);
  else put("stScript", `<span class="bad">まだ読んでいません</span>`);
  const syn = n ? state.lines.filter((l) => state.synth[l.key]).length : Object.keys(state.synth).length;
  const synLabel = s ? `<span class="count">(${escapeHtml(s.synth.label)})</span>` : "";
  put("stSynth", syn ? `${syn} 本${n && syn < n ? ` / ${n}` : ""}${synLabel}` : `<span class="bad">なし</span>${synLabel}`);
  if (s) {
    const have = state.lines.filter((l) => savedOnDisk(l.file)).length;
    put("stOut", `${escapeHtml(s.out.label)}<span class="count ${n && have === n ? "good" : ""}">(${have}/${n} 文 保存済み)</span>`);
  } else {
    put("stOut", `ダウンロード<span class="count">(ファイルとして開いています。serve.py から開くと作業フォルダに自動で保存)</span>`);
  }
}

// --- audio graph ---------------------------------------------------------------

let ctx = null, chain = null, outAnalyser = null, inAnalyser = null;
let mic = { stream: null, source: null, tap: null };
let player = null;

async function audio() {
  if (ctx) { if (ctx.state === "suspended") await ctx.resume(); return ctx; }
  ctx = new AudioContext({ latencyHint: "interactive" });
  await D.ensureWorklet(ctx);
  chain = D.buildChain(ctx, state.settings);
  chain.setBypass(state.bypass);
  outAnalyser = ctx.createAnalyser(); outAnalyser.fftSize = 2048;
  chain.output.connect(outAnalyser);
  chain.output.connect(ctx.destination);
  chain.deess.port.onmessage = (e) => { $("dsInfo").textContent = `歯擦音 −${e.data.reductionDb.toFixed(1)} dB`; };
  chain.gate.port.onmessage = (e) => { $("gateInfo").textContent = e.data.open ? "ゲート 開" : `ゲート 閉 ${e.data.gainDb.toFixed(0)} dB`; };
  requestAnimationFrame(meterLoop);
  return ctx;
}

async function listDevices() {
  const sel = $("device");
  const devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
  const want = lsGet(LS.device) || "";
  sel.innerHTML = '<option value="">既定のマイク</option>' +
    devs.map((d, i) => `<option value="${d.deviceId}">${escapeHtml(d.label || `マイク ${i + 1}`)}</option>`).join("");
  if ([...sel.options].some((o) => o.value === want)) sel.value = want;
}

async function openMic() {
  const c = await audio();
  if (mic.stream) mic.stream.getTracks().forEach((t) => t.stop());
  if (mic.source) mic.source.disconnect();
  const id = $("device").value;
  const audioC = { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false };
  if (id) audioC.deviceId = { exact: id };
  mic.stream = await navigator.mediaDevices.getUserMedia({ audio: audioC });
  mic.source = c.createMediaStreamSource(mic.stream);
  inAnalyser = c.createAnalyser(); inAnalyser.fftSize = 2048;
  mic.source.connect(inAnalyser);
  if (!mic.tap) {
    mic.tap = new AudioWorkletNode(c, "hiraku-tap", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
    mic.tap.connect(c.destination); // outputs silence; keeps the node pulled
    mic.tap.port.onmessage = onTap;
  }
  mic.source.connect(mic.tap);
  setMonitor($("monitorOn").checked);
  $("micBtn").textContent = "マイク ✓";
  await listDevices();
  if (id) $("device").value = id;
}

function setMonitor(on) {
  if (!mic.source || !chain) return;
  try { mic.source.disconnect(chain.input); } catch (e) { /* not connected */ }
  if (on) mic.source.connect(chain.input);
}

// --- meters --------------------------------------------------------------------

const meter = { peakHold: 0, peakAt: 0, clipAt: -1e9 };
const buf = new Float32Array(2048);
function levelOf(an) { an.getFloatTimeDomainData(buf); return D.peakOf(buf); }
function dbfs(v) { return 20 * Math.log10(Math.max(v, 1e-6)); }
function meterWidth(db) { return Math.max(0, Math.min(100, (db + 60) / 60 * 100)); }

function meterLoop(now) {
  if (inAnalyser) {
    const p = levelOf(inAnalyser), db = dbfs(p);
    if (p >= meter.peakHold || now - meter.peakAt > 1500) { meter.peakHold = p; meter.peakAt = now; }
    if (p >= 0.989) meter.clipAt = now;
    $("meterFill").style.width = meterWidth(db) + "%";
    $("meterFill").parentElement.classList.toggle("hot", db > -6);
    $("meterPeak").style.left = `calc(${meterWidth(dbfs(meter.peakHold))}% - 2px)`;
    $("meterText").textContent = `${db < -59 ? "−∞" : db.toFixed(1)} dBFS`;
    $("clip").classList.toggle("on", now - meter.clipAt < 2000);
  }
  if (outAnalyser) $("outFill").style.width = meterWidth(dbfs(levelOf(outAnalyser))) + "%";
  if (state.recording) updateBands();
  requestAnimationFrame(meterLoop);
}

// --- recording -----------------------------------------------------------------

let rec = null; // {chunks, frames, peak, t0, key}

function onTap(e) {
  if (!rec) return;
  rec.chunks.push(e.data.pcm);
  rec.frames += e.data.pcm.length;
  if (e.data.peak > rec.peak) rec.peak = e.data.peak;
}

function current() { return state.lines[state.pos]; }

async function toggleRecord() {
  if (state.countingDown) { cancelCountdown(); return; }
  if (state.recording) { await stopRecording(); return; }
  await startRecording();
}

let countdownTimer = null;
function cancelCountdown() {
  clearTimeout(countdownTimer);
  state.countingDown = false;
  $("countdown").classList.remove("on");
}

async function startRecording({ countdown } = {}) {
  if (!current()) return;
  stopPlayer();
  if (!mic.stream) await openMic();
  const useCountdown = countdown === undefined ? $("countdownOn").checked : countdown;
  if (useCountdown) {
    state.countingDown = true;
    $("countdown").classList.add("on");
    for (const n of [3, 2, 1]) {
      $("countNum").textContent = n;
      await new Promise((r) => { countdownTimer = setTimeout(r, 1000); });
      if (!state.countingDown) return;
    }
    state.countingDown = false;
    $("countdown").classList.remove("on");
  }
  rec = { chunks: [], frames: 0, peak: 0, t0: performance.now(), key: current().key, ja: current().ja };
  state.recording = true;
  document.body.classList.add("recording");
  $("recBtn").innerHTML = "■ 止める <kbd>Space</kbd>";
}

async function stopRecording() {
  if (!state.recording) return null;
  // let the last block of the tap arrive
  await new Promise((r) => setTimeout(r, 150));
  const r = rec;
  rec = null;
  state.recording = false;
  document.body.classList.remove("recording");
  $("recBtn").innerHTML = "● 録る <kbd>Space</kbd>";
  const pcm = new Float32Array(r.frames);
  let o = 0;
  for (const c of r.chunks) { pcm.set(c, o); o += c.length; }
  const take = makeTake(pcm, ctx.sampleRate, r.ja);
  take.peak = Math.max(take.peak, r.peak);
  take.clipped = take.peak >= 0.989;
  (state.takes[r.key] = state.takes[r.key] || []).push(take);
  if (!state.chosen[r.key]) state.chosen[r.key] = take.id;
  saveChosen();
  store.put(r.key, take);
  renderList(); renderPrompter();
  if (state.chosen[r.key] === take.id) maybeAutoSave(r.key);
  return take;
}

let takeSeq = 0;
function makeTake(pcm, rate, ja, id) {
  const b = D.trimBounds(pcm, rate, state.settings.out.pad);
  const peak = D.peakOf(pcm);
  return { id: id || `${Date.now().toString(36)}-${(takeSeq++).toString(36)}`, pcm, rate,
    seconds: pcm.length / rate, trimmed: (b.end - b.start) / rate, peak, clipped: peak >= 0.989, ja };
}

function chosenTake(key) {
  const list = state.takes[key] || [];
  return list.find((t) => t.id === state.chosen[key]) || list[list.length - 1] || null;
}

function saveChosen() { lsSet(LS.chosen, JSON.stringify(state.chosen)); }

// --- take storage (IndexedDB; the page works without it) -------------------------

const store = {
  db: null,
  async open() {
    try {
      this.db = await new Promise((res, rej) => {
        const q = indexedDB.open("hiraku-recorder", 1);
        q.onupgradeneeded = () => q.result.createObjectStore("takes", { keyPath: "id" });
        q.onsuccess = () => res(q.result);
        q.onerror = () => rej(q.error);
      });
    } catch (e) { this.db = null; }
  },
  tx(mode) { return this.db.transaction("takes", mode).objectStore("takes"); },
  put(key, t) {
    if (!this.db) return;
    try { this.tx("readwrite").put({ id: t.id, key, pcm: t.pcm, rate: t.rate, ja: t.ja }); } catch (e) { /* ignore */ }
  },
  del(id) { if (!this.db) return; try { this.tx("readwrite").delete(id); } catch (e) { /* ignore */ } },
  async all() {
    if (!this.db) return [];
    try {
      return await new Promise((res, rej) => {
        const q = this.tx("readonly").getAll();
        q.onsuccess = () => res(q.result); q.onerror = () => rej(q.error);
      });
    } catch (e) { return []; }
  },
};

async function restoreTakes() {
  await store.open();
  const rows = await store.all();
  rows.sort((a, b) => (a.id < b.id ? -1 : 1));
  for (const r of rows) (state.takes[r.key] = state.takes[r.key] || []).push(makeTake(r.pcm, r.rate, r.ja, r.id));
  try { state.chosen = JSON.parse(lsGet(LS.chosen) || "{}") || {}; } catch (e) { state.chosen = {}; }
}

function deleteTake(key, id) {
  state.takes[key] = (state.takes[key] || []).filter((t) => t.id !== id);
  if (state.chosen[key] === id) {
    const last = state.takes[key][state.takes[key].length - 1];
    if (last) state.chosen[key] = last.id; else delete state.chosen[key];
    saveChosen();
    if (last) maybeAutoSave(key); // with no take left, the saved file stays as it was
  }
  store.del(id);
  renderList(); renderPrompter();
}

// --- playback ------------------------------------------------------------------

function stopPlayer() {
  if (player) { try { player.stop(); } catch (e) { /* stopped */ } player = null; }
  document.querySelectorAll(".playing").forEach((b) => b.classList.remove("playing"));
}

async function playTake(take, { loop = false } = {}) {
  const c = await audio();
  stopPlayer();
  const b = c.createBuffer(1, take.pcm.length, take.rate);
  b.copyToChannel(take.pcm, 0);
  const src = c.createBufferSource();
  src.buffer = b;
  src.loop = loop;
  src.connect(chain.input);
  src.onended = () => { if (player === src) player = null; };
  src.start();
  player = src;
}

async function playSynth(key) {
  const s = state.synth[key];
  if (!s || !s.buffer) return;
  const c = await audio();
  stopPlayer();
  const src = c.createBufferSource();
  src.buffer = s.buffer;
  src.connect(c.destination); // the synthesized voice is played as it is
  src.start();
  player = src;
}

// --- export --------------------------------------------------------------------

async function renderExport(key, { bypass = false } = {}) {
  const t = chosenTake(key);
  if (!t) throw new Error(`${key}: 録音がありません`);
  const res = await D.processTake(t.pcm, t.rate, state.settings, { bypass });
  return { ...res, take: t, wav: D.encodeWav16(res.samples, res.rate) };
}

function download(name, arrayBuffer) {
  const url = URL.createObjectURL(new Blob([arrayBuffer], { type: "audio/wav" }));
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function describe(res) {
  const lim = res.limited ? "、ピークで止めた" : "";
  return `${res.seconds.toFixed(2)} 秒・${fmtLu(res.loudnessBefore)} → ${fmtLu(res.loudnessAfter)}${lim}`;
}
const fmtLu = (v) => (isFinite(v) ? `${v.toFixed(1)} LUFS 相当` : "無音");

async function exportOne() {
  const ln = current();
  if (!ln) return;
  if (state.server) {
    try { await saveKey(ln.key); } catch (e) { $("exportInfo").textContent = e.message; }
    return;
  }
  try {
    const res = await renderExport(ln.key);
    download(ln.file, res.wav);
    $("exportInfo").textContent = `${ln.file}: ${describe(res)}`;
  } catch (e) { $("exportInfo").textContent = e.message; }
}

async function exportAll(dirHandle) {
  const keys = state.lines.map((l) => l.key).filter((k) => chosenTake(k));
  if (!keys.length) { $("exportInfo").textContent = "書き出す録音がありません"; return; }
  if (state.server && !dirHandle) {
    try { for (const k of keys) await saveKey(k); } catch (e) { $("exportInfo").textContent = e.message; return; }
    $("exportInfo").textContent = `${keys.length} 本を ${state.server.out.label} に保存した` +
      (state.server.out.missing ? ` (録音のない文 ${state.server.out.missing})` : " (全部の文がそろった)");
    return;
  }
  let n = 0;
  for (const k of keys) {
    const ln = state.lines.find((l) => l.key === k);
    $("exportInfo").textContent = `書き出し中 ${n + 1}/${keys.length} ${ln.file}`;
    const res = await renderExport(k);
    if (dirHandle) {
      const fh = await dirHandle.getFileHandle(ln.file, { create: true });
      const w = await fh.createWritable();
      await w.write(res.wav); await w.close();
    } else {
      download(ln.file, res.wav);
      await new Promise((r) => setTimeout(r, 250)); // browsers drop downloads fired in one burst
    }
    n++;
  }
  const missing = state.lines.length - keys.length;
  $("exportInfo").textContent = `${n} 本を書き出した${missing ? `(録音のない文 ${missing})` : ""}`;
}

async function exportToDir() {
  if (!window.showDirectoryPicker) { $("exportInfo").textContent = "このブラウザはフォルダへの保存に対応していません (ダウンロードを使ってください)"; return; }
  let dir;
  try { dir = await window.showDirectoryPicker({ mode: "readwrite" }); } catch (e) { return; }
  await exportAll(dir);
}

// --- filter knobs --------------------------------------------------------------

const STAGES = [
  { id: "hp", name: "ハイパス", what: "highpass", text: "低い揺れ (机・空調・吹かれ) を切る。声の芯より下だけ。",
    knobs: [["freq", "周波数", 40, 160, 1, "Hz"]] },
  { id: "mud", name: "こもりを削る", what: "peaking −", text: "200〜400 Hz の箱鳴りを下げて、声を軽くする。",
    knobs: [["freq", "周波数", 150, 500, 5, "Hz"], ["gain", "量", -9, 0, 0.5, "dB"], ["q", "幅 (Q)", 0.4, 3, 0.1, ""]] },
  { id: "presence", name: "明瞭さ", what: "peaking +", text: "2〜5 kHz の山。子音がはっきりして言葉が聞き取りやすくなる。",
    knobs: [["freq", "周波数", 1500, 6000, 50, "Hz"], ["gain", "量", 0, 8, 0.5, "dB"], ["q", "幅 (Q)", 0.4, 3, 0.1, ""]] },
  { id: "deess", name: "歯擦音を抑える", what: "de-esser", text: "サ行の「シー」が強い瞬間だけ、その帯域を下げる。しきい値を下げるほどよく効く。",
    knobs: [["freq", "中心", 5000, 10000, 100, "Hz"], ["threshold", "しきい値", -50, -10, 1, "dB"], ["ratio", "比", 1, 10, 0.5, ":1"], ["maxCut", "最大の削り", 2, 18, 1, "dB"]] },
  { id: "gate", name: "ノイズゲート", what: "gate", text: "声の無いあいだの息や部屋の音を下げる。しきい値より小さい音を「量」だけ下げる。",
    knobs: [["threshold", "しきい値", -70, -20, 1, "dB"], ["range", "下げる量", -60, 0, 1, "dB"], ["attack", "開く速さ", 0.5, 20, 0.5, "ms"], ["hold", "保つ", 0, 500, 10, "ms"], ["release", "閉じる速さ", 20, 800, 10, "ms"]] },
  { id: "comp", name: "コンプレッサー", what: "compressor", text: "大きい所を抑えて、声の大きさのむらを減らす。最後に「持ち上げ」で戻す。",
    knobs: [["threshold", "しきい値", -50, 0, 1, "dB"], ["ratio", "比", 1, 12, 0.5, ":1"], ["attack", "アタック", 0.5, 80, 0.5, "ms"], ["release", "リリース", 20, 800, 10, "ms"], ["knee", "ニー", 0, 20, 1, "dB"], ["makeup", "持ち上げ", 0, 18, 0.5, "dB"]] },
  { id: "out", name: "書き出し", what: "normalize", text: "書き出すときに、文ごとに同じ大きさへそろえ、頭と尾の無音を詰める。",
    knobs: [["lufs", "大きさ", -24, -10, 0.5, "LUFS"], ["ceiling", "ピーク上限", -6, 0, 0.5, "dBFS"], ["pad", "前後に残す", 0, 0.3, 0.01, "秒"]],
    toggles: [["normalize", "大きさをそろえる"], ["trim", "無音を詰める"]] },
];

function fmt(v, step) { const d = step < 0.1 ? 2 : step < 1 ? 1 : 0; return Number(v).toFixed(d); }

function buildKnobs() {
  const root = $("knobs");
  root.innerHTML = "";
  for (const st of STAGES) {
    const sec = document.createElement("section");
    sec.className = "stage"; sec.dataset.stage = st.id;
    const onToggle = st.id === "out" ? "" :
      `<label><input type="checkbox" data-k="${st.id}.on"> ${st.name}</label>`;
    sec.innerHTML = `<div class="stage-head">${onToggle || `<b>${st.name}</b>`}<span class="what">${st.what}</span></div><p>${st.text}</p>`;
    for (const [k, label, min, max, step, unit] of st.knobs) {
      const row = document.createElement("div");
      row.className = "knob";
      row.innerHTML = `<span>${label}</span><input type="range" min="${min}" max="${max}" step="${step}" data-k="${st.id}.${k}"><output data-o="${st.id}.${k}"></output>`;
      row.querySelector("output").dataset.unit = unit;
      row.querySelector("output").dataset.step = step;
      sec.appendChild(row);
    }
    for (const [k, label] of st.toggles || []) {
      const row = document.createElement("label");
      row.className = "check"; row.style.marginRight = "12px";
      row.innerHTML = `<input type="checkbox" data-k="${st.id}.${k}"> ${label}`;
      sec.appendChild(row);
    }
    root.appendChild(sec);
  }
  root.addEventListener("input", (e) => {
    const k = e.target.dataset.k;
    if (!k) return;
    const [s, f] = k.split(".");
    state.settings[s][f] = e.target.type === "checkbox" ? e.target.checked : parseFloat(e.target.value);
    state.preset = null;
    settingsChanged();
  });
}

function syncKnobs() {
  document.querySelectorAll("#knobs [data-k]").forEach((el) => {
    const [s, f] = el.dataset.k.split(".");
    if (el.type === "checkbox") el.checked = !!state.settings[s][f];
    else el.value = state.settings[s][f];
  });
  document.querySelectorAll("#knobs output").forEach((o) => {
    const [s, f] = o.dataset.o.split(".");
    o.textContent = `${fmt(state.settings[s][f], parseFloat(o.dataset.step))} ${o.dataset.unit}`;
  });
  document.querySelectorAll("#knobs .stage").forEach((sec) => {
    const s = state.settings[sec.dataset.stage];
    sec.classList.toggle("off", s.on === false);
  });
  document.querySelectorAll(".preset").forEach((b) => b.classList.toggle("on", b.dataset.preset === state.preset));
  const o = state.settings.out;
  $("normNote").textContent = o.normalize
    ? `書き出すとき: フィルターを通した文の大きさを測り (ITU-R BS.1770 の K 特性と 400 ms のブロック・ゲートにならった簡易の計算。厳密な LUFS 計とは同じでない)、${o.lufs} LUFS 相当になるよう音量を一律に上げ下げする。ピークが ${o.ceiling} dBFS を超えるときは、そこで止める (その文は目標より小さくなる)。`
    : "書き出すとき: 大きさはそろえない (フィルターの後のまま)。";
}

function settingsChanged() {
  syncKnobs();
  if (chain) chain.update(state.settings);
  lsSet(LS.settings, JSON.stringify(state.settings));
  renderPrompter();
}

function applyPreset(name) {
  state.settings = D.cloneSettings(D.PRESETS[name]);
  state.preset = name;
  settingsChanged();
}

function setBypass(b) {
  state.bypass = b;
  if (chain) chain.setBypass(b);
  const btn = $("abBtn");
  btn.classList.toggle("on", !b);
  btn.textContent = b ? "フィルター前 (素の声)" : "フィルター後";
}

// --- rendering -----------------------------------------------------------------

function escapeHtml(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }
const sec = (v) => `${v.toFixed(1)} 秒`;
const clock = (t) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;

function renderList() {
  const ol = $("list");
  ol.innerHTML = "";
  for (const c of cutsOf(state.lines)) {
    const avail = cutAvailable(c);
    const used = c.lines.reduce((s, ln) => { const t = chosenTake(ln.key); return s + (t ? t.trimmed : 0); }, 0);
    const allTaken = c.lines.every((ln) => chosenTake(ln.key));
    const head = document.createElement("li");
    head.className = "cut-head";
    head.innerHTML = `<b>${escapeHtml(c.cut)}</b><span>${clock(c.start)}–${clock(c.end)}</span><span>枠 ${sec(c.end - c.start)}・声に使える ${sec(avail)}</span>` +
      (allTaken ? `<span class="${used > avail ? "over" : "ok"}">録った ${sec(used)}</span>` : "");
    ol.appendChild(head);
    for (const ln of c.lines) {
      const li = document.createElement("li");
      const i = state.lines.indexOf(ln);
      li.className = "row" + (i === state.pos ? " active" : "");
      const t = chosenTake(ln.key), n = (state.takes[ln.key] || []).length, budget = sentenceBudget(ln);
      const syn = state.synth[ln.key] ? `合成 ${state.synth[ln.key].seconds.toFixed(2)}` : "";
      const mine = t ? `<span class="${t.trimmed > budget ? "over" : "done"}">● ${t.trimmed.toFixed(2)}</span>${n > 1 ? ` ×${n}` : ""}` : "";
      const disk = !state.server ? "" : savedOnDisk(ln.file) ? ' · <span class="saved">保存 ✓</span>' : t ? " · 未保存" : "";
      li.innerHTML = `<span class="t"><span class="k">${ln.key}</span>${escapeHtml(ln.ja)}</span><span class="s">目安 ${budget.toFixed(1)} 秒${syn ? " · " + syn : ""}${mine ? " · 録音 " + mine : ""}${disk}</span>`;
      li.onclick = () => go(i);
      ol.appendChild(li);
    }
  }
  const act = ol.querySelector(".row.active");
  if (act) act.scrollIntoView({ block: "nearest" });
}

function ctxLine(ln) { return ln ? `<span class="ck">${ln.key}</span>${escapeHtml(ln.ja)}` : ""; }

function renderPrompter() {
  const ln = current();
  if (!ln) return;
  const c = cutsOf(state.lines).find((x) => x.cut === ln.cut);
  $("pKey").textContent = ln.key;
  $("pCut").textContent = `カット ${ln.cut} · ${clock(c.start)}–${clock(c.end)} · 枠 ${sec(c.end - c.start)} · ${c.lines.indexOf(ln) + 1}/${c.lines.length} 文目`;
  $("pPrev").innerHTML = ctxLine(state.lines[state.pos - 1]);
  $("pNext").innerHTML = ctxLine(state.lines[state.pos + 1]);
  $("pJa").textContent = ln.ja;
  $("pEn").textContent = ln.en;
  const s = state.synth[ln.key];
  $("synthInfo").textContent = s ? `合成の声 ${s.seconds.toFixed(2)} 秒` : "";
  $("synthPlay").hidden = !(s && s.buffer);
  renderTakes();
  updateBands();
}

function renderTakes() {
  const ln = current();
  const ul = $("takes");
  const list = state.takes[ln.key] || [];
  const budget = sentenceBudget(ln);
  ul.innerHTML = list.length ? "" : '<li class="empty">まだ録っていません。スペースで録り始めます。</li>';
  list.forEach((t, i) => {
    const li = document.createElement("li");
    li.className = "take" + (state.chosen[ln.key] === t.id ? " chosen" : "");
    const warn = [];
    if (t.clipped) warn.push('<span class="warn">クリップ</span>');
    if (t.ja !== ln.ja) warn.push('<span class="warn">原稿の文が変わっています</span>');
    li.innerHTML = `<button class="star" title="この 1 本を使う">${state.chosen[ln.key] === t.id ? "★" : "☆"}</button>` +
      `<span class="len ${t.trimmed > budget ? "over" : ""}">${t.trimmed.toFixed(2)} s</span>` +
      `<span class="info">${i + 1} 本目 · 録った長さ ${t.seconds.toFixed(1)} s · ピーク ${dbfs(t.peak).toFixed(1)} dBFS ${warn.join(" ")}</span>` +
      `<span class="acts"><button class="btn small ghost" data-a="play">▶</button><button class="btn small ghost" data-a="stop">■</button><button class="btn small ghost" data-a="del" title="この録音を捨てる">✕</button></span>`;
    li.querySelector(".star").onclick = () => {
      const changed = state.chosen[ln.key] !== t.id;
      state.chosen[ln.key] = t.id; saveChosen(); renderList(); renderTakes();
      if (changed) maybeAutoSave(ln.key);
    };
    li.querySelector('[data-a="play"]').onclick = () => playTake(t);
    li.querySelector('[data-a="stop"]').onclick = stopPlayer;
    li.querySelector('[data-a="del"]').onclick = () => deleteTake(ln.key, t.id);
    ul.appendChild(li);
  });
}

function updateBands() {
  const ln = current();
  if (!ln) return;
  const budget = sentenceBudget(ln);
  const c = cutsOf(state.lines).find((x) => x.cut === ln.cut);
  const avail = cutAvailable(c);
  const t = chosenTake(ln.key);
  const elapsed = state.recording && rec ? (performance.now() - rec.t0) / 1000 : (t ? t.trimmed : 0);
  const others = c.lines.filter((x) => x !== ln).reduce((s, x) => { const tt = chosenTake(x.key); return s + (tt ? tt.trimmed : sentenceBudget(x)); }, 0);
  const cutUsed = others + elapsed;
  // the bar spans 130 % of the budget so going over is visible
  const fill = $("bandFill");
  fill.style.width = Math.min(100, elapsed / (budget * 1.3) * 100) + "%";
  fill.className = "band-fill" + (elapsed > budget ? " over" : elapsed > budget * 0.9 ? " near" : "");
  const mark = $("bandMark");
  mark.style.display = "block"; mark.style.left = `${100 / 1.3}%`;
  const left = budget - elapsed;
  const bt = $("bandText");
  bt.textContent = state.recording
    ? `${elapsed.toFixed(1)} 秒 / 目安 ${budget.toFixed(1)} (${left >= 0 ? "残り " + left.toFixed(1) : (-left).toFixed(1) + " 超過"})`
    : t ? `この 1 本 ${t.trimmed.toFixed(2)} 秒 / 目安 ${budget.toFixed(1)}` : `目安 ${budget.toFixed(1)} 秒`;
  bt.classList.toggle("over", left < 0);
  const cf = $("cutFill");
  cf.style.width = Math.min(100, cutUsed / avail * 100) + "%";
  cf.className = "band-fill" + (cutUsed > avail ? " over" : cutUsed > avail * 0.95 ? " near" : "");
  const ct = $("cutText");
  ct.textContent = `${cutUsed.toFixed(1)} / ${avail.toFixed(1)} 秒 (${cutUsed <= avail ? "残り " + (avail - cutUsed).toFixed(1) : (cutUsed - avail).toFixed(1) + " 超過"})`;
  ct.classList.toggle("over", cutUsed > avail);
}

function go(i) {
  if (state.recording || state.countingDown || !state.lines.length) return;
  state.pos = Math.max(0, Math.min(state.lines.length - 1, i));
  lsSet(LS.pos, String(state.pos));
  stopPlayer();
  renderList(); renderPrompter();
}

// --- wiring --------------------------------------------------------------------

async function readScriptFile(file) {
  try { loadScriptText(await file.text(), file.name); }
  catch (e) { $("scriptInfo").textContent = `読めませんでした: ${e.message}`; }
}

function wire() {
  $("scriptFile").onchange = (e) => { if (e.target.files[0]) readScriptFile(e.target.files[0]); e.target.value = ""; };
  $("synthDir").onchange = async (e) => { const n = await loadSynthFiles([...e.target.files]); $("synthInfo").textContent = `合成の声 ${n} 本を読んだ`; e.target.value = ""; };
  for (const ev of ["dragenter", "dragover"]) document.addEventListener(ev, (e) => { e.preventDefault(); $("listPane").classList.add("dragover"); });
  for (const ev of ["dragleave", "drop"]) document.addEventListener(ev, (e) => { e.preventDefault(); $("listPane").classList.remove("dragover"); });
  document.addEventListener("drop", async (e) => {
    const files = [...e.dataTransfer.files];
    const tsv = files.find((f) => /\.(tsv|txt)$/i.test(f.name));
    if (tsv) await readScriptFile(tsv);
    const wavs = files.filter((f) => /\.wav$/i.test(f.name));
    if (wavs.length) await loadSynthFiles(wavs);
  });
  $("micBtn").onclick = () => openMic().catch((e) => { $("micBtn").textContent = "マイクが使えません"; console.error(e); });
  $("device").onchange = () => { lsSet(LS.device, $("device").value); if (mic.stream) openMic(); };
  $("recBtn").onclick = toggleRecord;
  $("retakeBtn").onclick = () => { if (!state.recording) startRecording(); };
  $("prevBtn").onclick = () => go(state.pos - 1);
  $("nextBtn").onclick = () => go(state.pos + 1);
  $("countdownOn").checked = lsGet(LS.countdown) !== "0";
  $("countdownOn").onchange = (e) => lsSet(LS.countdown, e.target.checked ? "1" : "0");
  // the button is on the page before a script is read; do nothing until there is a sentence
  $("synthPlay").onclick = () => { const ln = current(); if (ln) playSynth(ln.key); };
  $("exportOne").onclick = exportOne;
  $("exportAll").onclick = () => exportAll(null);
  $("exportDir").onclick = exportToDir;
  $("exportDir").hidden = !window.showDirectoryPicker;
  $("autoSave").checked = lsGet(LS.autoSave) !== "0";
  $("autoSave").onchange = (e) => lsSet(LS.autoSave, e.target.checked ? "1" : "0");
  document.querySelectorAll(".preset").forEach((b) => { b.onclick = () => applyPreset(b.dataset.preset); });
  $("abBtn").onclick = () => setBypass(!state.bypass);
  $("playLoop").onclick = () => { const t = current() && chosenTake(current().key); if (t) playTake(t, { loop: true }); };
  $("monitorOn").onchange = async (e) => { if (e.target.checked && !mic.stream) await openMic(); setMonitor(e.target.checked); };
  $("saveJson").onclick = () => {
    const blob = new Blob([JSON.stringify(state.settings, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "voice-filter.json"; a.click();
  };
  $("loadJson").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try { state.settings = D.completeSettings(JSON.parse(await f.text())); state.preset = null; settingsChanged(); }
    catch (err) { $("normNote").textContent = `JSON を読めませんでした: ${err.message}`; }
    e.target.value = "";
  };
  // Keys: Space record/stop, → next, ← previous, R record again.
  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "select" || (tag === "input" && e.target.type === "text") || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.code === "Space") { e.preventDefault(); toggleRecord(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); go(state.pos + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); go(state.pos - 1); }
    else if (e.key === "r" || e.key === "R") { e.preventDefault(); if (!state.recording && !state.countingDown) startRecording(); }
    else return;
    if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
  });
  // a clicked button keeps focus; Space must not press it again
  document.addEventListener("click", (e) => { if (e.target.closest("button")) e.target.closest("button").blur(); });
}

async function init() {
  buildKnobs();
  wire();
  syncKnobs();
  setBypass(false);
  await restoreTakes();
  renderStatus();
  // from serve.py the work folder's script is read; as a file, the last script read
  if (!(await connectServer())) {
    const saved = lsGet(LS.script);
    if (saved) { try { loadScriptText(saved); } catch (e) { /* stale script */ } }
  }
  if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) listDevices().catch(() => {});
}

// Hooks for the browser test (promo/tests/test_recorder.py).
window.hirakuRec = {
  state, loadScriptText, startRecording, stopRecording, go, applyPreset, setBypass, openMic, saveKey,
  idle: () => saveChain,
  async exportBase64(key, opts) {
    const r = await renderExport(key, opts);
    const bytes = new Uint8Array(r.wav);
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return { b64: btoa(s), seconds: r.seconds, loudnessAfter: r.loudnessAfter, gainDb: r.gainDb, limited: r.limited };
  },
  loudness: (arr, fs) => D.integratedLoudness(Float32Array.from(arr), fs),
  ready: init(),
};
