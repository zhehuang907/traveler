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

    hotelData: null,
    hotelLoading: false,
    hotelError: null,
    hotelTravelers: 1,

    _map: null,   // 路线 SVG 容器（renderMap 用）
    _chart: null,
    // 地图视图模式：'real' = Leaflet 真实底图（看位置/导航）；'sketch' = 手绘示意图（看顺序方位）
    mapView: 'real',

    // 折叠的天（存 day_index）。load() 结束时按默认规则初始化。
    // 编辑态强制全部展开（否则会改不到被折叠那天的条目）。
    collapsed: [],

    /* ---------- 天次折叠 ---------- */

    /** 首次进入时的默认状态：首日展开、末日展开、其余折叠。 */
    _defaultCollapsed(days) {
      if (!days || days.length <= 2) return [];
      // 末日通常是返程/离开日，信息量小；只保留首末日可见
      return days.slice(1, -1).map((d) => d.day_index);
    },

    isDayOpen(dayIndex) {
      // 编辑态强制展开：否则被折叠那天的条目根本点不到。
      // 注意本方法必须保持「纯读」——它会在渲染中被高频调用，
      // 若在此处懒初始化 collapsed 会触发 Alpine 的反复更新。
      if (this.editing) return true;
      return !this.collapsed.includes(dayIndex);
    },

    toggleDay(dayIndex) {
      const i = this.collapsed.indexOf(dayIndex);
      if (i >= 0) this.collapsed.splice(i, 1);
      else this.collapsed.push(dayIndex);
    },

    allCollapsed() {
      return !this._days().some((d) => this.isDayOpen(d.day_index));
    },

    toggleAllDays() {
      const days = this._days();
      if (!days.length) return;
      if (this.allCollapsed()) this.collapsed = [];
      else this.collapsed = days.map((d) => d.day_index);
    },

    _days() {
      if (this.editing && this.draft) return this.draft.days || [];
      return (this.plan && this.plan.days) || [];
    },

    /**
     * 计划被整体替换（AI 优化 / 回滚）后天次编号可能变化，
     * 丢弃已不存在的 day_index，避免折叠态指向"幽灵天次"。
     */
    _pruneCollapsed() {
      const valid = new Set((this.plan.days || []).map((d) => d.day_index));
      this.collapsed = this.collapsed.filter((i) => valid.has(i));
    },

    dayCount() {
      return this._days().length;
    },

    itemCount() {
      return this._days().reduce((sum, d) => sum + ((d.items || []).length), 0);
    },

    /** 折叠态下的一天摘要：「09:00 外滩观景台 · 11:00 …」。 */
    daySummary(day) {
      const items = (day.items || []).filter((i) => (i.title || '').trim());
      if (!items.length) return '';
      return items
        .map((i) => `${i.start_time ? i.start_time.slice(0, 5) + ' ' : ''}${i.title}`)
        .join(' · ');
    },

    _WEEKDAYS: ['周日', '周一', '周二', '周三', '周四', '周五', '周六'],

    weekdayOf(dateStr) {
      if (!dateStr) return '';
      const d = new Date(`${String(dateStr).slice(0, 10)}T00:00:00`);
      return Number.isNaN(d.getTime()) ? '' : this._WEEKDAYS[d.getDay()];
    },

    /** 预算余额：负数时标红（超支）。 */
    budgetLeft() {
      if (!this.plan || !this.plan.budget_cny) return 0;
      return this.plan.budget_cny - this.plan.total_cost_cny;
    },

    budgetStyle() {
      if (!this.plan || !this.plan.budget_cny) return '';
      return this.budgetLeft() < 0 ? 'color: #a83c2f' : 'color: var(--pj-jade)';
    },

    /** 三档徽标配色（内联 style，因 tailwind 是预编译产物，无这三档色）。 */
    tierBadge(tier) {
      if (tier === 'value') return 'background: var(--pj-jade-soft); color: var(--pj-jade);';
      if (tier === 'comfort') return 'background: var(--pj-amber-soft); color: #90601f;';
      return 'background: var(--pj-plum-soft); color: var(--pj-plum);';
    },

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
      // 房型建议的人数默认取计划人数
      if (this.plan && !this.hotelTravelers) this.hotelTravelers = this.plan.travelers;
      // 折叠态在此处一次性初始化（保持 isDayOpen 为纯读函数）
      this.collapsed = this._defaultCollapsed(this._days());
      this.$nextTick(() => {
        this._renderMap();
        this._renderChart();
      });
    },

    /* ---------- 住宿推荐 ---------- */

    async loadHotels() {
      if (this.hotelLoading) return;
      this.hotelLoading = true;
      this.hotelError = null;
      try {
        // 用分档端点：三档（性价比/轻奢/高奢）各给最优候选
        const res = await fetch(
          `/api/plan/${this.planId}/hotels/tiered?travelers=${encodeURIComponent(this.hotelTravelers || 1)}&per_tier=1`
        );
        if (!res.ok) {
          let msg = '酒店数据获取失败';
          try {
            const body = await res.json();
            if (body && body.error && body.error.message) msg = body.error.message;
          } catch {
            /* 响应体非 JSON 时用默认文案 */
          }
          this.hotelError = msg;
          this.hotelData = null;
          return;
        }
        this.hotelData = await res.json();
      } catch {
        this.hotelError = '网络错误，请稍后重试';
        this.hotelData = null;
      } finally {
        this.hotelLoading = false;
      }
    },

    get hotelMeta() {
      if (!this.hotelData) return '';
      return `${this.hotelData.city} · ${this.hotelData.travelers} 人 · ${this.hotelData.nights} 晚`;
    },

    roomLabel(type) {
      return {
        single: '单人间',
        double: '大床房',
        twin: '双床房',
        triple: '三人房',
        family: '家庭房',
        suite: '套房',
      }[type] || type;
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
      // 编辑态 isDayOpen 恒为 true；保存前记下当前折叠态，退出时恢复，
      // 这样「编辑 → 取消」不会把用户的展开状态重置掉。
      this._collapsedBeforeEdit = this.collapsed.slice();
      this.collapsed = [];
    },

    cancelEdit() {
      this.editing = false;
      this.draft = null;
      this.collapsed = this._collapsedBeforeEdit || [];
      this._collapsedBeforeEdit = null;
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
        // 恢复编辑前的折叠态，再按新计划裁掉已不存在的天次
        this.collapsed = this._collapsedBeforeEdit || [];
        this._collapsedBeforeEdit = null;
        this._pruneCollapsed();
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
        this._pruneCollapsed();
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
      this._pruneCollapsed();
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

    /* ---------- 路线地图：真实底图 / 手绘示意图切换 ---------- */

    toggleMapView() {
      this.mapView = this.mapView === 'real' ? 'sketch' : 'real';
      // 切换前必须销毁 Leaflet 实例：容器被 innerHTML 覆盖后，
      // Leaflet 仍持有已脱离文档的 DOM，zoom/事件会错乱并抛错。
      this._destroyMap();
      this.$nextTick(() => this._renderMap());
    },

    _destroyMap() {
      if (this._map) {
        try {
          this._map.remove();
        } catch {
          /* 容器已被移除时 remove 可能抛错，忽略即可 */
        }
        this._map = null;
      }
      this._resetRouteContainer();
    },

    /**
     * 把 #route 复位成干净容器。
     * 两个必须处理的残留（实测踩到）：
     * 1. L.map() 不会清空容器原有 innerHTML —— 手绘 SVG 会留在 DOM 里，
     *    只是被 Leaflet 的 pane（z-index 400+）盖住看不见，但节点/事件仍在；
     * 2. Leaflet 初始化时加的 leaflet-* class 不会随 map.remove() 摘掉。
     */
    _resetRouteContainer() {
      const container = document.getElementById('route');
      if (!container) return null;
      container.innerHTML = '';
      container.className = Array.from(container.classList)
        .filter((c) => !c.startsWith('leaflet-'))
        .join(' ');
      return container;
    },

    /** 收集有真实经纬度的地点；无坐标的条目无法上图，故跳过。 */
    _collectSpots() {
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
      return spots;
    },

    _renderMap() {
      if (!this.plan) return;
      const container = this._resetRouteContainer();
      if (!container) return;
      this._map = null;
      const legendHolder = document.getElementById('route-legend');
      if (legendHolder) legendHolder.innerHTML = '';

      if (this.mapView === 'sketch') {
        this._renderSketch(container);
      } else {
        this._renderLeaflet(container);
      }
    },

    /** 手绘示意图：SVG 静态渲染，无需依赖 Leaflet。 */
    _renderSketch(container) {
      if (!window.SketchMap) {
        container.innerHTML =
          '<div class="h-full grid place-items-center text-sm text-ink-400">示意图组件未加载</div>';
        return;
      }
      const spots = this._collectSpots();
      const days = [...new Set(spots.map((s) => s.day))].sort((a, b) => a - b);
      container.innerHTML = window.SketchMap.renderSketchMap(spots, {
        escape: (v) => this._escape(v),
        categoryLabel: CATEGORY_LABEL,
      });
      // 图例放在容器下方
      const legend = window.SketchMap.renderSketchLegend(days, CHART_COLORS);
      const holder = document.getElementById('route-legend');
      if (holder) holder.innerHTML = legend;
    },

    _renderLeaflet(container) {
      if (!window.L) {
        container.innerHTML =
          '<div class="h-full grid place-items-center text-sm text-ink-400">地图组件未加载</div>';
        return;
      }
      const spots = this._collectSpots();

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
