#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WOFF/WOFF2 子集文件合并工具
===========================
将字体厂商分片发布的多个 WOFF/WOFF2 子集文件合并为单一 TTF/OTF 字体文件。

核心原理：
- 每个 WOFF/WOFF2 子集包含字体的部分字形（subset）
- TrueType 路径（glyf）：逐个收集子集字形数据，合并 cmap/hmtx/vmtx
- CFF 路径（OTTO）：先 desubroutinize 展开所有子例程，再按 glyph name
  合并 CharStrings，避免合并后 CharString 引用失效
- 合并 cmap 表确保 format 4（BMP）+ format 12（SMP）子表齐全
- 通过 fontTools 处理表依赖和偏移量计算

用法：
    python merge_woff2_fonts.py --input-dir ./woff2_fragments/ --output ./merged.ttf

依赖：
    pip install fonttools brotli
"""

import argparse
import copy
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable, table__c_m_a_p

FONT_EXTENSIONS = ("*.woff2", "*.woff", "*.ttf", "*.otf")

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
# 输入收集与加载
# ============================================================================

def collect_font_files(input_dir: str) -> List[Path]:
    """收集目录下所有字体子集文件（woff2/woff/ttf/otf），按文件名排序"""
    dir_path = Path(input_dir)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"目录不存在: {input_dir}")

    seen: Dict[str, Path] = {}
    for pattern in FONT_EXTENSIONS:
        for path in dir_path.glob(pattern):
            seen[path.name.lower()] = path
    files = sorted(seen.values(), key=lambda p: p.name.lower())

    if not files:
        raise FileNotFoundError(f"在 {input_dir} 中未找到任何字体文件 ({', '.join(FONT_EXTENSIONS)})")

    logging.info(f"找到 {len(files)} 个字体子集文件")
    for f in files:
        logging.debug(f"  {f.name} ({f.stat().st_size:,} bytes)")
    return files


def load_fonts(font_paths: List[Path]) -> List[TTFont]:
    """加载所有字体文件为 TTFont 对象"""
    fonts = []
    for path in font_paths:
        try:
            font = TTFont(str(path))
            fonts.append(font)
            logging.debug(f"加载 {path.name}: {len(font.getGlyphOrder())} 字形, "
                          f"flavor={font.flavor}, sfnt={font.sfntVersion!r}")
        except Exception as e:
            logging.error(f"无法加载 {path.name}: {e}")
            raise
    return fonts


def is_cff_font(font: TTFont) -> bool:
    """判断字体是否为 CFF（PostScript）轮廓"""
    return font.get("CFF ") is not None

# ============================================================================
# TrueType (glyf) 合并路径
# ============================================================================

def merge_glyf_fonts(fonts: List[TTFont]) -> TTFont:
    """
    以第一个字体为模板，合并所有子集的字形数据（TrueType 轮廓）。

    合并策略：
    1. 第一个字体作为基础模板（保留表结构、度量信息）
    2. 后续字体的字形逐个追加到基础字体
    3. 更新 maxp / hhea / vhea 的度量计数
    4. 合并 cmap 表（字符到字形的映射）
    """
    base_font = fonts[0]
    glyph_order = list(base_font.getGlyphOrder())

    # 已有字形名称集合，用于去重（相同字形名 = 相同字符，保留基础字体版本）
    existing_names: Set[str] = set(glyph_order)

    for font_idx, source_font in enumerate(fonts[1:], start=1):
        source_glyf = source_font.get("glyf")
        source_hmtx = source_font.get("hmtx")
        source_order = source_font.getGlyphOrder()
        source_cmap = source_font.getBestCmap() or {}

        if source_glyf is None:
            logging.warning(f"子集 #{font_idx + 1} 无 glyf 表，跳过")
            continue

        # cmap 值集合（字形名），用于判断空字形是否被映射（空格字形等需保留）
        mapped_names = set(source_cmap.values())

        new_glyph_count = 0
        for glyph_name in source_order:
            if glyph_name in existing_names:
                continue
            if glyph_name.startswith(".notdef"):
                continue
            glyph_obj = source_glyf[glyph_name]
            if glyph_obj is None:
                continue
            # 跳过无轮廓且无 cmap 映射的空字形
            if glyph_obj.numberOfContours == 0 and not getattr(glyph_obj, "components", []):
                if glyph_name not in mapped_names:
                    continue

            # 追加字形
            base_font["glyf"][glyph_name] = glyph_obj
            glyph_order.append(glyph_name)
            existing_names.add(glyph_name)

            # 复制 hmtx 度量
            if source_hmtx and glyph_name in source_hmtx.metrics:
                base_font["hmtx"][glyph_name] = source_hmtx.metrics[glyph_name]

            # 复制 vmtx 度量（若两方均有垂直度量）
            source_vmtx = source_font.get("vmtx")
            base_vmtx = base_font.get("vmtx")
            if source_vmtx and base_vmtx and glyph_name in source_vmtx.metrics:
                base_vmtx[glyph_name] = source_vmtx.metrics[glyph_name]

            new_glyph_count += 1

        logging.info(f"  从子集 #{font_idx + 1} 添加 {new_glyph_count} 个新字形")

    base_font.setGlyphOrder(glyph_order)
    logging.info(f"合并后字形总数: {len(glyph_order)}")

    # 合并 cmap 表（内部基于最终 glyphOrder 构建字形索引）
    merge_cmap_tables(base_font, fonts)

    return base_font

# ============================================================================
# CFF (OTTO) 合并路径
# ============================================================================

def merge_cff_fonts(fonts: List[TTFont]) -> TTFont:
    """
    合并 CFF（PostScript 轮廓）字体子集。

    关键步骤：
    1. 对所有源字体执行 desubroutinize——展开所有子例程引用，
       否则复制到其他字体后 CharString 的子例程编号会失效
    2. 以第一个字体为基础，按 glyph name 追加后续字体的 CharStrings
    3. 同步 charset（GID 顺序）与 glyphOrder
    4. 合并 cmap / hmtx / vmtx

    注意：GSUB/GPOS/GDEF 等布局表仅保留基础字体的版本，
    合并后的字体在追加字形上可能缺少 kerning 等特性（不影响有效性）。
    """
    base_font = fonts[0]
    base_cff = base_font["CFF "].cff  # table_C_F_F_ 下的 CFFFontSet

    # CID-keyed CFF：单 FD 时可按 name-keyed 同样方式合并（无多 FD 依赖）；
    # FDSelect 会在追加字形后统一扩展。多 FD 的跨字体合并超出本工具范围。
    for font in fonts:
        cff_set = font["CFF "].cff
        top = cff_set[cff_set.fontNames[0]]
        fd_array = getattr(top, "FDArray", None)
        if fd_array is not None and len(fd_array) > 1:
            raise NotImplementedError(
                "检测到多 FD 的 CID-keyed CFF（FDArray 含多个 Font DICT），暂不支持合并。"
            )

    logging.info("对所有子集执行 CFF desubroutinize ...")
    for font in fonts:
        font["CFF "].cff.desubroutinize()

    base_top = base_cff[base_cff.fontNames[0]]
    base_charstrings = base_top.CharStrings
    # charset 是否包含 .notdef（fontTools 编译时按原样处理，需保持一致）
    charset_includes_notdef = bool(base_top.charset) and base_top.charset[0] == ".notdef"

    glyph_order = list(base_font.getGlyphOrder())
    existing_names: Set[str] = set(glyph_order)

    for font_idx, source_font in enumerate(fonts[1:], start=1):
        source_cff = source_font["CFF "].cff
        source_top = source_cff[source_cff.fontNames[0]]
        source_charstrings = source_top.CharStrings
        source_hmtx = source_font.get("hmtx")
        source_vmtx = source_font.get("vmtx")
        base_vmtx = base_font.get("vmtx")

        new_glyph_count = 0
        for glyph_name in source_font.getGlyphOrder():
            if glyph_name in existing_names or glyph_name.startswith(".notdef"):
                continue
            if glyph_name not in source_charstrings:
                continue
            charstring = copy.deepcopy(source_charstrings[glyph_name])

            # CharStrings.__setitem__ 只支持更新已有字形；
            # 新增字形需同时登记 name→索引 并向索引追加 CharString
            new_index = len(base_charstrings)
            base_charstrings.charStrings[glyph_name] = new_index
            base_charstrings.charStringsIndex.append(charstring)

            glyph_order.append(glyph_name)
            existing_names.add(glyph_name)

            if source_hmtx and glyph_name in source_hmtx.metrics:
                base_font["hmtx"][glyph_name] = source_hmtx.metrics[glyph_name]
            if source_vmtx and base_vmtx and glyph_name in source_vmtx.metrics:
                base_vmtx[glyph_name] = source_vmtx.metrics[glyph_name]

            new_glyph_count += 1

        logging.info(f"  从子集 #{font_idx + 1} 添加 {new_glyph_count} 个新字形")

    # 同步 glyphOrder 与 CFF charset（charset 决定 CFF 内部的 GID 顺序）
    base_font.setGlyphOrder(glyph_order)
    base_top.charset = list(glyph_order) if charset_includes_notdef else glyph_order[1:]
    logging.info(f"合并后字形总数: {len(glyph_order)}")

    # 扩展 FDSelect（CID-keyed 单 FD）：追加的字形全部映射到 FD 0
    fd_select = getattr(base_top, "FDSelect", None)
    if fd_select is not None:
        gid_array = getattr(fd_select, "gidArray", None)
        if gid_array is not None:
            while len(gid_array) < len(glyph_order):
                gid_array.append(0)
            logging.info(f"FDSelect 已扩展至 {len(gid_array)} 个字形 (单 FD, 全部映射 FD 0)")

    if base_font.get("maxp") is not None:
        base_font["maxp"].numGlyphs = len(glyph_order)

    merge_cmap_tables(base_font, fonts)

    return base_font

# ============================================================================
# cmap 合并（两种轮廓路径共用）
# ============================================================================

def merge_cmap_tables(base_font: TTFont, all_fonts: List[TTFont]):
    """
    合并所有 cmap 表。

    策略：收集所有字符码点 → 字形名称的映射，统一写入基础字体。
    确保 format 4（BMP 字符）和 format 12（SMP 字符）均被覆盖。
    字形索引映射基于合并完成后的最终 glyphOrder 构建。
    """
    glyph_order = base_font.getGlyphOrder()
    valid_names = set(glyph_order)

    unified_map: Dict[int, str] = {}
    for font in all_fonts:
        best_cmap = font.getBestCmap()
        if best_cmap is None:
            continue
        for code_point, glyph_name in best_cmap.items():
            if code_point in unified_map:
                # 同一码点有多个映射时，保留初值（基础字体优先）
                continue
            unified_map[code_point] = glyph_name

    final_cmap: Dict[int, str] = {}
    skipped = 0
    for cp, gname in sorted(unified_map.items()):
        if gname in valid_names:
            final_cmap[cp] = gname
        else:
            skipped += 1
            logging.debug(f"  cmap 跳过: U+{cp:04X} → {gname} (字形不存在)")

    if skipped > 0:
        logging.warning(f"cmap 合并中有 {skipped} 个映射因字形不存在而被跳过")

    # 重建 cmap 表：创建一个全新干净的 cmap 表
    new_cmap = table__c_m_a_p()
    new_cmap.tableVersion = 0
    new_cmap.tables = []

    make_format4_subtable(new_cmap, final_cmap)
    make_format12_subtable(new_cmap, final_cmap)

    base_font["cmap"] = new_cmap
    logging.info(f"cmap 合并完成: format 4 ({len([c for c in final_cmap if c <= 0xFFFF])} 映射) "
                 f"+ format 12 ({len([c for c in final_cmap if c > 0xFFFF])} 映射)")


def make_format4_subtable(cmap_table, unified: Dict[int, str]):
    """构建 cmap format 4 子表（BMP 字符映射，Windows 平台 Unicode BMP 编码）"""
    bmp = {cp: gn for cp, gn in unified.items() if cp <= 0xFFFF}
    if not bmp:
        return

    subtable = CmapSubtable.newSubtable(4)
    subtable.platformID = 3  # Windows
    subtable.platEncID = 1   # Unicode BMP — 非符号字体必须为 1，0 是 Symbol 编码
    subtable.format = 4
    subtable.language = 0    # newSubtable 默认 None，不设置则编译时 struct.pack 崩溃
    subtable.cmap = bmp
    cmap_table.tables.append(subtable)


def make_format12_subtable(cmap_table, unified: Dict[int, str]):
    """构建 cmap format 12 子表（SMP 字符映射，Windows 平台完整 Unicode 编码）"""
    smp = {cp: gn for cp, gn in unified.items() if cp > 0xFFFF}
    if not smp:
        return

    subtable = CmapSubtable.newSubtable(12)
    subtable.platformID = 3  # Windows
    subtable.platEncID = 10  # Unicode 全集
    subtable.format = 12
    subtable.language = 0
    subtable.cmap = smp
    cmap_table.tables.append(subtable)


def update_metric_counts(base_font: TTFont):
    """
    更新字形计数相关字段。

    - maxp.numGlyphs：字形总数
    - hhea.numberOfHMetrics / vhea.numberOfVMetrics：度量长表长度。
      fontTools 保存 hmtx/vmtx 时只写入 numberOfHMetrics 个 (advanceWidth, lsb)，
      其余字形的 advance 会被压缩为最后一个长表项的值——
      若不更新为字形总数，追加字形在渲染时 advance 会全部错误。
    """
    glyph_count = len(base_font.getGlyphOrder())

    maxp = base_font.get("maxp")
    if maxp is not None:
        maxp.numGlyphs = glyph_count
        logging.info(f"maxp.numGlyphs = {glyph_count}")

    hhea = base_font.get("hhea")
    if hhea is not None and "hmtx" in base_font:
        hhea.numberOfHMetrics = glyph_count
        logging.info(f"hhea.numberOfHMetrics = {glyph_count}")

    vhea = base_font.get("vhea")
    if vhea is not None and "vmtx" in base_font:
        vhea.numberOfVMetrics = glyph_count
        logging.info(f"vhea.numberOfVMetrics = {glyph_count}")

# ============================================================================
# 保存
# ============================================================================

def ensure_required_names(font: TTFont) -> int:
    """
    确保合并产物的 name 表含 Windows 平台必需记录 (nameID 1/2/3/4/6)。

    GDI 注册字体要求这些记录存在，缺失时静默失败（字体查看器报
    "不是有效的字体文件"）。网络子集常缺 nameID 3（唯一标识），
    从现有记录合成补齐。

    Returns:
        补充的记录数
    """
    name_table = font.get("name")
    if name_table is None:
        return 0

    def find(name_id: int):
        for record in name_table.names:
            if record.nameID == name_id:
                try:
                    return record.toUnicode()
                except Exception:
                    return None
        return None

    family = find(1) or "Unknown Family"
    subfamily = find(2) or "Regular"
    full_name = find(4) or (family if subfamily == "Regular" else f"{family} {subfamily}")
    ps_name = find(6) or re.sub(r"[^A-Za-z0-9-]", "", full_name)[:63] or "Font"

    added = 0

    def add_if_missing(name_id: int, value: str):
        nonlocal added
        if find(name_id) is None:
            name_table.setName(value, nameID=name_id, platformID=3,
                               platEncID=1, langID=0x409)
            logging.info(f"补充 name 记录 nameID={name_id}: '{value}'")
            added += 1

    add_if_missing(3, f"{ps_name};{full_name}")
    add_if_missing(4, full_name)
    add_if_missing(6, ps_name)
    add_if_missing(5, "Version 1.000")
    return added


def _proper_extension(font: TTFont) -> str:
    """根据 sfnt 版本返回正确的文件扩展名"""
    return ".otf" if font.sfntVersion == "OTTO" else ".ttf"


def save_font(font: TTFont, output_path: str, font_name: Optional[str] = None) -> str:
    """
    保存合并后的字体。

    - 如果加载时字体 flavor 为 'woff'/'woff2'，保存前需将 flavor 设为 None
      以输出纯 sfnt 格式（而非再次封装为 Web 字体）
    - CFF (OTTO) 字体自动改用 .otf 扩展名
    - 如果指定 font_name，更新 name 表中的 Family Name

    Returns:
        实际写入的文件路径
    """
    # 移除 Web 字体封装标记
    if font.flavor in ("woff", "woff2"):
        flavor = font.flavor
        font.flavor = None
        logging.info(f"已移除 {flavor.upper()} flavor 标记，将保存为纯 sfnt 格式")

    # CFF 字体应为 .otf 扩展名
    root, ext = os.path.splitext(output_path)
    proper_ext = _proper_extension(font)
    if ext and ext != proper_ext and ext.lower() in (".ttf", ".otf"):
        output_path = root + proper_ext
        logging.info(f"输出扩展名调整为 {proper_ext}（sfntVersion={font.sfntVersion!r}）")
    elif not ext:
        output_path = output_path + proper_ext

    # 可选：设置字体家族名称
    if font_name:
        name_table = font["name"]
        name_table.setName(font_name, nameID=1, platformID=3, platEncID=1, langID=0x409)
        name_table.setName(font_name, nameID=16, platformID=3, platEncID=1, langID=0x409)
        logging.info(f"字体家族名称设置为: {font_name}")

    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    font.save(output_path)
    file_size = os.path.getsize(output_path)
    logging.info(f"字体已保存至: {output_path} ({file_size:,} bytes)")
    return output_path

# ============================================================================
# 高层入口（供 CLI 与 font_toolkit.py 调用）
# ============================================================================

def merge_fonts(input_dir: str, output_path: str, font_name: Optional[str] = None) -> str:
    """
    完整合并流程：收集 → 加载 → 合并 → 更新计数 → 保存。

    Returns:
        实际写入的文件路径
    """
    font_paths = collect_font_files(input_dir)
    fonts = load_fonts(font_paths)

    outlines = "CFF (OTTO)" if is_cff_font(fonts[0]) else "TrueType (glyf)"
    logging.info(f"基础字体轮廓格式: {outlines}")

    if is_cff_font(fonts[0]):
        merged_font = merge_cff_fonts(fonts)
    else:
        merged_font = merge_glyf_fonts(fonts)

    update_metric_counts(merged_font)
    ensure_required_names(merged_font)
    saved_path = save_font(merged_font, output_path, font_name)

    for font in fonts:
        try:
            font.close()
        except Exception:
            pass
    return saved_path

# ============================================================================
# 命令行入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="WOFF/WOFF2 子集文件合并工具 - 将多个字体分片合并为单一 TTF/OTF 字体",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python merge_woff2_fonts.py --input-dir ./woff2_parts/ --output ./merged.ttf
  python merge_woff2_fonts.py --input-dir ./woff2_parts/ --output ./merged.ttf --font-name "MyCustomFont"
  python merge_woff2_fonts.py --input-dir ./woff2_parts/ --output ./merged.ttf --verbose

支持输入: .woff2 / .woff / .ttf / .otf（TrueType 与 CFF 轮廓均可）
        """,
    )
    parser.add_argument(
        "--input-dir", required=True,
        help="包含字体子集文件的目录路径",
    )
    parser.add_argument(
        "--output", required=True,
        help="合并后字体的输出路径（.ttf/.otf 扩展名会按轮廓格式自动校正）",
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

    logging.info("=== 字体子集合并工具 ===")

    try:
        merge_fonts(args.input_dir, args.output, args.font_name)
        logging.info("=== 合并完成 ===")
    except Exception as e:
        logging.error(f"合并失败: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
