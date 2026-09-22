/* 行程详情页：加载行程 + 地图标记 + 费用图表 + 手动编辑 + AI 优化 + 版本回滚 */

const CATEGORY_LABEL = {
  attraction: '景点', restaurant: '餐饮', hotel: '住宿',
  transport: '交通', activity: '活动', note: '备注',
};
const FIELD_LABEL = {
  start_time: '开始时间', end_time: '结束时间', duration_min: '时长',
  cost_cny: '费用', indoor: '室内', weather_adjusted: '雨天调整',
  travel_mode_to_next: '交通方式', notes: '备注',
};
const FREE_CATEGORIES = ['activity', 'note', 'transport'];
const CHART_COLORS = ['#6c5ce7', '#8b7cf6', '#b8aef9', '#00b894', '#fdcb6e', '#74b9ff'];
const _DIFF_CAP = 12;

function planApp(planId) {
  return {
    planId,
    plan: null,
    versions: [],
    loading: true,
    error: null,

    editing: false,
    draft: null,
    saving: false,

    showOptimize: false,
    optimizeNote: '',
    optimizing: false,

    exporting: false,
    sharing: false,

    lastDiff: null,
    lastWarnings: [],
    message: null,

    _map: null,   // 路线 SVG 容器（renderMap 用）
    _chart: null,

    async init() {
      await this.load();
    },

    async load() {
      this.loading = true;
      this.error = null;
      try {
        const [planRes, verRes] = await Promise.all([
          fetch(`/api/plan/${this.planId}`),
          fetch(`/api/plan/${this.planId}/versions`),
        ]);
        if (!planRes.ok) {
          this.error = planRes.status === 404 ? '行程不存在或已被删除' : '行程加载失败';
        } else {
          this.plan = (await planRes.json()).plan;
        }
        if (verRes.ok) this.versions = await verRes.json();
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

    /* ---------- 手动编辑 ---------- */

    startEdit() {
      const draft = JSON.parse(JSON.stringify(this.plan));
      draft.days = draft.days || [];
      if (draft.days.length === 0) draft.days = this._emptyDays();
      draft.days.forEach((day) => {
        day.items = day.items || [];
        day.items.forEach((item) => {
          item.start_time = (item.start_time || '').slice(0, 5);
          item.end_time = (item.end_time || '').slice(0, 5);
        });
      });
      this.draft = draft;
      this.editing = true;
      this.message = null;
    },

    cancelEdit() {
      this.editing = false;
      this.draft = null;
    },

    addItem(day) {
      day.items.push({
        item_id: `new-${Math.random().toString(36).slice(2, 8)}`,
        title: '',
        category: 'activity',
        poi_id: null,
        location: null,
        start_time: '',
        end_time: '',
        duration_min: 60,
        cost_cny: null,
        indoor: false,
        travel_mode_to_next: '',
        notes: '',
        sources: [],
      });
    },

    removeItem(day, index) {
      day.items.splice(index, 1);
    },

    async saveEdit() {
      if (this.saving) return;
      this.saving = true;
      this.message = null;
      try {
        const res = await fetch(`/api/plan/${this.planId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ plan: this._normalizeDraft() }),
        });
        const data = await res.json();
        if (!res.ok) {
          this.message = { type: 'error', text: (data && data.error && data.error.message) || '保存失败' };
          return;
        }
        this.plan = data.plan;
        this.editing = false;
        this.draft = null;
        this.lastDiff = data.diff;
        this.lastWarnings = [];
        this.message = { type: 'ok', text: this._diffLineText(data.diff, `修改已保存为 v${data.plan_version}`) };
        await this._refreshVersions();
        this.$nextTick(() => {
          this._renderMap();
          this._renderChart();
        });
      } catch {
        this.message = { type: 'error', text: '网络错误，请稍后重试' };
      } finally {
        this.saving = false;
      }
    },

    _emptyDays() {
      const days = [];
      const end = new Date(`${this.plan.end_date}T00:00:00`);
      const cursor = new Date(`${this.plan.start_date}T00:00:00`);
      let index = 1;
      while (cursor <= end) {
        days.push({ day_index: index, date: cursor.toISOString().slice(0, 10), items: [], note: '' });
        cursor.setDate(cursor.getDate() + 1);
        index += 1;
      }
      return days;
    },

    _normalizeDraft() {
      const draft = JSON.parse(JSON.stringify(this.draft));
      draft.days.forEach((day) => {
        day.items = (day.items || [])
          .filter((item) => (item.title || '').trim() !== '')
          .map((item) => {
            const out = Object.assign({}, item);
            out.title = String(item.title).trim();
            out.start_time = item.start_time || null;
            out.end_time = item.end_time || null;
            const duration = Number(item.duration_min);
            out.duration_min = !Number.isFinite(duration) || item.duration_min === '' ? 60 : Math.round(duration);
            const cost = Number(item.cost_cny);
            out.cost_cny = item.cost_cny === null || item.cost_cny === '' || !Number.isFinite(cost) ? null : cost;
            out.travel_mode_to_next = item.travel_mode_to_next || null;
            return out;
          });
      });
      return draft;
    },

    /* ---------- AI 优化 ---------- */

    async optimize() {
      if (this.optimizing) return;
      this.optimizing = true;
      this.message = null;
      try {
        const res = await fetch(`/api/plan/${this.planId}/optimize`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ instruction: this.optimizeNote.trim() || null }),
        });
        const data = await res.json();
        if (!res.ok) {
          this.message = { type: 'error', text: (data && data.error && data.error.message) || 'AI 优化失败' };
          return;
        }
        this.plan = data.plan;
        this.lastDiff = data.diff;
        this.lastWarnings = data.warnings || [];
        this.showOptimize = false;
        this.optimizeNote = '';
        this.message = { type: 'ok', text: this._diffLineText(data.diff, `AI 优化完成，已生成最新方案 v${data.plan_version}`) };
        await this._refreshVersions();
        this.$nextTick(() => {
          this._renderMap();
          this._renderChart();
        });
      } catch {
        this.message = { type: 'error', text: '网络错误，请稍后重试' };
      } finally {
        this.optimizing = false;
      }
    },

    /* ---------- 版本 ---------- */

    async rollback(version) {
      if (!confirm(`确定回滚到版本 ${version}？当前版本会保留在历史中。`)) return;
      const res = await fetch(`/api/plan/${this.planId}/rollback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version }),
      });
      if (!res.ok) return;
      const data = await res.json();
      this.plan = data.plan;
      this.lastDiff = null;
      this.lastWarnings = [];
      this.message = { type: 'ok', text: `已回滚到 v${version}（写入为最新版本 v${data.plan_version}）` };
      await this._refreshVersions();
      this.$nextTick(() => {
        this._renderMap();
        this._renderChart();
      });
    },

    /* ---------- PDF 导出 ---------- */

    async exportPdf() {
      this.exporting = true;
      this.message = null;
      try {
        const res = await fetch(`/api/plan/${this.planId}/pdf`, { method: 'POST' });
        if (!res.ok) {
          const data = await res.json().catch(() => null);
          this.message = {
            type: 'error',
            text: (data && data.error && data.error.message) || 'PDF 导出失败',
          };
          return;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${this.planId}.pdf`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        this.message = { type: 'ok', text: 'PDF 已导出' };
      } catch {
        this.message = { type: 'error', text: '网络错误，请稍后重试' };
      } finally {
        this.exporting = false;
      }
    },

    /* ---------- 分享 ---------- */

    async share() {
      this.sharing = true;
      this.message = null;
      try {
        const res = await fetch('/api/share', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ plan_id: this.planId }),
        });
        const data = await res.json().catch(() => null);
        if (!res.ok) {
          this.message = {
            type: 'error',
            text: (data && data.error && data.error.message) || '生成分享链接失败',
          };
          return;
        }
        const url = `${location.origin}${data.url}`;
        try {
          await navigator.clipboard.writeText(url);
          this.message = { type: 'ok', text: '分享链接已复制到剪贴板' };
        } catch {
          this.message = { type: 'ok', text: `分享链接：${url}` };
        }
      } catch {
        this.message = { type: 'error', text: '网络错误，请稍后重试' };
      } finally {
        this.sharing = false;
      }
    },

    async _refreshVersions() {
      try {
        const res = await fetch(`/api/plan/${this.planId}/versions`);
        if (res.ok) this.versions = await res.json();
      } catch {
        /* 版本列表刷新失败不影响主流程 */
      }
    },

    fmtTime(value) {
      return value ? value.slice(0, 16).replace('T', ' ') : '';
    },

    _diffLineText(diff, prefix) {
      if (!diff) return prefix;
      const added = (diff.added || []).length;
      const removed = (diff.removed || []).length;
      const changed = (diff.changed || []).length;
      if (added + removed + changed === 0) return `${prefix}（内容无变化）`;
      return `${prefix}：新增 ${added} · 删除 ${removed} · 调整 ${changed}`;
    },

    diffLines() {
      if (!this.lastDiff) return [];
      const lines = [];
      (this.lastDiff.added || []).forEach((entry) => {
        lines.push({ cls: 'text-emerald-600', text: `第${entry.day}天 新增「${entry.title}」` });
      });
      (this.lastDiff.removed || []).forEach((entry) => {
        lines.push({ cls: 'text-red-500', text: `第${entry.day}天 删除「${entry.title}」` });
      });
      (this.lastDiff.changed || []).forEach((change) => {
        const label = FIELD_LABEL[change.field] || change.field;
        const title = this._titleOf(change.item_id) || change.item_id;
        lines.push({ cls: 'text-amber-600', text: `第${change.day}天「${title}」${label}：${change.before || '—'} → ${change.after || '—'}` });
      });
      return lines.slice(0, _DIFF_CAP);
    },

    diffOverflow() {
      if (!this.lastDiff) return 0;
      const total = (this.lastDiff.added || []).length + (this.lastDiff.removed || []).length + (this.lastDiff.changed || []).length;
      return Math.max(0, total - _DIFF_CAP);
    },

    _titleOf(itemId) {
      if (!this.plan) return null;
      for (const day of this.plan.days) {
        for (const item of day.items) {
          if (item.item_id === itemId) return item.title;
        }
      }
      return null;
    },

    categoryLabel(item) {
      return CATEGORY_LABEL[item.category] || item.category;
    },

    dayTotal(day) {
      if (!day || !day.items) return 0;
      const nights = Math.max(
        (new Date(this.plan.end_date) - new Date(this.plan.start_date)) / 86_400_000,
        1
      );
      return Math.round(
        day.items.reduce((sum, item) => {
          if (!item.cost_cny) return sum;
          return sum + (item.category === 'hotel' ? item.cost_cny * nights : item.cost_cny);
        }, 0)
      );
    },

    /* ---------- 路线示意（二维纯白 SVG，替代实际地图） ---------- */

    _renderMap() {
      if (!this.plan || !document.getElementById('route')) return;
      const container = document.getElementById('route');
      container.innerHTML = '';
      // 收集地点：跳过备注类、空标题
      const spots = [];
      this.plan.days.forEach((day) => {
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

      // 按天分组，每天一行，行内横向排布；跨天用虚线连接
      const byDay = new Map();
      spots.forEach((s) => {
        if (!byDay.has(s.day)) byDay.set(s.day, []);
        byDay.get(s.day).push(s);
      });
      const dayKeys = [...byDay.keys()];

      const COL_W = 76;      // 同一行相邻地点间距
      const ROW_H = 78;      // 天与天之间的行高
      const PAD = 38;
      const R = 14;          // 节点半径
      const maxCols = Math.max(...dayKeys.map((d) => byDay.get(d).length));
      const width = Math.max(PAD * 2 + maxCols * COL_W, 480);
      const height = PAD * 2 + dayKeys.length * ROW_H;

      const coord = (rowIdx, colIdx) => ({
        x: PAD + colIdx * COL_W,
        y: PAD + rowIdx * ROW_H,
      });

      // 构建线段：先按天算坐标，生成 path
      const rowIndex = {};
      dayKeys.forEach((d, i) => { rowIndex[d] = i; });
      const colIndex = {};
      spots.forEach((s) => {
        const key = s.day + '::' + s.title;
        if (!(key in colIndex)) {
          colIndex[key] = byDay.get(s.day).indexOf(s);
        }
      });
      const posOf = (s) => {
        const p = coord(rowIndex[s.day], colIndex[s.day + '::' + s.title]);
        return p;
      };

      // 同一天内部实线，跨天虚线
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

      // 天节点颜色
      const dayColor = (i) => CHART_COLORS[i % CHART_COLORS.length];

      // 节点 html
      const nodeHtml = spots.map((s, idx) => {
        const p = posOf(s);
        const rowIdx = rowIndex[s.day];
        const color = dayColor(rowIdx);
        const label = String(idx + 1);
        return `
          <g class="route-node" data-title="${this._escape(s.title)}" data-day="${s.day}" data-time="${this._escape(s.time)}"
             transform="translate(${p.x},${p.y})" style="cursor:pointer;">
            <circle r="${R}" fill="#ffffff" stroke="${color}" stroke-width="2.5"/>
            <text text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="600" fill="${color}">${label}</text>
            <title>${this._escape(s.title)}（第${s.day}天）</title>
          </g>`;
      }).join('');

      // 天标签（左侧）+ 行基线
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

      // hover tooltip
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
      if (!this.plan || !window.echarts || !document.getElementById('cost-chart')) return;
      if (this._chart) {
        this._chart.dispose();
        this._chart = null;
      }
      const nights = Math.max(
        (new Date(this.plan.end_date) - new Date(this.plan.start_date)) / 86_400_000,
        1
      );
      const itemsByLabel = {};
      this.plan.days.forEach((day) => {
        day.items.forEach((item) => {
          if (!item.cost_cny) return;
          const label = CATEGORY_LABEL[item.category] || item.category;
          const base = item.category === 'hotel' ? Math.round(item.cost_cny) : Math.round(item.cost_cny);
          const total = item.category === 'hotel' ? Math.round(item.cost_cny * nights) : Math.round(item.cost_cny);
          (itemsByLabel[label] = itemsByLabel[label] || []).push({
            title: item.title,
            unit: base,
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
      const chart = echarts.init(document.getElementById('cost-chart'));
      this._chart = chart;
      chart.setOption({
        color: CHART_COLORS,
        tooltip: {
          trigger: 'item',
          confine: true,
          formatter(params) {
            const d = params && params.data;
            if (!d || !d.items) return params.name;
            const rows = d.items
              .map((it) => {
                const unit = it.unit === it.total ? '' : `（每晚 ¥${it.unit}）`;
                return `<tr><td>第${it.day}天 ${escapeHtml(it.title)}${unit}</td><td style="text-align:right;padding-left:12px;">¥${it.total}</td></tr>`;
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

    _escape(s) {
      return window.escapeHtml(s);
    },
  };
}
