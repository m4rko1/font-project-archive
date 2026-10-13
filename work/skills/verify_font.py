#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows 字体加载验证工具
==========================
在 Windows 环境下验证 TTF 字体是否可被系统字体引擎正确加载和解析。

验证策略（按优先级）：
1. 使用 .NET PrivateFontCollection API（需 pythonnet）
   - 加载字体到内存，通过 .NET 字体引擎验证
   - 检查字体家族名称、样式、字形数
   - 与 Windows 字体查看器行为一致
2. 回退方案：使用 fontTools 解析验证 + 提示用户手动双击验证

用法：
    python verify_font.py --input font.ttf
    python verify_font.py --input font.ttf --verbose

依赖：
    pip install fonttools
    pip install pythonnet  （可选，仅 .NET 方式需要）

注意：
    .NET 方式的 pythonnet 在某些环境可能安装失败。
    届时会自动回退到 fontTools 解析验证 + 手动验证提示。
"""

import argparse
import logging
import os
import sys
import subprocess
from typing import Optional, Tuple


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
# 验证方式 1: fontTools 解析验证（基础回退方案）
# ============================================================================

def verify_via_fonttools(font_path: str) -> Tuple[bool, str]:
    """
    使用 fontTools 解析字体文件，验证其结构完整性。
    
    这是最基础的验证，确保文件不是损坏的二进制数据。
    但不保证 Windows 字体引擎能正确渲染。
    """
    from fontTools.ttLib import TTFont

    try:
        font = TTFont(font_path)
        glyph_count = len(font.getGlyphOrder())
        tables = sorted(font.reader.keys()) if font.reader else []

        # 检查关键表
        required_tables = ["cmap", "head", "hhea", "maxp", "hmtx", "name", "OS/2", "post", "glyf", "loca"]
        present = [t for t in required_tables if t in tables]
        missing = [t for t in required_tables if t not in tables]

        # 提取基本信息
        name_table = font.get("name")
        family_name = "Unknown"
        subfamily = "Unknown"
        if name_table:
            for r in name_table.names:
                if r.nameID == 1 and r.platformID == 3:
                    family_name = r.toUnicode()
                if r.nameID == 2 and r.platformID == 3:
                    subfamily = r.toUnicode()

        os2 = font.get("OS/2")
        weight = os2.usWeightClass if os2 else "?"

        info_lines = [
            f"  Family: {family_name}",
            f"  Subfamily: {subfamily}",
            f"  Weight: {weight}",
            f"  Glyphs: {glyph_count}",
            f"  Tables: {', '.join(tables)}",
        ]

        if missing:
            missing_str = ", ".join(missing)
            info_lines.append(f"  Missing tables: {missing_str}")
            font.close()
            return False, "\n".join(info_lines)

        font.close()
        return True, "\n".join(info_lines)

    except Exception as e:
        return False, f"  fontTools 解析失败: {e}"


# ============================================================================
# 验证方式 2: .NET PrivateFontCollection（Windows 原生验证）
# ============================================================================

def verify_via_dotnet(font_path: str) -> Tuple[bool, str]:
    """
    使用 .NET PrivateFontCollection API 加载验证字体。
    
    这是与 Windows 字体查看器行为完全一致的验证方式。
    通过 System.Drawing.Text.PrivateFontCollection 加载字体，
    然后检查字体家族是否可被 .NET 字体系统识别。
    
    需要 pythonnet: pip install pythonnet
    """
    try:
        import clr
        clr.AddReference("System.Drawing")
        from System.Drawing.Text import PrivateFontCollection
        from System.Drawing import Font, FontFamily, FontStyle
    except ImportError:
        return False, "  pythonnet 未安装，无法使用 .NET 验证"
    except Exception as e:
        return False, f"  .NET 运行时初始化失败: {e}"

    try:
        pfc = PrivateFontCollection()

        # 加载字体
        abs_path = os.path.abspath(font_path)
        pfc.AddFontFile(abs_path)

        families = list(pfc.Families)
        if not families:
            return False, "  .NET 加载后无字体家族返回（AddFontFile 可能静默失败）"

        info_lines = []
        for family in families:
            fn = family.GetName(0)
            is_style_available = family.IsStyleAvailable(FontStyle.Regular)
            styles = []
            for style in [FontStyle.Regular, FontStyle.Bold, FontStyle.Italic,
                          FontStyle.Bold | FontStyle.Italic]:
                if family.IsStyleAvailable(style):
                    styles.append(str(style))

            cell_ascent = family.GetCellAscent(FontStyle.Regular)
            cell_descent = family.GetCellDescent(FontStyle.Regular)
            linespace = family.GetLineSpacing(FontStyle.Regular)

            info_lines.append(f"  Family: {fn}")
            info_lines.append(f"  Regular available: {is_style_available}")
            info_lines.append(f"  Styles: {', '.join(styles)}")
            info_lines.append(f"  Metrics: ascent={cell_ascent}, descent={cell_descent}, linespace={linespace}")

            if not is_style_available:
                return False, "\n".join(info_lines) + "\n  .NET 标记 Regular 样式不可用"

        return True, "\n".join(info_lines)

    except Exception as e:
        return False, f"  .NET 加载字体时出错: {e}"


# ============================================================================
# 验证方式 3: 打开 Windows 字体查看器（用户视觉验证）
# ============================================================================

def open_in_font_viewer(font_path: str) -> bool:
    """
    尝试使用 Windows 字体查看器（fontview.exe）打开字体。
    
    字体查看器会尝试加载并渲染字体预览。
    如果字体有问题，查看器会显示错误或无法打开。
    
    注意：这会弹出一个 GUI 窗口。
    """
    abs_path = os.path.abspath(font_path)
    try:
        subprocess.Popen(
            ["fontview.exe", abs_path],
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return True
    except Exception as e:
        logging.debug(f"无法打开字体查看器: {e}")
        return False


# ============================================================================
# 主验证流程
# ============================================================================

def verify_font(font_path: str, open_viewer: bool = False) -> Tuple[bool, str]:
    """
    验证字体是否可被 Windows 正确加载。
    
    验证流程：
    1. 尝试 .NET PrivateFontCollection 加载（最接近系统行为）
    2. 如果 .NET 不可用，使用 fontTools 解析验证
    3. 可选：打开 Windows 字体查看器进行视觉验证
    
    Args:
        font_path: 字体文件路径
        open_viewer: 是否打开字体查看器 GUI
    
    Returns:
        (success, info_str) 元组
    """
    if not os.path.isfile(font_path):
        raise FileNotFoundError(f"字体文件不存在: {font_path}")

    result_lines = []
    dotnet_success = False

    # 阶段1: .NET 验证
    logging.info("阶段1: .NET PrivateFontCollection 验证...")
    success, info = verify_via_dotnet(font_path)
    if "pythonnet" in info or "运行时初始化失败" in info:
        result_lines.append("[.NET 验证] 跳过（pythonnet 不可用）")
        result_lines.append(info)
    elif success:
        result_lines.append("[.NET 验证] 通过")
        result_lines.append(info)
        dotnet_success = True
    else:
        result_lines.append("[.NET 验证] 失败")
        result_lines.append(info)

    result_lines.append("")

    # 阶段2: fontTools 回退验证
    logging.info("阶段2: fontTools 解析验证...")
    success_ft, info_ft = verify_via_fonttools(font_path)
    if success_ft:
        result_lines.append("[fontTools 验证] 通过")
    else:
        result_lines.append("[fontTools 验证] 失败")
    result_lines.append(info_ft)

    # 阶段3: 字体查看器
    if open_viewer:
        logging.info("阶段3: 打开 Windows 字体查看器...")
        if open_in_font_viewer(font_path):
            result_lines.append("\n[字体查看器] 已打开，请查看预览窗口手动验证")
        else:
            result_lines.append("\n[字体查看器] 无法打开，请手动双击字体文件验证")

    # 结论
    result_lines.append("\n" + "-" * 40)
    if dotnet_success and success_ft:
        result_lines.append("结论: 字体在所有验证方式中均通过。")
        final_success = True
    elif success_ft:
        result_lines.append("结论: 字体结构完整（fontTools 解析通过），但 .NET 验证未通过。")
        result_lines.append("建议: 使用字体查看器手动确认。")
        final_success = False
    else:
        result_lines.append("结论: 字体验证失败，可能存在严重问题。请运行 diagnose_font.py 诊断。")
        final_success = False

    return final_success, "\n".join(result_lines)


# ============================================================================
# 命令行入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Windows 字体加载验证工具 - 验证 TTF 字体能否被系统正确加载",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python verify_font.py --input fixed.ttf
  python verify_font.py --input fixed.ttf --open-viewer
  python verify_font.py --input fixed.ttf --verbose

验证方式:
  1. .NET PrivateFontCollection API（需 pythonnet）
  2. fontTools 结构解析（回退方案）
  3. Windows 字体查看器 GUI（需 --open-viewer）
        """,
    )
    parser.add_argument(
        "--input", required=True,
        help="待验证的 TTF 字体文件路径",
    )
    parser.add_argument(
        "--open-viewer", action="store_true",
        help="额外打开 Windows 字体查看器进行视觉确认",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="启用详细日志输出",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    logging.info("=== Windows 字体验证工具 ===")

    try:
        success, output = verify_font(
            font_path=args.input,
            open_viewer=args.open_viewer,
        )
        print("\n" + output)

        if not success:
            sys.exit(1)

    except Exception as e:
        logging.error(f"验证失败: {e}", exc_info=args.verbose)
        sys.exit(2)


if __name__ == "__main__":
    main()
