/* Edge-Case Miner voice assistant. Loaded by the dashboard; drives it through the same API the page uses.
   The page can listen for `edgecase:run` (detail: {id}) to attach to a run the assistant started, and the
   assistant uses these page hooks only if they exist: #request, #confirmed, #rejected, #coverage, #gaps, #prompt.
   Speech out: the browser's speech synthesis (captions always). Speech in: 16 kHz WAV to /api/transcribe (Canary-1B). */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
  const sentences = (t, n) => ((t || "").match(/[^.!?]+[.!?]*/g) || []).slice(0, n).join(" ").trim();
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  /* ---------- a quiet orb in the corner; the assistant talks, you press Space to talk back ---------- */
  const css = `
  #eca { position:fixed; right:28px; bottom:28px; z-index:900; display:flex; flex-direction:column; align-items:flex-end; gap:10px; font:inherit; color:var(--ink, #1d1d1f); pointer-events:none; }
  #eca-orb { position:relative; width:56px; height:56px; border-radius:50%; cursor:pointer; pointer-events:auto;
    background:radial-gradient(circle at 35% 35%, #b9dcff, var(--blue, #0071e3) 72%); box-shadow:0 8px 24px rgba(0,113,227,.25);
    transition:transform .3s var(--ease, ease), background .4s, box-shadow .4s; }
  #eca-orb::before, #eca-orb::after { content:""; position:absolute; inset:0; border-radius:50%; border:1.5px solid rgba(0,113,227,.35); opacity:0; }
  #eca.talking #eca-orb { animation:eca-breathe 1.8s ease-in-out infinite; }
  #eca.talking #eca-orb::before { animation:eca-ring 1.8s ease-out infinite; }
  #eca.talking #eca-orb::after { animation:eca-ring 1.8s ease-out .6s infinite; }
  #eca.waiting #eca-orb { animation:eca-breathe 3.2s ease-in-out infinite; }
  #eca.listening #eca-orb { background:radial-gradient(circle at 35% 35%, #ffc9c4, var(--red, #ff3b30) 72%); box-shadow:0 8px 24px rgba(255,59,48,.3); animation:eca-breathe .9s ease-in-out infinite; }
  #eca.listening #eca-orb::before { border-color:rgba(255,59,48,.4); animation:eca-ring .9s ease-out infinite; }
  #eca.thinking #eca-orb { animation:eca-spin 1.2s linear infinite; background:conic-gradient(from 0deg, #b9dcff, var(--blue, #0071e3), #b9dcff); }
  #eca.off #eca-orb { background:radial-gradient(circle at 35% 35%, #e8e8ed, #c7c7cc 72%); box-shadow:0 6px 18px rgba(0,0,0,.12); }
  @keyframes eca-breathe { 50% { transform:scale(1.06); } }
  @keyframes eca-ring { 0% { transform:scale(1); opacity:.6; } 100% { transform:scale(1.9); opacity:0; } }
  @keyframes eca-spin { to { transform:rotate(360deg); } }
  #eca-hint { font-size:12px; letter-spacing:.02em; color:var(--ink-2, #6e6e73); background:rgba(255,255,255,.8); -webkit-backdrop-filter:blur(12px); backdrop-filter:blur(12px);
    border:1px solid rgba(0,0,0,.06); border-radius:980px; padding:5px 11px; opacity:0; transform:translateY(4px); transition:opacity .3s, transform .3s; white-space:nowrap; }
  #eca-hint.on { opacity:1; transform:none; }
  #eca-hint kbd { font:inherit; font-size:11px; padding:1px 6px; border-radius:6px; border:1px solid rgba(0,0,0,.12); background:#fff; color:var(--ink, #1d1d1f); margin:0 2px; }
  #eca-cap { display:none; max-width:360px; font-size:14px; line-height:1.4; color:var(--ink, #1d1d1f); background:rgba(255,255,255,.9); -webkit-backdrop-filter:blur(12px); backdrop-filter:blur(12px);
    border:1px solid rgba(0,0,0,.06); border-radius:14px; padding:10px 14px; text-align:right; }
  #eca.silent #eca-cap { display:block; }
  #eca-hero { display:none; } #eca-hero.on { display:block; width:340px; border-radius:14px; overflow:hidden; box-shadow:0 24px 60px rgba(0,0,0,.18); background:#000; }
  #eca-hero video { width:100%; display:block; }
  .eca-focus { outline:2px solid rgba(0,113,227,.55); outline-offset:10px; border-radius:12px; transition:outline-color .3s; }
  .eca-filled { box-shadow:0 0 0 3px rgba(0,113,227,.25) !important; transition:box-shadow .3s; }`;
  document.head.append(Object.assign(el("style"), { textContent: css }));

  const panel = el("div"); panel.id = "eca"; panel.className = "off";
  panel.innerHTML = `<div id="eca-hero"></div><div id="eca-cap"></div><div id="eca-hint" class="on">Press <kbd>space</kbd> to talk to me</div><div id="eca-orb" title="Press space"></div>`;
  document.body.append(panel);
  const hint = html => { const h = $("eca-hint"); if (!html) { h.classList.remove("on"); return; } h.innerHTML = html; h.classList.add("on"); };
  const mode = m => { const silentNow = panel.classList.contains("silent"); panel.className = m + (silentNow ? " silent" : ""); };
  let runId = null, busy = false, started = false;

  /* ---------- speech out ---------- */
  let speaking = null;
  let serverTts = null;   // null: unknown, true/false once probed
  const FEMALE = /Google US English|Samantha|Aria|Jenny|Ava|Allison|Zira|Karen|Moira|Fiona|Tessa|Victoria|Susan|female|Natural/i;
  async function speakServer(text) {
    if (serverTts === false) return false;
    try {
      const r = await fetch("/api/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
      if (r.status === 404) { serverTts = false; return false; }
      if (!r.ok) return false;
      serverTts = true;
      const url = URL.createObjectURL(await r.blob());
      await new Promise(res => { const a = new Audio(url); speak.cancel = () => { a.pause(); res(); }; a.onended = res; a.onerror = res; a.play().catch(res); });
      URL.revokeObjectURL(url); return true;
    } catch (e) { return false; }
  }
  function speak(text) {
    $("eca-cap").textContent = text; mode("talking");
    return (speaking = new Promise(async resolve => {
      const done = () => { panel.classList.remove("talking"); resolve(); };
      if (await speakServer(text)) return done();
      const synth = window.speechSynthesis;
      if (!synth) { panel.classList.add("silent"); return setTimeout(done, Math.max(1500, text.split(/\s+/).length * 330)); }
      synth.cancel();
      const parts = text.match(/[^.!?]+[.!?]*/g) || [text];
      let i = 0, begun = false, cancelled = false;
      const words = text.split(/\s+/).length, guard = setTimeout(done, Math.max(2000, words * 450));
      speak.cancel = () => { cancelled = true; clearTimeout(guard); synth.cancel(); done(); };
      const next = () => {
        if (cancelled) return;
        if (i >= parts.length) { clearTimeout(guard); return done(); }
        const u = new SpeechSynthesisUtterance(parts[i++].trim());
        const vs = synth.getVoices();
        u.voice = vs.find(v => /^en/i.test(v.lang) && FEMALE.test(v.name)) || vs.find(v => /^en/i.test(v.lang)) || null;
        u.rate = 0.98; u.pitch = 1.05; u.onstart = () => { begun = true; }; u.onend = next; u.onerror = next; synth.speak(u);
      };
      next();
      // No audio device (e.g. a remote desktop): the utterance never starts. Fall back to caption reading time.
      setTimeout(() => { if (!begun && !cancelled) { panel.classList.add("silent"); synth.cancel(); clearTimeout(guard); setTimeout(done, Math.max(800, words * 260 - 1500)); } }, 1500);
    }));
  }

  /* ---------- speech in: WAV recorder with silence stop, Canary via /api/transcribe ---------- */
  let micOk = null, rec = null;
  async function record(maxMs = 10000) {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    const src = ctx.createMediaStreamSource(stream), proc = ctx.createScriptProcessor(4096, 1, 1), chunks = [];
    let heardVoice = false, quietSince = 0, stopper;
    const finish = () => new Promise(res => { proc.disconnect(); src.disconnect(); stream.getTracks().forEach(t => t.stop()); ctx.close().then(() => res(wav(chunks, 16000))); });
    const p = new Promise(resolve => { stopper = () => finish().then(resolve); });
    proc.onaudioprocess = e => {
      const d = e.inputBuffer.getChannelData(0); chunks.push(new Float32Array(d));
      let s = 0; for (let i = 0; i < d.length; i += 8) s += d[i] * d[i]; const rms = Math.sqrt(s / (d.length / 8));
      const now = ctx.currentTime;
      if (rms > 0.02) { heardVoice = true; quietSince = 0; } else if (heardVoice && !quietSince) quietSince = now;
      if (heardVoice && quietSince && now - quietSince > 1.3) stopper();      // 1.3 s of silence after speech
      if (now > maxMs / 1000) stopper();
    };
    src.connect(proc); proc.connect(ctx.destination);
    rec = { stop: stopper };
    const blob = await p; rec = null; return blob;
  }
  function wav(chunks, rate) {
    const n = chunks.reduce((a, c) => a + c.length, 0), buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    const str = (o, s) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
    str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE"); str(12, "fmt "); v.setUint32(16, 16, true);
    v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, n * 2, true);
    let o = 44; for (const c of chunks) for (let i = 0; i < c.length; i++, o += 2) v.setInt16(o, Math.max(-1, Math.min(1, c[i])) * 0x7fff, true);
    return new Blob([buf], { type: "audio/wav" });
  }
  async function listenOnce() {
    if (micOk === false) return null;
    mode("listening"); hint("Listening… I stop when you pause");
    let blob;
    try { blob = await record(); micOk = true; }
    catch (e) { micOk = false; mode("waiting"); hint("No microphone · <kbd>enter</kbd> yes · <kbd>esc</kbd> no"); return null; }
    mode("thinking"); hint("One moment…");
    try {
      const fd = new FormData(); fd.append("file", blob, "speech.wav");
      const r = await fetch("/api/transcribe", { method: "POST", body: fd });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const { text } = await r.json(); mode("waiting"); hint(""); return text;
    } catch (e) { mode("waiting"); hint("I couldn't hear that · <kbd>space</kbd> to try again"); return null; }
  }

  /* ---------- answers: Space to talk, Enter = yes / all, Esc = no. The page's own inputs keep working. ---------- */
  let pending = null, early = null, yesNoQuestion = false;
  function waitForAnswer(yesNo) {
    if (early) { const v = early; early = null; return Promise.resolve(v); }
    yesNoQuestion = !!yesNo; mode("waiting");
    hint(yesNo ? "<kbd>space</kbd> answer · <kbd>enter</kbd> yes · <kbd>esc</kbd> no" : "<kbd>space</kbd> to answer" + (micOk === false ? " · <kbd>enter</kbd> for all" : ""));
    return new Promise(resolve => {
      pending = { resolve: v => { pending = null; hint(""); resolve(v); } };
      if (micOk !== false) listenOnce().then(t => { if (t && pending) pending.resolve(t); });
    });
  }
  function answer(v) {
    if (pending) return pending.resolve(v);
    if (busy) { early = v; if (speak.cancel) speak.cancel(); return; }
    newRequest(v);
  }
  const typingInPage = () => /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName) || !!(document.activeElement || {}).isContentEditable;
  document.addEventListener("keydown", async e => {
    if (typingInPage() || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.code === "Space") {
      e.preventDefault();
      if (!started) return start();
      if (rec) return rec.stop();
      if (pending && micOk !== false) return;             // already listening for this question
      const t = await listenOnce(); if (t) answer(t);
    } else if (e.key === "Enter" && (pending || busy)) { e.preventDefault(); answer(yesNoQuestion ? "yes" : "all"); }
    else if (e.key === "Escape" && (pending || busy)) { e.preventDefault(); answer("no"); }
  });
  $("eca-orb").addEventListener("click", async () => { if (!started) return start(); if (rec) return rec.stop(); if (!pending || micOk === false) { const t = await listenOnce(); if (t) answer(t); } });
  const isYes = t => /^(yes|yeah|yep|sure|ok|okay|go|do it|approve|please)/i.test((t || "").trim());

  /* ---------- API ---------- */
  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) { let d = r.statusText; try { d = (await r.json()).detail || d; } catch (e) {} throw new Error(typeof d === "string" ? d : JSON.stringify(d)); }
    return r;
  }
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  let focused = null;
  const scrollTo = id => {                       // bring the section into view and outline it while it is discussed
    const n = $(id); if (focused) focused.classList.remove("eca-focus"); focused = null;
    if (!n) return; n.scrollIntoView({ behavior: "smooth", block: "start" }); n.classList.add("eca-focus"); focused = n;
  };

  /* ---------- page adapter: use the dashboard's own hooks when they exist, so everything shows on the page ---------- */
  const page = {
    setRequest(text) { const r = $("request"); if (r) { r.value = text; r.classList.add("eca-filled"); r.dispatchEvent(new Event("input")); } },
    setGroups(ids) {
      for (const b of document.querySelectorAll("#groups input[type=checkbox]")) b.checked = ids.includes(b.value);
      const scopes = $("scopes");
      if (scopes) for (const b of scopes.querySelectorAll(".src")) b.classList.toggle("on", b.dataset.id ? ids.includes(b.dataset.id) : !ids.length);
    },
    attach(id, request, groups) {   // the page takes over rendering the run
      window.dispatchEvent(new CustomEvent("edgecase:run", { detail: { id, request, groups } }));
    },
    showClip(c) {                    // the page's detail view if it has one; otherwise the panel's own player
      try { if (typeof openSheet === "function" && typeof cardsById === "object" && cardsById[c.clip_id]) { openSheet(cardsById[c.clip_id]); return true; } } catch (e) {}
      return false;
    },
    hideClip() { const b = $("d-close"); if (b) b.click(); },
    filter(name) { const b = document.querySelector('#filters .f[data-f="' + name + '"]'); if (b) b.click(); },
    propose() { const b = $("propose"); if (b && !b.hidden) b.click(); },   // page renders the proposal its own way
    exportBtn() { const b = $("export"); return b && !b.hidden && !b.disabled ? (b.click(), true) : false; },
    section(...ids) { for (const id of ids) if ($(id)) return scrollTo(id); },
  };

  /* ---------- the conversation ---------- */
  async function start() {
    if (started) return; started = true; hint("");
    await speak("Hi there! I'm Edge-Case Miner, and I'd love to help you build a dataset. Just tell me, in one sentence, the moment you need training data for, " +
      "something like: a forklift passing close to a person. I'll search the video archive, check every candidate with Cosmos Reason, label and score it, " +
      "and then walk you through what I found and what's still missing. So, what kind of dataset are you looking for?");
    const t = await waitForAnswer(false);
    newRequest(t);
  }
  async function newRequest(text) {
    if (busy || !text) return;
    if (/^(export|download)/i.test(text)) return exportRun();
    busy = true;
    try {
      page.setRequest(text);
      let cams = [];
      try { cams = (await (await api("/api/cameras")).json()).cameras.map(c => typeof c === "string" ? { id: c, name: c } : c); } catch (e) {}
      const groups = [];
      if (cams.length) {
        const names = cams.map(c => c.name || c.id);
        await speak("Great choice! I heard: " + text + ". Which camera groups would you like me to search? All of them, or some of " + names.slice(0, 6).join(", ") + "?");
        const g = (await waitForAnswer(false) || "").toLowerCase();
        if (g && !/^(all|every|any|no|none|whatever)/.test(g.trim()))
          for (const c of cams) { const words = ((c.name || "") + " " + c.id).toLowerCase().split(/[^a-z0-9]+/).filter(w => w.length > 2);
            if (words.some(w => g.includes(w))) groups.push(c.id); }
        page.setGroups(groups);
      }
      await speak("Perfect. I'll search " + (groups.length ? groups.map(id => (cams.find(c => c.id === id) || {}).name || id).join(" and ") : "every camera group") +
        " for: " + text + ". Shall I go ahead?");
      const a = await waitForAnswer(true);
      if (!isYes(a)) { await speak("No problem at all. Just tell me the request again, or type it, and we'll take it from there."); busy = false; const t = await waitForAnswer(false); return newRequest(t); }
      runId = (await (await post("/api/mine", { request: text, groups })).json()).id;
      page.attach(runId, text, groups);
      speak("On it! I'm searching the archive now, and then I'll check each candidate with Cosmos Reason. This takes about a minute, you can watch the steps on the page.");
      let run;
      while (true) {
        await sleep(1500);
        run = await (await api("/api/runs/" + runId)).json();
        mode("thinking");
        if (run.error) { await speak("Something failed: " + run.error); busy = false; return; }
        if (run.stage === "done") break;
      }
      await present(run);
    } catch (e) { await speak("Hmm, that did not work: " + e.message); }
    busy = false;
  }

  // Runs started from the page's own box are narrated too: wrap the page's startRun once it exists.
  async function follow(id) {
    if (busy || !started) return;
    busy = true; runId = id;
    try {
      let run;
      while (true) {
        await sleep(1500); run = await (await api("/api/runs/" + runId)).json(); mode("thinking");
        if (run.error || run.stage === "done") break;
      }
      if (!run.error) await present(run);
    } catch (e) {}
    busy = false;
  }
  function hookPage() {
    try {
      if (typeof startRun === "function" && !startRun.__eca) {
        const orig = startRun;
        startRun = function (request, groups) {
          const r = orig.apply(this, arguments);
          const pageRunId = (0, eval)("typeof runId !== 'undefined' ? runId : null");   // the page's own run id
          if (!busy && pageRunId) setTimeout(() => { try { follow(pageRunId); } catch (e) {} }, 0);
          return r;
        };
        startRun.__eca = true;
      }
    } catch (e) {}
  }
  hookPage(); document.addEventListener("DOMContentLoaded", hookPage);

  const rank = c => (["contact", "under_2m"].includes((c.labels || {}).distance_class) ? 2 : (c.labels || {}).distance_class === "2_to_5m" ? 1 : 0) * 10 + ((c.quality && c.quality.score) || 0);
  async function present(run) {
    const confirmed = run.candidates.filter(c => c.status === "confirmed"), n = run.counts;
    if (confirmed.length) {
      const c = [...confirmed].sort((a, b) => rank(b) - rank(a) || b.similarity - a.similarity)[0], L = c.labels || {};
      page.filter("confirmed"); page.section("confirmed", "grid");
      await sleep(600);
      const hero = $("eca-hero"); hero.replaceChildren();
      if (!page.showClip(c)) {
        hero.classList.add("on");
        const v = el("video"); v.src = "/api/clip/" + c.clip_id; v.controls = true; v.muted = true; v.autoplay = true; v.loop = true; hero.append(v);
      }
      await speak("Here's a lovely example from " + (c.camera || c.camera_id) + ". Cosmos Reason watched it and said: " + sentences(c.reasoning, 2) +
        " Labels: " + [L.action, L.distance_class && "distance " + L.distance_class.replace(/_/g, " "), L.lighting].filter(Boolean).join(", ") + ".");
      hero.classList.remove("on"); page.hideClip();
      page.filter("rejected"); page.section("rejected", "grid");
      await speak("Altogether I confirmed " + n.confirmed + " clips and rejected " + n.rejected + (n.unverified ? ", with " + n.unverified + " unverified" : "") +
        ". Rejected means Cosmos said the event isn't actually there, or the quality was too low. So every clip you see was really checked, not just matched.");
    } else {
      await speak("I checked " + n.verified + " candidates and couldn't confirm a single one. That's actually a useful finding in itself, let me show you what's missing.");
    }
    const rep = run.report || {};
    page.filter("confirmed"); page.section("coverage", "heat", "gaps");
    await speak("Now, what's missing. " + sentences(rep.gap_report, 2) + (rep.collection_plan && rep.collection_plan.length ? " If I were you, I'd " + rep.collection_plan.slice(0, 2).join(", and ").toLowerCase() : ""));
    await improve(run);
  }
  async function improve(run) {
    await speak("Want to find more? Here's my favourite trick: I can rewrite the ingestion prompt. The prompt decides what the captioner writes, and the captions decide what search can find. " +
      "I keep a general description first, then ask for distances, lighting and the event itself. Then I re-index the videos that came closest and search again.");
    let proposal;
    try { proposal = await (await post("/api/runs/" + runId + "/loop/propose")).json(); } catch (e) { await speak("I could not prepare a proposal: " + e.message); return offerExport(run); }
    if ($("propose") && typeof $("propose").onclick === "function") page.propose();     // redesigned page renders it
    else { const box = $("prompt"); if (box && "value" in box) box.value = proposal.prompt; const prop = $("proposal"); if (prop) prop.hidden = false; }
    page.section("improve", "loop-note");
    if (!(run.needs_loop && proposal.chunks.length)) {
      await speak("Here's the prompt I'd use. You've already reached the target of " + run.target + " confirmed clips, so no re-indexing is needed this time. Nice!");
      return offerExport(run);
    }
    await speak("Here's the prompt. Shall I re-index " + proposal.chunks.length + " video" + (proposal.chunks.length > 1 ? "s" : "") + " with it and search again? It takes about a minute and a half.");
    const a = await waitForAnswer(true);
    if (!isYes(a)) { await speak("Sure, we'll leave the index as it is."); return offerExport(run); }
    await post("/api/runs/" + runId + "/loop/approve", { prompt: proposal.prompt, chunks: proposal.chunks });
    const prop = $("proposal"); if (prop) prop.hidden = true;
    page.attach(runId);   // page: poll again
    speak("Wonderful. Re-indexing with the new prompt, then I'll search and verify again.");
    let r2;
    while (true) {
      await sleep(2000);
      r2 = await (await api("/api/runs/" + runId)).json();
      const it = (r2.loop && r2.loop.iterations || []).slice(-1)[0] || {};
      mode("thinking");
      if (r2.stage === "done") break;
    }
    const it = r2.loop.iterations.slice(-1)[0];
    await speak("All done in " + Math.round(it.seconds || 0) + " seconds! Confirmed clips before: " + it.before.confirmed + ". After: " + (it.after ? it.after.confirmed : "unknown") + ".");
    if (it.after && it.after.confirmed > it.before.confirmed) await present(r2); else await offerExport(r2);
    if (focused) focused.classList.remove("eca-focus");
  }
  async function offerExport(run) {
    if (!run.counts || !run.counts.confirmed) { await speak("There's nothing to export yet. Tell me another request whenever you like!"); return; }
    await speak("Would you like me to export the dataset? You'd get the manifest, labels, clips and a dataset card.");
    const a = await waitForAnswer(true);
    if (isYes(a)) await exportRun(); else await speak("Okay, it stays right here on screen. Tell me another request whenever you like!");
  }
  async function exportRun() {
    if (!runId) return speak("There is no finished run to export yet.");
    try {
      if (!page.exportBtn()) {
        const r = await post("/api/runs/" + runId + "/export");
        const a = el("a"); a.href = URL.createObjectURL(await r.blob()); a.download = "edgecase-" + runId + ".zip"; a.click();
      }
      await speak("Exported! Tell me another request whenever you like.");
    } catch (e) { await speak("Export failed: " + e.message); }
  }

  if (window.speechSynthesis) speechSynthesis.getVoices();
  window.edgecaseAssistant = { speak, newRequest, exportRun };
})();
