/* 小批量标签拼版工具 —— 前端逻辑（原生 JS，无构建） */
"use strict";

const $ = (id) => document.getElementById(id);

let lastPlan = null;     // 最近一次 /api/layout 返回的拼版结果
let lastParams = null;   // 对应的请求参数
let currentPage = 1;
let calibrationData = null;  // 已通过求解校验的校准录入（导出时随 /api/svg 发送）

/* ---------------- 标签行 ---------------- */

function addLabelRow(spec) {
  const data = spec || { name: "", width: 60, height: 30, count: 10, allow_rotate: false };
  const row = document.createElement("div");
  row.className = "label-row";
  row.innerHTML = `
    <input type="text" class="l-name" placeholder="标签名称" value="${escapeAttr(data.name)}" />
    <input type="number" class="l-w" value="${data.width}" step="0.5" min="0.1" title="宽 mm" />
    <input type="number" class="l-h" value="${data.height}" step="0.5" min="0.1" title="高 mm" />
    <input type="number" class="l-c" value="${data.count}" step="1" min="1" title="份数" />
    <label class="rot-cell" title="允许旋转 90°">
      <input type="checkbox" class="l-r" ${data.allow_rotate ? "checked" : ""} />旋转
    </label>
    <button type="button" class="del" title="删除">✕</button>
  `;
  row.querySelector(".del").addEventListener("click", () => row.remove());
  $("label-rows").appendChild(row);
}

function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

/* ---------------- 首张纸占用区行 ---------------- */

function addZoneRow(zone) {
  const data = zone || { x: 20, y: 20, width: 80, height: 40 };
  const row = document.createElement("div");
  row.className = "zone-row";
  row.innerHTML = `
    <span class="z-idx"></span>
    <input type="number" class="z-x" value="${data.x}" step="0.5" title="左上角 X（距纸左）mm" />
    <input type="number" class="z-y" value="${data.y}" step="0.5" title="左上角 Y（距纸顶）mm" />
    <input type="number" class="z-w" value="${data.width}" step="0.5" min="0.1" title="占用区宽 mm" />
    <input type="number" class="z-h" value="${data.height}" step="0.5" min="0.1" title="占用区高 mm" />
    <button type="button" class="del" title="删除">✕</button>
  `;
  row.querySelector(".del").addEventListener("click", () => {
    row.remove();
    refreshZoneIndex();
  });
  $("zone-rows").appendChild(row);
  refreshZoneIndex();
}

function refreshZoneIndex() {
  document.querySelectorAll(".zone-row").forEach((row, i) => {
    row.querySelector(".z-idx").textContent = `#${i + 1}`;
  });
}

/* ---------------- 参数收集 ---------------- */

function num(id) {
  return parseFloat($(id).value);
}

function collectParams() {
  const labels = [...document.querySelectorAll(".label-row")].map((row) => ({
    name: row.querySelector(".l-name").value.trim(),
    width: parseFloat(row.querySelector(".l-w").value),
    height: parseFloat(row.querySelector(".l-h").value),
    count: parseInt(row.querySelector(".l-c").value, 10),
    allow_rotate: row.querySelector(".l-r").checked,
  }));
  const occupied_zones = [...document.querySelectorAll(".zone-row")].map((row) => ({
    x: parseFloat(row.querySelector(".z-x").value),
    y: parseFloat(row.querySelector(".z-y").value),
    width: parseFloat(row.querySelector(".z-w").value),
    height: parseFloat(row.querySelector(".z-h").value),
  }));
  return {
    page_width: num("page-width"),
    page_height: num("page-height"),
    margin_top: num("margin-top"),
    margin_right: num("margin-right"),
    margin_bottom: num("margin-bottom"),
    margin_left: num("margin-left"),
    gap_x: num("gap-x"),
    gap_y: num("gap-y"),
    labels,
    occupied_zones,
  };
}

function validateParams(p) {
  const bad = (msg) => { throw new Error(msg); };
  for (const [name, v] of [
    ["纸宽", p.page_width], ["纸高", p.page_height],
  ]) if (!(v > 0)) bad(`${name}必须为正数。`);
  for (const z of p.occupied_zones) {
    if ([z.x, z.y, z.width, z.height].some((v) => !Number.isFinite(v))) {
      bad("占用区的 X / Y / 宽 / 高必须全部填写为数字。");
    }
    if (z.x < 0 || z.y < 0) bad("占用区左上角 X / Y 不能为负数。");
    if (z.width <= 0 || z.height <= 0) bad("占用区宽、高必须为正数。");
    if (z.x + z.width > p.page_width + 1e-6) {
      bad(`占用区越界：X + 宽 = ${+(z.x + z.width).toFixed(2)}mm，超过纸张宽度 ${p.page_width}mm。`);
    }
    if (z.y + z.height > p.page_height + 1e-6) {
      bad(`占用区越界：Y + 高 = ${+(z.y + z.height).toFixed(2)}mm，超过纸张高度 ${p.page_height}mm。`);
    }
  }
}

async function apiError(resp, fallback) {
  let detail;
  try { detail = (await resp.json()).detail; } catch { detail = null; }
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join("；") || fallback;
  return detail || fallback;
}

async function fetchSvg(page, { showOccupied = false, withCalibration = false } = {}) {
  const body = {
    page,
    show_margins: $("show-margins").checked,
    show_annotations: $("show-annotations").checked,
    show_occupied: showOccupied,
    params: lastParams,
  };
  // 仅下载 / 打印携带校准；页面常规预览不带，坐标保持原样
  if (withCalibration) {
    const cal = activeCalibration();
    if (cal) body.calibration = cal;
  }
  const resp = await fetch("/api/svg", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(await apiError(resp, "SVG 生成失败"));
  return resp.text();
}

async function renderPage() {
  if (!lastPlan) return;
  // 预览显示灰色占用区（仅第一页有），与下载/打印的 SVG 坐标完全一致
  const svg = await fetchSvg(currentPage, { showOccupied: true });
  $("svg-host").innerHTML = svg;
  $("page-info").textContent = `${currentPage} / ${lastPlan.pages.length}`;
  $("prev-page").disabled = currentPage <= 1;
  $("next-page").disabled = currentPage >= lastPlan.pages.length;
}

/* ---------------- 结果汇总 ---------------- */

function renderSummary(plan) {
  const el = $("summary");
  el.classList.remove("empty");
  const allPlaced = plan.total_placed === plan.total_requested;
  const zoneInfo = plan.occupied_zones.length
    ? `，首张纸含 <strong>${plan.occupied_zones.length}</strong> 个占用区（灰色）`
    : "";
  let html = `<div class="totals">
      共 <strong>${plan.total_requested}</strong> 个标签，
      排入 <strong class="ok">${plan.total_placed}</strong> 个，
      使用 <strong>${plan.pages.length}</strong> 张纸${zoneInfo}
      ${allPlaced ? "（全部排入 ✔）" : `，<strong style="color:var(--danger)">${plan.total_requested - plan.total_placed} 个未排入</strong>`}
    </div>
    <table>
      <thead><tr>
        <th>标签</th><th>规格 (mm)</th><th>需求</th><th>排入</th><th>其中旋转</th><th>未排入</th>
      </tr></thead><tbody>`;
  for (const s of plan.summaries) {
    html += `<tr class="${s.unplaced > 0 ? "unplaced" : ""}">
      <td>${escapeHtml(s.name || `#${s.type_index + 1}`)}</td>
      <td>${s.width} × ${s.height}</td>
      <td>${s.requested}</td>
      <td>${s.placed}</td>
      <td>${s.rotated}</td>
      <td>${s.unplaced > 0 ? s.unplaced : "—"}</td>
    </tr>`;
  }
  html += "</tbody></table>";
  if (plan.warnings.length) {
    html += `<div class="warn"><strong>提示：</strong><ul>${
      plan.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")
    }</ul></div>`;
  }
  el.innerHTML = html;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* ---------------- 打印位置校准 ---------------- */

function calRow(tag, cls, design) {
  const row = document.createElement("div");
  row.className = "cal-row";
  row.innerHTML = `
    <span class="z-idx">${tag}</span>
    <input type="number" class="${cls}-design" value="${design}" step="0.1" title="设计坐标 mm" />
    <input type="number" class="${cls}-measured" step="0.1" placeholder="实测" title="纸上实测坐标 mm" />
  `;
  return row;
}

function fillCalMarks(marks) {
  const hosts = [
    [$("cal-x-rows"), "X", "cx", marks.x_marks],
    [$("cal-y-rows"), "Y", "cy", marks.y_marks],
  ];
  for (const [host, tag, cls, designs] of hosts) {
    host.innerHTML = "";
    designs.forEach((d, i) => host.appendChild(calRow(`${tag}${i + 1}`, cls, d)));
  }
  invalidateCalibration("");
}

async function refreshCalMarks(note) {
  const w = num("page-width"), h = num("page-height");
  if (!(w > 0) || !(h > 0)) return;
  try {
    const resp = await fetch(
      `/api/calibration/marks?page_width=${w}&page_height=${h}`
    );
    if (!resp.ok) return;
    const marks = await resp.json();
    // 设计坐标未变化（如同纸张下再次点下载测试页）时保留已填实测值
    const cur = (sel) =>
      [...document.querySelectorAll(sel)].map((el) => parseFloat(el.value));
    const unchanged =
      JSON.stringify([marks.x_marks, marks.y_marks]) ===
      JSON.stringify([cur(".cx-design"), cur(".cy-design")]);
    if (!unchanged) fillCalMarks(marks);
    if (note) calNote(note);
  } catch { /* 后端不可用时保持表格原状 */ }
}

function collectCalibration() {
  const read = (sel) =>
    [...document.querySelectorAll(sel)].map((el) => parseFloat(el.value));
  const xd = read(".cx-design"), xm = read(".cx-measured");
  const yd = read(".cy-design"), ym = read(".cy-measured");
  if ([...xd, ...xm, ...yd, ...ym].some((v) => !Number.isFinite(v))) return null;
  return {
    x_lines: xd.map((d, i) => ({ design: d, measured: xm[i] })),
    y_lines: yd.map((d, i) => ({ design: d, measured: ym[i] })),
  };
}

function calResultHtml(cls, html) {
  const el = $("cal-result");
  el.className = `cal-result ${cls}`;
  el.innerHTML = html;
}
const calOk = (msg) => calResultHtml("ok", msg);
const calError = (msg) => calResultHtml("err", `<strong>无法校准：</strong>${escapeHtml(msg)}`);
const calNote = (msg) => calResultHtml("muted", escapeHtml(msg));

function invalidateCalibration(msg) {
  calibrationData = null;
  if (msg) calNote(msg);
}

function activeCalibration() {
  if (!$("cal-enabled").checked) return null;
  if (!calibrationData) {
    throw new Error(
      "已勾选应用校准，但尚无有效校准参数：请完整填写两组实测坐标后点击「② 计算 X / Y 校准参数」。"
    );
  }
  return calibrationData;
}

/* ---------------- 事件 ---------------- */

$("add-label").addEventListener("click", () => addLabelRow());
$("add-zone").addEventListener("click", () => addZoneRow());

$("preset").addEventListener("change", (e) => {
  if (!e.target.value) return;
  const [w, h] = e.target.value.split(",").map(parseFloat);
  $("page-width").value = w;
  $("page-height").value = h;
  refreshCalMarks("纸张预设已切换，设计坐标已更新；请重新打印测试页并测量。");
});
$("page-width").addEventListener("change", () =>
  refreshCalMarks("纸张尺寸已修改，设计坐标已更新；请重新打印测试页并测量。"));
$("page-height").addEventListener("change", () =>
  refreshCalMarks("纸张尺寸已修改，设计坐标已更新；请重新打印测试页并测量。"));

$("cal-download").addEventListener("click", async () => {
  const w = num("page-width"), h = num("page-height");
  if (!(w > 0) || !(h > 0)) { calError("请先填写有效的纸张尺寸。"); return; }
  await refreshCalMarks("");
  window.open(`/api/calibration/page?page_width=${w}&page_height=${h}`, "_blank");
  calNote("测试页已在新窗口打开：请 100% 缩放打印，测量后将读数填入实测列。");
});

$("cal-solve").addEventListener("click", async () => {
  const data = collectCalibration();
  if (!data) {
    calError("请完整填写两组刻线的设计坐标与纸上实测坐标。");
    return;
  }
  try {
    const resp = await fetch("/api/calibration/solve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!resp.ok) {
      calibrationData = null;
      $("cal-enabled").checked = false;
      calError(await apiError(resp, "校准求解失败"));
      return;
    }
    const solved = await resp.json();
    calibrationData = data;
    $("cal-enabled").checked = true;
    calOk(
      "X 方向：缩放 <strong>" + solved.x.scale + "</strong>、偏移 "
      + "<strong>" + solved.x.offset + "</strong>mm；"
      + "Y 方向：缩放 <strong>" + solved.y.scale + "</strong>、偏移 "
      + "<strong>" + solved.y.offset + "</strong>mm。"
      + "下载 / 打印的 SVG 将按逆变换补偿标签轮廓、裁切线与标注。"
    );
  } catch (err) {
    calError(err.message);
  }
});

// 表格行整体随纸张动态重建，输入监听委托给固定的行容器
$("cal-x-rows").addEventListener("input", () =>
  invalidateCalibration("校准数据已修改，请重新「计算 X / Y 校准参数」。"));
$("cal-y-rows").addEventListener("input", () =>
  invalidateCalibration("校准数据已修改，请重新「计算 X / Y 校准参数」。"));

$("params-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  lastParams = collectParams();
  currentPage = 1;
  try {
    validateParams(lastParams);
    const resp = await fetch("/api/layout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastParams),
    });
    if (!resp.ok) {
      const detail = (await resp.json()).detail;
      throw new Error(Array.isArray(detail)
        ? detail.map((d) => d.msg).join("；")
        : detail);
    }
    lastPlan = await resp.json();
    renderSummary(lastPlan);
    $("preview-wrap").hidden = false;
    await renderPage();
  } catch (err) {
    lastPlan = null;
    $("preview-wrap").hidden = true;
    $("summary").className = "summary";
    $("summary").innerHTML =
      `<div class="warn"><strong>参数错误：</strong>${escapeHtml(err.message)}</div>`;
  }
});

$("prev-page").addEventListener("click", async () => {
  if (currentPage > 1) { currentPage--; await renderPage(); }
});
$("next-page").addEventListener("click", async () => {
  if (lastPlan && currentPage < lastPlan.pages.length) {
    currentPage++; await renderPage();
  }
});

$("show-margins").addEventListener("change", renderPage);
$("show-annotations").addEventListener("change", renderPage);

function downloadBlob(text, filename, type) {
  const blob = new Blob([text], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

$("download-svg").addEventListener("click", async () => {
  try {
    const svg = await fetchSvg(currentPage, { withCalibration: true });
    downloadBlob(svg, `labels-page-${currentPage}.svg`, "image/svg+xml");
  } catch (err) {
    alert(err.message);
  }
});

$("download-all").addEventListener("click", async () => {
  try {
    activeCalibration();  // 提前校验校准勾选状态，避免中途失败
    for (let p = 1; p <= lastPlan.pages.length; p++) {
      const svg = await fetchSvg(p, { withCalibration: true });
      downloadBlob(svg, `labels-page-${p}.svg`, "image/svg+xml");
      await new Promise((r) => setTimeout(r, 150)); // 避免浏览器拦截批量下载
    }
  } catch (err) {
    alert(err.message);
  }
});

$("print-all").addEventListener("click", async () => {
  let svgs;
  try {
    activeCalibration();
    svgs = [];
    for (let p = 1; p <= lastPlan.pages.length; p++) {
      svgs.push(await fetchSvg(p, { withCalibration: true }));
    }
  } catch (err) {
    alert(err.message);
    return;
  }
  const w = lastPlan.page_width, h = lastPlan.page_height;
  const win = window.open("", "_blank");
  if (!win) { alert("浏览器拦截了打印窗口，请允许弹窗后重试。"); return; }
  win.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>打印标签</title>
    <style>
      @page { size: ${w}mm ${h}mm; margin: 0; }
      html,body { margin:0; padding:0; }
      .sheet { width:${w}mm; height:${h}mm; overflow:hidden; page-break-after:always; }
      .sheet:last-child { page-break-after:auto; }
      svg { display:block; width:${w}mm; height:${h}mm; }
    </style></head><body>
    ${svgs.map((s) => `<div class="sheet">${s}</div>`).join("")}
    </body></html>`);
  win.document.close();
  win.focus();
  setTimeout(() => win.print(), 400);
});

/* ---------------- 初始数据 ---------------- */

addLabelRow({ name: "品名标签", width: 60, height: 30, count: 20, allow_rotate: true });
addLabelRow({ name: "箱贴", width: 100, height: 70, count: 6, allow_rotate: false });
addLabelRow({ name: "小标", width: 40, height: 20, count: 50, allow_rotate: true });

// 按默认纸张尺寸预填校准测试页的设计坐标（实测列留空）
refreshCalMarks("");
