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

  /* ---------- panel ---------- */
  const css = `
  #eca { position:fixed; right:20px; bottom:20px; width:min(520px, calc(100vw - 40px)); z-index:1000; background:#0e0f12; color:#f4f4f6;
         border:1px solid #2a2c33; border-radius:16px; box-shadow:0 12px 40px rgba(0,0,0,.45); font:17px/1.4 system-ui, sans-serif; }
  #eca.min { width:auto; } #eca.min > :not(#eca-head) { display:none; }
  #eca-head { display:flex; align-items:center; gap:10px; padding:10px 14px; border-bottom:1px solid #2a2c33; cursor:pointer; }
  #eca-head b { flex:1; font-weight:600; } #eca-head span { color:#9a9ca6; font-size:14px; }
  #eca-say { padding:14px 16px; font-size:20px; line-height:1.4; min-height:4.2em; max-height:34vh; overflow:auto; }
  #eca-say.speaking::after { content:" ▍"; color:#60a5fa; animation:eca-blink 1s infinite; } @keyframes eca-blink { 50% { opacity:0; } }
  #eca-hero { display:none; padding:0 16px 10px; } #eca-hero video { width:100%; border-radius:10px; background:#000; }
  #eca-hero.on { display:block; }
  #eca-ctl { display:flex; gap:10px; align-items:center; padding:10px 14px 14px; flex-wrap:wrap; }
  #eca button { font:inherit; font-weight:600; border:1px solid #f4f4f6; border-radius:999px; background:#f4f4f6; color:#0e0f12; padding:8px 16px; cursor:pointer; }
  #eca button.sec { background:transparent; color:#f4f4f6; } #eca button:disabled { opacity:.4; cursor:default; }
  #eca-mic { width:52px; height:52px; padding:0 !important; border-radius:50% !important; display:grid; place-items:center; }
  #eca-mic.on { background:#f87171 !important; border-color:#f87171 !important; color:#fff; animation:eca-pulse 1.2s infinite; } @keyframes eca-pulse { 50% { transform:scale(1.08); } }
  #eca-mic.off { opacity:.35; }
  #eca-typed { flex:1 1 160px; font:inherit; padding:8px 12px; border:1px solid #2a2c33; border-radius:10px; background:#15171c; color:#f4f4f6; min-width:120px; }
  #eca-note { width:100%; color:#9a9ca6; font-size:14px; }
  #eca-yn { display:none; gap:10px; } #eca-yn.on { display:flex; }
  .eca-focus { outline:3px solid #60a5fa; outline-offset:8px; border-radius:8px; transition:outline-color .3s; }
  .eca-filled { box-shadow:0 0 0 3px #60a5fa !important; transition:box-shadow .3s; }`;
  document.head.append(Object.assign(el("style"), { textContent: css }));

  const panel = el("div"); panel.id = "eca";
  panel.innerHTML = `
    <div id="eca-head"><b>Voice assistant</b><span id="eca-state">off</span><span>▾</span></div>
    <div id="eca-say">I can run this dashboard for you: say what dataset you need and I will search, verify, show you the clips, the gaps, and how to improve the indexing. You can type or click at any time.</div>
    <div id="eca-hero"></div>
    <div id="eca-ctl">
      <button id="eca-start">Start</button>
      <button id="eca-mic" class="sec" hidden aria-label="Microphone"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8"/></svg></button>
      <input id="eca-typed" type="text" placeholder="…or type here" maxlength="300" hidden>
      <div id="eca-yn"><button id="eca-yes">Yes</button><button id="eca-no" class="sec">No</button></div>
      <div id="eca-note"></div>
    </div>`;
  document.body.append(panel);
  $("eca-head").addEventListener("click", () => panel.classList.toggle("min"));
  const note = t => { $("eca-note").textContent = t; };
  const state = t => { $("eca-state").textContent = t; };

  /* ---------- speech out ---------- */
  let speaking = null;
  function speak(text) {
    $("eca-say").textContent = text; $("eca-say").classList.add("speaking");
    return (speaking = new Promise(resolve => {
      const done = () => { $("eca-say").classList.remove("speaking"); resolve(); };
      const synth = window.speechSynthesis;
      if (!synth) return setTimeout(done, Math.max(1500, text.split(/\s+/).length * 330));
      synth.cancel();
      const parts = text.match(/[^.!?]+[.!?]*/g) || [text];
      let i = 0; const guard = setTimeout(done, Math.max(2000, text.split(/\s+/).length * 450));
      const next = () => {
        if (i >= parts.length) { clearTimeout(guard); return done(); }
        const u = new SpeechSynthesisUtterance(parts[i++].trim());
        const vs = synth.getVoices();
        u.voice = vs.find(v => /^en/i.test(v.lang) && /Google|Samantha|Daniel|Natural/i.test(v.name)) || vs.find(v => /^en/i.test(v.lang)) || null;
        u.rate = 1.02; u.onend = next; u.onerror = next; synth.speak(u);
      };
      next();
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
    $("eca-mic").classList.add("on"); state("listening"); note("Listening… I stop when you pause. Or type below.");
    let blob;
    try { blob = await record(); micOk = true; }
    catch (e) { micOk = false; $("eca-mic").classList.add("off"); note("No microphone here. Type your answer below."); $("eca-mic").classList.remove("on"); state("ready"); return null; }
    $("eca-mic").classList.remove("on"); state("transcribing"); note("Transcribing with Canary-1B…");
    try {
      const fd = new FormData(); fd.append("file", blob, "speech.wav");
      const r = await fetch("/api/transcribe", { method: "POST", body: fd });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const { text } = await r.json(); note(""); state("ready"); return text;
    } catch (e) { note("Could not transcribe (" + e.message + "). Type instead."); state("ready"); return null; }
  }

  /* ---------- getting an answer: voice, typed box, or yes/no buttons ---------- */
  let pending = null;   // {resolve}
  function waitForAnswer(yesNo) {
    $("eca-yn").classList.toggle("on", !!yesNo);
    return new Promise(resolve => {
      pending = { resolve: v => { pending = null; $("eca-yn").classList.remove("on"); resolve(v); } };
      listenOnce().then(t => { if (t && pending) pending.resolve(t); });
    });
  }
  $("eca-typed").addEventListener("keydown", e => { if (e.key === "Enter" && $("eca-typed").value.trim()) { const v = $("eca-typed").value.trim(); $("eca-typed").value = ""; if (pending) pending.resolve(v); else newRequest(v); } });
  $("eca-yes").addEventListener("click", () => pending && pending.resolve("yes"));
  $("eca-no").addEventListener("click", () => pending && pending.resolve("no"));
  $("eca-mic").addEventListener("click", async () => {
    if (rec) return rec.stop();
    if (pending) return;                                   // already listening for the pending question
    const t = await listenOnce(); if (t) newRequest(t);
  });
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
  let runId = null, busy = false;
  async function start() {
    $("eca-start").hidden = true; $("eca-mic").hidden = false; $("eca-typed").hidden = false; state("ready");
    await speak("Hi, I am Edge-Case Miner. Tell me in one sentence the event you need training data for, like: forklift passing close to a person. " +
      "I will search the indexed video, check every candidate with Cosmos Reason, label and score it, and show you the clips, the gaps, and how to improve the indexing. " +
      "What kind of dataset are you looking for?");
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
        await speak("I heard: " + text + ". Which camera groups should I search: all of them, or some of " + names.slice(0, 6).join(", ") + "?");
        const g = (await waitForAnswer(false) || "").toLowerCase();
        if (g && !/^(all|every|any|no|none|whatever)/.test(g.trim()))
          for (const c of cams) { const words = ((c.name || "") + " " + c.id).toLowerCase().split(/[^a-z0-9]+/).filter(w => w.length > 2);
            if (words.some(w => g.includes(w))) groups.push(c.id); }
        page.setGroups(groups);
      }
      await speak("I will search " + (groups.length ? groups.map(id => (cams.find(c => c.id === id) || {}).name || id).join(" and ") : "every camera group") +
        " for: " + text + ". Shall I start?");
      const a = await waitForAnswer(true);
      if (!isYes(a)) { await speak("Okay. Tell me the request again, or type it."); busy = false; const t = await waitForAnswer(false); return newRequest(t); }
      runId = (await (await post("/api/mine", { request: text, groups })).json()).id;
      page.attach(runId, text, groups);
      speak("Searching every camera group, then verifying each candidate with Cosmos Reason. About a minute.");
      let run;
      while (true) {
        await sleep(1500);
        run = await (await api("/api/runs/" + runId)).json();
        const n = run.counts || {}; state(run.stage + " · " + (n.verified || 0) + " verified, " + (n.confirmed || 0) + " confirmed");
        if (run.error) { await speak("Something failed: " + run.error); busy = false; return; }
        if (run.stage === "done") break;
      }
      await present(run);
    } catch (e) { note(e.message); await speak("That did not work: " + e.message); }
    busy = false;
  }

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
      await speak("Here is one example from " + (c.camera || c.camera_id) + ". Cosmos Reason watched it and said: " + sentences(c.reasoning, 2) +
        " Labels: " + [L.action, L.distance_class && "distance " + L.distance_class.replace(/_/g, " "), L.lighting].filter(Boolean).join(", ") + ".");
      hero.classList.remove("on"); page.hideClip();
      page.filter("rejected"); page.section("rejected", "grid");
      await speak("Altogether I confirmed " + n.confirmed + " clips and rejected " + n.rejected + (n.unverified ? ", with " + n.unverified + " unverified" : "") +
        ". Rejected means Cosmos said the event is not there, or the quality was too low. Every clip was checked, not just matched.");
    } else {
      await speak("I checked " + n.verified + " candidates and could not confirm one. That is a finding in itself.");
    }
    const rep = run.report || {};
    page.filter("confirmed"); page.section("coverage", "heat", "gaps");
    await speak("What is missing: " + sentences(rep.gap_report, 2) + (rep.collection_plan && rep.collection_plan.length ? " Next I would " + rep.collection_plan.slice(0, 2).join(", and ").toLowerCase() : ""));
    await improve(run);
  }
  async function improve(run) {
    await speak("To find more, I can rewrite the ingestion prompt: the prompt decides what the captioner writes, and the captions decide what search can find. " +
      "General description first, then distances, lighting and the event itself. Then I re-index the chunks that came closest and search again.");
    let proposal;
    try { proposal = await (await post("/api/runs/" + runId + "/loop/propose")).json(); } catch (e) { await speak("I could not prepare a proposal: " + e.message); return offerExport(run); }
    if ($("propose") && typeof $("propose").onclick === "function") page.propose();     // redesigned page renders it
    else { const box = $("prompt"); if (box && "value" in box) box.value = proposal.prompt; const prop = $("proposal"); if (prop) prop.hidden = false; }
    page.section("improve", "loop-note");
    $("eca-say").textContent = proposal.prompt;
    if (!(run.needs_loop && proposal.chunks.length)) {
      await speak("Here is the prompt I would use. The target of " + run.target + " confirmed clips is already reached, so no re-ingest is needed this time.");
      return offerExport(run);
    }
    await speak("Here is the prompt. Shall I re-ingest " + proposal.chunks.length + " chunk" + (proposal.chunks.length > 1 ? "s" : "") + " with it and search again? It takes about a minute and a half.");
    const a = await waitForAnswer(true);
    if (!isYes(a)) { await speak("Okay, not re-ingesting."); return offerExport(run); }
    await post("/api/runs/" + runId + "/loop/approve", { prompt: proposal.prompt, chunks: proposal.chunks });
    const prop = $("proposal"); if (prop) prop.hidden = true;
    page.attach(runId);   // page: poll again
    speak("Re-ingesting with the new prompt, then searching and verifying again.");
    let r2;
    while (true) {
      await sleep(2000);
      r2 = await (await api("/api/runs/" + runId)).json();
      const it = (r2.loop && r2.loop.iterations || []).slice(-1)[0] || {};
      state((it.status || "") + " " + (it.jobs || []).map(j => j.progress).join("; "));
      if (r2.stage === "done") break;
    }
    const it = r2.loop.iterations.slice(-1)[0];
    await speak("Done in " + Math.round(it.seconds || 0) + " seconds. Confirmed before: " + it.before.confirmed + ". After: " + (it.after ? it.after.confirmed : "unknown") + ".");
    if (it.after && it.after.confirmed > it.before.confirmed) await present(r2); else await offerExport(r2);
    if (focused) focused.classList.remove("eca-focus");
  }
  async function offerExport(run) {
    if (!run.counts || !run.counts.confirmed) { await speak("Nothing to export. Tell me another request whenever you like."); return; }
    await speak("Shall I export the dataset: manifest, labels, clips and a dataset card?");
    const a = await waitForAnswer(true);
    if (isYes(a)) await exportRun(); else await speak("Okay. It stays on screen. Tell me another request whenever you like.");
  }
  async function exportRun() {
    if (!runId) return speak("There is no finished run to export yet.");
    try {
      if (!page.exportBtn()) {
        const r = await post("/api/runs/" + runId + "/export");
        const a = el("a"); a.href = URL.createObjectURL(await r.blob()); a.download = "edgecase-" + runId + ".zip"; a.click();
      }
      await speak("Exported. Tell me another request whenever you like.");
    } catch (e) { await speak("Export failed: " + e.message); }
  }

  $("eca-start").addEventListener("click", start);
  if (window.speechSynthesis) speechSynthesis.getVoices();
  window.edgecaseAssistant = { speak, newRequest, exportRun };
})();
