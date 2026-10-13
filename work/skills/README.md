# Font Toolkit - 字体子集合并·诊断·修复·验证 技能包

## 概述

本技能包提供一套完整的 TTF/OTF 字体处理工具链，覆盖从 WOFF2 子集合并到最终 Windows 加载验证的全流程。

## 适用场景

1. 从字体厂商官网抓取的 WOFF2 子集分片文件，需要合并为单一字体文件
2. 合并后的字体在 Windows 上无法安装或加载（字体查看器报错/无法渲染）
3. 需要对已有 TTF 字体进行深度合规性诊断（cmap/OS/2/head/name/glyf 表）
4. 诊断后需要自动修复常见问题（WOFF2 封装残留、OS/2 不规范、校验和错误等）
5. 修复后需要在 Windows 环境下验证字体是否可正常加载

## 前置依赖

- **Python 3.8+**
- **fontTools**：`pip install fonttools`
- **Python.NET** (可选，仅 verify_font.py 需要)：`pip install pythonnet`
  - 若无法安装 pythonnet，可使用 Windows 字体查看器手动双击验证
- **操作系统**：Windows 10/11（验证脚本的 .NET 方式为 Windows 专属）

## 文件清单

| 文件 | 功能 | 输入 | 输出 |
|------|------|------|------|
| `merge_woff2_fonts.py` | 合并多个 WOFF2 子集为单一 TTF | 目录（含 .woff2 文件） | 合并后的 .ttf |
| `diagnose_font.py` | 深度诊断 TTF 字体所有关键表 | 单个 .ttf 文件 | 终端诊断报告 |
| `fix_font.py` | 修复诊断发现的问题 | 单个 .ttf 文件 | 修复后的 .ttf |
| `verify_font.py` | 在 Windows 上加载验证字体 | 单个 .ttf 文件 | 加载成功/失败信息 |

## 使用方法

### 典型工作流

```bash
# 第一步：合并 WOFF2 子集文件
python merge_woff2_fonts.py --input-dir ./woff2_fragments/ --output ./output/merged.ttf

# 第二步：诊断合并后的字体
python diagnose_font.py --input ./output/merged.ttf

# 第三步：根据诊断结果修复
python fix_font.py --input ./output/merged.ttf --output ./output/fixed.ttf

# 第四步：在 Windows 上验证
python verify_font.py --input ./output/fixed.ttf
```

### 各脚本详细参数

#### merge_woff2_fonts.py

```
python merge_woff2_fonts.py --input-dir <WOFF2文件目录> --output <输出TTF路径> [--font-name <字体名称>] [--verbose]
```

- `--input-dir`：包含 .woff2 子集文件的目录（必填）
- `--output`：合并后 TTF 的输出路径（必填）
- `--font-name`：自定义字体家族名称（可选，默认取第一个文件的 name 记录）
- `--verbose`：启用详细日志

#### diagnose_font.py

```
python diagnose_font.py --input <TTF文件路径> [--save-report <报告输出路径>] [--verbose]
```

- `--input`：待诊断的 TTF 文件路径（必填）
- `--save-report`：将诊断报告保存为文本文件（可选）
- `--verbose`：启用详细日志

检查项目：
- font.flavor 检查（WOFF2 封装残留检测）
- cmap 表完整性（format 4 和 format 12 子表）
- OS/2 表合规性（usWidthClass, fsSelection, usWeightClass, macStyle）
- head 表（flags, magicNumber, 校验和）
- name 表（Subfamily 名称与字重匹配）
- glyf 表（字形数量、复合字形引用、边界检查）
- 全局校验和一致性

#### fix_font.py

```
python fix_font.py --input <TTF文件路径> --output <输出TTF路径> [--weight <100-900>] [--width <1-9>] [--verbose]
```

- `--input`：待修复的 TTF 文件路径（必填）
- `--output`：修复后 TTF 输出路径（必填）
- `--weight`：目标字重（100-900），不指定则从现有 name 表推断
- `--width`：目标宽度等级（1-9），默认 5（Medium/Regular）
- `--verbose`：启用详细日志

修复项目：
- WOFF2 封装移除（font.flavor = None）
- cmap 表重建（确保 format 4 + format 12 均存在且正确）
- OS/2 表规范化（usWidthClass, fsSelection, usWeightClass）
- head 表 flags 和 macStyle 修正
- fontTools 重新编译以修复校验和

#### verify_font.py

```
python verify_font.py --input <TTF文件路径> [--verbose]
```

- `--input`：待验证的 TTF 文件路径（必填）
- `--verbose`：启用详细日志

## 设计原则

1. **只读诊断优先**：诊断脚本不修改原文件，仅输出问题清单
2. **修复有据可查**：修复脚本的每一项改动均有日志说明原因
3. **独立可运行**：每个脚本可独立使用，不强制要求走完整流水线
4. **Windows 原生验证**：验证脚本使用 .NET PrivateFontCollection API，与系统行为一致

## 许可证

本工具包仅供字体学习与研究用途。请确保对字体文件的操作符合原始字体许可协议。
