#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WOFF2 子集文件合并工具
=======================
将字体厂商分片发布的多个 WOFF2 子集文件合并为单一 TTF/OTF 字体文件。

核心原理：
- 每个 WOFF2 子集包含字体的部分字形（subset）
- 合并过程需要逐个收集所有子集中的字形数据
- 合并 cmap 表、glyf/loca/hmtx 等关键表
- 通过 fontTools 处理表依赖和偏移量计算

用法：
    python merge_woff2_fonts.py --input-dir ./woff2_fragments/ --output ./merged.ttf

依赖：
    pip install fonttools
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from fontTools.pens.pointPen import SegmentToPointPen

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
# 核心合并逻辑
# ============================================================================

def collect_woff2_files(input_dir: str) -> List[Path]:
    """收集目录下所有 .woff2 文件，按文件名排序"""
    dir_path = Path(input_dir)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"目录不存在: {input_dir}")

    woff2_files = sorted(dir_path.glob("*.woff2"))
    if not woff2_files:
        raise FileNotFoundError(f"在 {input_dir} 中未找到任何 .woff2 文件")

    logging.info(f"找到 {len(woff2_files)} 个 WOFF2 子集文件")
    for f in woff2_files:
        logging.debug(f"  {f.name} ({f.stat().st_size:,} bytes)")
    return woff2_files


def load_woff2_fonts(woff2_paths: List[Path]) -> List[TTFont]:
    """加载所有 WOFF2 文件为 TTFont 对象"""
    fonts = []
    for path in woff2_paths:
        try:
            font = TTFont(str(path))
            # WOFF2 字体加载后 flavor 应为 'woff2'，这会影响后续处理
            fonts.append(font)
            logging.debug(f"加载 {path.name}: {len(font.getGlyphOrder())} 字形, flavor={font.flavor}")
        except Exception as e:
            logging.error(f"无法加载 {path.name}: {e}")
            raise
    return fonts


def merge_glyphs(fonts: List[TTFont]) -> TTFont:
    """
    以第一个字体为模板，合并所有子集的字形数据。
    
    合并策略：
    1. 第一个字体作为基础模板（保留表结构、度量信息）
    2. 后续字体的字形逐个追加到基础字体
    3. 合并 cmap 表（字符到字形的映射）
    4. 更新 maxp 表（字形总数）
    """
    base_font = fonts[0]
    total_glyph_count = len(base_font.getGlyphOrder())
    glyph_order = list(base_font.getGlyphOrder())

    # 建立已有字形名称集合，用于去重
    existing_names: Set[str] = set(glyph_order)

    # 字形名称 → 字形索引 映射（用于 cmap 合并）
    name_to_index: Dict[str, int] = {name: i for i, name in enumerate(glyph_order)}

    # 字形数据缓存：仅对后续字体提取需要复制的字形
    for font_idx, source_font in enumerate(fonts[1:], start=1):
        source_glyf = source_font.get("glyf")
        source_hmtx = source_font.get("hmtx")
        source_order = source_font.getGlyphOrder()
        source_cmap = source_font.getBestCmap() or {}

        if source_glyf is None:
            logging.warning(f"子集 #{font_idx + 1} 无 glyf 表，跳过")
            continue

        new_glyph_count = 0
        for glyph_name in source_order:
            # 跳过 .notdef 和其他空字形、已存在的字形
            if glyph_name in existing_names:
                continue
            if glyph_name.startswith(".notdef"):
                continue
            glyph_obj: Optional[Glyph] = source_glyf[glyph_name]
            if glyph_obj is None:
                continue
            # 跳过无轮廓的空字形（无实际字形数据）
            if glyph_obj.numberOfContours == 0 and not glyph_obj.components:
                # 但有 cmap 映射时仍需保留（空格字形等）
                has_cmap = any(k in source_cmap for k in source_cmap)
                if not has_cmap:
                    continue

            # 追加字形
            base_font["glyf"][glyph_name] = glyph_obj
            glyph_order.append(glyph_name)
            existing_names.add(glyph_name)

            # 复制 hmtx 度量
            if source_hmtx and glyph_name in source_hmtx.metrics:
                base_font["hmtx"][glyph_name] = source_hmtx.metrics[glyph_name]

            new_glyph_count += 1

        logging.info(f"  从子集 #{font_idx + 1} 添加 {new_glyph_count} 个新字形")

    # 更新 glyphOrder
    base_font.setGlyphOrder(glyph_order)
    total_glyph_count = len(glyph_order)
    logging.info(f"合并后字形总数: {total_glyph_count}")

    # ============================================================================
    # 合并 cmap 表
    # ============================================================================
    merge_cmap_tables(base_font, fonts, name_to_index)

    return base_font


def merge_cmap_tables(base_font: TTFont, all_fonts: List[TTFont], name_to_index: Dict[str, int]):
    """
    合并所有 cmap 表。
    
    策略：收集所有字符码点 → 字形名称的映射，统一写入基础字体。
    确保 format 4（BMP 字符）和 format 12（扩展字符）均被覆盖。
    """
    base_cmap_table = base_font["cmap"]
    unified_map: Dict[int, str] = {}

    # 遍历所有字体，收集完整的 cmap 映射
    for font in all_fonts:
        best_cmap = font.getBestCmap()
        if best_cmap is None:
            continue
        for code_point, glyph_name in best_cmap.items():
            if code_point in unified_map:
                # 同一码点有多个映射时，保留初值（基础字体优先）
                continue
            unified_map[code_point] = glyph_name

    # 将字形名称修正为合并后的索引
    # 构建最终 cmap: codepoint → glyph_name（glyph_name 必须在 glyph_order 中）
    final_cmap: Dict[int, str] = {}
    skipped = 0
    for cp, gname in sorted(unified_map.items()):
        if gname in name_to_index:
            final_cmap[cp] = gname
        else:
            skipped += 1
            logging.debug(f"  cmap 跳过: U+{cp:04X} → {gname} (字形不存在)")

    if skipped > 0:
        logging.warning(f"cmap 合并中有 {skipped} 个映射因字形不存在而被跳过")

    # 重建 cmap 表：创建一个全新干净的 cmap 表
    from fontTools.ttLib.tables._c_m_a_p import table__c_m_a_p

    new_cmap = table__c_m_a_p()
    new_cmap.tableVersion = 0
    new_cmap.tables = []

    # 构建 format 4 子表 (BMP: U+0000 - U+FFFF)
    make_format4_subtable(new_cmap, final_cmap)

    # 构建 format 12 子表 (SMP: U+10000+)
    make_format12_subtable(new_cmap, final_cmap)

    base_font["cmap"] = new_cmap
    logging.info(f"cmap 合并完成: format 4 ({len([c for c in final_cmap if c <= 0xFFFF])} 映射) + format 12 ({len([c for c in final_cmap if c > 0xFFFF])} 映射)")


def make_format4_subtable(cmap_table, unified: Dict[int, str]):
    """构建 cmap format 4 子表（BMP 字符映射）"""
    bmp = {cp: gn for cp, gn in unified.items() if cp <= 0xFFFF}
    if not bmp:
        return

    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    subtable = CmapSubtable.newSubtable(4)
    subtable.platEncID = 0
    subtable.platformID = 3  # Windows
    subtable.format = 4
    subtable.cmap = bmp
    cmap_table.tables.append(subtable)


def make_format12_subtable(cmap_table, unified: Dict[int, str]):
    """构建 cmap format 12 子表（SMP 字符映射）"""
    smp = {cp: gn for cp, gn in unified.items() if cp > 0xFFFF}
    if not smp:
        return

    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    subtable = CmapSubtable.newSubtable(12)
    subtable.platEncID = 10
    subtable.platformID = 3  # Windows
    subtable.format = 12
    subtable.cmap = smp
    cmap_table.tables.append(subtable)


def update_maxp(base_font: TTFont):
    """更新 maxp 表（字形总数）"""
    maxp = base_font["maxp"]
    glyph_count = len(base_font.getGlyphOrder())
    maxp.numGlyphs = glyph_count
    logging.info(f"maxp.numGlyphs 更新为 {glyph_count}")


def save_font(font: TTFont, output_path: str, font_name: Optional[str] = None):
    """
    保存合并后的字体。
    
    - 如果加载时字体 flavor 为 'woff2'，保存前需将 flavor 设为 None
      以输出纯 TTF 格式（而非再次封装为 WOFF2）
    - 如果指定 font_name，更新 name 表中的 Family Name
    """
    # 移除 WOFF2 封装标记
    if font.flavor == "woff2":
        font.flavor = None
        logging.info("已移除 WOFF2 flavor 标记，将保存为纯 TTF")

    # 可选：设置字体家族名称
    if font_name:
        name_table = font["name"]
        name_table.setName(font_name, nameID=1, platformID=3, platEncID=1, langID=0x409)
        name_table.setName(font_name, nameID=16, platformID=3, platEncID=1, langID=0x409)
        logging.info(f"字体家族名称设置为: {font_name}")

    font.save(output_path)
    file_size = os.path.getsize(output_path)
    logging.info(f"字体已保存至: {output_path} ({file_size:,} bytes)")


# ============================================================================
# 命令行入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="WOFF2 子集文件合并工具 - 将多个 WOFF2 分片合并为单一 TTF 字体",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python merge_woff2_fonts.py --input-dir ./woff2_parts/ --output ./merged.ttf
  python merge_woff2_fonts.py --input-dir ./woff2_parts/ --output ./merged.ttf --font-name "MyCustomFont"
  python merge_woff2_fonts.py --input-dir ./woff2_parts/ --output ./merged.ttf --verbose
        """,
    )
    parser.add_argument(
        "--input-dir", required=True,
        help="包含 .woff2 子集文件的目录路径",
    )
    parser.add_argument(
        "--output", required=True,
        help="合并后 TTF 字体的输出路径",
    )
    parser.add_argument(
        "--font-name", default=None,
        help="自定义字体家族名称（可选，默认使用第一个文件的名称）",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="启用详细日志输出",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    logging.info("=== WOFF2 子集合并工具 ===")

    try:
        # 步骤1: 收集 WOFF2 文件
        woff2_paths = collect_woff2_files(args.input_dir)

        # 步骤2: 加载所有字体
        fonts = load_woff2_fonts(woff2_paths)

        # 步骤3: 合并字形
        merged_font = merge_glyphs(fonts)

        # 步骤4: 更新 maxp 表
        update_maxp(merged_font)

        # 步骤5: 保存
        save_font(merged_font, args.output, args.font_name)

        logging.info("=== 合并完成 ===")

    except Exception as e:
        logging.error(f"合并失败: {e}", exc_info=args.verbose)
        sys.exit(1)
    finally:
        # 关闭所有打开的字体对象
        for font in locals().get("fonts", []):
            try:
                font.close()
            except Exception:
                pass
        try:
            locals().get("merged_font", None).close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
