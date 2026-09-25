"""打印 / 预览 SVG 生成。

与页面预览共用 compute_layout 输出的毫米坐标：浏览器和打印工具都
按 viewBox 等比缩放，坐标本身永远是毫米，因此预览所见即导出所得。

打印位置校准：calibration 给定时（仅下载 / 打印的 SVG 会带），所有
绘制元素按线性走纸误差的逆变换逐点修正——坐标与尺寸平移/缩放，
线宽与字号保持不变；viewBox 与纸张尺寸仍是原始纸张。常规预览不传
calibration，坐标与 /api/layout 返回完全一致。

裁切角线避让：每个标签四角的 8 段角线逐段求可绘部分——与本页其他
标签轮廓相交、与首页占用区相交、或落到纸张范围之外的残段被裁去，
残段短于 0.5mm 时整段省略。因此标签紧贴（间距为 0）或靠近纸边时
角线不会侵入邻签、占用区或画到纸外。判定一律在最终 SVG 坐标空间
进行：应用走纸校准时，邻签、占用区与纸边都按逆变换补偿后的坐标
参与避让。标签轮廓本身、排版数量与位置不随角线避让改变。

流水号：启用编号的标签在内部绘制分配的编号（预览 / 下载 / 打印同一
编号）。有名称标注时编号靠标签底边、标注上移避让；无标注时编号
垂直居中。字号由 serial.serial_font_size 按标签内部空间折算，
排版时已校验放得下，此处不会越界。
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from .calibration import calibration_marks
from .models import Calibration, LayoutResponse
from .serial import SERIAL_PAD, serial_font_size, serial_zone_height

_CUT = 3.0  # 裁切角线单臂长度 mm
_CUT_INSET = 1.5  # 角线相对标签轮廓的外扩 mm
_MIN_MARK = 0.5  # 残段短于该长度（mm）则整段省略
_EPS = 1e-9


def _fmt(v: float) -> str:
    """SVG 属性中的毫米数：整数不带小数点，其余保留 2 位。"""
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _cut_segments(x: float, y: float, w: float, h: float):
    """矩形四角 8 段轴对齐角线（标签轮廓外扩 _CUT_INSET 的角框上）。

    返回 ("h"/"v", 起点, 固定坐标, 终点)：h 表示水平线 y 固定、沿 x；
    v 表示竖直线 x 固定、沿 y。
    """
    x0, y0 = x - _CUT_INSET, y - _CUT_INSET
    x1, y1 = x + w + _CUT_INSET, y + h + _CUT_INSET
    c = _CUT
    return [
        ("v", y0 + c, x0, y0),          # 左上竖
        ("h", x0, y0, x0 + c),          # 左上横
        ("v", y0, x1, y0 + c),          # 右上竖
        ("h", x1 - c, y0, x1),          # 右上横
        ("v", y1 - c, x1, y1),          # 右下竖
        ("h", x1, y1, x1 - c),          # 右下横
        ("v", y1, x0, y1 - c),          # 左下竖
        ("h", x0 + c, y1, x0),          # 左下横
    ]


def _subtract_interval(
    a0: float, a1: float, b0: float, b1: float
) -> list[tuple[float, float]]:
    """区间 [a0,a1] 减去区间 (b0,b1)（开区间）后的剩余部分。

    开区间相减：本角线恰好贴着对方矩形边缘（端点接触或共线）时不算
    相交，该部分予以保留；只有真正探入矩形内部的段被裁掉。
    """
    if b0 >= a0 + _EPS and b1 <= a1 - _EPS:  # (b) 完全在内部，两侧残段
        return [(a0, b0), (b1, a1)]
    if b0 <= a0 + _EPS and b1 >= a1 - _EPS:  # [a] 完全被覆盖
        return []
    if a0 < b1 - _EPS and a1 > b0 + _EPS:  # 部分相交
        if b0 > a0 + _EPS:  # 相交段在右侧
            return [(a0, b0)]
        return [(b1, a1)]  # 相交段在左侧
    return [(a0, a1)]  # 不相交


def _clip_mark(
    orientation: str, p0: float, fixed: float, p1: float,
    paper: tuple[float, float, float, float],
    blockers: list[tuple[float, float, float, float]],
) -> list[tuple[float, float]]:
    """求单段角线的可绘区间（轴向参数为 p0→p1，固定坐标为 fixed）。

    paper 为纸张矩形 (x0,y0,x1,y1)；blockers 为本页其他标签轮廓与
    （首页）占用区矩形。先裁掉纸外部分，再逐个减去与障碍矩形内部
    相交的区间；区间反转时先归一化。
    """
    lo, hi = (p0, p1) if p0 <= p1 else (p1, p0)
    if orientation == "h":
        lo = max(lo, paper[0])
        hi = min(hi, paper[2])
        if not (fixed > paper[1] + _EPS and fixed < paper[3] - _EPS):
            return []
    else:
        lo = max(lo, paper[1])
        hi = min(hi, paper[3])
        if not (fixed > paper[0] + _EPS and fixed < paper[2] - _EPS):
            return []
    if hi - lo < _EPS:
        return []

    intervals: list[tuple[float, float]] = [(lo, hi)]
    for bx0, by0, bx1, by1 in blockers:
        if orientation == "h":
            # 水平线在矩形的上下边界上（共线）不算探入内部
            if not (fixed > by0 + _EPS and fixed < by1 - _EPS):
                continue
            b_lo, b_hi = bx0, bx1
        else:
            if not (fixed > bx0 + _EPS and fixed < bx1 - _EPS):
                continue
            b_lo, b_hi = by0, by1
        next_intervals: list[tuple[float, float]] = []
        for a0, a1 in intervals:
            next_intervals.extend(_subtract_interval(a0, a1, b_lo, b_hi))
        intervals = next_intervals
        if not intervals:
            break
    return [(a0, a1) for a0, a1 in intervals if a1 - a0 >= _MIN_MARK - _EPS]


def _cut_marks(
    x: float, y: float, w: float, h: float,
    paper: tuple[float, float, float, float],
    blockers: list[tuple[float, float, float, float]],
) -> str:
    """四角角线：逐段裁去纸外、其他标签与占用区内的部分。

    残段短于 0.5mm 时省略；所有段都被裁光时返回空字符串（连角线组
    也不输出）。
    """
    cmds: list[str] = []
    for orientation, p0, fixed, p1 in _cut_segments(x, y, w, h):
        for a0, a1 in _clip_mark(orientation, p0, fixed, p1, paper, blockers):
            if orientation == "h":
                cmds.append(
                    f"M{_fmt(a0)} {_fmt(fixed)} H{_fmt(a1)}"
                )
            else:
                cmds.append(
                    f"M{_fmt(fixed)} {_fmt(a0)} V{_fmt(a1)}"
                )
    if not cmds:
        return ""
    return (
        f'<g class="cutmarks" stroke="#d23f3f" stroke-width="{_fmt(0.15)}" '
        f'fill="none">'
        f'<path d="{"".join(cmds)}"/>'
        "</g>"
    )


def build_svg(plan: LayoutResponse, page_no: int,
              show_margins: bool = True, show_annotations: bool = True,
              show_occupied: bool = False,
              calibration: Calibration | None = None) -> str:
    page = next((p for p in plan.pages if p.page == page_no), None)
    if page is None:
        raise ValueError(f"页码超出范围：共 {len(plan.pages)} 页，请求第 {page_no} 页。")

    # 逆变换：打印机满足 实测 = scale·设计 + offset，导出写入 设计' 使得
    # scale·设计' + offset = 设计，即 (设计 - offset) / scale。
    # 无校准时恒等，输出与未加校准功能前逐字节同构。
    if calibration is None:
        def fx(v: float) -> float:
            return v
        fy = fw = fh = fx
    else:
        xs, xo = calibration.x.scale, calibration.x.offset
        ys, yo = calibration.y.scale, calibration.y.offset

        def fx(v: float) -> float:
            return (v - xo) / xs

        def fy(v: float) -> float:
            return (v - yo) / ys

        def fw(v: float) -> float:
            return v / xs

        def fh(v: float) -> float:
            return v / ys

    w, h = plan.page_width, plan.page_height
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'width="{_fmt(w)}mm" height="{_fmt(h)}mm" '
            f'viewBox="0 0 {_fmt(w)} {_fmt(h)}">'
        ),
        f'<rect x="0" y="0" width="{_fmt(w)}" height="{_fmt(h)}" fill="#ffffff"/>',
        (
            '<style>.label{fill:none;stroke:#222;stroke-width:0.15;}'
            ".annot{font-family:Arial,'Noto Sans CJK SC',sans-serif;"
            "font-size:2.2mm;fill:#9aa0a6;text-anchor:middle;}"
            ".serial{font-family:Arial,'Noto Sans CJK SC',sans-serif;"
            "fill:#1a1a1a;text-anchor:middle;}"
            ".marginguide{fill:none;stroke:#5b8def;stroke-width:0.1;stroke-dasharray:1 1;}"
            ".pageno{font-family:Arial,sans-serif;font-size:2.5mm;fill:#9aa0a6;}</style>"
        ),
    ]
    if calibration is not None:
        parts.append(
            f"<!-- 打印位置校准已应用：X scale={calibration.x.scale} "
            f"offset={calibration.x.offset}mm；Y scale={calibration.y.scale} "
            f"offset={calibration.y.offset}mm（按逆变换补偿） -->"
        )

    # 占用区仅存在于第一张纸；仅在预览（show_occupied）时绘制，
    # 下载 / 打印的 SVG 跳过本块（连同其专用样式），因此只含新标签。
    if show_occupied and page_no == 1 and plan.occupied_zones:
        parts.append(
            '<defs><pattern id="occHatch" patternUnits="userSpaceOnUse" '
            'width="3" height="3" patternTransform="rotate(45)">'
            '<rect width="3" height="3" fill="#e8eaed"/>'
            '<line x1="0" y1="0" x2="0" y2="3" stroke="#b9bdc4" stroke-width="0.5"/>'
            "</pattern></defs>"
        )
        parts.append(
            "<style>.occupied{fill:url(#occHatch);stroke:#9aa0a6;"
            "stroke-width:0.15;stroke-dasharray:1.2 0.8;}"
            ".occtext{font-family:Arial,'Noto Sans CJK SC',sans-serif;"
            "font-size:2.5mm;fill:#7b808a;text-anchor:middle;}</style>"
        )
        for z in plan.occupied_zones:
            parts.append(
                f'<rect class="occupied" x="{_fmt(fx(z.x))}" y="{_fmt(fy(z.y))}" '
                f'width="{_fmt(fw(z.width))}" height="{_fmt(fh(z.height))}"/>'
            )
            if min(z.width, z.height) >= 12:
                cx, cy = fx(z.x + z.width / 2), fy(z.y + z.height / 2)
                parts.append(
                    f'<text class="occtext" x="{_fmt(cx)}" y="{_fmt(cy + 0.9)}">'
                    "已占用</text>"
                )

    if show_margins:
        mx0, my0 = fx(plan.margin_left), fy(plan.margin_top)
        mx1, my1 = fx(w - plan.margin_right), fy(h - plan.margin_bottom)
        parts.append(
            f'<rect class="marginguide" x="{_fmt(mx0)}" y="{_fmt(my0)}" '
            f'width="{_fmt(mx1 - mx0)}" height="{_fmt(my1 - my0)}"/>'
        )

    # 角线避让用几何（全部在最终 SVG 坐标空间，校准时即补偿后坐标）：
    # 纸张矩形——物理纸边映射回本坐标系为 fx(0)/fx(w)（校准时并非 0/w）；
    # 本页每个标签轮廓；首页占用区（即便导出不绘制占用区，角线仍须避让）。
    paper_rect = (fx(0), fy(0), fx(w), fy(h))
    label_rects = [
        (
            fx(lab.x), fy(lab.y),
            fx(lab.x + lab.width), fy(lab.y + lab.height),
        )
        for lab in page.labels
    ]
    zone_rects: list[tuple[float, float, float, float]] = []
    if page_no == 1:
        zone_rects = [
            (
                fx(z.x), fy(z.y),
                fx(z.x + z.width), fy(z.y + z.height),
            )
            for z in plan.occupied_zones
        ]

    for idx, lab in enumerate(page.labels):
        lx, ly, lw, lh = fx(lab.x), fy(lab.y), fw(lab.width), fh(lab.height)
        parts.append(
            f'<rect class="label" x="{_fmt(lx)}" y="{_fmt(ly)}" '
            f'width="{_fmt(lw)}" height="{_fmt(lh)}"/>'
        )
        blockers = [r for k, r in enumerate(label_rects) if k != idx]
        blockers.extend(zone_rects)
        marks = _cut_marks(lx, ly, lw, lh, paper_rect, blockers)
        if marks:
            parts.append(marks)
        # 编号字号按设计空间标签尺寸折算（与排版时的放下校验一致；
        # 校准只平移/缩放坐标，字号保持不变）
        sfs = (
            serial_font_size(lab.serial, lab.width, lab.height)
            if lab.serial
            else None
        )
        has_annot = show_annotations and min(lab.width, lab.height) >= 8
        if has_annot:
            name = escape((lab.name or f"#{lab.type_index + 1}")[:12])
            tag = name + (" ↻" if lab.rotated else "")
            size_txt = f"{_fmt(lab.width)}×{_fmt(lab.height)}"
            fs = 2.2
            if len(tag) > 8:
                fs = 1.8
            cx, cy = lx + lw / 2, ly + lh / 2
            if sfs is not None:
                # 编号占据标签底部区域，标注在上方剩余区域内居中，
                # 避免与编号重叠
                cy = ly + (lh - serial_zone_height(sfs)) / 2
                name_y = cy - 0.1
                size_y = cy + min(fs, 2.0) + 0.7
            else:
                name_y = cy - 0.2
                size_y = cy + fs + 0.8
            parts.append(
                f'<text class="annot" x="{_fmt(cx)}" y="{_fmt(name_y)}" '
                f'font-size="{_fmt(fs)}mm">{tag}</text>'
                f'<text class="annot" x="{_fmt(cx)}" y="{_fmt(size_y)}" '
                f'font-size="{_fmt(min(fs, 2.0))}mm">{size_txt}</text>'
            )
        if sfs is not None:
            scx = lx + lw / 2
            if has_annot:
                scy = ly + lh - SERIAL_PAD  # 标注在上，编号靠底边
            else:
                scy = ly + lh / 2 + sfs * 0.35  # 无标注时编号垂直居中
            parts.append(
                f'<text class="serial" x="{_fmt(scx)}" y="{_fmt(scy)}" '
                f'font-size="{_fmt(sfs)}mm">{escape(lab.serial)}</text>'
            )

    parts.append(
        f'<text class="pageno" x="{_fmt(fx(w / 2))}" y="{_fmt(fy(h - 2))}">'
        f"Page {page.page} / {len(plan.pages)}</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts)


def build_calibration_page(page_width: float, page_height: float) -> str:
    """打印位置校准测试页：横、纵两组基准刻线，每根刻线标注设计坐标。

    纵向实线（沿 X 方向均布）用于校准 X：尺量其到纸张左边缘的距离；
    横向虚线（沿 Y 方向均布）用于校准 Y：尺量其到纸张顶边缘的距离。
    所有位置按纸张比例布置，任意纸张尺寸均可使用；坐标标注与
    /api/calibration/marks 返回的设计坐标一致。
    """
    x_marks, y_marks = calibration_marks(page_width, page_height)
    w, h = page_width, page_height
    cx = w / 2
    line_top, line_bottom = h * 0.14, h * 0.95
    line_left, line_right = w * 0.08, w * 0.96
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'width="{_fmt(w)}mm" height="{_fmt(h)}mm" '
            f'viewBox="0 0 {_fmt(w)} {_fmt(h)}">'
        ),
        f'<rect x="0" y="0" width="{_fmt(w)}" height="{_fmt(h)}" fill="#ffffff"/>',
        (
            "<style>"
            ".cal-title{font-family:Arial,'Noto Sans CJK SC',sans-serif;"
            "font-size:4mm;fill:#111;text-anchor:middle;font-weight:bold;}"
            ".cal-note{font-family:Arial,'Noto Sans CJK SC',sans-serif;"
            "font-size:2.6mm;fill:#333;text-anchor:middle;}"
            ".vline{stroke:#0b57d0;stroke-width:0.3;}"
            ".hline{stroke:#0b57d0;stroke-width:0.3;stroke-dasharray:5 2;}"
            ".marklab{font-family:Arial,sans-serif;font-size:2.8mm;fill:#0b57d0;}"
            ".frame{fill:none;stroke:#9aa0a6;stroke-width:0.2;}"
            "</style>"
        ),
        f'<rect class="frame" x="0.4" y="0.4" width="{_fmt(w - 0.8)}" height="{_fmt(h - 0.8)}"/>',
        f'<text class="cal-title" x="{_fmt(cx)}" y="{_fmt(h * 0.04)}">'
        f"打印位置校准测试页（纸张 {_fmt(w)}×{_fmt(h)}mm）</text>",
        f'<text class="cal-note" x="{_fmt(cx)}" y="{_fmt(h * 0.04 + 4.5)}">'
        f"请以 100% 缩放打印并关闭「适应页面/无边距缩放」；"
        f"竖直实线 X1–X{len(x_marks)} 量到纸左缘、水平虚线 Y1–Y{len(y_marks)} "
        "量到纸顶缘，读数(mm)填入软件实测列</text>",
    ]
    for i, mx in enumerate(x_marks):
        parts.append(
            f'<line class="vline" x1="{_fmt(mx)}" y1="{_fmt(line_top)}" '
            f'x2="{_fmt(mx)}" y2="{_fmt(line_bottom)}"/>'
        )
        parts.append(
            f'<text class="marklab" text-anchor="middle" x="{_fmt(mx)}" '
            f'y="{_fmt(line_top - 2)}">X{i + 1}={_fmt(mx)}</text>'
        )
    for i, my in enumerate(y_marks):
        parts.append(
            f'<line class="hline" x1="{_fmt(line_left)}" y1="{_fmt(my)}" '
            f'x2="{_fmt(line_right)}" y2="{_fmt(my)}"/>'
        )
        parts.append(
            f'<text class="marklab" text-anchor="start" x="{_fmt(line_left)}" '
            f'y="{_fmt(my - 1.5)}">Y{i + 1}={_fmt(my)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)
