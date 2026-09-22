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

    /* ---------- 行程路线：Leaflet 真实地图（按经纬度定位，悬停显示地点名） ---------- */

    _renderMap() {
      if (!this.plan || !document.getElementById('route')) return;
      const container = document.getElementById('route');
      // 旧地图实例先销毁，避免重复初始化
      if (this._map) {
        this._map.remove();
        this._map = null;
      }
      if (!window.L) {
        container.innerHTML = '<div class="h-full grid place-items-center text-sm text-ink-400">地图组件未加载</div>';
        return;
      }

      // 收集有经纬度的地方条目：跳过备注类、空标题、无坐标（真实位置定位）
      const spots = [];
      this.plan.days.forEach((day) => {
        (day.items || []).forEach((item) => {
          const title = (item.title || '').trim();
          if (!title || item.category === 'note') return;
          const loc = item.location;
          if (!loc || typeof loc.lat !== 'number' || typeof loc.lng !== 'number') return;
          spots.push({
            title,
            day: day.day_index,
            time: item.start_time ? item.start_time.slice(0, 5) : '',
            category: item.category || 'activity',
            lat: loc.lat,
            lng: loc.lng,
          });
        });
      });

      if (spots.length === 0) {
        container.innerHTML = (
          '<div class="h-full grid place-items-center text-sm text-ink-400">'
          + '暂无地点坐标（部分条目来自文本规划，未关联真实地理位置）</div>'
        );
        return;
      }

      const map = L.map(container, {
        zoomControl: true,
        scrollWheelZoom: false, // 页面滚动优先；点击地图后滚轮缩放
      });
      this._map = map;

      // 真实底图瓦片：高德（国内可达、无 key）
      L.tileLayer(
        'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
        { subdomains: '1234', maxZoom: 18, attribution: '© 高德地图' }
      ).addTo(map);

      // 按天分组（保持原顺序），每天一种主题色
      const byDay = new Map();
      spots.forEach((s) => {
        if (!byDay.has(s.day)) byDay.set(s.day, []);
        byDay.get(s.day).push(s);
      });
      const dayKeys = [...byDay.keys()].sort((a, b) => a - b);
      const dayColor = (i) => CHART_COLORS[dayKeys.indexOf(i) % CHART_COLORS.length];

      // 连线：同一天实线，跨天虚线（真实坐标的折线）
      dayKeys.forEach((d, i) => {
        const list = byDay.get(d);
        const pts = list.map((s) => [s.lat, s.lng]);
        L.polyline(pts, {
          color: CHART_COLORS[i % CHART_COLORS.length],
          weight: 3,
          opacity: 0.85,
        }).addTo(map);
        if (i < dayKeys.length - 1) {
          const a = list[list.length - 1];
          const b = byDay.get(dayKeys[i + 1])[0];
          L.polyline([[a.lat, a.lng], [b.lat, b.lng]], {
            color: CHART_COLORS[i % CHART_COLORS.length],
            weight: 2,
            opacity: 0.45,
            dashArray: '5 6',
          }).addTo(map);
        }
      });

      // 节点：真实经纬度圆点，悬停弹出名称/时间/天/类别
      spots.forEach((s, idx) => {
        const color = dayColor(s.day);
        L.circleMarker([s.lat, s.lng], {
          radius: 9,
          fillColor: color,
          fillOpacity: 0.92,
          color: '#ffffff',
          weight: 2,
        })
          .addTo(map)
          .bindTooltip(
            `<div class="text-xs leading-snug">`
            + `<div class="font-semibold">${idx + 1}. ${this._escape(s.title)}</div>`
            + `<div class="opacity-70 mt-0.5">`
            + `${s.time ? s.time + ' · ' : ''}第${s.day}天 · ${CATEGORY_LABEL[s.category] || s.category}`
            + `</div></div>`,
            { direction: 'top', offset: L.point(0, -10), opacity: 1 }
          );
      });

      // 缩放到全部节点的外接范围
      const latlngs = spots.map((s) => [s.lat, s.lng]);
      map.fitBounds(L.latLngBounds(latlngs).pad(0.15), { maxZoom: 15 });
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
