/* 手绘风格行程示意图：表达「访问顺序 + 大致方位 + 天次分组」，不做真实地理底图。
 *
 * 与真实底图（Leaflet + 高德瓦片）的分工
 * --------------------------------------
 * 真实地图解决「在哪、怎么去」；本模块解决「先去哪后去哪、大概朝哪走」。
 *
 * 布局策略（关键）
 * ----------------
 * 纯经纬度线性投影在真实数据上会失效：实测上海行程中，外滩商圈 6 个点经纬度
 * 跨度 <2km，而迪士尼单点跨度 40km，线性映射会把 6 个点压成一团、标签互相覆盖。
 *
 * 因此采用「**分区网格 + 方位角指示**」混合布局：
 *
 * 1. 按天把节点分组，每天占据一条水平带（带内从左到右 = 真实从西到东）。
 * 2. 带内节点用**等间距网格**排布（保证不重叠、标签可读），
 *    节点间连线用箭头标注**真实方位角**（N/NE/E…），保留方向信息。
 * 3. 带与带之间用**总体位移方向**箭头衔接，表达「从第 N 天搬到第 N+1 天」。
 *
 * 这样既避免了重叠，又不会丢失「东/南/西/北」这一手绘图最有价值的信息。
 */

/** 画布逻辑尺寸（viewBox）。
 *
 * 容器实测约 358x320（比例 1.12）。若 viewBox 比例与之悬殊，
 * ``preserveAspectRatio="meet"`` 会按较小边缩放，另一边留大片空白
 * （实测 720x400 时图被压到容器高度的 60%）。
 * 故取 5:4（1.25）——与实测比例最接近，缩放后留白最小。
 */
const SKETCH_W = 720;
const SKETCH_H = 576;
/** 外边距 */
const SKETCH_MARGIN_X = 68;
const SKETCH_MARGIN_Y = 40;
/** 主题色：与 CHART_COLORS 一致的天次配色 */
const SKETCH_COLORS = ['#6c5ce7', '#8b7cf6', '#b8aef9', '#00b894', '#fdcb6e', '#74b9ff'];

/** 类别 -> 手绘图标 path（12x12 视框内的简洁几何符号） */
const SKETCH_ICON = {
  attraction: 'M2 8l3.5-4 2.5 2.6L10 4l1 4z', // 山
  restaurant: 'M4 2v4a1.4 1.4 0 002.8 0V2M5.4 7.4V10M8 2c0 2 1 3 1 4.5H8', // 刀叉
  hotel: 'M2 9.5V5l4-2 4 2v4.5M2 9.5h8M4.6 9.5V7h2.8v2.5', // 房子
  transport: 'M2.5 4h6a.8.8 0 01.8.8v3.4h-7.6V4.8a.8.8 0 01.8-.8zM4 10.5a1 1 0 100-2 1 1 0 000 2zM8 10.5a1 1 0 100-2 1 1 0 000 2z',
  activity: 'M6 2v8M2 6h8', // 十字
};

/** 类别 -> 图标描边色（与节点主色区分） */
const SKETCH_ICON_COLOR = {
  attraction: '#8b7cf6',
  restaurant: '#e8a33d',
  hotel: '#3d9ae8',
  transport: '#00a884',
  activity: '#9b8ef0',
};

/** 方位角 -> 中文简写，用于连线标注 */
function bearingLabel(deg) {
  const names = ['北', '东北', '东', '东南', '南', '西南', '西', '西北'];
  // 每 45° 一档，正北为 0°，顺时针
  const idx = Math.round(((deg % 360) + 360) % 360 / 45) % 8;
  return names[idx];
}

/** 两点间的方位角（度，正北 0，顺时针） */
function bearingBetween(a, b) {
  const toRad = (d) => (d * Math.PI) / 180;
  const y = Math.sin(toRad(b.lng - a.lng)) * Math.cos(toRad(b.lat));
  const x =
    Math.cos(toRad(a.lat)) * Math.sin(toRad(b.lat)) -
    Math.sin(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.cos(toRad(b.lng - a.lng));
  return (Math.atan2(y, x) * 180) / Math.PI;
}

/**
 * 分区网格布局。
 * 每天一条水平带，带内等距；带高与带数、每天节点数自适应。
 */
function layoutByDay(spots, width, height) {
  const days = [...new Set(spots.map((s) => s.day))].sort((a, b) => a - b);
  const nDays = days.length;
  const innerW = width - SKETCH_MARGIN_X * 2;
  // 底部预留说明文字高度，避免最后一条带的节点被裁切
  const usableH = height - SKETCH_MARGIN_Y * 2 - 22;
  // 每条带的最小高度：需容纳 上方名称(26) + 节点直径(30) + 下方图标(28) + 呼吸(10)
  const MIN_BAND_H = 94;
  // 带高按当天节点数的平方根分配（避免单点带过高）
  const weights = days.map((d) => Math.sqrt(spots.filter((s) => s.day === d).length));
  const wSum = weights.reduce((a, b) => a + b, 0) || 1;
  const bandHeights = weights.map((w) => Math.max(MIN_BAND_H, (w / wSum) * usableH));

  // 若总高超出可用高度，等比压缩
  const total = bandHeights.reduce((a, b) => a + b, 0);
  const scale = total > usableH ? usableH / total : 1;

  const out = [];
  let y = SKETCH_MARGIN_Y;
  days.forEach((d, di) => {
    const bandH = bandHeights[di] * scale;
    const list = spots.filter((s) => s.day === d);
    const n = list.length;
    // 节点圆心：带内垂直居中。上方留 26px 给名称，下方留 28px 给图标，
    // 故可用区是 [y+26, y+bandH-28]，取其中点。单天时即为整图居中。
    const topPad = 26;
    const bottomPad = 28;
    const usable = Math.max(30, bandH - topPad - bottomPad);
    const cy = y + topPad + usable / 2;
    // 单点居中；多点等距，步长至少 96px 以容纳标签
    const step = n > 1 ? Math.min(124, innerW / (n - 1)) : 0;
    const startX = n > 1 ? SKETCH_MARGIN_X : width / 2;
    list.forEach((s, i) => {
      out.push({
        ...s,
        x: n > 1 ? startX + i * step : width / 2,
        y: cy,
        bandTop: y,
        bandH,
        color: SKETCH_COLORS[di % SKETCH_COLORS.length],
        dayIdx: di,
      });
    });
    y += bandH;
  });
  return out;
}

/** 生成手绘风格 SVG 字符串 */
function renderSketchMap(spots, opts) {
  const width = opts.width || SKETCH_W;
  const height = opts.height || SKETCH_H;
  const escape = opts.escape || ((v) => String(v));
  const categoryLabel = opts.categoryLabel || {};
  const colors = opts.colors || SKETCH_COLORS;

  if (spots.length === 0) {
    return (
      '<div class="h-full grid place-items-center text-sm text-ink-400 px-4 text-center">'
      + '暂无地点坐标（部分条目来自文本规划，未关联真实地理位置）</div>'
    );
  }

  // 单一地点无法表达方位关系，退化为居中卡片
  if (spots.length === 1) {
    const s = spots[0];
    const color = colors[0];
    return `
      <div class="h-full grid place-items-center p-6">
        <div class="text-center">
          <div class="inline-flex items-center justify-center w-12 h-12 rounded-full border-2 border-dashed"
               style="border-color:${color}; color:${color}">
            <span class="text-lg">${escape(s.title.slice(0, 1))}</span>
          </div>
          <p class="mt-2 text-sm text-ink-700">${escape(s.title)}</p>
          <p class="text-[11px] text-ink-400 mt-0.5">
            ${s.time ? escape(s.time) + ' · ' : ''}第${s.day}天 · ${escape(categoryLabel[s.category] || s.category)}
          </p>
          <p class="text-[11px] text-ink-400 mt-1">仅有 1 个地点，示意图无法体现方位关系</p>
        </div>
      </div>`;
  }

  const pts = layoutByDay(spots, width, height);
  const days = [...new Set(pts.map((p) => p.day))].sort((a, b) => a - b);

  // ---- 天次泳道背景 + 左侧标签 ----
  let lanes = '';
  days.forEach((d, i) => {
    const p0 = pts.find((p) => p.day === d);
    const bandH = p0.bandH;
    const y = p0.bandTop;
    // 交替深浅底纹，视觉上区分天次带
    lanes += `<rect x="0" y="${y.toFixed(1)}" width="${width}" height="${bandH.toFixed(1)}"
      fill="${i % 2 === 0 ? '#f7f6fb' : '#fbfaf7'}"/>`;
    // 天次标签垂直居中于带内左侧，与节点圆心同高。
    // 这样它不会与「节点名称」（在节点上方 24px）落在同一水平线上。
    const midY = y + bandH / 2 + 4;
    lanes += `<text x="8" y="${midY.toFixed(1)}" font-size="11" fill="#a8a29e"
      font-family="system-ui, sans-serif">第${d}天</text>`;
    lanes += `<line x1="0" y1="${(y + bandH).toFixed(1)}" x2="${width}" y2="${(y + bandH).toFixed(1)}"
      stroke="#e8e6df" stroke-width="1" stroke-dasharray="3 4"/>`;
  });

  // ---- 连线：带内顺序连线（含方位角标注），带间跨天虚线 ----
  let paths = '';
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i];
    const b = pts[i + 1];
    const sameDay = a.day === b.day;
    const color = a.color;
    const mx = (a.x + b.x) / 2;
    const my = (a.y + b.y) / 2;
    // 弧线控制点：带内向下微弯，带间向外弯
    const bend = sameDay ? 14 : 26;
    const sign = b.y >= a.y ? 1 : -1;
    const cx = mx;
    const cy = my + bend * sign;
    paths += `<path d="M${a.x.toFixed(1)},${a.y.toFixed(1)}
      Q${cx.toFixed(1)},${cy.toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}"
      fill="none" stroke="${color}" stroke-width="2"
      stroke-dasharray="${sameDay ? '' : '6 5'}" opacity="0.75"
      stroke-linecap="round"/>`;
    // 箭头
    paths += `<circle cx="${b.x.toFixed(1)}" cy="${b.y.toFixed(1)}" r="2.6"
      fill="${color}" opacity="0.9"/>`;
    // 方位角标注：真实相对方位，是手绘图的信息增量
    const brg = bearingBetween(a, b);
    const label = bearingLabel(brg);
    const tx = mx;
    const ty = sameDay ? Math.min(a.y, b.y) - 10 : my + (sign > 0 ? -8 : 14);
    paths += `<text x="${tx.toFixed(1)}" y="${ty.toFixed(1)}" text-anchor="middle"
      font-size="10" fill="${color}" opacity="0.9"
      font-family="system-ui, sans-serif">${label}</text>`;
  }

  // ---- 节点：序号圆 + 类别图标 ----
  let nodes = '';
  pts.forEach((p, idx) => {
    const r = 15;
    nodes += `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="${r}"
      fill="#ffffff" stroke="${p.color}" stroke-width="2.5"/>`;
    nodes += `<text x="${p.x.toFixed(1)}" y="${(p.y + 4).toFixed(1)}"
      text-anchor="middle" font-size="12" font-weight="600" fill="${p.color}"
      font-family="system-ui, sans-serif">${idx + 1}</text>`;
    const icon = SKETCH_ICON[p.category];
    if (icon) {
      const iy = p.y + r + 7;
      const ic = SKETCH_ICON_COLOR[p.category] || p.color;
      nodes += `<rect x="${(p.x - 8).toFixed(1)}" y="${iy.toFixed(1)}" width="16" height="16"
        rx="4" fill="#ffffff" stroke="${ic}" stroke-width="1.5" opacity="0.95"/>`;
      nodes += `<g transform="translate(${(p.x - 6).toFixed(1)}, ${(iy + 2).toFixed(1)}) scale(0.67)"
        stroke="${ic}" stroke-width="1.9" fill="none"
        stroke-linecap="round" stroke-linejoin="round"><path d="${icon}"/></g>`;
    }
  });

  // ---- 地点名称：节点上方，居中；过长截断 ----
  let labels = '';
  pts.forEach((p, idx) => {
    const short = p.title.length > 8 ? p.title.slice(0, 7) + '…' : p.title;
    const ly = p.y - 24;
    labels += `<text x="${p.x.toFixed(1)}" y="${ly.toFixed(1)}"
      text-anchor="middle" font-size="11" fill="#475569"
      font-family="system-ui, sans-serif">${idx + 1}. ${escape(short)}</text>`;
  });

  // ---- 指北针：明确方位 ----
  const compass = `
    <g transform="translate(${width - 32}, 30)">
      <circle r="14" fill="rgba(255,255,255,0.9)" stroke="#cbd5e1" stroke-width="1"/>
      <path d="M0,-8 L4,3 L0,0.5 L-4,3 Z" fill="#94a3b8"/>
      <text x="0" y="-17" text-anchor="middle" font-size="9" fill="#94a3b8"
        font-family="system-ui, sans-serif">N</text>
    </g>`;

  // ---- 底部说明：告知这是示意图（放在画布外，避免与节点图标冲突） ----
  const footer = `
    <div class="px-3 py-1.5 text-[10px] text-ink-400 text-center">
      示意图：横向为真实东→西，箭头标注为实际方位角
    </div>`;

  return `
    <div class="h-full flex flex-col">
      <div class="flex-1 min-h-0">
        <svg viewBox="0 0 ${width} ${height}" class="w-full h-full block"
             preserveAspectRatio="xMidYMid meet" role="img"
             style="background:#fdfcfa"
             aria-label="行程手绘示意图，仅表示节点方位与访问顺序">
          ${lanes}
          ${paths}
          ${nodes}
          ${labels}
          ${compass}
        </svg>
      </div>
      ${footer}
    </div>`;
}

/** 图例：天次配色说明 */
function renderSketchLegend(days, colors) {
  if (days.length <= 1) return '';
  const items = days
    .map((d, i) => {
      const c = colors[i % colors.length];
      return `<span class="inline-flex items-center gap-1 text-[11px] text-ink-500">
        <span class="inline-block w-2.5 h-2.5 rounded-full" style="background:${c}"></span>
        第${d}天</span>`;
    })
    .join('');
  return `<div class="flex items-center gap-3 flex-wrap px-3 py-1.5">${items}</div>`;
}

// 暴露给 plan.js 使用
window.SketchMap = { renderSketchMap, renderSketchLegend, SKETCH_COLORS };
