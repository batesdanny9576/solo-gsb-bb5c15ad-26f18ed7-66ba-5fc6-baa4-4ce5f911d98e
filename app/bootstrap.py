"""启动初始化：校验配置、自检关键模块可导入。

容器启动时由 entrypoint.sh 调用（python -m app.bootstrap）。
无数据库等外部依赖；这里执行运行环境自检并输出启动横幅。
"""

from __future__ import annotations

import sys


def main() -> int:
    print("[bootstrap] 小批量标签拼版工具 —— 初始化开始")

    # 依赖与核心模块自检
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        import pydantic  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        print(f"[bootstrap] 依赖缺失: {exc}", file=sys.stderr)
        return 1

    from .calibration import (
        ensure_calibrated_bounds,
        solve_calibration,
    )
    from .layout import compute_layout
    from .models import (
        CalibrationData,
        CalibrationLine,
        LayoutRequest,
        LabelSpec,
        OccupiedZone,
        SerialConfig,
    )
    from .serial import format_serial, serial_font_size
    from .svg import _fmt, build_calibration_page, build_svg

    import re

    def _cut_segments_of(svg_text: str):
        """解析 cutmarks，返回每段子线 (x0,y0,x1,y1)（轴对齐）。"""
        m = re.search(r'<g class="cutmarks"[^>]*><path d="([^"]*)"', svg_text)
        if not m:
            return []
        out = []
        for x, y, axis, end in re.findall(
            r"M(-?[\d.]+) (-?[\d.]+) ([VH])(-?[\d.]+)", m.group(1)
        ):
            x, y, end = float(x), float(y), float(end)
            if axis == "H":
                out.append((min(x, end), y, max(x, end), y))
            else:
                out.append((x, min(y, end), x, max(y, end)))
        return out

    # 裁切角线避让：20×20mm 标签、0 间距紧贴、40mm 纸宽下两列铺满，
    # 角线既不能探入邻签，也不能画到纸外
    tight = LayoutRequest(
        page_width=40, page_height=60,
        gap_x=0, gap_y=0,
        labels=[LabelSpec(width=20, height=20, count=6)],
    )
    tight_plan = compute_layout(tight)
    assert len(tight_plan.pages[0].labels) == 6
    assert tight_plan.pages[0].labels[0].x == 0
    tight_svg = build_svg(tight_plan, 1, show_annotations=False)
    rects = [(l.x, l.y, l.x + l.width, l.y + l.height)
             for l in tight_plan.pages[0].labels]
    for sx0, sy0, sx1, sy1 in _cut_segments_of(tight_svg):
        assert sx0 >= 0 and sy0 >= 0 and sx1 <= 40 and sy1 <= 60, (
            f"角线超出纸边: {(sx0, sy0, sx1, sy1)}"
        )
        for rx0, ry0, rx1, ry1 in rects:
            # 与任何标签轮廓内部不得相交（共边 / 端点接触允许）
            assert not (
                sx0 < rx1 - 1e-6 and sx1 > rx0 + 1e-6
                and sy0 < ry1 - 1e-6 and sy1 > ry0 + 1e-6
            ), f"角线侵入标签 ({rx0},{ry0},{rx1},{ry1}): {(sx0, sy0, sx1, sy1)}"

    # 20×20 标签铺满 20×20 整张纸：角线全部在纸外，cutmarks 组不输出
    one = LayoutRequest(
        page_width=20, page_height=20,
        labels=[LabelSpec(width=20, height=20, count=1)],
    )
    assert 'class="cutmarks"' not in build_svg(compute_layout(one), 1)

    # 开阔纸面上的单个标签，四角 8 段齐全
    free = LayoutRequest(
        page_width=100, page_height=100,
        margin_top=30, margin_left=30,
        labels=[LabelSpec(width=20, height=20, count=1)],
    )
    assert len(_cut_segments_of(build_svg(compute_layout(free), 1))) == 8

    # 首页占用区避让：导出即使不画占用区，角线也不得进入占用区矩形
    occ_avoid = LayoutRequest(
        page_width=120, page_height=120,
        labels=[LabelSpec(width=20, height=20, count=1)],
        occupied_zones=[OccupiedZone(x=30, y=0, width=40, height=30)],
    )
    occ_plan = compute_layout(occ_avoid)
    z = occ_plan.occupied_zones[0]
    export_no_zone = build_svg(occ_plan, 1)  # show_occupied=False
    assert 'class="occupied"' not in export_no_zone
    for sx0, sy0, sx1, sy1 in _cut_segments_of(export_no_zone):
        assert not (
            sx0 < z.x + z.width - 1e-6 and sx1 > z.x + 1e-6
            and sy0 < z.y + z.height - 1e-6 and sy1 > z.y + 1e-6
        ), f"角线侵入首页占用区: {(sx0, sy0, sx1, sy1)}"

    # 用一个最小样例冒烟测试排版 + SVG 管线
    smoke = LayoutRequest(
        page_width=210, page_height=297,
        margin_top=8, margin_right=8, margin_bottom=8, margin_left=8,
        gap_x=2, gap_y=2,
        labels=[LabelSpec(width=60, height=30, count=4, allow_rotate=True)],
    )
    plan = compute_layout(smoke)
    build_svg(plan, 1)
    assert plan.total_placed == 4

    # 首张纸占用区：障碍位于 (48,48)、100×100，间距 2。
    # 标签盒为 52×32，障碍碰撞盒右缘 150、下沿 150。
    occ = LayoutRequest(
        page_width=210, page_height=297,
        margin_top=8, margin_right=8, margin_bottom=8, margin_left=8,
        gap_x=2, gap_y=2,
        labels=[LabelSpec(name="避让标签", width=50, height=30, count=40)],
        occupied_zones=[OccupiedZone(x=48, y=48, width=100, height=100)],
    )
    p2 = compute_layout(occ)
    p2_svg = build_svg(p2, 1, show_occupied=True)
    export_svg = build_svg(p2, 1)  # 导出不含占用区绘制
    assert 'class="occupied"' in p2_svg and "已占用" in p2_svg
    assert 'class="occupied"' not in export_svg and "已占用" not in export_svg
    # 不丢失、不重复
    assert sum(len(pg.labels) for pg in p2.pages) == 40
    assert p2.total_placed == 40
    assert len(p2.pages) >= 2  # 障碍占掉首页空间，必然续页
    z = occ.occupied_zones[0]
    for lab in p2.pages[0].labels:
        # 盒 = 标签矩形 + 右/下一个间距；盒与障碍碰撞盒不得重叠
        bx2, by2 = lab.x + lab.width + 2, lab.y + lab.height + 2
        assert not (
            lab.x < z.x + z.width + 2 and bx2 > z.x
            and lab.y < z.y + z.height + 2 and by2 > z.y
        ), f"标签与占用区间距不足: {lab}"
    # 续页为无障碍空白页，正常排满
    assert len(p2.pages[1].labels) > 0

    # 越界占用区必须报明确错误
    def _expect_error(z: OccupiedZone) -> None:
        bad = LayoutRequest(
            page_width=210, page_height=297,
            labels=[LabelSpec(width=10, height=10, count=1)],
            occupied_zones=[z],
        )
        try:
            compute_layout(bad)
        except ValueError:
            return
        raise AssertionError(f"占用区 {z} 越界却未报错")

    _expect_error(OccupiedZone(x=120, y=10, width=100, height=10))
    _expect_error(OccupiedZone(x=10, y=250, width=10, height=60))
    _expect_error(OccupiedZone(x=-1, y=0, width=10, height=10))

    # ---- 打印位置校准：拟合、逆变换修正与三类拦截 ----
    def _lines(scale: float, offset: float) -> list[CalibrationLine]:
        return [
            CalibrationLine(design=d, measured=round(scale * d + offset, 3))
            for d in (21.0, 63.0, 105.0, 147.0, 189.0)
        ]

    cal_data = CalibrationData(
        x_lines=_lines(1.01, -0.3), y_lines=_lines(0.99, 0.5)
    )
    cal = solve_calibration(cal_data)
    assert abs(cal.x.scale - 1.01) < 5e-4 and abs(cal.x.offset + 0.3) < 1e-2
    assert abs(cal.y.scale - 0.99) < 5e-4 and abs(cal.y.offset - 0.5) < 1e-2

    # 校准导出：标签坐标按逆变换 (设计 - offset) / scale 修正
    lab0 = plan.pages[0].labels[0]
    svg_cal = build_svg(plan, 1, calibration=cal)
    exp_x = _fmt((lab0.x - cal.x.offset) / cal.x.scale)
    exp_w = _fmt(lab0.width / cal.x.scale)
    assert f'x="{exp_x}"' in svg_cal and f'width="{exp_w}"' in svg_cal
    assert "打印位置校准已应用" in svg_cal
    # 未校准导出仍是原坐标，且不含校准注释
    svg_plain = build_svg(plan, 1)
    assert f'x="{_fmt(lab0.x)}"' in svg_plain
    assert "打印位置校准已应用" not in svg_plain
    # 无校准参数的导出与新增功能前保持一致（同一渲染管线恒等分支）
    assert build_svg(plan, 1, True, True, False) == build_svg(
        plan, 1, True, True, False, calibration=None
    )

    # 基准点重合：设计坐标重复 / 实测坐标全部相同，均拒绝
    def _expect_cal_error(data: CalibrationData, key: str) -> None:
        try:
            solve_calibration(data)
        except ValueError as exc:
            assert key in str(exc), f"错误信息未包含「{key}」: {exc}"
            return
        raise AssertionError(f"校准数据应被拦截（{key}）却通过求解")

    same_design = [
        CalibrationLine(design=100.0, measured=100.0 + i) for i in range(3)
    ]
    _expect_cal_error(
        CalibrationData(x_lines=same_design, y_lines=_lines(1.0, 0.0)),
        "基准点重合",
    )
    same_measured = [
        CalibrationLine(design=float(d), measured=50.0)
        for d in (21.0, 63.0, 105.0)
    ]
    _expect_cal_error(
        CalibrationData(x_lines=same_measured, y_lines=_lines(1.0, 0.0)),
        "基准点重合",
    )
    # 实测顺序颠倒（从右往左读数），拒绝
    reversed_rows = [
        CalibrationLine(design=21.0, measured=190.0),
        CalibrationLine(design=63.0, measured=148.0),
        CalibrationLine(design=105.0, measured=105.0),
        CalibrationLine(design=147.0, measured=62.0),
        CalibrationLine(design=189.0, measured=20.0),
    ]
    _expect_cal_error(
        CalibrationData(x_lines=reversed_rows, y_lines=_lines(1.0, 0.0)),
        "顺序",
    )
    # 补偿后内容超出纸张：offset=50 使修正后内容整体左移 50mm
    cal_shift = solve_calibration(
        CalibrationData(x_lines=_lines(1.0, 50.0), y_lines=_lines(1.0, 0.0))
    )
    try:
        ensure_calibrated_bounds(plan, 1, cal_shift)
    except ValueError as exc:
        assert "超出纸张" in str(exc), str(exc)
    else:
        raise AssertionError("补偿后标签出界却未拦截校准导出")

    # 校准测试页：横竖两组刻线及设计坐标标注齐全
    page_svg = build_calibration_page(210, 297)
    assert 'class="vline"' in page_svg and 'class="hline"' in page_svg
    assert "X1=21" in page_svg and "X5=189" in page_svg
    assert "Y1=29.7" in page_svg and "Y5=267.3" in page_svg

    # 校准导出的角线避让按补偿后坐标判定：纸边映射为 (0-offset)/scale，
    # 邻签矩形也整体逆变换；角线残段必须落在补偿后的纸内且不进补偿后邻签
    tight_cal_svg = build_svg(
        tight_plan, 1, show_annotations=False, calibration=cal
    )

    def _inv(v: float, scale: float, offset: float) -> float:
        return (v - offset) / scale

    cal_paper = (
        _inv(0, cal.x.scale, cal.x.offset),
        _inv(0, cal.y.scale, cal.y.offset),
        _inv(40, cal.x.scale, cal.x.offset),
        _inv(60, cal.y.scale, cal.y.offset),
    )
    cal_rects = [
        (
            _inv(l.x, cal.x.scale, cal.x.offset),
            _inv(l.y, cal.y.scale, cal.y.offset),
            _inv(l.x + l.width, cal.x.scale, cal.x.offset),
            _inv(l.y + l.height, cal.y.scale, cal.y.offset),
        )
        for l in tight_plan.pages[0].labels
    ]
    for sx0, sy0, sx1, sy1 in _cut_segments_of(tight_cal_svg):
        assert sx0 >= cal_paper[0] - 1e-6 and sy0 >= cal_paper[1] - 1e-6, (
            f"校准角线越过补偿后纸边: {(sx0, sy0, sx1, sy1)} / {cal_paper}"
        )
        assert sx1 <= cal_paper[2] + 1e-6 and sy1 <= cal_paper[3] + 1e-6
        for rx0, ry0, rx1, ry1 in cal_rects:
            assert not (
                sx0 < rx1 - 1e-6 and sx1 > rx0 + 1e-6
                and sy0 < ry1 - 1e-6 and sy1 > ry0 + 1e-6
            ), f"校准角线侵入补偿后邻签: {(sx0, sy0, sx1, sy1)} / {(rx0, ry0, rx1, ry1)}"

    # 校准导出的角线同样避让补偿后的首页占用区
    occ_cal_svg = build_svg(occ_plan, 1, calibration=cal)
    zc = (
        _inv(z.x, cal.x.scale, cal.x.offset),
        _inv(z.y, cal.y.scale, cal.y.offset),
        _inv(z.x + z.width, cal.x.scale, cal.x.offset),
        _inv(z.y + z.height, cal.y.scale, cal.y.offset),
    )
    for sx0, sy0, sx1, sy1 in _cut_segments_of(occ_cal_svg):
        assert not (
            sx0 < zc[2] - 1e-6 and sx1 > zc[0] + 1e-6
            and sy0 < zc[3] - 1e-6 and sy1 > zc[1] + 1e-6
        ), f"校准角线侵入补偿后占用区: {(sx0, sy0, sx1, sy1)} / {zc}"

    # ---- 流水号：按位置分配、跨页/占用区/旋转不跳号不重号、未排入不占号 ----
    # 场景：首页带占用区、允许旋转、份数跨页；编号应按
    # 页码 → 页内上→下 → 左→右 的实际位置连续分配
    ser_req = LayoutRequest(
        page_width=100, page_height=80,
        margin_top=5, margin_right=5, margin_bottom=5, margin_left=5,
        gap_x=2, gap_y=2,
        labels=[
            LabelSpec(
                name="序列品", width=28, height=14, count=30, allow_rotate=True,
                serial=SerialConfig(prefix="SN-", start=7, pad=4),
            ),
            LabelSpec(name="无编号", width=20, height=10, count=5),
        ],
        occupied_zones=[OccupiedZone(x=30, y=5, width=30, height=20)],
    )
    ser_plan = compute_layout(ser_req)
    assert len(ser_plan.pages) >= 2  # 确认场景确实跨页
    s0 = ser_plan.summaries[0]
    assert s0.placed == 30 and s0.unplaced == 0
    serials = [
        lab.serial for p in ser_plan.pages for lab in p.labels
        if lab.type_index == 0
    ]
    assert all(s is not None for s in serials)
    assert len(serials) == s0.placed
    assert len(set(serials)) == len(serials)  # 无重号
    # 按 (页码, y, x) 重排后必须是 SN-0007 起的连续编号（无跳号）
    ordered = [
        lab.serial
        for p in ser_plan.pages
        for lab in sorted(
            (l for l in p.labels if l.type_index == 0),
            key=lambda l: (l.y, l.x),
        )
    ]
    expected = [format_serial("SN-", 7, 4, k) for k in range(s0.placed)]
    assert ordered == expected, f"编号分配顺序错误: {ordered}"
    # 汇总给出本次编号范围；末号 = 起始 + 排入数 - 1（未排入不占号）
    assert s0.serial_first == "SN-0007"
    assert s0.serial_last == expected[-1]
    # 未启用编号的规格不分配序号
    assert all(
        lab.serial is None for p in ser_plan.pages for lab in p.labels
        if lab.type_index == 1
    )
    assert ser_plan.summaries[1].serial_first is None
    # 预览（含占用区）与下载 / 打印的 SVG 在标签内显示同一编号
    p1_serials = [
        lab.serial for lab in ser_plan.pages[0].labels if lab.type_index == 0
    ]
    ser_prev = build_svg(ser_plan, 1, show_occupied=True)
    ser_export = build_svg(ser_plan, 1)
    assert 'class="serial"' in ser_prev and 'class="serial"' in ser_export
    for text in p1_serials:
        assert text in ser_prev and text in ser_export, f"SVG 缺少编号 {text}"
    # 未启用编号时 SVG 保持现有标注，不出现编号元素
    plain_plan = compute_layout(LayoutRequest(
        page_width=100, page_height=60,
        labels=[LabelSpec(name="普通", width=30, height=20, count=2)],
    ))
    assert 'class="serial"' not in build_svg(plain_plan, 1)
    # 编号中的特殊字符在 SVG 中转义
    esc_plan = compute_layout(LayoutRequest(
        page_width=120, page_height=60,
        labels=[LabelSpec(
            name="转义", width=40, height=15, count=1,
            serial=SerialConfig(prefix="A&<", start=1, pad=0),
        )],
    ))
    assert "A&amp;&lt;1" in build_svg(esc_plan, 1)
    # 旋转标签同样参与连续编号（窄页迫使 20×8 旋转为 8×20）
    rot_plan = compute_layout(LayoutRequest(
        page_width=15, page_height=60,
        labels=[LabelSpec(
            name="旋转品", width=20, height=8, count=2, allow_rotate=True,
            serial=SerialConfig(prefix="R-", start=1, pad=3),
        )],
    ))
    rot_labels = rot_plan.pages[0].labels
    assert all(l.rotated for l in rot_labels)
    assert [l.serial for l in rot_labels] == ["R-001", "R-002"]
    rot_svg = build_svg(rot_plan, 1)
    assert "R-001" in rot_svg and "R-002" in rot_svg
    # 启用编号但全部未排入：不消耗编号，范围为 null
    none_placed = compute_layout(LayoutRequest(
        page_width=50, page_height=50,
        labels=[LabelSpec(
            name="超大", width=80, height=80, count=2,
            serial=SerialConfig(prefix="B", start=3, pad=2),
        )],
    ))
    assert none_placed.summaries[0].placed == 0
    assert none_placed.summaries[0].serial_first is None
    assert none_placed.summaries[0].serial_last is None
    # 编号放不进标签：明确中文错误，不产生排版结果（避免文字越界）
    def _expect_serial_error(spec: LabelSpec) -> None:
        bad = LayoutRequest(
            page_width=100, page_height=60, labels=[spec],
        )
        try:
            compute_layout(bad)
        except ValueError as exc:
            assert "放不进" in str(exc), str(exc)
            return
        raise AssertionError(f"编号放不进标签却未报错: {spec}")

    _expect_serial_error(LabelSpec(
        name="小标签", width=14, height=6, count=1,
        serial=SerialConfig(prefix="SERIAL-NUMBER-", start=1, pad=12),
    ))
    _expect_serial_error(LabelSpec(
        name="扁标签", width=40, height=2.5, count=1,
        serial=SerialConfig(prefix="A", start=1, pad=0),
    ))
    # 字号折算函数本身：够大给字号、太小返回 None
    assert serial_font_size("A-1", 30, 10) is not None
    assert serial_font_size("X" * 30, 10, 5) is None


    print(f"[bootstrap] 自检通过（FastAPI {fastapi.__version__}, "
          f"Pydantic {pydantic.VERSION}）")
    print("[bootstrap] 初始化完成，等待 uvicorn 启动 ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
