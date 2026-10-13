// Font Toolkit — background service worker
// 职责：字体请求捕获注册表、动态字型 API 全量抓取、进度推送、交付字节给 popup 打包
// 仅供字体学习研究用途；抓取行为须遵守各字体厂商服务条款与授权协议。

const FONT_URL_RE = /\.(woff2?|ttf|otf)(\?|$)/i;
const API_ENDPOINT = "https://go.justfont.com/jfont/api";
const MAX_CAPTURES = 800;

// ---------------------------------------------------------------------------
// 捕获注册表（webRequest 观测 + 内容脚本上报）
// ---------------------------------------------------------------------------

async function getCaptures() {
  const { ftk_captures = [] } = await chrome.storage.session.get("ftk_captures");
  return ftk_captures;
}

async function addCapture(entry) {
  const caps = await getCaptures();
  if (caps.some(c => c.url === entry.url)) return;
  caps.unshift(entry);
  if (caps.length > MAX_CAPTURES) caps.length = MAX_CAPTURES;
  await chrome.storage.session.set({ ftk_captures: caps });
}

chrome.webRequest.onCompleted.addListener(
  details => {
    if (details.type === "font" || FONT_URL_RE.test(details.url)) {
      addCapture({
        url: details.url,
        size: details.statusCode === 200 ? (details.responseHeaders || [])
          .find(h => h.name.toLowerCase() === "content-length")?.value || 0 : 0,
        mime: (details.responseHeaders || [])
          .find(h => h.name.toLowerCase() === "content-type")?.value || "",
        source: "network",
        pageUrl: details.initiator || details.documentLifecycle || "",
        time: Date.now(),
      });
    }
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

// 内容脚本（来自 inject.js 钩子）上报：API JSON 响应中的字体 URL、WebSocket base64 字体帧
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === "ftk-capture" && Array.isArray(msg.entries)) {
    msg.entries.forEach(e => addCapture({ source: "hook", time: Date.now(), ...e }));
    sendResponse({ ok: true });
  } else if (msg?.type === "ftk-get-captures") {
    getCaptures().then(c => sendResponse({ ok: true, captures: c }));
    return true;
  } else if (msg?.type === "ftk-clear-captures") {
    chrome.storage.session.set({ ftk_captures: [] }).then(() => sendResponse({ ok: true }));
    return true;
  }
});

// ---------------------------------------------------------------------------
// justfont 页面字体配置探测（站点 ID + 家族/字重）
// ---------------------------------------------------------------------------

async function detectJustfontConfig(pageUrl) {
  const origin = new URL(pageUrl).origin;
  const textParts = [];

  try {
    textParts.push(await (await fetch(pageUrl)).text());
  } catch (e) { /* 页面拉取失败则只探测 JS */ }

  const html = textParts[0] || "";
  const jsRefs = [...html.matchAll(/src="([^"]+\.js[^"]*)"/g)].map(m => m[1]).slice(0, 12);
  for (const ref of jsRefs) {
    try {
      const jsUrl = new URL(ref, pageUrl).href;
      textParts.push(await (await fetch(jsUrl)).text());
    } catch (e) { /* 忽略单个 JS 拉取失败 */ }
  }

  const all = textParts.join("\n");
  const siteIdMatch = all.match(/vC\((\d{3,})\)/);
  const families = [];
  const seen = new Set();
  for (const m of all.matchAll(/\{family:"([^"]+)",weight:\{([^}]*)\}/g)) {
    for (const w of m[2].matchAll(/(\d+):"(\w+)"/g)) {
      const key = `${m[1]}-${w[2]}`;
      if (!seen.has(key)) { seen.add(key); families.push({ family: m[1], weight: +w[1], suffix: w[2], fn: key }); }
    }
  }
  return { siteId: siteIdMatch ? siteIdMatch[1] : null, families, origin };
}

// ---------------------------------------------------------------------------
// 动态字型 API 全量抓取
// ---------------------------------------------------------------------------

let harvestAbort = false;
chrome.runtime.onMessage.addListener(msg => { if (msg?.type === "ftk-abort-harvest") harvestAbort = true; });

function chunk(s, n) {
  const out = [];
  for (let i = 0; i < s.length; i += n) out.push(s.slice(i, i + n));
  return out;
}

async function setProgress(p) {
  await chrome.storage.session.set({ ftk_progress: p });
  try { chrome.runtime.sendMessage({ type: "ftk-progress", progress: p }); } catch (e) { /* popup 未开 */ }
}

async function harvestJustfont({ pageUrl, siteId, family, chars, batchSize, format, autoDnR }) {
  harvestAbort = false;
  const origin = new URL(pageUrl).origin;
  const unique = [...new Set([...chars])].filter(c => !/\s/.test(c));
  const batches = chunk(unique, batchSize);

  // 为 API 请求设置与目标页面一致的 Referer/Origin（扩展发起的跨域请求默认带扩展源）
  let ruleId = null;
  if (autoDnR) {
    try {
      ruleId = 9001;
      await chrome.declarativeNetRequest.updateSessionRules({
        removeRuleIds: [ruleId],
        addRules: [{
          id: ruleId,
          priority: 1,
          action: {
            type: "modifyHeaders",
            requestHeaders: [
              { header: "Referer", operation: "set", value: pageUrl },
              { header: "Origin", operation: "set", value: origin },
            ],
          },
          condition: { urlFilter: "||go.justfont.com/jfont/api", resourceTypes: ["xmlhttprequest"] },
        }],
      });
    } catch (e) { ruleId = null; }
  }

  const files = [];
  try {
    for (let i = 0; i < batches.length; i++) {
      if (harvestAbort) break;
      await setProgress({ phase: "requesting", batch: i + 1, total: batches.length, files: files.length });

      const api = API_ENDPOINT + "?" + new URLSearchParams({ o: origin, p: String(siteId), t: format });
      const body = new URLSearchParams({ fn: family, fs: batches[i] });
      const resp = await fetch(api, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });
      const json = await resp.json();
      if (json.err || !json.fu || !json.fu.length) {
        throw new Error(`API 错误码 ${json.err || "无文件返回"}（第 ${i + 1} 批）`);
      }

      for (let j = 0; j < json.fu.length; j++) {
        await setProgress({ phase: "downloading", batch: i + 1, total: batches.length, files: files.length });
        const data = new Uint8Array(await (await fetch(json.fu[j])).arrayBuffer());
        files.push({ name: `${family}_batch${String(i + 1).padStart(3, "0")}.${format}`, data });
      }
    }
    await setProgress({ phase: "done", batch: batches.length, total: batches.length, files: files.length, aborted: harvestAbort });
  } catch (e) {
    await setProgress({ phase: "error", message: String(e.message || e), files: files.length });
  } finally {
    if (ruleId !== null) {
      try { await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: [ruleId] }); } catch (e) {}
    }
  }
  return files;
}

// ---------------------------------------------------------------------------
// 与 popup 的指令通道
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === "ftk-detect") {
    detectJustfontConfig(msg.pageUrl)
      .then(cfg => sendResponse({ ok: true, cfg }))
      .catch(e => sendResponse({ ok: false, error: String(e.message || e) }));
    return true;
  }
  if (msg?.type === "ftk-harvest") {
    harvestJustfont(msg).then(files => {
      // 字节以 base64 交付（service worker 无 createObjectURL）
      const b64files = files.map(f => ({ name: f.name, b64: bytesToB64(f.data) }));
      sendResponse({ ok: true, files: b64files });
    }).catch(e => sendResponse({ ok: false, error: String(e.message || e) }));
    return true; // async sendResponse
  }
  if (msg?.type === "ftk-fetch-captures-bytes") {
    // 把捕获的 URL 逐一拉取字节返回（用于打包）
    (async () => {
      const out = [];
      for (let i = 0; i < msg.items.length; i++) {
        await setProgress({ phase: "downloading", batch: i + 1, total: msg.items.length, files: out.length });
        try {
          const r = await fetch(msg.items[i].url);
          const buf = new Uint8Array(await r.arrayBuffer());
          out.push({ name: msg.items[i].filename || urlBasename(msg.items[i].url), b64: bytesToB64(buf) });
        } catch (e) { out.push({ name: `error_${i}`, b64: "", error: String(e.message || e) }); }
      }
      await setProgress({ phase: "done", total: msg.items.length, files: out.length });
      sendResponse({ ok: true, files: out });
    })();
    return true;
  }
});

function urlBasename(u) {
  try { return decodeURIComponent(new URL(u).pathname.split("/").pop()) || "font.bin"; }
  catch (e) { return "font.bin"; }
}

function bytesToB64(bytes) {
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}
