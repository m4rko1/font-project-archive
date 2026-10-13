#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
justfont 字体子集抓取工具
========================
从 justfont 营销页/活动页获取字体子集文件，补齐工具链的抓取环节。

两种抓取模式：

1. 静态收割（默认）
   解析页面 HTML 与其引用的 CSS 中的 @font-face 规则，
   下载页面直接托管的所有字体文件（woff/woff2/ttf/otf）。

2. 动态 API（--dynamic）
   调用 justfont 网页字型服务的动态接口，按字符批次拉取 WOFF 子集：
       POST https://go.justfont.com/jfont/api?o=<站点源>&p=<站点ID>&t=woff
       Body: fn=<字体名(含字重后缀)>&fs=<URL编码字符集>
   响应 JSON 含 fn（字体名数组）与 fu（子集文件 URL 数组）。
   站点 ID 可自动从页面 JS 的 `provider:vC(<id>)` 中发现。

用法：
    # 静态收割页面托管的所有字体文件
    python fetch_justfont.py --url https://justfont.com/lanyanghei/ --out ./fonts

    # 动态 API：按字符批次拉取指定字重的 WOFF 子集
    python fetch_justfont.py --url https://justfont.com/lanyanghei/ --out ./woff \
        --dynamic --family jf-lanyanghei-bold --text-file chars.txt --batch-size 1700

    # 仅列出页面上注册的字体家族与字重
    python fetch_justfont.py --url https://justfont.com/lanyanghei/ --list-families

依赖：
    无第三方依赖（仅标准库）。下载 WOFF2 后的处理需 fonttools + brotli。

注意：
    抓取行为须遵守 justfont 服务条款与字体授权协议。动态 API 返回的
    子集仅限该站点授权展示的字符；本工具仅供字体学习与研究用途。
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API_ENDPOINT = "https://go.justfont.com/jfont/api"
DEFAULT_BATCH_SIZE = 1700  # justfont 动态 API 单批字符数上限
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
FONT_EXT_PATTERN = r'\.(?:woff2?|ttf|otf)'

# ============================================================================
# 日志配置
# ============================================================================

def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


# ============================================================================
# HTTP 基础
# ============================================================================

def http_get(url: str, referer: str = None, timeout: int = 60) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def http_post_form(url: str, params: dict, body: dict, referer: str = None,
                   timeout: int = 60) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if referer:
        headers["Referer"] = referer
        headers["Origin"] = referer.split("/")[0] + "//" + referer.split("/")[2] \
            if "://" in referer else referer
    query = urllib.parse.urlencode(params)
    data = urllib.parse.urlencode(body).encode("utf-8")
    req = urllib.request.Request(url + "?" + query, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def save_bytes(data: bytes, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path

# ============================================================================
# 模式 1：静态收割 @font-face
# ============================================================================

def discover_static_fonts(page_url: str) -> list:
    """
    从页面 HTML 及其引用的 CSS 中发现 @font-face 声明的字体文件 URL。
    返回去重后的绝对 URL 列表（按发现顺序）。
    """
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(page_url))
    html = http_get(page_url, referer=page_url).decode("utf-8", errors="replace")

    css_sources = re.findall(r'<link[^>]+href="([^"]+\.css[^"]*)"', html)
    css_texts = []
    for css_ref in css_sources[:20]:
        css_url = urllib.parse.urljoin(page_url, css_ref)
        try:
            css_texts.append((css_url, http_get(css_url, referer=page_url).decode("utf-8", errors="replace")))
        except Exception as e:
            logging.debug(f"跳过 CSS {css_url}: {e}")

    font_urls = []

    def extract_from_text(text, base_url):
        for block in re.findall(r'@font-face\s*\{[^}]*\}', text):
            for m in re.finditer(r'url\(\s*([^)]+?)\s*\)', block):
                raw = m.group(1).strip().strip('"\'')
                if re.search(FONT_EXT_PATTERN, raw.split("?")[0]):
                    font_urls.append(urllib.parse.urljoin(base_url, raw))
        # 兜底：直接扫描文本中的字体文件引用（如 preload、JS 内联）
        for m in re.finditer(r'["\'(]([^"\'()]+{})["\')]'.format(FONT_EXT_PATTERN), text):
            font_urls.append(urllib.parse.urljoin(base_url, m.group(1)))

    extract_from_text(html, page_url)
    for css_url, css_text in css_texts:
        extract_from_text(css_text, css_url)

    seen, result = set(), []
    for url in font_urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def fetch_static(page_url: str, out_dir: str) -> list:
    """静态收割：下载页面托管的所有字体文件。返回保存的文件路径列表。"""
    urls = discover_static_fonts(page_url)
    if not urls:
        logging.warning("页面上未发现任何 @font-face 字体文件")
        return []

    logging.info(f"发现 {len(urls)} 个字体文件引用")
    saved = []
    for url in urls:
        filename = os.path.basename(urllib.parse.urlparse(url).path) or "font.bin"
        dest = Path(out_dir) / filename
        try:
            data = http_get(url, referer=page_url)
            save_bytes(data, dest)
            logging.info(f"  {filename} ({len(data):,} bytes)")
            saved.append(dest)
        except Exception as e:
            logging.error(f"  下载失败 {url}: {e}")
    return saved

# ============================================================================
# 模式 2：动态 API 按字符批次拉取
# ============================================================================

def discover_site_id(page_url: str) -> str:
    """从页面及其 JS 资源中发现 justfont 网页字型服务的站点 ID（provider:vC(<id>)）"""
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(page_url))
    html = http_get(page_url, referer=page_url).decode("utf-8", errors="replace")

    m = re.search(r'vC\((\d{3,})\)', html)
    if m:
        return m.group(1)

    js_refs = re.findall(r'src="([^"]+\.js[^"]*)"', html)
    for js_ref in js_refs[:10]:
        js_url = urllib.parse.urljoin(page_url, js_ref)
        try:
            js = http_get(js_url, referer=page_url).decode("utf-8", errors="replace")
        except Exception:
            continue
        m = re.search(r'vC\((\d{3,})\)', js)
        if m:
            logging.debug(f"站点 ID 发现于 {js_url}")
            return m.group(1)
    raise RuntimeError("无法从页面自动发现站点 ID，请用 --site-id 手动指定")


def discover_families(page_url: str, site_id: str) -> list:
    """
    从页面 JS 中发现注册的字体家族与字重配置，
    返回 [(family, weight_value, weight_suffix), ...]（fn 参数 = family-suffix）。
    """
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(page_url))
    html = http_get(page_url, referer=page_url).decode("utf-8", errors="replace")
    texts = [html]
    for js_ref in re.findall(r'src="([^"]+\.js[^"]*)"', html)[:10]:
        try:
            texts.append(http_get(urllib.parse.urljoin(page_url, js_ref),
                                  referer=page_url).decode("utf-8", errors="replace"))
        except Exception:
            continue

    combos, seen = [], set()
    for text in texts:
        for m in re.finditer(r'\{family:"([^"]+)",weight:\{([^}]*)\}', text):
            family, weights = m.group(1), m.group(2)
            for wm in re.finditer(r'(\d+):"(\w+)"', weights):
                key = (family, wm.group(1), wm.group(2))
                if key not in seen:
                    seen.add(key)
                    combos.append(key)
    return combos


def fetch_dynamic(page_url: str, out_dir: str, family: str, text: str,
                  batch_size: int, site_id: str = None) -> list:
    """
    动态 API 抓取：将字符集去重后按 batch_size 分批，
    每批 POST 请求获取子集 URL 并下载 WOFF 文件。
    """
    if site_id is None:
        site_id = discover_site_id(page_url)
        logging.info(f"自动发现站点 ID: {site_id}")

    referer = page_url
    params = {"o": origin_of(page_url), "p": site_id, "t": "woff"}

    # 字符去重，保持出现顺序
    unique_chars = list(dict.fromkeys(c for c in text if not c.isspace()))
    total_batches = (len(unique_chars) + batch_size - 1) // batch_size
    logging.info(f"共 {len(unique_chars)} 个去重字符，分 {total_batches} 批拉取 "
                 f"(family={family}, 站点ID={site_id})")

    saved = []
    for i in range(0, len(unique_chars), batch_size):
        batch = unique_chars[i:i + batch_size]
        batch_no = i // batch_size + 1
        body = {"fn": family, "fs": "".join(batch)}

        resp = http_post_form(API_ENDPOINT, params, body, referer=referer)
        if "err" in resp:
            raise RuntimeError(f"API 返回错误码 {resp['err']} (batch {batch_no})")
        if not resp.get("fu"):
            raise RuntimeError(f"API 未返回文件 URL (batch {batch_no})")

        woff_url = resp["fu"][0]
        data = http_get(woff_url, referer=referer)
        dest = Path(out_dir) / f"{family}_batch{batch_no:03d}.woff"
        save_bytes(data, dest)
        logging.info(f"  batch {batch_no}/{total_batches}: {len(batch)} 字符 "
                     f"→ {dest.name} ({len(data):,} bytes)")
        saved.append(dest)
        time.sleep(0.5)  # 请求间礼貌间隔
    return saved


def origin_of(page_url: str) -> str:
    p = urllib.parse.urlparse(page_url)
    return f"{p.scheme}://{p.netloc}"

# ============================================================================
# 命令行入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="justfont 字体子集抓取工具 - 静态 @font-face 收割 / 动态 API 批次拉取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python fetch_justfont.py --url https://justfont.com/lanyanghei/ --out ./fonts
  python fetch_justfont.py --url https://justfont.com/lanyanghei/ --list-families
  python fetch_justfont.py --url https://justfont.com/lanyanghei/ --out ./woff \\
      --dynamic --family jf-lanyanghei-bold --text "蘭陽黑體" --batch-size 1700
        """,
    )
    parser.add_argument("--url", required=True, help="justfont 页面 URL")
    parser.add_argument("--out", default="./fetched_fonts", help="输出目录")
    parser.add_argument("--list-families", action="store_true",
                        help="仅列出页面上注册的字体家族与字重")
    parser.add_argument("--dynamic", action="store_true",
                        help="使用动态 API 按字符批次拉取 WOFF 子集")
    parser.add_argument("--family", default=None,
                        help="动态模式：字体名（含字重后缀，如 jf-lanyanghei-bold）")
    parser.add_argument("--text", default=None, help="动态模式：要拉取的字符集")
    parser.add_argument("--text-file", default=None,
                        help="动态模式：从文件读取字符集（UTF-8）")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"动态模式：单批字符数上限（默认 {DEFAULT_BATCH_SIZE}）")
    parser.add_argument("--site-id", default=None,
                        help="动态模式：站点 ID（默认从页面 JS 自动发现）")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    setup_logging(args.verbose)

    logging.info("=== justfont 字体子集抓取工具 ===")
    logging.info("提醒：抓取与后续处理仅供学习研究，须遵守 justfont 服务条款与字体授权。")

    try:
        if args.list_families:
            site_id = args.site_id or discover_site_id(args.url)
            combos = discover_families(args.url, site_id)
            logging.info(f"站点 ID: {site_id}，注册的字体家族/字重：")
            for family, weight, suffix in combos:
                print(f"  {family}-{suffix}  (weight {weight})")
            if not combos:
                logging.warning("未在页面 JS 中发现字体配置")
            return

        if args.dynamic:
            if not args.family:
                parser.error("--dynamic 需要 --family（如 jf-lanyanghei-bold）")
            text = args.text or ""
            if args.text_file:
                text += Path(args.text_file).read_text(encoding="utf-8")
            if not text:
                parser.error("--dynamic 需要 --text 或 --text-file 提供字符集")
            saved = fetch_dynamic(args.url, args.out, args.family, text,
                                  args.batch_size, args.site_id)
        else:
            saved = fetch_static(args.url, args.out)

        logging.info(f"=== 抓取完成: {len(saved)} 个文件于 {args.out} ===")

    except Exception as e:
        logging.error(f"抓取失败: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
