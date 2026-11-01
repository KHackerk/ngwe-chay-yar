#!/usr/bin/env node
/* Visual QA harness for the Ngwe Chay Yar Add screen.
 *
 * Boots the real Flask app on a throwaway SQLite DB, registers a throwaway
 * user in a headless Chromium (CDP, no external deps), sets lang=Myanmar,
 * opens the Add page and — for each viewport/theme config — dumps real
 * geometry (rects, scroll vs client sizes = clipping detectors) as JSON and
 * saves a screenshot PNG next to this script.
 *
 * Usage: node qa.js [tag]     (tag prefixes screenshot names, default "add")
 */
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');

const APP_DIR = path.dirname(path.dirname(__dirname));
const OUT_DIR = __dirname;
const PORT = 5421;
const BASE = `http://127.0.0.1:${PORT}`;
const DB = `/tmp/ncy_qa_${Date.now()}.db`;
const PY = process.env.QA_PY || path.join(APP_DIR, 'venv', 'bin', 'python');
const CHROME = '/opt/meta-chromium/chrome';
const CDP_PORT = 9333;
const TAG = process.argv[2] || 'add';

const sleep = ms => new Promise(r => setTimeout(r, ms));

function httpJSON(port, p) {
  return new Promise((resolve, reject) => {
    http.get({ host: '127.0.0.1', port, path: p }, res => {
      let d = '';
      res.on('data', c => (d += c));
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}

async function waitFor(fn, timeout = 20000, step = 250) {
  const t0 = Date.now();
  for (;;) {
    try { const v = await fn(); if (v) return v; } catch (e) { /* retry */ }
    if (Date.now() - t0 > timeout) throw new Error('waitFor timeout');
    await sleep(step);
  }
}

// ---------------- CDP client ----------------
let ws, msgId = 0;
const pending = new Map();
const events = [];
function cdp(method, params = {}, sessionId) {
  return new Promise((resolve, reject) => {
    const id = ++msgId;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params, sessionId }));
  });
}
async function evaluate(sessionId, expr) {
  const r = await cdp('Runtime.evaluate', {
    expression: expr, returnByValue: true, awaitPromise: true,
  }, sessionId);
  if (r.exceptionDetails) {
    throw new Error('evaluate failed: ' +
      JSON.stringify(r.exceptionDetails).slice(0, 400));
  }
  return r.result ? r.result.value : undefined;
}

// ---------------- measurement ----------------
const MEASURE = `(() => {
  const R = (el) => { if (!el) return null; const b = el.getBoundingClientRect();
    return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width),
             h: Math.round(b.height), top: Math.round(b.top),
             bottom: Math.round(b.bottom), left: Math.round(b.left),
             right: Math.round(b.right) }; };
  const clip = (el) => el ? {
    sh: el.scrollHeight, ch: el.clientHeight, sw: el.scrollWidth,
    cw: el.clientWidth,
    clipY: el.scrollHeight > el.clientHeight + 1,
    clipX: el.scrollWidth > el.clientWidth + 1 } : null;
  const q = s => document.querySelector(s);
  const qa = s => Array.from(document.querySelectorAll(s));
  const out = {};
  out.viewport = { w: innerWidth, h: innerHeight,
    docScrollH: document.documentElement.scrollHeight,
    bodyScrollH: document.body.scrollHeight };
  out.theme = document.documentElement.getAttribute('data-theme');
  out.bodyClass = document.body.className;
  out.mobiletop = R(q('.mobiletop'));
  out.langBtn = R(q('#langbtn-m'));
  out.langCur = Object.assign(R(q('#lang-cur-m')), clip(q('#lang-cur-m')));
  out.pageAdd = R(q('#page-add'));
  out.sechead = R(q('#page-add .sechead'));
  out.addTitle = Object.assign(R(q('#add-title')), clip(q('#add-title')));
  out.addcard = Object.assign(R(q('#addcard')), clip(q('#addcard')));
  out.addLeft = Object.assign(R(q('.add-left')), clip(q('.add-left')));
  out.addRight = R(q('.add-right'));
  out.toggle = R(q('#addcard .toggle'));
  out.amtrow = R(q('.amtrow'));
  out.amount = R(q('#add-amount'));
  out.amountsub = Object.assign(R(q('.amountsub')), clip(q('.amountsub')));
  out.catLbl = R(q('.addcat-lbl'));
  out.catgrid = R(q('#add-catgrid'));
  out.catcells = qa('#add-catgrid .catcell').map(c => {
    const lab = c.querySelector('span:last-child');
    return { rect: R(c), label: lab ? lab.textContent : '',
             labelClip: clip(lab), cellClip: clip(c) };
  });
  out.walletwrap = R(q('.walletwrap'));
  out.walletHint = Object.assign(R(q('#add-wallet-hint')), clip(q('#add-wallet-hint')));
  out.wallets = R(q('#add-wallets'));
  out.daterow = R(q('.daterow'));
  out.dateField = R(q('.daterow .date-f'));
  out.noteField = R(q('.daterow .note-f'));
  out.dateInput = Object.assign(R(q('#add-date')), clip(q('#add-date')));
  out.noteInput = Object.assign(R(q('#add-note')), clip(q('#add-note')));
  out.dateLabel = Object.assign(R(q('.daterow .date-f label')), clip(q('.daterow .date-f label')));
  out.noteLabel = Object.assign(R(q('.daterow .note-f label')), clip(q('.daterow .note-f label')));
  out.keypad = R(q('#keypad'));
  const key = q('#keypad button');
  out.key = R(key);
  out.save = R(q('#add-save'));
  out.bottomnav = R(q('.bottomnav'));
  out.navLabels = qa('.bn-in > button[data-nav] span').map(s =>
    Object.assign({ text: s.textContent }, clip(s)));
  out.saveVsNav = (out.save && out.bottomnav)
    ? { saveBottom: out.save.bottom, navTop: out.bottomnav.top,
        visible: out.save.bottom <= out.bottomnav.top } : null;
  out.dateNoteGap = (out.dateInput && out.noteField)
    ? { dateRight: out.dateInput.right, noteLeft: out.noteField.left,
        overlap: out.dateInput.right > out.noteField.left } : null;
  return out;
})()`;

async function main() {
  const server = spawn(PY, ['app.py'], {
    cwd: APP_DIR,
    env: { ...process.env, DATABASE_URL: `sqlite:///${DB}`,
           PORT: String(PORT), SECRET_KEY: 'qa-secret' },
    stdio: 'ignore',
  });
  // The Hatch Chromium build refuses to start without a WebGL domain
  // policy (normally supplied by browser-daemon). For this local QA
  // browser (throwaway profile, local app only) use the all-origins
  // contract file + its sha256.
  const WG = '/tmp/ncy-webgl-policy.txt';
  fs.writeFileSync(WG, 'hatch-webgl-all-origins-v1\n');
  const wgSha = require('crypto').createHash('sha256')
    .update(fs.readFileSync(WG)).digest('hex');
  const chrome = spawn(CHROME, [
    '--headless=new', '--no-sandbox', '--disable-gpu',
    `--remote-debugging-port=${CDP_PORT}`,
    `--hatch-webgl-allowed-domains-file=${WG}`,
    `--hatch-webgl-allowed-domains-sha256=${wgSha}`,
    '--user-data-dir=/tmp/ncy-chrome-qa', '--window-size=420,900',
  ], { stdio: 'ignore' });
  try {
    await waitFor(async () => {
      const r = await fetch(`${BASE}/login`).catch(() => null);
      return r && r.status === 200;
    });
    const ver = await waitFor(() => httpJSON(CDP_PORT, '/json/version'));
    ws = new WebSocket(ver.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) {
        const p = pending.get(m.id); pending.delete(m.id);
        if (m.error) p.reject(new Error(JSON.stringify(m.error)));
        else p.resolve(m.result);
      } else if (m.method) events.push(m);
    };
    const { targetId } = await cdp('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await cdp('Target.attachToTarget',
      { targetId, flatten: true });
    await cdp('Page.enable', {}, sessionId);
    await cdp('Runtime.enable', {}, sessionId);

    async function setViewport(w, h) {
      await cdp('Emulation.setDeviceMetricsOverride', {
        width: w, height: h, deviceScaleFactor: 2, mobile: true,
        screenWidth: w, screenHeight: h,
      }, sessionId);
    }
    async function nav(url) {
      await cdp('Page.navigate', { url }, sessionId);
      await waitFor(async () =>
        (await evaluate(sessionId, 'document.readyState')) === 'complete',
        15000, 150);
      await sleep(400);
    }

    await setViewport(390, 844);
    await nav(`${BASE}/register`);
    await evaluate(sessionId, `(() => {
      const set = (n, v) => { const i = document.querySelector('input[name="' + n + '"]');
        if (i) { i.value = v; i.dispatchEvent(new Event('input', {bubbles:true})); } };
      set('name', 'QA User'); set('email', 'qa@test.local');
      set('password', 'qa-pass-123'); set('confirm_password', 'qa-pass-123');
      const t = document.querySelector('input[name="terms"]'); if (t) t.checked = true;
      document.querySelector('form').submit(); return true; })()`);
    await waitFor(async () =>
      await evaluate(sessionId,
        "typeof go === 'function' && !!document.querySelector('#page-home')"),
      20000, 300);
    await sleep(800);

    async function applyPrefs(lang, theme) {
      await evaluate(sessionId, `fetch('/api/settings', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({lang: ${JSON.stringify(lang)},
                              theme: ${JSON.stringify(theme)}})})
        .then(() => { try { localStorage.setItem('ncy_theme',
          ${JSON.stringify(theme)}); } catch(e) {} return true; })`);
      await nav(`${BASE}/`);
      await waitFor(async () =>
        await evaluate(sessionId, "typeof go === 'function'"), 20000, 300);
      await sleep(600);
    }
    async function openAdd(kind) {
      await evaluate(sessionId,
        `go('add'); setKind(${JSON.stringify(kind)}); true`);
      await sleep(500);
    }
    async function shot(name) {
      const r = await cdp('Page.captureScreenshot',
        { format: 'png', captureBeyondViewport: false }, sessionId);
      const p = path.join(OUT_DIR, name);
      fs.writeFileSync(p, Buffer.from(r.data, 'base64'));
      return p;
    }

    const report = {};
    const configs = [
      { name: 'night', theme: 'dark' },
      { name: 'day', theme: 'light' },
    ];
    for (const cfg of configs) {
      await applyPrefs('my', cfg.theme);
      for (const vp of [[390, 844], [360, 740]]) {
        await setViewport(vp[0], vp[1]);
        await sleep(300);
        await openAdd('expense');
        const key = `${cfg.name}-${vp[0]}x${vp[1]}`;
        report[key] = await evaluate(sessionId, MEASURE);
        report[key].shot = await shot(
          `${TAG}-my-${cfg.name}-${vp[0]}x${vp[1]}.png`);
      }
    }
    // one wallet-linked state (chips instead of the hint), night 390x844
    await applyPrefs('my', 'dark');
    await evaluate(sessionId, `fetch('/api/accounts', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name:'KBZ Pay', type:'ewallet', brand:'kbzpay',
                            balance: 1500000, note:''})}).then(r => r.json())`);
    await nav(`${BASE}/`);
    await waitFor(async () =>
      await evaluate(sessionId, "typeof go === 'function'"), 20000, 300);
    await sleep(600);
    await setViewport(390, 844);
    await openAdd('expense');
    report['night-390x844-wallet'] = await evaluate(sessionId, MEASURE);
    report['night-390x844-wallet'].shot = await shot(
      `${TAG}-my-night-390x844-wallet.png`);
    // income mode (green CTA) at the main size
    await openAdd('income');
    report['night-390x844-income'] = await evaluate(sessionId, MEASURE);
    report['night-390x844-income'].shot = await shot(
      `${TAG}-my-night-390x844-income.png`);

    console.log(JSON.stringify(report, null, 1));
  } finally {
    try { chrome.kill('SIGKILL'); } catch (e) {}
    try { server.kill('SIGKILL'); } catch (e) {}
    try { fs.unlinkSync(DB); } catch (e) {}
  }
}

main().catch(e => { console.error('QA FAILED:', e.message); process.exit(1); });
