# Font Toolkit — 字体 Web 子集抓取与合并修复工具包

一套完整的 TTF/OTF 字体处理工具链，覆盖从字体厂商官网抓取 WOFF/WOFF2 子集分片，到合并为单一字体文件、深度诊断、自动修复，最终在 Windows 上验证加载的全流程。

> 仅供字体学习与研究用途。抓取与合并行为须遵守各字体厂商的服务条款与版权声明，
> 对字体文件的操作须符合原始字体许可协议。

## 快速开始

```bash
pip install -r requirements.txt

# 一键流水线：合并 → 诊断 → 修复 → Windows 验证
python font_toolkit.py all --input-dir ./woff_fragments/ --output ./output/merged.ttf
```

也可以分步执行，或直接使用各独立脚本（见下表）。

## 工具脚本说明

| 文件 | 功能 | 输入 | 输出 |
|------|------|------|------|
| `font_toolkit.py` | 统一 CLI 入口（fetch / merge / diagnose / fix / verify / all） | — | — |
| `fetch_justfont.py` | justfont 页面子集抓取（静态收割 / 动态 API） | 页面 URL | 字体子集文件 |
| `merge_woff2_fonts.py` | 合并多个 WOFF/WOFF2 子集为单一 TTF/OTF | 目录（含字体文件） | 合并后的 .ttf/.otf |
| `diagnose_font.py` | 深度诊断字体所有关键表 | 单个 .ttf/.otf | 终端诊断报告 |
| `fix_font.py` | 修复诊断发现的问题 | 单个 .ttf/.otf | 修复后的 .ttf/.otf |
| `verify_font.py` | 在 Windows 上加载验证字体（GDI + .NET + fontTools 三路） | 单个 .ttf/.otf | 加载成功/失败信息 |
| `extension/` | 浏览器插件：捕获网页字体 + 动态 API 全量抓取（见 extension/README.md） | — | — |

> name 表完整性（nameID 1/2/3/4/6）是 Windows 可安装性的关键：
> GDI 注册字体要求这些记录齐全，网络子集常缺 nameID 3/4/6，
> 表现为字体查看器报"不是有效的字体文件"。合并与修复流程会自动合成。

## justfont 页面字体机制（实测）

justfont 营销页/活动页通过两条通道交付字体，`fetch_justfont.py` 均已支持：

1. **静态 @font-face 文件**：页面 HTML/CSS 内联的 `@font-face` 规则直接指向
   `/fonts/*.woff2` 等静态文件（通常为页面文案的字符子集，可能为 CFF 轮廓）。
   静态收割模式自动发现并下载全部此类文件。

2. **动态字型 API**：页面 JS 注册于 justfont 网页字型服务（站点 ID 可从 JS 中的
   `provider:vC(<id>)` 自动发现），按需按字符批次拉取子集：

   ```
   POST https://go.justfont.com/jfont/api?o=<站点源>&p=<站点ID>&t=woff
   Body: fn=<字体名含字重后缀，如 jf-lanyanghei-bold>&fs=<URL编码字符集>
   响应: {"fn": [...], "fu": [<CDN 子集 URL>]}
   ```

   单批字符数上限约 1700，脚本自动去重分批。返回的子集为 TrueType 轮廓 WOFF。

## 合并引擎

按基础字体的轮廓格式自动选择合并路径：

- **TrueType (glyf) 路径**：按 glyph name 去重追加字形，复制 hmtx/vmtx 度量，
  更新 maxp / hhea.numberOfHMetrics / vhea.numberOfVMetrics
- **CFF (OTTO) 路径**：先对全部子集 desubroutinize（展开 CharString 子例程引用），
  再按 glyph name 追加 CharStrings（同步 charset 与 glyphOrder、扩展单 FD 的
  FDSelect），支持 name-keyed 与单 FD 的 CID-keyed（Adobe-Identity）字体
- **共用**：重建 cmap（format 4 + format 12 子表齐全，Windows 平台编码），
  GSUB/GPOS/GDEF 等布局表保留基础字体版本

## 诊断项

- flavor：WOFF/WOFF2 封装残留检测
- cmap：format 4 / format 12 子表存在性、映射数量、平台编码
- OS/2：usWidthClass / usWeightClass / fsSelection（含 Bold 与 Regular 位互斥检查）
- head：flags / magicNumber / macStyle 与 OS/2 交叉比对
- name：Family / Subfamily / PostScript 名称与字重一致性
- glyf：复合字形引用边界、空字形映射检查（TrueType 路径）
- CFF：CharStrings 与 glyphOrder 一致性、子例程状态（OTTO 路径）
- post：斜体角度与 OS/2 一致性
- 全局：保存-重载完整性校验

## 验证策略（Windows）

1. .NET `PrivateFontCollection` 加载（与字体查看器行为一致，需 pythonnet）
2. fontTools 结构解析（按轮廓格式区分必需表，回退方案）
3. 可选弹出 Windows 字体查看器进行视觉确认（`--open-viewer`）

## 兰阳黑实测记录（2026-09）

以 [justfont 兰阳黑体页面](https://justfont.com/lanyanghei/) 为真实数据源的全流程回归：

| 测试 | 路径 | 结果 |
|------|------|------|
| 动态 API 两批次（24+29 字符，重叠 2）合并 | glyf/WOFF | 51 字符合并，与源子集轮廓、度量逐一一致 |
| 静态 CFF 子集拆 3 批重合并 | CFF/WOFF2 | 54 文本码点轮廓逐一一致 |
| W7/W10/CFF/sbix 彩色/变量字体 5 个成品 | 修复+验证 | 全部通过 .NET + fontTools 双路验证 |

## 已验证字体清单

以下字体已通过本工具包完整流程验证，可在 Windows 上正常安装与渲染：

- 凝明朝體（CreamMincho）
- 泪体
- 台湾道路体（TW Road Font）
- 日花
- OhSong（兮宋）
- MNewsMPro
- 平湖（6 个字重）

## 典型工作流

```bash
# 1. 抓取：静态收割页面托管的字体文件
python font_toolkit.py fetch --url https://justfont.com/lanyanghei/ --out ./fonts/

# 1'. 或动态 API 按字符批次拉取（字符集来自页面文案）
python font_toolkit.py fetch --url https://justfont.com/lanyanghei/ --out ./woff/ \
    --dynamic --family jf-lanyanghei-bold --text "蘭陽黑體範例文字"

# 2. 一键合并 → 诊断 → 修复 → 验证
python font_toolkit.py all --input-dir ./woff/ --output ./output/merged.ttf

# 或分步执行：
python font_toolkit.py merge    --input-dir ./woff/ --output ./output/merged.ttf
python font_toolkit.py diagnose --input ./output/merged.ttf
python font_toolkit.py fix      --input ./output/merged.ttf --output ./output/fixed.ttf
python font_toolkit.py verify   --input ./output/fixed.ttf
```

## 前置依赖与安装

要求 **Python 3.8+**：

```bash
pip install fonttools brotli
pip install pythonnet   # 可选，仅 Windows 上 verify 的 .NET 验证方式需要
```

> 若无法安装 `pythonnet`，验证脚本自动回退到 fontTools 解析验证，
> 或使用 Windows 字体查看器手动双击验证。
