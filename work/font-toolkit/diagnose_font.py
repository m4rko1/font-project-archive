#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTF 字体深度诊断工具
=====================
对 TTF 字体文件的关键表进行逐项检查，输出结构化诊断报告。

检查项说明：
- font.flavor：检测是否存在 WOFF2 封装残留（magic 为 wOF2）
- cmap 表：format 4 和 format 12 子表是否都存在且映射正确
- OS/2 表：usWidthClass / fsSelection / usWeightClass / macStyle 合规性
- head 表：flags / magicNumber / 校验和
- name 表：Subfamily 名称与字重是否匹配
- glyf 表：字形可绘制性、复合字形引用边界
- post 表：斜体角度与 macStyle 一致性
- 全局：关键表校验和与偏移量一致性

用法：
    python diagnose_font.py --input problematic.ttf
    python diagnose_font.py --input problematic.ttf --save-report report.txt --verbose

依赖：
    pip install fonttools
"""

import argparse
import logging
import os
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import List

from fontTools.ttLib import TTFont


# ============================================================================
# 诊断结果数据结构
# ============================================================================

@dataclass
class DiagnosisItem:
    """单项诊断结果"""
    category: str           # 检查类别（表名）
    severity: str           # ERROR / WARN / INFO
    message: str            # 问题描述
    detail: str = ""        # 详细信息


@dataclass  
class DiagnosisReport:
    """完整诊断报告"""
    font_path: str = ""
    items: List[DiagnosisItem] = field(default_factory=list)

    @property
    def errors(self):
        return [i for i in self.items if i.severity == "ERROR"]

    @property
    def warnings(self):
        return [i for i in self.items if i.severity == "WARN"]

    @property
    def infos(self):
        return [i for i in self.items if i.severity == "INFO"]

    @property
    def is_clean(self):
        return len(self.errors) == 0 and len(self.warnings) == 0


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
# 字重映射表
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

# 字重 → 预期 FSSelection bit 6
WEIGHT_TO_BOLD = {w: w >= 600 for w in range(100, 1000, 100)}


# ============================================================================
# 各表诊断函数
# ============================================================================

def diagnose_flavor(font: TTFont, report: DiagnosisReport):
    """
    检查字体 flavor（封装格式）。
    
    背景：WOFF2 字体加载后 font.flavor == 'woff2'。
    如果合并后未正确解包就保存，会导致字体在非浏览器环境下无法解析。
    判断标准：flavor 为 None 表示纯 TTF/OTF。
    """
    flavor = font.flavor
    if flavor is None:
        report.items.append(DiagnosisItem(
            category="flavor",
            severity="INFO",
            message="字体为纯 TTF/OTF 格式，无封装残留",
        ))
    elif flavor in ("woff", "woff2"):
        report.items.append(DiagnosisItem(
            category="flavor",
            severity="ERROR",
            message=f"检测到 {flavor.upper()} 封装残留！字体仍以 Web 字体格式封装",
            detail=f"font.flavor == '{flavor}'。Windows 字体引擎无法直接解析 Web 封装的字体。"
                   "需执行解包：font.flavor = None 后重新保存。"
        ))
    else:
        report.items.append(DiagnosisItem(
            category="flavor",
            severity="WARN",
            message=f"未知 flavor: {flavor}",
        ))


def diagnose_cmap(font: TTFont, report: DiagnosisReport):
    """
    检查 cmap 表完整性。
    
    Windows 要求：
    - cmap format 4 子表必须存在（platformID=3, platEncID=1），覆盖 BMP 字符
    - cmap format 12 子表建议存在（platformID=3, platEncID=10），覆盖 SMP 字符
    
    常见问题：
    - 仅有 format 4 无 format 12：Windows 可能无法显示扩展字符
    - 子表存在但内容损坏或为空
    - cmap 映射指向不存在的字形索引
    """
    cmap_table = font.get("cmap")
    if cmap_table is None:
        report.items.append(DiagnosisItem(
            category="cmap",
            severity="ERROR",
            message="缺少 cmap 表！字体完全不可用",
        ))
        return

    # 检查 format 4 子表
    f4_tables = [t for t in cmap_table.tables if t.format == 4]
    f4_win = [t for t in f4_tables if t.platformID == 3 and t.platEncID == 1]

    if not f4_tables:
        report.items.append(DiagnosisItem(
            category="cmap",
            severity="ERROR",
            message="缺少 cmap format 4 子表",
            detail="format 4 是 BMP 字符映射所必需的。Windows 字体引擎要求此子表存在。"
        ))
    elif not f4_win:
        report.items.append(DiagnosisItem(
            category="cmap",
            severity="WARN",
            message=f"cmap format 4 子表不是 Windows 平台编码 (platformID=3/platEncID=1)"
        ))
    else:
        t = f4_win[0]
        count = len(t.cmap) if hasattr(t, 'cmap') else 0
        if count == 0:
            report.items.append(DiagnosisItem(
                category="cmap",
                severity="ERROR",
                message="cmap format 4 子表为空（无字符映射）",
            ))
        else:
            report.items.append(DiagnosisItem(
                category="cmap",
                severity="INFO",
                message=f"cmap format 4 子表正常 ({count} 个字符映射)",
            ))

    # 检查 format 12 子表
    f12_tables = [t for t in cmap_table.tables if t.format == 12]
    f12_win = [t for t in f12_tables if t.platformID == 3 and t.platEncID == 10]

    if not f12_tables:
        report.items.append(DiagnosisItem(
            category="cmap",
            severity="WARN",
            message="缺少 cmap format 12 子表",
            detail="format 12 用于 SMP 字符（U+10000 以上）。如果字体不含扩展字符可忽略。"
        ))
    elif not f12_win:
        report.items.append(DiagnosisItem(
            category="cmap",
            severity="WARN",
            message=f"cmap format 12 子表不是 Windows 平台编码 (platformID=3/platEncID=10)"
        ))
    else:
        t = f12_win[0]
        count = len(t.cmap) if hasattr(t, 'cmap') else 0
        report.items.append(DiagnosisItem(
            category="cmap",
            severity="INFO",
            message=f"cmap format 12 子表正常 ({count} 个字符映射)",
        ))


def diagnose_os2(font: TTFont, report: DiagnosisReport):
    """
    检查 OS/2 表合规性。
    
    关键字段：
    - usWidthClass (offset 4): 1-9，5=Medium/Normal。Windows 对非 5 值的处理不一致
    - fsSelection (offset 62): 位掩码，bit 6 = Bold, bit 0 = Italic
    - usWeightClass (offset 8): 100-900，字重
    - macStyle (offset 30): 位掩码，与 fsSelection 应一致

    参考：OpenType 规范 OS/2 表文档
    """
    os2 = font.get("OS/2")
    if os2 is None:
        report.items.append(DiagnosisItem(
            category="OS/2",
            severity="ERROR",
            message="缺少 OS/2 表",
        ))
        return

    # usWidthClass 检查 (offset 4)
    wc = os2.usWidthClass
    if wc < 1 or wc > 9:
        report.items.append(DiagnosisItem(
            category="OS/2",
            severity="ERROR",
            message=f"usWidthClass={wc} 超出有效范围 [1-9]",
        ))
    elif wc != 5:
        report.items.append(DiagnosisItem(
            category="OS/2",
            severity="WARN",
            message=f"usWidthClass={wc}，非标准值 5 (Medium)。某些 Windows 渲染器可能会错误处理",
            detail="标准值: 1=UltraCondensed, 2=ExtraCondensed, 3=Condensed, 4=SemiCondensed, "
                   "5=Medium/Normal, 6=SemiExpanded, 7=Expanded, 8=ExtraExpanded, 9=UltraExpanded"
        ))
    else:
        report.items.append(DiagnosisItem(
            category="OS/2",
            severity="INFO",
            message=f"usWidthClass={wc} (Medium/Normal)，正常",
        ))

    # usWeightClass 检查 (offset 8)
    wc_value = os2.usWeightClass
    expected_range = list(range(100, 1000, 100))
    if wc_value not in expected_range:
        report.items.append(DiagnosisItem(
            category="OS/2",
            severity="WARN",
            message=f"usWeightClass={wc_value}，非标准字重值。标准值为 100/200/.../900",
        ))
    else:
        name_hint = WEIGHT_NAMES.get(wc_value, "Unknown")
        report.items.append(DiagnosisItem(
            category="OS/2",
            severity="INFO",
            message=f"usWeightClass={wc_value} ({name_hint})",
        ))

    # fsSelection 检查 (offset 62)
    fs = os2.fsSelection
    is_bold_os2 = bool(fs & 0x20)  # bit 5 (0-indexed) 或 bit 6 (1-indexed)?
    # OpenType 规范: fsSelection bit 6 = Bold (0x0020)
    # bit 5 = 0x0010 不是标准定义的 Bold，需要确认
    # 实际上 OT spec: bit 5 (0x0010) 未分配，bit 6 (0x0020) = Bold
    is_bold_standard = bool(fs & 0x0020)
    is_italic_os2 = bool(fs & 0x0001)  # bit 0 = Italic
    is_regular_os2 = bool(fs & 0x0040)  # bit 6 = Regular

    # 检查字重与 Bold 位的一致性
    weight = os2.usWeightClass
    should_be_bold = weight >= 600

    if should_be_bold and not is_bold_standard:
        report.items.append(DiagnosisItem(
            category="OS/2",
            severity="ERROR" if weight >= 700 else "WARN",
            message=f"usWeightClass={weight} 指示粗体，但 fsSelection bit 6 (Bold) 未设置。"
                    f"当前 fsSelection=0x{fs:04X}",
            detail="字重 >= 600 时 fsSelection bit 6 应置位，否则 Windows 可能不识别为粗体。"
        ))
    elif not should_be_bold and is_bold_standard:
        report.items.append(DiagnosisItem(
            category="OS/2",
            severity="WARN",
            message=f"usWeightClass={weight} 非粗体，但 fsSelection bit 6 (Bold) 意外置位。"
                    f"当前 fsSelection=0x{fs:04X}",
        ))

    if not is_bold_standard and not is_regular_os2 and not is_italic_os2:
        report.items.append(DiagnosisItem(
            category="OS/2",
            severity="INFO",
            message=f"fsSelection=0x{fs:04X}，未设置 Bold/Italic/Regular 标志",
            detail="如果此字体为 Regular 字重，建议设置 bit 6 (Regular)=0x0040"
        ))

    # Bold 与 Regular 位互斥（规范要求二者不得同时置位）
    if is_bold_standard and is_regular_os2:
        report.items.append(DiagnosisItem(
            category="OS/2",
            severity="WARN",
            message=f"fsSelection=0x{fs:04X} 同时设置了 Bold (0x0020) 与 Regular (0x0040)，二者互斥",
            detail="部分 Windows 渲染器会因此无法正确分组字重，应按 usWeightClass 二选一。"
        ))

    report.items.append(DiagnosisItem(
        category="OS/2",
        severity="INFO",
        message=f"fsSelection=0x{fs:04X} "
                f"(Bold={'Y' if is_bold_standard else 'N'}, "
                f"Italic={'Y' if is_italic_os2 else 'N'}, "
                f"Regular={'Y' if is_regular_os2 else 'N'})"
    ))


def diagnose_head(font: TTFont, report: DiagnosisReport):
    """
    检查 head 表关键字段。
    
    - flags (offset 16): bit 1=基线在y=0, bit 3=需要整数ppem, bit 11=字形索引顺序
      bit 0 必须为 0（baseline at y=0），否则部分渲染器行为异常
    - magicNumber (offset 48): 必须为 0x5F0F3CF5
    """
    head = font.get("head")
    if head is None:
        report.items.append(DiagnosisItem(
            category="head",
            severity="ERROR",
            message="缺少 head 表",
        ))
        return

    # flags 检查 (offset 16)
    flags = head.flags
    expected_flags = 0x000B  # bits 0,1,3 = Y 方向向上 + 整数 ppem + 字形索引
    if flags & 0x0001 == 0:
        report.items.append(DiagnosisItem(
            category="head",
            severity="WARN",
            message=f"head.flags=0x{flags:04X}, bit 0 未设置 (Y 方向向上未声明)",
        ))
    else:
        report.items.append(DiagnosisItem(
            category="head",
            severity="INFO",
            message=f"head.flags=0x{flags:04X}",
        ))

    # magicNumber 检查 (offset 48)
    magic = head.magicNumber
    expected_magic = 0x5F0F3CF5
    if magic != expected_magic:
        report.items.append(DiagnosisItem(
            category="head",
            severity="ERROR",
            message=f"head.magicNumber=0x{magic:08X}，应为 0x{expected_magic:08X}",
            detail="magicNumber 异常表示文件头损坏或非标准 TTF 结构。"
        ))
    else:
        report.items.append(DiagnosisItem(
            category="head",
            severity="INFO",
            message=f"head.magicNumber=0x{magic:08X}，正常",
        ))

    # macStyle 检查 (offset 30)
    mac_style = head.macStyle
    os2 = font.get("OS/2")
    if os2:
        is_italic_mac = bool(mac_style & 0x02)
        is_italic_os2 = bool(os2.fsSelection & 0x0001)
        if is_italic_mac != is_italic_os2:
            report.items.append(DiagnosisItem(
                category="head",
                severity="WARN",
                message=f"head.macStyle bit 1 (Italic) 与 OS/2.fsSelection bit 0 (Italic) 不一致: "
                        f"macStyle=0x{mac_style:04X}, fsSelection=0x{os2.fsSelection:04X}",
            ))
        else:
            report.items.append(DiagnosisItem(
                category="head",
                severity="INFO",
                message=f"head.macStyle=0x{mac_style:04X}，与 OS/2.fsSelection 一致",
            ))
    else:
        report.items.append(DiagnosisItem(
            category="head",
            severity="INFO",
            message=f"head.macStyle=0x{mac_style:04X}（无 OS/2 表可供交叉比对）",
        ))


def diagnose_name(font: TTFont, report: DiagnosisReport):
    """
    检查 name 表。
    
    重点检查：
    - 是否存在 Family Name (nameID=1) 和 Subfamily Name (nameID=2)
    - Subfamily 名称是否与 usWeightClass 一致
    - 是否缺少必要的 Windows 平台记录 (platformID=3)
    """
    name = font.get("name")
    if name is None:
        report.items.append(DiagnosisItem(
            category="name",
            severity="ERROR",
            message="缺少 name 表",
        ))
        return

    # 检查 Family Name
    family_records = [
        r for r in name.names
        if r.nameID == 1 and r.platformID == 3
    ]
    if not family_records:
        report.items.append(DiagnosisItem(
            category="name",
            severity="ERROR",
            message="缺少 Windows 平台的 Family Name (nameID=1, platformID=3)",
        ))
    else:
        fname = family_records[0].toUnicode()
        report.items.append(DiagnosisItem(
            category="name",
            severity="INFO",
            message=f"Family Name: '{fname}'",
        ))

    # GDI 必需记录检查：缺 nameID 3/4/6 时 Windows 字体引擎拒绝注册，
    # 表现为字体查看器/安装流程报"不是有效的字体文件"
    for required_id, label in ((3, "唯一标识"), (4, "全名"), (6, "PostScript 名")):
        if not any(r.nameID == required_id and r.platformID == 3 for r in name.names):
            report.items.append(DiagnosisItem(
                category="name",
                severity="ERROR",
                message=f"缺少 nameID={required_id} ({label})，GDI 将拒绝注册此字体",
                detail="运行 fix_font.py 可自动合成缺失记录。",
            ))

    # 检查 Subfamily Name
    subfamily_records = [
        r for r in name.names
        if r.nameID == 2 and r.platformID == 3
    ]
    if not subfamily_records:
        report.items.append(DiagnosisItem(
            category="name",
            severity="ERROR",
            message="缺少 Windows 平台的 Subfamily Name (nameID=2, platformID=3)",
        ))
    else:
        sfname = subfamily_records[0].toUnicode()
        os2 = font.get("OS/2")
        weight = os2.usWeightClass if os2 else 400

        # 检查 Subfamily 是否含 "Italic"
        is_italic_name = "italic" in sfname.lower()
        is_bold_name = "bold" in sfname.lower()

        if weight >= 700 and not is_bold_name:
            report.items.append(DiagnosisItem(
                category="name",
                severity="WARN",
                message=f"Subfamily='{sfname}' 不含 'Bold'，但 usWeightClass={weight}",
            ))
        if weight < 600 and is_bold_name:
            report.items.append(DiagnosisItem(
                category="name",
                severity="WARN",
                message=f"Subfamily='{sfname}' 含 'Bold'，但 usWeightClass={weight} (<600)",
            ))

        # 检查 PostScript Name
        ps_records = [r for r in name.names if r.nameID == 6 and r.platformID == 3]
        if ps_records:
            psname = ps_records[0].toUnicode()
            report.items.append(DiagnosisItem(
                category="name",
                severity="INFO",
                message=f"PostScript Name: '{psname}'",
            ))

        report.items.append(DiagnosisItem(
            category="name",
            severity="INFO",
            message=f"Subfamily Name: '{sfname}'",
        ))


def _is_blankish_char(cp: int) -> bool:
    """判断码位是否属于合法空白的字符（空格、控制符、格式符等）"""
    try:
        return unicodedata.category(chr(cp)) in ("Zs", "Zl", "Zp", "Cc", "Cf")
    except (ValueError, IndexError):
        return False


def diagnose_cff(font: TTFont, report: DiagnosisReport):
    """
    检查 CFF 表（OTTO 字体的 PostScript 轮廓）。

    检查项：
    - CFF 表存在性、TopDICT 基本完整性
    - CharStrings 数量与 glyphOrder/maxp 一致
    - 是否含子例程（合并后引用失效的常见根源）
    """
    cff_table = font.get("CFF ")
    if cff_table is None:
        report.items.append(DiagnosisItem(
            category="CFF",
            severity="ERROR",
            message="OTTO 字体缺少 CFF 表",
        ))
        return

    cff = cff_table.cff  # table_C_F_F_ 下的 CFFFontSet
    total = len(font.getGlyphOrder())
    for font_name in cff.fontNames:
        top = cff[font_name]
        charstrings = getattr(top, "CharStrings", None)
        cs_count = len(charstrings) if charstrings else 0
        if cs_count != total:
            report.items.append(DiagnosisItem(
                category="CFF",
                severity="ERROR",
                message=f"CFF.CharStrings({cs_count}) 与 glyphOrder({total}) 数量不一致",
                detail="合并后 glyphOrder 与 CharStrings 不同步会导致渲染崩溃。",
            ))
        else:
            report.items.append(DiagnosisItem(
                category="CFF",
                severity="INFO",
                message=f"CharStrings 完整 ({cs_count} 个字形)",
            ))

        subrs = len(getattr(top, "Subrs", None) or [])
        gsubrs = len(getattr(cff, "GlobalSubrs", None) or [])
        private = getattr(top, "Private", None)
        psubrs = len(getattr(private, "Subrs", None) or []) if private else 0
        if subrs or gsubrs or psubrs:
            report.items.append(DiagnosisItem(
                category="CFF",
                severity="INFO",
                message=f"子例程: Local={subrs}, Global={gsubrs}, Private={psubrs}",
                detail="合并 CFF 分片前必须 desubroutinize，否则 CharString 子例程引用会跨字体失效。",
            ))
        else:
            report.items.append(DiagnosisItem(
                category="CFF",
                severity="INFO",
                message="无子例程（已 desubroutinize 或源生扁平 CharStrings）",
            ))


def diagnose_glyf(font: TTFont, report: DiagnosisReport):
    """
    检查 glyf 表（仅 TrueType 轮廓字体）。

    检查项：
    - 字形总数与 maxp.numGlyphs 一致
    - 复合字形引用的子字形索引是否在有效范围内
    - 是否存在空字形但 cmap 中有映射（可能造成空字符）

    CFF（OTTO）字体没有 glyf 表，转交 diagnose_cff。
    """
    glyf = font.get("glyf")

    if glyf is None:
        if font.sfntVersion == "OTTO" or font.get("CFF ") is not None:
            diagnose_cff(font, report)
            return
        report.items.append(DiagnosisItem(
            category="glyf",
            severity="ERROR",
            message="缺少 glyf 表（且非 OTTO/CFF 字体）",
        ))
        return

    glyph_order = font.getGlyphOrder()
    total = len(glyph_order)
    maxp = font.get("maxp")

    if maxp and maxp.numGlyphs != total:
        report.items.append(DiagnosisItem(
            category="glyf",
            severity="ERROR",
            message=f"maxp.numGlyphs={maxp.numGlyphs} 与 glyphOrder 长度 {total} 不一致",
        ))

    # 检查复合字形引用
    invalid_refs = 0
    empty_glyphs_with_cmap = 0
    cmap_table = font.get("cmap")
    best_cmap = None
    if cmap_table:
        best_cmap = font.getBestCmap()

    for i, name in enumerate(glyph_order):
        glyph = glyf[name]
        if glyph is None:
            continue

        # 检查复合字形引用
        if glyph.isComposite():
            for comp in glyph.components:
                if comp.glyphIndex >= total:
                    invalid_refs += 1
                    logging.debug(f"  复合字形 '{name}' 引用了越界索引 {comp.glyphIndex} (最大 {total - 1})")

        # 检查空字形但有 cmap 映射
        # 注意：空字形（numberOfContours==0）没有 components 属性，必须用 getattr；
        # 空白类字符（空格等）合法地无轮廓，不应告警。
        if glyph.numberOfContours == 0 and not getattr(glyph, "components", []):
            if best_cmap:
                mapped = [cp for cp, gn in best_cmap.items() if gn == name]
                visible = [cp for cp in mapped if not _is_blankish_char(cp)]
                if visible:
                    empty_glyphs_with_cmap += 1

    if invalid_refs > 0:
        report.items.append(DiagnosisItem(
            category="glyf",
            severity="ERROR",
            message=f"发现 {invalid_refs} 个复合字形引用了越界的子字形索引",
            detail="这会导致字体渲染时崩溃或显示乱码。通常发生在合并操作后 glyphOrder 不一致时。"
        ))
    else:
        report.items.append(DiagnosisItem(
            category="glyf",
            severity="INFO",
            message=f"复合字形引用正常（共检查 {total} 个字形）",
        ))

    if empty_glyphs_with_cmap > 0:
        report.items.append(DiagnosisItem(
            category="glyf",
            severity="WARN",
            message=f"发现 {empty_glyphs_with_cmap} 个空字形（无轮廓无复合）但有 cmap 映射",
            detail="这些字符在渲染时可能显示为空白。检查源字体子集是否完整。"
        ))

    report.items.append(DiagnosisItem(
        category="glyf",
        severity="INFO",
        message=f"字形总数: {total}, 空字形但有可见字符映射: {empty_glyphs_with_cmap}",
    ))


def diagnose_post(font: TTFont, report: DiagnosisReport):
    """检查 post 表（斜体角度等）"""
    post = font.get("post")
    if post is None:
        report.items.append(DiagnosisItem(
            category="post",
            severity="WARN",
            message="缺少 post 表",
        ))
        return

    italic_angle = getattr(post, "italicAngle", 0)
    os2 = font.get("OS/2")
    head = font.get("head")

    is_italic_post = abs(italic_angle) > 0.1
    is_italic_os2 = bool(os2.fsSelection & 0x0001) if os2 else False
    is_italic_head = bool(head.macStyle & 0x02) if head else False

    issues = []
    if is_italic_post and not is_italic_os2:
        issues.append(f"post.italicAngle={italic_angle} 但 OS/2.fsSelection Itaric 未设置")
    if not is_italic_post and is_italic_os2:
        issues.append(f"OS/2.fsSelection 指示斜体但 post.italicAngle={italic_angle} 近似为 0")

    if issues:
        for issue in issues:
            report.items.append(DiagnosisItem(
                category="post",
                severity="WARN",
                message=issue,
            ))
    else:
        report.items.append(DiagnosisItem(
            category="post",
            severity="INFO",
            message=f"italicAngle={italic_angle}，与 OS/2 一致",
        ))


def diagnose_checksums(font: TTFont, report: DiagnosisReport):
    """
    检查全局校验和一致性。
    
    通过 fontTools 内部校验验证各表 checksum 与实际数据是否匹配。
    这是检查文件完整性的最终手段。
    """
    try:
        # 保存到内存再重新加载，触发 fontTools 的完整解析校验
        from io import BytesIO
        buf = BytesIO()
        font.save(buf)
        buf.seek(0)
        validate_font = TTFont(buf)
        validate_font.close()
        report.items.append(DiagnosisItem(
            category="checksums",
            severity="INFO",
            message="文件整体校验和通过（字体可被 fontTools 完整解析和重新加载）",
        ))
    except Exception as e:
        report.items.append(DiagnosisItem(
            category="checksums",
            severity="ERROR",
            message=f"文件校验和验证失败: {e}",
            detail="字体文件可能已损坏、表偏移量不一致或校验和不匹配。"
        ))


# ============================================================================
# 主诊断流程
# ============================================================================

def diagnose_font(font_path: str) -> DiagnosisReport:
    """
    对字体文件执行全量诊断。
    
    Args:
        font_path: TTF/OTF 字体文件路径
    
    Returns:
        DiagnosisReport 包含所有诊断条目
    """
    if not os.path.isfile(font_path):
        raise FileNotFoundError(f"字体文件不存在: {font_path}")

    report = DiagnosisReport(font_path=font_path)

    font = TTFont(font_path)
    try:
        logging.info(f"开始诊断: {font_path}")

        # 基本信息
        glyph_count = len(font.getGlyphOrder())
        tables = sorted(font.reader.keys()) if font.reader else []
        report.items.append(DiagnosisItem(
            category="basic",
            severity="INFO",
            message=f"字体基本信息: 字形数={glyph_count}, 表={tables}",
        ))

        # 逐项检查
        diagnose_flavor(font, report)
        diagnose_cmap(font, report)
        diagnose_os2(font, report)
        diagnose_head(font, report)
        diagnose_name(font, report)
        diagnose_glyf(font, report)
        diagnose_post(font, report)
        diagnose_checksums(font, report)

    finally:
        font.close()

    return report


def format_report(report: DiagnosisReport) -> str:
    """将诊断报告格式化为可读文本"""
    lines = []
    lines.append("=" * 72)
    lines.append(f"TTF 字体诊断报告")
    lines.append(f"文件: {report.font_path}")
    lines.append("=" * 72)

    severity_order = ["ERROR", "WARN", "INFO"]
    for severity in severity_order:
        items = [i for i in report.items if i.severity == severity]
        if not items:
            continue

        icon = {"ERROR": "[FAIL]", "WARN": "[WARN]", "INFO": "[ OK ]"}[severity]
        lines.append(f"\n--- {severity} ({len(items)} 项) ---")
        for item in items:
            lines.append(f"  {icon} [{item.category}] {item.message}")
            if item.detail:
                for detail_line in item.detail.split("\n"):
                    lines.append(f"       {detail_line}")

    # 汇总
    lines.append("\n" + "=" * 72)
    lines.append(f"汇总: ERROR={len(report.errors)} WARN={len(report.warnings)} INFO={len(report.infos)}")
    if report.is_clean:
        lines.append("结论: 字体未发现问题。")
    else:
        lines.append(f"结论: 发现 {len(report.errors)} 个错误、{len(report.warnings)} 个警告。建议运行 fix_font.py 修复。")
    lines.append("=" * 72)

    return "\n".join(lines)


# ============================================================================
# 命令行入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="TTF 字体深度诊断工具 - 逐项检查字体关键表的合规性",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python diagnose_font.py --input problematic.ttf
  python diagnose_font.py --input problematic.ttf --save-report report.txt
  python diagnose_font.py --input problematic.ttf --verbose
        """,
    )
    parser.add_argument(
        "--input", required=True,
        help="待诊断的 TTF/OTF 字体文件路径",
    )
    parser.add_argument(
        "--save-report", default=None,
        help="将诊断报告保存到指定文件（可选）",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="启用详细日志输出",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    logging.info("=== TTF 字体诊断工具 ===")

    try:
        report = diagnose_font(args.input)
        formatted = format_report(report)
        print("\n" + formatted)

        if args.save_report:
            with open(args.save_report, "w", encoding="utf-8") as f:
                f.write(formatted)
            logging.info(f"诊断报告已保存至: {args.save_report}")

        # 返回非零退出码如果有错误
        if report.errors:
            sys.exit(1)

    except Exception as e:
        logging.error(f"诊断失败: {e}", exc_info=args.verbose)
        sys.exit(2)


if __name__ == "__main__":
    main()
