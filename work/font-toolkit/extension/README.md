# Font Toolkit 浏览器插件

MV3 浏览器插件（Chrome 111+ / Edge），两层能力：

1. **捕获**：自动记录网页加载的所有字体文件请求（woff/woff2/ttf/otf），
   包括 @font-face 静态文件与动态字型 API 返回的 CDN 子集；
   同时钩住 fetch/XHR/WebSocket，捕获 API JSON 响应中的字体 URL
   与 base64 字体帧（cjkfonts 类服务）。
2. **全量抓取**：对接 justfont 动态字型服务，按内置字表
   （简体 GB2312 全 6763 / 一级 3755、繁体 Big5 常用 5401、
   简繁常用合集 7560、西文与拉丁 550、日文假名、符号全角）
   或自定义字符，自动去重分批（默认 1700/批）拉取全部子集，
   打包 ZIP 下载。

## 安装（加载已解压的扩展程序）

1. 打开 `chrome://extensions`（Edge 为 `edge://extensions`）
2. 开启「开发者模式」
3. 「加载已解压的扩展程序」→ 选择本目录（`extension/`）

## 使用

- **捕获页**：浏览任意含字体的页面后打开插件，列表展示本会话捕获的字体请求；
  「打包 ZIP 下载」把全部子集拉回本地合并成一个 ZIP。
- **全量抓取页**：
  1. 打开 justfont 字体页（如 justfont.com/lanyanghei/）
  2. 插件 → 全量抓取 → 「探测当前页字体服务」
     （自动发现站点 ID 与全部家族/字重）
  3. 选择字体（fn）与字表，开始抓取 → 完成后 ZIP 自动下载
- 抓取产物用桌面端合并：

  ```bash
  python font_toolkit.py all --input-dir ./下载的子集解压目录/ --output ./merged.ttf
  ```

## 文件说明

| 文件 | 职责 |
|------|------|
| `manifest.json` | MV3 配置（webRequest / downloads / DNR 权限） |
| `background.js` | Service Worker：捕获注册表、justfont 配置探测、批次抓取 |
| `inject.js` | MAIN world 钩子：fetch/XHR/WebSocket 只读观测 |
| `content.js` | ISOLATED 中继：注入脚本 ↔ background |
| `popup.html/js` | UI：捕获列表、字表选择、进度、下载 |
| `zip.js` | 零依赖 ZIP 打包（STORE + UTF-8 文件名） |
| `charsets.js` | 自动生成的字表数据（勿手改） |

> ⚠️ 仅供字体学习研究。抓取（尤其是按字表全量拉取商业字体）须遵守
> 各字体厂商服务条款与授权协议；由此获得的字体文件不得用于未授权用途。
