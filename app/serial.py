"""标签流水号：编号文本生成、按排版位置分配与可放下性校验。

每种标签规格可独立启用流水号（前缀 + 起始整数 + 补零位数）。排版完成
后按 页码 → 页内从上到下 → 从左到右 的实际位置顺序，为各规格分别连续
编号；旋转、首页占用区与续页不影响连续性，未排入的份数不消耗编号。

编号绘制在标签内部。本模块用保守的字宽估计（ASCII 0.7em、宽字符
1.0em）校验文字是否放得下：标签内可用宽度 / 高度不足时给出明确中文
错误，避免 SVG 文字越界。layout 在排版时校验（放不下即整体报错），
svg 用同一函数取字号，两处结果一致。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 避免运行时循环导入；仅用于类型标注
    from .models import LabelSpec, PageLayout

# 编号字号（mm）：不超过 MAX；按可用空间折算后低于 MIN 视为放不下
SERIAL_FONT_MAX = 2.4
SERIAL_FONT_MIN = 1.0
# 编号文字与标签边缘的内边距（mm）
SERIAL_PAD = 1.0
# 行高系数：字号 × 该系数 ≈ 文字实际占用高度（含上下留白）
_LINE_HEIGHT = 1.25
# 单字宽度估计（em）：ASCII 保守取 0.7（Arial 数字/大写 0.55–0.95），
# 其余字符（含 CJK）按全宽 1.0
_ASCII_EM = 0.7
_WIDE_EM = 1.0


def format_serial(prefix: str, start: int, pad: int, offset: int) -> str:
    """第 offset 个（0 起）编号文本：前缀 + (起始整数 + offset) 按位数补零。

    pad 为 0 表示不补零；编号位数超过 pad 时按实际位数显示（不截断）。
    """
    return f"{prefix}{str(start + offset).zfill(pad)}"


def text_em(text: str) -> float:
    """估计文本宽度（em）：ASCII 0.7em，其余按全宽 1.0em。"""
    return sum(_ASCII_EM if ord(c) < 0x2E80 else _WIDE_EM for c in text)


def serial_zone_height(fs: float) -> float:
    """编号在标签底部占据的区域高度（mm）：行高 + 底边内边距。"""
    return _LINE_HEIGHT * fs + SERIAL_PAD


def serial_font_size(text: str, label_w: float, label_h: float) -> float | None:
    """编号文本在 w×h（mm）标签内可用的字号（mm）；放不下返回 None。

    可用区域为标签四边各内缩 SERIAL_PAD；宽度按估计字宽折算，高度按
    行高系数折算，二者与 SERIAL_FONT_MAX 取最小。结果低于
    SERIAL_FONT_MIN 时视为放不下。
    """
    em = text_em(text)
    avail_w = label_w - 2 * SERIAL_PAD
    avail_h = label_h - 2 * SERIAL_PAD
    if em <= 0 or avail_w <= 0 or avail_h <= 0:
        return None
    fs = min(SERIAL_FONT_MAX, avail_w / em, avail_h / _LINE_HEIGHT)
    if fs < SERIAL_FONT_MIN:
        return None
    return fs


def check_serial_fit(text: str, label_w: float, label_h: float, owner: str) -> float:
    """校验编号放得进标签并返回字号；放不下时抛出明确中文 ValueError。

    owner 为标签显示名（用于错误信息）。错误同时说明按最小字号折算
    所需的宽度 / 高度，便于用户调整标签尺寸或编号设置。
    """
    fs = serial_font_size(text, label_w, label_h)
    if fs is not None:
        return fs
    need_w = text_em(text) * SERIAL_FONT_MIN + 2 * SERIAL_PAD
    need_h = _LINE_HEIGHT * SERIAL_FONT_MIN + 2 * SERIAL_PAD
    raise ValueError(
        f"标签「{owner}」的编号「{text}」放不进 "
        f"{round(label_w, 2)}×{round(label_h, 2)}mm 的标签：按最小字号 "
        f"{SERIAL_FONT_MIN}mm 约需 {round(need_w, 2)}×{round(need_h, 2)}mm "
        "内部空间。请加大标签尺寸、缩短前缀或减小补零位数。"
    )


def assign_serials(
    specs: list["LabelSpec"], pages: list["PageLayout"]
) -> dict[int, tuple[str, str]]:
    """为启用流水号的规格分配编号，返回 {规格序号: (首个编号, 末个编号)}。

    直接写入各 PlacedLabel.serial。每种规格独立排序编号：页码升序，
    页内先按 y（上 → 下）再按 x（左 → 右），与放置先后、是否旋转、
    是否跨页无关，因此同规格不会跳号或重号；未排入的份数不在 pages
    中，自然不消耗编号。编号放不下标签时抛出明确错误（不放行排版）。
    """
    ranges: dict[int, tuple[str, str]] = {}
    for i, spec in enumerate(specs):
        cfg = spec.serial
        if cfg is None:
            continue
        mine = [
            (page.page, lab)
            for page in pages
            for lab in page.labels
            if lab.type_index == i
        ]
        # 页码 → 页内上→下（y）→ 左→右（x）；坐标已统一保留 2 位小数
        mine.sort(key=lambda t: (t[0], t[1].y, t[1].x))
        owner = spec.name or f"#{i + 1}"
        first = last = None
        for offset, (_, lab) in enumerate(mine):
            text = format_serial(cfg.prefix, cfg.start, cfg.pad, offset)
            # 旋转标签按排版后的实际宽高校验（编号文字不随标签旋转）
            check_serial_fit(text, lab.width, lab.height, owner)
            lab.serial = text
            if first is None:
                first = text
            last = text
        if first is not None and last is not None:
            ranges[i] = (first, last)
    return ranges
