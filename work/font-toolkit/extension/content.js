// Font Toolkit — ISOLATED world 中继
// 接收 MAIN world 注入脚本的捕获消息，转发给 background。

window.addEventListener("message", ev => {
  if (ev.source !== window) return;
  const data = ev.data;
  if (!data || data.source !== "ftk-inject" || !data.payload) return;
  try { chrome.runtime.sendMessage({ type: "ftk-capture", entries: data.payload.entries || [] }); } catch (e) { /* 扩展上下文失效 */ }
});
