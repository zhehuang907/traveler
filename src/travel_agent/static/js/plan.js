/* 行程详情页：加载行程 + 地图标记 + 费用图表 + 版本回滚 */

const CATEGORY_LABEL = {
  attraction: '景点', restaurant: '餐饮', hotel: '住宿',
  transport: '交通', activity: '活动', note: '备注',
};
const CHART_COLORS = ['#6c5ce7', '#8b7cf6', '#b8aef9', '#00b894', '#fdcb6e', '#74b9ff'];

function planApp(planId) {
  return {
    planId,
    plan: null,
    versions: [],
    loading: true,
    error: null,

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

    async rollback(version) {
      if (!confirm(`确定回滚到版本 ${version}？当前版本会保留在历史中。`)) return;
      const res = await fetch(`/api/plan/${this.planId}/rollback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version }),
      });
      if (res.ok) await this.load();
    },

    _renderMap() {
      if (!this.plan || !window.L) return;
      const map = L.map('map');
      // 高德底图（国内偏移与 POI 坐标一致）
      L.tileLayer(
        'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
        { subdomains: ['1', '2', '3', '4'], maxZoom: 18 }
      ).addTo(map);
      const bounds = [];
      this.plan.days.forEach((day) => {
        day.items.forEach((item) => {
          if (item.location) {
            const point = [item.location.lat, item.location.lng];
            bounds.push(point);
            L.marker(point).addTo(map).bindPopup(
              `<b>${this._escape(item.title)}</b><br/>第${day.day_index}天`
            );
          }
        });
      });
      if (bounds.length > 0) map.fitBounds(bounds, { padding: [30, 30] });
      else map.setView([30.57, 104.07], 11);
    },

    _renderChart() {
      if (!this.plan || !window.echarts) return;
      const costs = {};
      this.plan.days.forEach((day) => {
        day.items.forEach((item) => {
          if (item.cost_cny) {
            const label = CATEGORY_LABEL[item.category] || item.category;
            costs[label] = (costs[label] || 0) + item.cost_cny;
          }
        });
      });
      const data = Object.entries(costs).map(([name, value]) => ({ name, value: Math.round(value) }));
      const chart = echarts.init(document.getElementById('cost-chart'));
      chart.setOption({
        color: CHART_COLORS,
        tooltip: { trigger: 'item', formatter: '{b}: ¥{c} ({d}%)' },
        legend: { bottom: 0, icon: 'circle', textStyle: { color: '#4e5563', fontSize: 12 } },
        series: [{
          type: 'pie', radius: ['42%', '65%'], center: ['50%', '44%'],
          itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
          label: { formatter: '{b}\n¥{c}', fontSize: 11, color: '#4e5563' },
          data,
        }],
      });
      window.addEventListener('resize', () => chart.resize());
    },

    _escape(s) {
      return window.escapeHtml(s);
    },
  };
}
