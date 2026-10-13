#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Font Toolkit 统一命令行入口
===========================
把抓取、合并、诊断、修复、验证五个环节整合为单一 CLI。

子命令：
    fetch     从 justfont 页面抓取字体子集（静态收割 / 动态 API）
    merge     合并 WOFF/WOFF2 子集目录为单一 TTF/OTF
    diagnose  深度诊断字体关键表
    fix       自动修复常见问题
    verify    Windows .NET + fontTools 双路验证
    all       一键流水线：merge → diagnose → fix → verify

用法示例：
    python font_toolkit.py fetch --url https://justfont.com/lanyanghei/ --out ./fonts
    python font_toolkit.py merge --input-dir ./woff/ --output ./merged.ttf
    python font_toolkit.py all --input-dir ./woff/ --output ./merged.ttf
    python font_toolkit.py diagnose --input ./merged.ttf

依赖：见 requirements.txt（fonttools + brotli 必需，pythonnet 可选）
"""

import argparse
import logging
import os
import sys

import diagnose_font
import fetch_justfont
import fix_font as fix_module
import merge_woff2_fonts as merge_module
import verify_font as verify_module


def cmd_fetch(args):
    if args.list_families:
        site_id = args.site_id or fetch_justfont.discover_site_id(args.url)
        combos = fetch_justfont.discover_families(args.url, site_id)
        logging.info(f"站点 ID: {site_id}，注册的字体家族/字重：")
        for family, weight, suffix in combos:
            print(f"  {family}-{suffix}  (weight {weight})")
        return

    if args.dynamic:
        if not args.family:
            raise SystemExit("--dynamic 需要 --family（如 jf-lanyanghei-bold）")
        text = args.text or ""
        if args.text_file:
            import pathlib
            text += pathlib.Path(args.text_file).read_text(encoding="utf-8")
        if not text:
            raise SystemExit("--dynamic 需要 --text 或 --text-file 提供字符集")
        fetch_justfont.fetch_dynamic(args.url, args.out, args.family, text,
                                     args.batch_size, args.site_id)
    else:
        fetch_justfont.fetch_static(args.url, args.out)


def cmd_merge(args):
    merge_module.merge_fonts(args.input_dir, args.output, args.font_name)


def cmd_diagnose(args):
    report = diagnose_font.diagnose_font(args.input)
    formatted = diagnose_font.format_report(report)
    print("\n" + formatted)
    if args.save_report:
        with open(args.save_report, "w", encoding="utf-8") as f:
            f.write(formatted)
        logging.info(f"诊断报告已保存至: {args.save_report}")
    if report.errors:
        sys.exit(1)


def cmd_fix(args):
    fix_module.fix_font(args.input, args.output, args.weight, args.width)


def cmd_verify(args):
    success, output = verify_module.verify_font(args.input, args.open_viewer)
    print("\n" + output)
    if not success:
        sys.exit(1)


def cmd_all(args):
    """一键流水线：合并 → 诊断 → 修复 → 验证"""
    saved = merge_module.merge_fonts(args.input_dir, args.output, args.font_name)
    print(f"\n[1/4] 合并完成: {saved}")

    print(f"\n[2/4] 修复前诊断:")
    report = diagnose_font.diagnose_font(saved)
    print(diagnose_font.format_report(report))

    root, ext = os.path.splitext(saved)
    fixed = root + "-fixed" + ext
    fix_count = fix_module.fix_font(saved, fixed)
    print(f"\n[3/4] 修复完成 ({fix_count} 项): {fixed}")

    print(f"\n[4/4] Windows 加载验证:")
    success, output = verify_module.verify_font(fixed)
    print("\n" + output)
    if not success:
        sys.exit(1)
    print(f"\n成品: {fixed}")


def main():
    parser = argparse.ArgumentParser(
        prog="font_toolkit",
        description="Font Toolkit - 字体 Web 子集抓取、合并、诊断、修复、验证一体化工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # fetch
    p = sub.add_parser("fetch", help="从 justfont 页面抓取字体子集")
    p.add_argument("--url", required=True, help="justfont 页面 URL")
    p.add_argument("--out", default="./fetched_fonts", help="输出目录")
    p.add_argument("--list-families", action="store_true", help="仅列出注册的字体家族/字重")
    p.add_argument("--dynamic", action="store_true", help="使用动态 API 按字符批次拉取")
    p.add_argument("--family", help="动态模式：字体名（含字重后缀）")
    p.add_argument("--text", help="动态模式：要拉取的字符集")
    p.add_argument("--text-file", help="动态模式：从文件读取字符集")
    p.add_argument("--batch-size", type=int, default=fetch_justfont.DEFAULT_BATCH_SIZE)
    p.add_argument("--site-id", help="动态模式：站点 ID（默认自动发现）")
    p.set_defaults(func=cmd_fetch)

    # merge
    p = sub.add_parser("merge", help="合并 WOFF/WOFF2 子集目录")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--font-name", default=None)
    p.set_defaults(func=cmd_merge)

    # diagnose
    p = sub.add_parser("diagnose", help="深度诊断字体关键表")
    p.add_argument("--input", required=True)
    p.add_argument("--save-report", default=None)
    p.set_defaults(func=cmd_diagnose)

    # fix
    p = sub.add_parser("fix", help="自动修复常见问题")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--weight", type=int, choices=range(100, 1000, 100), default=None)
    p.add_argument("--width", type=int, choices=range(1, 10), default=5)
    p.set_defaults(func=cmd_fix)

    # verify
    p = sub.add_parser("verify", help="Windows 加载验证")
    p.add_argument("--input", required=True)
    p.add_argument("--open-viewer", action="store_true")
    p.set_defaults(func=cmd_verify)

    # all
    p = sub.add_parser("all", help="一键流水线: merge → diagnose → fix → verify")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--font-name", default=None)
    p.set_defaults(func=cmd_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
