/* 分享只读页：加载快照 + 地图标记 + 费用分布 */

const SHARE_CATEGORY_LABEL = {
  attraction: '景点', restaurant: '餐饮', hotel: '住宿',
  transport: '交通', activity: '活动', note: '备注',
};
const SHARE_CHART_COLORS = ['#6c5ce7', '#8b7cf6', '#b8aef9', '#00b894', '#fdcb6e', '#74b9ff'];

function shareApp(token) {
  return {
    token,
    snapshot: null,
    loading: true,
    error: null,

    async init() {
      try {
        const res = await fetch(`/api/share/${this.token}`);
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          this.error = err.error?.message || '分享内容不存在或已过期';
        } else {
          this.snapshot = await res.json();
        }
      } catch {
        this.error = '网络错误，请稍后重试';
      } finally {
        this.loading = false;
      }
      this.$nextTick(() => {
        this._renderMap();
        this._renderChart();
      });
    },

    /* ---------- 路线示意（二维纯白 SVG，替代实际地图） ---------- */

    _renderMap() {
      if (!this.snapshot || !document.getElementById('route')) return;
      const container = document.getElementById('route');
      container.innerHTML = '';
      const plan = this.snapshot.plan;

      // 收集地点：跳过备注类、空标题
      const spots = [];
      plan.days.forEach((day) => {
        (day.items || []).forEach((item) => {
          const title = (item.title || '').trim();
          if (!title || item.category === 'note') return;
          spots.push({
            title,
            day: day.day_index,
            time: item.start_time ? item.start_time.slice(0, 5) : '',
            category: item.category || 'activity',
          });
        });
      });

      if (spots.length === 0) {
        container.innerHTML = '<div class="h-full grid place-items-center text-sm text-ink-400">暂无地点信息</div>';
        return;
      }

      const byDay = new Map();
      spots.forEach((s) => {
        if (!byDay.has(s.day)) byDay.set(s.day, []);
        byDay.get(s.day).push(s);
      });
      const dayKeys = [...byDay.keys()];

      const COL_W = 76;
      const ROW_H = 78;
      const PAD = 38;
      const R = 14;
      const maxCols = Math.max(...dayKeys.map((d) => byDay.get(d).length));
      const width = Math.max(PAD * 2 + maxCols * COL_W, 480);
      const height = PAD * 2 + dayKeys.length * ROW_H;

      const coord = (rowIdx, colIdx) => ({
        x: PAD + colIdx * COL_W,
        y: PAD + rowIdx * ROW_H,
      });

      const rowIndex = {};
      dayKeys.forEach((d, i) => { rowIndex[d] = i; });
      const colIndex = {};
      spots.forEach((s) => {
        const key = s.day + '::' + s.title;
        if (!(key in colIndex)) {
          colIndex[key] = byDay.get(s.day).indexOf(s);
        }
      });
      const posOf = (s) => coord(rowIndex[s.day], colIndex[s.day + '::' + s.title]);

      const dayPaths = [];
      const crossPaths = [];
      dayKeys.forEach((d, i) => {
        const list = byDay.get(d);
        for (let j = 0; j < list.length - 1; j++) {
          const a = posOf(list[j]);
          const b = posOf(list[j + 1]);
          dayPaths.push(this._seg(a, b));
        }
        if (i < dayKeys.length - 1) {
          const a = posOf(list[list.length - 1]);
          const bList = byDay.get(dayKeys[i + 1]);
          const b = posOf(bList[0]);
          crossPaths.push(this._seg(a, b));
        }
      });

      const dayColor = (i) => SHARE_CHART_COLORS[i % SHARE_CHART_COLORS.length];

      const nodeHtml = spots.map((s, idx) => {
        const p = posOf(s);
        const rowIdx = rowIndex[s.day];
        const color = dayColor(rowIdx);
        const label = String(idx + 1);
        return `
          <g class="route-node" data-title="${window.escapeHtml(s.title)}" data-day="${s.day}" data-time="${window.escapeHtml(s.time)}"
             transform="translate(${p.x},${p.y})" style="cursor:pointer;">
            <circle r="${R}" fill="#ffffff" stroke="${color}" stroke-width="2.5"/>
            <text text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="600" fill="${color}">${label}</text>
            <title>${window.escapeHtml(s.title)}（第${s.day}天）</title>
          </g>`;
      }).join('');

      const dayLabelHtml = dayKeys.map((d, i) => {
        const p = coord(i, 0);
        const color = dayColor(i);
        return `
          <g transform="translate(${PAD - 8},${p.y})">
            <text text-anchor="end" dominant-baseline="central" font-size="11" font-weight="600" fill="${color}">D${d}</text>
          </g>`;
      }).join('');

      const svg = `
        <svg viewBox="0 0 ${width} ${height}" class="w-full h-full" preserveAspectRatio="xMidYMid meet"
             style="background:#ffffff;">
          <g stroke="#e9e6f7" stroke-width="1.2" stroke-dasharray="4 5">
            ${dayKeys.map((d, i) => {
              const p = coord(i, 0);
              return `<line x1="${PAD}" y1="${p.y}" x2="${width - PAD}" y2="${p.y}"/>`;
            }).join('')}
          </g>
          <g fill="none" stroke-linecap="round">
            ${dayPaths.map((s) => `<path d="${s}" stroke="#b8aef9" stroke-width="2"/>`).join('')}
            ${crossPaths.map((s) => `<path d="${s}" stroke="#c9c6ef" stroke-width="1.6" stroke-dasharray="5 5"/>`).join('')}
          </g>
          ${dayLabelHtml}
          ${nodeHtml}
        </svg>`;
      container.innerHTML = svg;

      const tooltip = document.getElementById('route-tooltip');
      if (!tooltip) return;
      const nodes = container.querySelectorAll('.route-node');
      nodes.forEach((node) => {
        node.addEventListener('mouseenter', () => {
          const title = node.getAttribute('data-title');
          const day = node.getAttribute('data-day');
          const time = node.getAttribute('data-time');
          tooltip.textContent = `${time ? time + ' · ' : ''}${title}（第${day}天）`;
          tooltip.classList.remove('hidden');
        });
        node.addEventListener('mousemove', (e) => {
          const rect = container.getBoundingClientRect();
          tooltip.style.left = `${e.clientX - rect.left + 14}px`;
          tooltip.style.top = `${e.clientY - rect.top - 12}px`;
        });
        node.addEventListener('mouseleave', () => {
          tooltip.classList.add('hidden');
        });
      });
    },

    _seg(a, b) {
      const mx = (a.x + b.x) / 2;
      return `M ${a.x} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x} ${b.y}`;
    },

    _renderChart() {
      if (!this.snapshot || !window.echarts) return;
      const plan = this.snapshot.plan;
      const nights = Math.max(
        (new Date(plan.end_date) - new Date(plan.start_date)) / 86_400_000,
        1
      );
      const itemsByLabel = {};
      plan.days.forEach((day) => {
        day.items.forEach((item) => {
          if (!item.cost_cny) return;
          const label = SHARE_CATEGORY_LABEL[item.category] || item.category;
          const unit = Math.round(item.cost_cny);
          const total = item.category === 'hotel' ? Math.round(item.cost_cny * nights) : unit;
          (itemsByLabel[label] = itemsByLabel[label] || []).push({
            title: item.title,
            unit,
            total,
            day: day.day_index,
          });
        });
      });
      const data = Object.entries(itemsByLabel).map(([name, list]) => ({
        name,
        value: list.reduce((sum, it) => sum + it.total, 0),
        items: list,
      }));
      if (data.length === 0) return;
      const chart = echarts.init(document.getElementById('cost-chart'));
      chart.setOption({
        color: SHARE_CHART_COLORS,
        tooltip: {
          trigger: 'item',
          confine: true,
          formatter(params) {
            const d = params && params.data;
            if (!d || !d.items) return params.name;
            const rows = d.items
              .map((it) => {
                const unit = it.unit === it.total ? '' : `（每晚 ¥${it.unit}）`;
                return `<tr><td>第${it.day}天 ${window.escapeHtml(it.title)}${unit}</td><td style="text-align:right;padding-left:12px;">¥${it.total}</td></tr>`;
              })
              .join('');
            return `<div>${params.name} · ¥${params.value}（${params.percent}%）</div>` +
              `<table style="margin-top:6px;border-spacing:0;">${rows}</table>`;
          },
        },
        legend: { bottom: 0, icon: 'circle', textStyle: { color: '#4e5563', fontSize: 12 } },
        series: [{
          type: 'pie', radius: ['42%', '65%'], center: ['50%', '44%'],
          itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
          label: { formatter: '{b}\n¥{c}', fontSize: 11, color: '#4e5563' },
          emphasis: {
            scale: true,
            scaleSize: 6,
            itemStyle: { shadowBlur: 12, shadowColor: 'rgba(108,92,231,0.35)' },
          },
          data,
        }],
      });
      if (this._chartResizeHandler) {
        window.removeEventListener('resize', this._chartResizeHandler);
      }
      this._chartResizeHandler = () => chart.resize();
      window.addEventListener('resize', this._chartResizeHandler);
    },
  };
}
