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

    _renderMap() {
      if (!this.snapshot || !window.L) return;
      const plan = this.snapshot.plan;
      const map = L.map('map');
      L.tileLayer(
        'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
        { subdomains: ['1', '2', '3', '4'], maxZoom: 18 }
      ).addTo(map);
      const bounds = [];
      plan.days.forEach((day) => {
        day.items.forEach((item) => {
          if (item.location) {
            const point = [item.location.lat, item.location.lng];
            bounds.push(point);
            L.marker(point).addTo(map).bindPopup(
              `<b>${window.escapeHtml(item.title)}</b><br/>第${day.day_index}天`
            );
          }
        });
      });
      if (bounds.length > 0) map.fitBounds(bounds, { padding: [30, 30] });
      else map.setView([30.57, 104.07], 11);
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
