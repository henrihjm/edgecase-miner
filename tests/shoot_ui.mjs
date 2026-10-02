// Screenshots of the UI against the preview server, via headless Chrome + CDP. No dependencies.
//   .venv/bin/python tests/preview_ui.py warehouse &
//   node tests/shoot_ui.mjs docs/images/edgecase
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

const OUT = process.argv[2] || "docs/images/edgecase";
const URL = "http://127.0.0.1:8765/";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const W = 1440, H = 900;
mkdirSync(OUT, { recursive: true });

const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=9333", `--window-size=${W},${H}`, "--force-device-scale-factor=2",
  "--hide-scrollbars", "--no-first-run", "--user-data-dir=/tmp/edgecase-shoot", "about:blank"], { stdio: ["ignore", "ignore", "inherit"] });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let target;
for (let i = 0; i < 40 && !target; i++) { await sleep(500); try { target = await (await fetch("http://127.0.0.1:9333/json/new?about:blank", { method: "PUT" })).json(); } catch (e) {} }
if (!target) { chrome.kill(); throw new Error("Chrome did not open the debugging port"); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.onopen = r);
let id = 0; const pending = new Map();
ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result || m.error); pending.delete(m.id); } };
const send = (method, params = {}) => new Promise(r => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = expr => send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }).then(r => r.result?.value);
const shot = async name => { const r = await send("Page.captureScreenshot", { format: "png" }); writeFileSync(`${OUT}/${name}.png`, Buffer.from(r.data, "base64")); console.log("saved", name); };

await send("Page.enable");
await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 2, mobile: false });
await send("Page.navigate", { url: URL }); await sleep(1800);
await evaluate("document.activeElement && document.activeElement.blur(); 1");
await shot("landing");

await evaluate(`document.getElementById('request').value = 'Forklift passing close to a person'; document.getElementById('form').requestSubmit(); 1`);
await sleep(7500); await shot("verifying");
await sleep(10000); await evaluate("scrollTo(0,0); 1"); await sleep(600); await shot("results");

await evaluate(`document.querySelector('#grid .tile').click(); 1`); await sleep(1800);
await evaluate(`const v = document.getElementById('d-video'); v.pause(); v.currentTime = 1.4; 1`); await sleep(600);
await shot("verification");
await evaluate(`document.getElementById('sheet').close(); 1`); await sleep(400);

await evaluate(`document.getElementById('propose').click(); 1`); await sleep(2600);
await evaluate("scrollTo(0,0); 1"); await sleep(300); await shot("proposal");

await evaluate(`document.getElementById('approve').click(); 1`); await sleep(3500);
await evaluate("scrollTo(0,0); 1"); await sleep(300); await shot("reindexing");
await sleep(7000); await evaluate("scrollTo(0,0); 1"); await sleep(600); await shot("improved");

ws.close(); chrome.kill();
