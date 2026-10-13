// Font Toolkit — popup 逻辑
// 捕获列表管理 + 全量抓取调度 + ZIP 打包下载。字节拉取在 background 完成，
// popup 仅负责 UI、打包（Blob 可用上下文）与触发 chrome.downloads。

const $ = id => document.getElementById(id);

// ---- 标签页切换 ----
document.querySelectorAll(".tabs button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $("page-" + btn.dataset.tab).classList.add("active");
  });
});

// ---- 字表下拉 ----
for (const [key, cs] of Object.entries(FTK_CHARSETS)) {
  const opt = document.createElement("option");
  opt.value = key; opt.textContent = cs.label;
  $("charset").appendChild(opt);
}

function safeName(s) {
  return String(s).replace(/[\\/:*?"<>|]/g, "_");
}

async function downloadZip(entries, zipName) {
  const zip = ftkMakeZip(entries);
  const blob = new Blob([zip], { type: "application/zip" });
  const url = URL.createObjectURL(blob);
  await chrome.downloads.download({ url, filename: `font-toolkit/${safeName(zipName)}` });
}

function b64FilesToEntries(files) {
  return files.filter(f => f.b64).map(f => ({ name: safeName(f.name), data: ftkB64ToBytes(f.b64) }));
}

// ---- 捕获页 ----
async function refreshCaptures() {
  const { captures = [] } = await chrome.storage.session.get("ftk_captures");
  const box = $("capList");
  if (!captures.length) {
    box.innerHTML = '<div class="cap" style="color:#667">本会话尚未捕获字体请求。</div>';
    return;
  }
  box.innerHTML = captures.slice(0, 60).map(c =>
    `<div class="cap"><div class="u">${escapeHtml(c.url)}</div><div class="m">${escapeHtml(c.mime || "?")} · ${c.size || "?"} B · ${c.source}</div></div>`
  ).join("");
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
refreshCaptures();

$("btnClear").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "ftk-clear-captures" });
  refreshCaptures();
});

$("btnZipCap").addEventListener("click", async () => {
  const { captures = [] } = await chrome.storage.session.get("ftk_captures");
  if (!captures.length) return;
  $("status").textContent = `拉取 ${captures.length} 个文件…`;
  const resp = await chrome.runtime.sendMessage({
    type: "ftk-fetch-captures-bytes",
    items: captures.map(c => ({ url: c.url })),
  });
  if (!resp?.ok) { $("status").textContent = "失败: " + (resp?.error || "?"); return; }
  const entries = b64FilesToEntries(resp.files);
  await downloadZip(entries, `captures_${Date.now()}.zip`);
  $("status").textContent = `已打包 ${entries.length} 个文件`;
});

$("btnEachCap").addEventListener("click", async () => {
  const { captures = [] } = await chrome.storage.session.get("ftk_captures");
  for (let i = 0; i < captures.length; i++) {
    await chrome.downloads.download({ url: captures[i].url, filename: `font-toolkit/captures/${safeName(urlBasename(captures[i].url))}` });
  }
  $("status").textContent = `已发起 ${captures.length} 个下载`;
});

function urlBasename(u) {
  try { return decodeURIComponent(new URL(u).pathname.split("/").pop()) || "font.bin"; }
  catch (e) { return "font.bin"; }
}

// ---- 全量抓取页 ----
let detectedFamilies = [];

$("btnDetect").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url) { $("detected").textContent = "无法获取当前页 URL"; return; }
  $("detected").textContent = "探测中…";
  const resp = await chrome.runtime.sendMessage({ type: "ftk-detect", pageUrl: tab.url });
  if (!resp?.ok) { $("detected").textContent = "探测失败: " + resp?.error; return; }
  const { siteId, families } = resp.cfg;
  detectedFamilies = families;
  if (siteId) $("siteId").value = siteId;
  const sel = $("family");
  sel.innerHTML = "";
  for (const f of families) {
    const opt = document.createElement("option");
    opt.value = f.fn; opt.textContent = `${f.fn} (weight ${f.weight})`;
    sel.appendChild(opt);
  }
  $("detected").textContent = families.length
    ? `站点 ID: ${siteId ?? "未发现"}；注册字体:\n` + families.map(f => `  ${f.fn} (w${f.weight})`).join("\n")
    : `站点 ID: ${siteId ?? "未发现"}；未发现动态字型配置（页面可能使用静态 @font-face，用「捕获」页即可）。`;
});

$("fileIn").addEventListener("change", ev => {
  const file = ev.target.files[0];
  if (!file) return;
  file.text().then(t => { $("custom").value = ($("custom").value + "\n" + t).slice(0, 200000); });
});

$("btnAbort").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "ftk-abort-harvest" });
  $("progress").textContent += "\n（正在中止…）";
});

$("btnHarvest").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const family = $("family").value;
  const siteId = ($("siteId").value || "").trim();
  if (!siteId) { $("status").textContent = "缺少站点 ID（先探测或手动输入）"; return; }
  const cs = FTK_CHARSETS[$("charset").value]?.chars || "";
  const text = cs + ($("custom").value || "");
  if (!text.trim()) { $("status").textContent = "字表为空"; return; }

  $("btnHarvest").disabled = true; $("btnAbort").disabled = false;
  $("status").textContent = "";
  $("progress").textContent = "准备请求…";

  const resp = await chrome.runtime.sendMessage({
    type: "ftk-harvest",
    pageUrl: tab?.url || "https://justfont.com/",
    siteId,
    family,
    chars: text,
    batchSize: Math.max(100, +$("batchSize").value || 1700),
    format: "woff",
    autoDnR: true,
  });

  $("btnHarvest").disabled = false; $("btnAbort").disabled = true;
  if (!resp?.ok) { $("status").textContent = "抓取失败: " + (resp?.error || "?"); return; }

  const entries = b64FilesToEntries(resp.files);
  if (!entries.length) { $("status").textContent = "未获得文件"; return; }
  await downloadZip(entries, `${family}_full_${Date.now()}.zip`);
  $("status").textContent = `完成：${entries.length} 个子集已打包下载。可用 font_toolkit.py 合并。`;
});
