#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTF 字体修复工具
=================
根据诊断结果或已知问题模式，自动修复 TTF 字体的常见问题。

修复策略（按优先级排序）：
1. WOFF2 封装移除 → font.flavor = None
2. cmap 表重建 → 确保 format 4 + format 12 子表均存在且格式正确
3. OS/2 表规范化 → 修正 usWidthClass、fsSelection、usWeightClass
4. head 表修正 → 修正 flags、macStyle
5. name 表 → 修正 Subfamily 名称以匹配字重
6. fontTools 重新编译 → 修正校验和与偏移量

用法：
    python fix_font.py --input broken.ttf --output fixed.ttf
    python fix_font.py --input broken.ttf --output fixed.ttf --weight 700
    python fix_font.py --input broken.ttf --output fixed.ttf --verbose

依赖：
    pip install fonttools
"""

import argparse
import logging
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable, table__c_m_a_p
from fontTools.ttLib.tables.O_S_2f_2 import table_O_S_2f_2


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
# 字重映射
# ============================================================================

WEIGHT_NAMES = {
    100: "Thin",
    200: "ExtraLight",
    300: "Light",
    400: "Regular",
    500: "Medium",
    600: "SemiBold",
    700: "Bold",
    800: "ExtraBold",
    900: "Black",
}

FSSELECTION_BOLD_BIT = 0x0020   # bit 6: Bold
FSSELECTION_ITALIC_BIT = 0x0001  # bit 0: Italic
FSSELECTION_REGULAR_BIT = 0x0040  # bit 6 (1-indexed): Regular


# ============================================================================
# 修复操作
# ============================================================================

def fix_flavor_woff2(font: TTFont) -> bool:
    """
    移除 WOFF/WOFF2 封装标记。

    当字体从 Web 子集合并后，TTFont 对象可能仍带有 flavor='woff'/'woff2'。
    这会阻止字体在非浏览器环境中被正确解析。设置为 None 即可解包。

    Returns:
        True 如果执行了修复
    """
    if font.flavor in ("woff", "woff2"):
        logging.info(f"  [FIX] {font.flavor.upper()} flavor 已移除 → 字体将保存为纯 sfnt 格式")
        font.flavor = None
        return True
    logging.debug("  [SKIP] flavor 正常，无需修复")
    return False


def fix_cmap(font: TTFont) -> bool:
    """
    重建 cmap 表，确保 format 4 和 format 12 子表均存在且格式正确。
    
    步骤：
    1. 获取当前所有字符→字形映射
    2. 区分 BMP 字符 (≤U+FFFF) 和 SMP 字符 (>U+FFFF)
    3. 重建 format 4 子表（platformID=3, platEncID=1）
    4. 如存在 SMP 字符，重建 format 12 子表（platformID=3, platEncID=10）
    
    目的：Windows GDI 渲染器要求 format 4 子表必须存在，
    DirectWrite 在特定场景也需要 format 12 来处理扩展字符。
    
    Returns:
        True 如果执行了重建
    """
    cmap_table = font.get("cmap")
    if cmap_table is None:
        logging.info("  [FIX] cmap 表缺失，从零构建")
        font["cmap"] = table__c_m_a_p()
        cmap_table = font["cmap"]

    # 收集所有字符映射
    unified: Dict[int, str] = {}
    for subtable in cmap_table.tables:
        if hasattr(subtable, "cmap"):
            for cp, name in subtable.cmap.items():
                if cp not in unified:
                    unified[cp] = name

    if not unified:
        logging.warning("  [SKIP] 无 cmap 映射数据，无法重建")
        return False

    # 检查是否需要重建
    has_f4 = any(t.format == 4 and t.platformID == 3 and t.platEncID == 1
                 for t in cmap_table.tables)
    has_f12 = any(t.format == 12 and t.platformID == 3 and t.platEncID == 10
                  for t in cmap_table.tables)
    has_smp = any(cp > 0xFFFF for cp in unified)

    needs_rebuild = not has_f4 or (has_smp and not has_f12)

    if not needs_rebuild:
        logging.debug("  [SKIP] cmap 表结构完整，无需重建")
        return False

    # 重建 cmap 表
    new_cmap = table__c_m_a_p()
    new_cmap.tableVersion = 0
    new_cmap.tables = []

    # Format 4: BMP 字符
    bmp = {cp: gn for cp, gn in unified.items() if cp <= 0xFFFF}
    if bmp:
        sub4 = CmapSubtable.newSubtable(4)
        sub4.platformID = 3
        sub4.platEncID = 1
        sub4.format = 4
        sub4.language = 0  # newSubtable 默认 None，不设置则保存时崩溃
        sub4.cmap = bmp
        new_cmap.tables.append(sub4)
        logging.info(f"  [FIX] cmap format 4 重建: {len(bmp)} 个 BMP 字符映射")

    # Format 12: SMP 字符
    smp = {cp: gn for cp, gn in unified.items() if cp > 0xFFFF}
    if smp:
        sub12 = CmapSubtable.newSubtable(12)
        sub12.platformID = 3
        sub12.platEncID = 10
        sub12.format = 12
        sub12.language = 0
        sub12.cmap = smp
        new_cmap.tables.append(sub12)
        logging.info(f"  [FIX] cmap format 12 重建: {len(smp)} 个 SMP 字符映射")

    font["cmap"] = new_cmap
    return True


def fix_os2(font: TTFont, weight: int, width: int) -> bool:
    """
    规范化 OS/2 表。
    
    修正项目：
    - usWidthClass: 设为 5 (Medium) 除非指定其他值
    - fsSelection: 根据字重设置 Bold/Regular 位
    - usWeightClass: 设为指定字重
    
    Args:
        weight: 目标字重 (100-900)
        width: 目标宽度 (1-9, 默认 5)
    
    Returns:
        True 如果执行了修改
    """
    os2 = font.get("OS/2")
    if os2 is None:
        logging.info("  [FIX] OS/2 表缺失，创建新表")
        font["OS/2"] = table_O_S_2f_2()
        os2 = font["OS/2"]

    modified = False

    # 修正 usWidthClass
    if os2.usWidthClass != width:
        old = os2.usWidthClass
        os2.usWidthClass = width
        logging.info(f"  [FIX] OS/2.usWidthClass: {old} → {width}")
        modified = True

    # 修正 usWeightClass
    if os2.usWeightClass != weight:
        old = os2.usWeightClass
        os2.usWeightClass = weight
        logging.info(f"  [FIX] OS/2.usWeightClass: {old} → {weight} ({WEIGHT_NAMES.get(weight, 'Unknown')})")
        modified = True

    # 修正 fsSelection
    old_fs = os2.fsSelection
    new_fs = old_fs

    # 清除 Bold 和 Regular 位
    new_fs &= ~FSSELECTION_BOLD_BIT
    new_fs &= ~FSSELECTION_REGULAR_BIT

    if weight >= 700:
        # 粗体：设置 Bold 位
        new_fs |= FSSELECTION_BOLD_BIT
    elif weight >= 600:
        # SemiBold：设置 Bold 位以兼容 Windows GDI 分组
        new_fs |= FSSELECTION_BOLD_BIT
    else:
        # 普通字重：设置 Regular 位
        new_fs |= FSSELECTION_REGULAR_BIT

    if new_fs != old_fs:
        os2.fsSelection = new_fs
        logging.info(f"  [FIX] OS/2.fsSelection: 0x{old_fs:04X} → 0x{new_fs:04X} "
                     f"(Bold={'Y' if new_fs & FSSELECTION_BOLD_BIT else 'N'}, "
                     f"Regular={'Y' if new_fs & FSSELECTION_REGULAR_BIT else 'N'})")
        modified = True

    return modified


def fix_head(font: TTFont) -> bool:
    """
    修正 head 表关键字段。
    
    - flags: 确保 bit 0 (基线在 y=0) 设置
    - magicNumber: 修正为标准值 0x5F0F3CF5
    - macStyle: 与 OS/2.fsSelection 对齐
    
    Returns:
        True 如果执行了修改
    """
    head = font.get("head")
    if head is None:
        logging.warning("  [SKIP] head 表缺失，无法修复")
        return False

    modified = False

    # 修正 magicNumber
    if head.magicNumber != 0x5F0F3CF5:
        old = head.magicNumber
        head.magicNumber = 0x5F0F3CF5
        logging.info(f"  [FIX] head.magicNumber: 0x{old:08X} → 0x5F0F3CF5")
        modified = True

    # 修正 flags
    flags = head.flags
    if flags & 0x0001 == 0:
        head.flags = flags | 0x0001
        logging.info(f"  [FIX] head.flags: 0x{flags:04X} → 0x{head.flags:04X} (设置 bit 0)")
        modified = True

    # 修正 macStyle：与 OS/2.fsSelection 的 Italic 位对齐
    os2 = font.get("OS/2")
    if os2:
        is_italic_os2 = bool(os2.fsSelection & FSSELECTION_ITALIC_BIT)
        mac_style = head.macStyle
        is_italic_mac = bool(mac_style & 0x02)

        if is_italic_os2 and not is_italic_mac:
            head.macStyle = mac_style | 0x02
            logging.info(f"  [FIX] head.macStyle: 0x{mac_style:04X} → 0x{head.macStyle:04X} (设置 Italic 位以匹配 OS/2)")
            modified = True
        elif not is_italic_os2 and is_italic_mac:
            head.macStyle = mac_style & ~0x02
            logging.info(f"  [FIX] head.macStyle: 0x{mac_style:04X} → 0x{head.macStyle:04X} (清除 Italic 位以匹配 OS/2)")
            modified = True

    return modified


def fix_name(font: TTFont, weight: int) -> bool:
    """
    修正 name 表 Subfamily 名称以匹配字重。
    
    Subfamily 命名规范（Windows）：
    - Regular: "Regular"
    - Bold: "Bold"
    - Italic: "Italic"
    - Bold Italic: "Bold Italic"
    - 其他字重用数字后缀，如 "Light"、"ExtraBold"
    
    Args:
        weight: 字重值
    Returns:
        True 如果执行了修改
    """
    name_table = font.get("name")
    os2 = font.get("OS/2")
    if name_table is None or os2 is None:
        return False

    is_italic = bool(os2.fsSelection & FSSELECTION_ITALIC_BIT)
    expected_subfamily = WEIGHT_NAMES.get(weight, str(weight))
    if is_italic:
        if weight >= 600:
            expected_subfamily = f"{expected_subfamily} Italic"
        else:
            expected_subfamily = f"Italic" if weight == 400 else f"{expected_subfamily} Italic"

    # 检查当前 Subfamily
    modified = False
    for record in name_table.names:
        if record.nameID == 2 and record.platformID == 3:
            current = record.toUnicode()
            if current != expected_subfamily:
                record.string = expected_subfamily
                logging.info(f"  [FIX] name Subfamily: '{current}' → '{expected_subfamily}'")
                modified = True
            break

    return modified


def ensure_required_names(font: TTFont) -> int:
    """
    确保 name 表包含 Windows 平台的必需记录 (nameID 1/2/3/4/6)。

    GDI 注册字体（AddFontResource / 字体查看器 / 安装流程）要求 name 表
    存在这些记录，缺失时静默失败，表现为"不是有效的字体文件"。
    网络字体子集常只含 1/2/4/6 而缺 3（唯一标识），这是本工具链
    产出无法安装的最常见原因。缺失的记录从现有字段合成。

    Returns:
        补充的记录数
    """
    name_table = font.get("name")
    if name_table is None:
        return 0

    def find(name_id: int) -> Optional[str]:
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
            logging.info(f"  [FIX] name 表补充 nameID={name_id}: '{value}'")
            added += 1

    add_if_missing(3, f"{ps_name};{full_name}")
    add_if_missing(4, full_name)
    add_if_missing(6, ps_name)
    add_if_missing(5, "Version 1.000")
    return added


def fix_recalc_tables(font: TTFont) -> bool:
    """
    触发 fontTools 重新计算所有校验和和表偏移量。
    
    虽然此函数标记 modified=False（因为实际写入在 save 时发生），
    但它确保字体保存时所有二进制结构都是正确的。
    """
    # fontTools 在 save 时会自动重新计算校验和
    # 这里显式说明以保持修复日志的完整性
    logging.info("  [INFO] 保存时 fontTools 将自动重新计算所有表校验和与偏移量")
    return False  # 不标记为修改（修改在 save 时发生）


def infer_weight_from_font(font: TTFont) -> int:
    """从现有 name 表推断字重值"""
    name = font.get("name")
    os2 = font.get("OS/2")
    
    # 优先使用 OS/2 中的值
    if os2 and os2.usWeightClass in range(100, 1000, 100):
        return os2.usWeightClass

    # 从 Subfamily name 推断
    if name:
        for record in name.names:
            if record.nameID == 2:
                sfname = record.toUnicode().lower()
                keywords = {
                    "thin": 100, "hairline": 100,
                    "extralight": 200, "ultralight": 200,
                    "light": 300,
                    "regular": 400, "normal": 400, "": 400,
                    "medium": 500,
                    "semibold": 600, "demibold": 600,
                    "bold": 700,
                    "extrabold": 800, "ultrabold": 800,
                    "black": 900, "heavy": 900,
                }
                for kw, w in keywords.items():
                    if kw in sfname:
                        return w
                break

    # 默认 Regular
    return 400


# ============================================================================
# 主修复流程
# ============================================================================

def fix_font(
    input_path: str,
    output_path: str,
    weight: Optional[int] = None,
    width: int = 5,
) -> int:
    """
    对字体文件执行全量修复。
    
    修复流程：
    1. 移除 WOFF2 封装标记
    2. 重建 cmap 表
    3. 规范化 OS/2 表
    4. 修正 head 表
    5. 修正 name 表
    6. 保存（触发校验和重算）
    
    Args:
        input_path: 输入字体路径
        output_path: 输出字体路径
        weight: 目标字重（None=从字体推断）
        width: 目标宽度等级（1-9，默认 5）
    
    Returns:
        修复项数量
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"字体文件不存在: {input_path}")

    logging.info(f"开始修复: {input_path}")
    font = TTFont(input_path)

    try:
        # 推断字重
        if weight is None:
            weight = infer_weight_from_font(font)
            logging.info(f"从字体推断字重: {weight} ({WEIGHT_NAMES.get(weight, 'Unknown')})")

        # 验证参数
        if weight not in range(100, 1000, 100):
            logging.warning(f"字重 {weight} 不是标准值。标准值为 100/200/.../900，将继续使用。")
        if width not in range(1, 10):
            raise ValueError(f"宽度 {width} 超出范围 [1-9]")

        # 执行修复步骤
        fix_count = 0

        # 步骤1: flavor 解包
        if fix_flavor_woff2(font):
            fix_count += 1

        # 步骤2: cmap 重建
        if fix_cmap(font):
            fix_count += 1

        # 步骤3: OS/2 规范化
        if fix_os2(font, weight, width):
            fix_count += 1

        # 步骤4: head 修正
        if fix_head(font):
            fix_count += 1

        # 步骤5: name 修正
        if fix_name(font, weight):
            fix_count += 1

        # 步骤5.5: 补齐 GDI 必需的 name 记录 (nameID 1/2/3/4/6)
        fix_count += ensure_required_names(font)

        # 步骤6: 重新编译（校验和重算在 save 时自动触发）
        fix_recalc_tables(font)

        # 保存
        font.save(output_path)
        file_size = os.path.getsize(output_path)
        logging.info(f"修复完成: {output_path} ({file_size:,} bytes)")
        logging.info(f"共执行 {fix_count} 项修复")

    except Exception as e:
        logging.error(f"修复过程中出现错误: {e}", exc_info=True)
        raise
    finally:
        font.close()

    return fix_count


# ============================================================================
# 命令行入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="TTF 字体修复工具 - 自动修复常见字体问题",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python fix_font.py --input broken.ttf --output fixed.ttf
  python fix_font.py --input broken.ttf --output fixed.ttf --weight 700
  python fix_font.py --input broken.ttf --output fixed.ttf --width 3 --verbose

修复项目:
  1. WOFF2 封装移除 → 解包为纯 TTF
  2. cmap 表重建 → 确保 format 4 + format 12 子表完整
  3. OS/2 表规范化 → 修正 usWidthClass / fsSelection / usWeightClass
  4. head 表修正 → 修正 flags / magicNumber / macStyle
  5. name 表修正 → Subfamily 名称匹配字重
  6. 保存时自动重新计算校验和与偏移量
        """,
    )
    parser.add_argument(
        "--input", required=True,
        help="待修复的 TTF 字体文件路径",
    )
    parser.add_argument(
        "--output", required=True,
        help="修复后字体的输出路径",
    )
    parser.add_argument(
        "--weight", type=int, choices=range(100, 1000, 100), default=None,
        help="目标字重 (100/200/300/400/500/600/700/800/900)。不指定则从字体推断。",
    )
    parser.add_argument(
        "--width", type=int, choices=range(1, 10), default=5,
        help="目标宽度等级 (1=UltraCondensed ... 5=Medium ... 9=UltraExpanded)，默认 5",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="启用详细日志输出",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    logging.info("=== TTF 字体修复工具 ===")

    try:
        fix_count = fix_font(
            input_path=args.input,
            output_path=args.output,
            weight=args.weight,
            width=args.width,
        )
        logging.info(f"=== 修复完成 ({fix_count} 项) ===")

    except Exception as e:
        logging.error(f"修复失败: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
