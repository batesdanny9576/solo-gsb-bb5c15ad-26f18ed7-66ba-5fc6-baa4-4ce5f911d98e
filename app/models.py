"""请求 / 响应数据模型（单位均为毫米）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LabelSpec(BaseModel):
    """一种标签规格及其需要的份数。"""

    name: str = Field("", description="标签名称")
    width: float = Field(..., gt=0, description="标签宽度 mm")
    height: float = Field(..., gt=0, description="标签高度 mm")
    count: int = Field(..., ge=1, description="需要份数")
    allow_rotate: bool = Field(False, description="是否允许旋转 90 度排版")


class OccupiedZone(BaseModel):
    """首张纸上已被占用的区域（如已贴过的标签），单位毫米。

    坐标原点为纸张左上角。排版时视为障碍，新标签与其保持裁切间距；
    仅作用于第一页，放不下的份数续到后续空白页。
    """

    x: float = Field(..., description="左上角横坐标 mm")
    y: float = Field(..., description="左上角纵坐标 mm")
    width: float = Field(..., gt=0, description="占用区宽度 mm")
    height: float = Field(..., gt=0, description="占用区高度 mm")


class LayoutRequest(BaseModel):
    """拼版参数。"""

    page_width: float = Field(..., gt=0, description="纸张宽度 mm")
    page_height: float = Field(..., gt=0, description="纸张高度 mm")
    margin_top: float = Field(0, ge=0)
    margin_right: float = Field(0, ge=0)
    margin_bottom: float = Field(0, ge=0)
    margin_left: float = Field(0, ge=0)
    gap_x: float = Field(0, ge=0, description="水平裁切间距 mm")
    gap_y: float = Field(0, ge=0, description="垂直裁切间距 mm")
    labels: list[LabelSpec] = Field(..., min_length=1)
    occupied_zones: list[OccupiedZone] = Field(
        default_factory=list, description="首张纸占用区（障碍），可多个"
    )


class PlacedLabel(BaseModel):
    """单个已排入标签（毫米坐标，原点为纸张左上角）。"""

    type_index: int
    name: str
    x: float
    y: float
    width: float
    height: float
    rotated: bool


class PageLayout(BaseModel):
    page: int
    width: float
    height: float
    labels: list[PlacedLabel]


class LabelSummary(BaseModel):
    type_index: int
    name: str
    width: float
    height: float
    requested: int
    placed: int
    rotated: int
    unplaced: int


class LayoutResponse(BaseModel):
    pages: list[PageLayout]
    summaries: list[LabelSummary]
    total_requested: int
    total_placed: int
    warnings: list[str]
    # 回显参数，供绘制 / 导出使用，保证预览与导出同源
    page_width: float
    page_height: float
    margin_top: float
    margin_right: float
    margin_bottom: float
    margin_left: float
    gap_x: float
    gap_y: float
    occupied_zones: list[OccupiedZone] = Field(
        default_factory=list, description="回显首张纸占用区"
    )


class CalibrationLine(BaseModel):
    """一条基准刻线：设计坐标与纸上实测坐标（毫米）。"""

    design: float = Field(..., description="刻线的设计坐标 mm")
    measured: float = Field(..., description="刻线在纸上的实测坐标 mm")


class CalibrationData(BaseModel):
    """打印位置校准录入：横、纵两组基准刻线，各至少 2 条。

    x_lines 为纵向刻线（校准 X 方向走纸误差），
    y_lines 为横向刻线（校准 Y 方向走纸误差）。
    """

    x_lines: list[CalibrationLine] = Field(
        ..., min_length=2, description="纵向刻线（测 X），至少 2 条"
    )
    y_lines: list[CalibrationLine] = Field(
        ..., min_length=2, description="横向刻线（测 Y），至少 2 条"
    )


class AxisCalibration(BaseModel):
    """单方向线性走纸误差：实测坐标 = scale × 设计坐标 + offset。"""

    scale: float = Field(..., description="缩放系数（理想为 1）")
    offset: float = Field(..., description="平移偏移 mm（理想为 0）")


class Calibration(BaseModel):
    """X / Y 两个方向的线性走纸误差拟合结果。"""

    x: AxisCalibration
    y: AxisCalibration


class SvgRequest(BaseModel):
    """SVG 导出请求：参数与 /api/layout 完全一致，附带页码与显示开关。"""

    page: int = Field(1, ge=1)
    show_margins: bool = True
    show_annotations: bool = True
    show_occupied: bool = Field(
        False,
        description="是否绘制首张纸占用区（预览用；下载/打印应为 false，只含新标签）",
    )
    params: LayoutRequest
    calibration: CalibrationData | None = Field(
        None,
        description="打印位置校准数据；提供时按拟合误差的逆变换修正导出坐标"
        "（页面常规预览不要传，保持原样）",
    )


class SvgResponse(BaseModel):
    svg: str
