// Font Toolkit — MAIN world 注入脚本
// 钩住 fetch / XHR / WebSocket，捕获动态字型 API 响应与 base64 字体帧。
// 只读观测，不修改任何页面行为。

(function () {
  if (window.__ftkInjected) return;
  window.__ftkInjected = true;

  const FONT_URL_RE = /\.(woff2?|ttf|otf)(\?|$)/i;
  const post = payload => {
    try { window.postMessage({ source: "ftk-inject", payload }, "*"); } catch (e) { /* ignore */ }
  };

  function scanTextForFontUrls(text, baseUrl) {
    const found = [];
    try {
      // 形如 {"fn": [...], "fu": [...]} 的动态字型 API 响应
      const json = JSON.parse(text);
      if (json && Array.isArray(json.fu)) {
        for (const u of json.fu) found.push({ url: String(u), size: 0, mime: "font/api", origin: location.origin });
        return found;
      }
    } catch (e) { /* 非 JSON，走正则 */ }
    if (typeof text === "string" && text.length < 2_000_000) {
      for (const m of text.matchAll(/["'(]([^"')]+\.woff2?|[^"')]+\.ttf|[^"')]+\.otf)["')]/gi)) {
        try {
          found.push({ url: new URL(m[1], baseUrl || location.href).href, size: 0, mime: "font/css", origin: location.origin });
        } catch (e) { /* ignore */ }
      }
    }
    return found;
  }

  // --- fetch ---
  const origFetch = window.fetch;
  window.fetch = async function (...args) {
    const resp = await origFetch.apply(this, args);
    try {
      const url = typeof args[0] === "string" ? args[0] : args[0]?.url || "";
      const ct = resp.headers.get("content-type") || "";
      if (FONT_URL_RE.test(url)) {
        post({ entries: [{ url: new URL(url, location.href).href, size: +resp.headers.get("content-length") || 0, mime: ct, origin: location.origin }] });
      } else if (ct.includes("json") || ct.includes("text")) {
        const clone = resp.clone();
        clone.text().then(t => {
          const found = scanTextForFontUrls(t, url);
          if (found.length) post({ entries: found });
        }).catch(() => {});
      }
    } catch (e) { /* ignore */ }
    return resp;
  };

  // --- XMLHttpRequest ---
  const XHR = XMLHttpRequest.prototype;
  const origOpen = XHR.open;
  XHR.open = function (method, url, ...rest) {
    this.__ftkUrl = url;
    return origOpen.call(this, method, url, ...rest);
  };
  const origSend = XHR.send;
  XHR.send = function (...args) {
    this.addEventListener("load", () => {
      try {
        const ct = this.getResponseHeader("content-type") || "";
        const url = String(this.__ftkUrl || "");
        if (FONT_URL_RE.test(url)) {
          post({ entries: [{ url: new URL(url, location.href).href, size: (this.response || "").length || 0, mime: ct, origin: location.origin }] });
        } else if (ct.includes("json") || ct.includes("text")) {
          const found = scanTextForFontUrls(this.responseText, url);
          if (found.length) post({ entries: found });
        }
      } catch (e) { /* ignore */ }
    });
    return origSend.apply(this, args);
  };

  // --- WebSocket（cjkfonts 类服务以 base64 帧回传字体）---
  const OrigWS = window.WebSocket;
  function HookWS(url, protocols) {
    const ws = protocols !== undefined ? new OrigWS(url, protocols) : new OrigWS(url);
    ws.addEventListener("message", ev => {
      try {
        const data = ev.data;
        let s = null;
        if (typeof data === "string") s = data;
        else if (data instanceof ArrayBuffer && data.byteLength > 64) {
          // 二进制字体帧直接上报长度
          if (FONT_URL_RE.test(String(url)) || data.byteLength > 50000) {
            post({ entries: [{ url: `ws://${url}#frame`, size: data.byteLength, mime: "font/ws-binary", origin: location.origin, wsUrl: String(url), binary: true }] });
          }
          return;
        }
        if (s && s.length > 2000) {
          // base64 字体特征：长 base64 串
          const m = s.match(/[A-Za-z0-9+/=]{2000,}/);
          if (m) {
            post({ entries: [{ url: `ws://${url}#b64`, size: m[0].length, mime: "font/ws-base64", origin: location.origin, wsUrl: String(url), base64: m[0] }] });
          }
        }
      } catch (e) { /* ignore */ }
    });
    return ws;
  }
  HookWS.prototype = OrigWS.prototype;
  HookWS.CONNECTING = 0; HookWS.OPEN = 1; HookWS.CLOSING = 2; HookWS.CLOSED = 3;
  window.WebSocket = HookWS;
})();
