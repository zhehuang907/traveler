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

    /* ---------- 行程路线：Leaflet 真实地图（按经纬度定位，悬停显示地点名） ---------- */

    _renderMap() {
      if (!this.snapshot || !document.getElementById('route')) return;
      const container = document.getElementById('route');
      if (this._map) {
        this._map.remove();
        this._map = null;
      }
      if (!window.L) {
        container.innerHTML = '<div class="h-full grid place-items-center text-sm text-ink-400">地图组件未加载</div>';
        return;
      }
      const plan = this.snapshot.plan;

      // 收集有经纬度的地方条目：跳过备注类、空标题、无坐标
      const spots = [];
      plan.days.forEach((day) => {
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
        scrollWheelZoom: false,
      });
      this._map = map;

      L.tileLayer(
        'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
        { subdomains: '1234', maxZoom: 18, attribution: '© 高德地图' }
      ).addTo(map);

      const byDay = new Map();
      spots.forEach((s) => {
        if (!byDay.has(s.day)) byDay.set(s.day, []);
        byDay.get(s.day).push(s);
      });
      const dayKeys = [...byDay.keys()].sort((a, b) => a - b);

      // 连线：同一天实线，跨天虚线
      dayKeys.forEach((d, i) => {
        const list = byDay.get(d);
        const pts = list.map((s) => [s.lat, s.lng]);
        L.polyline(pts, {
          color: SHARE_CHART_COLORS[i % SHARE_CHART_COLORS.length],
          weight: 3,
          opacity: 0.85,
        }).addTo(map);
        if (i < dayKeys.length - 1) {
          const a = list[list.length - 1];
          const b = byDay.get(dayKeys[i + 1])[0];
          L.polyline([[a.lat, a.lng], [b.lat, b.lng]], {
            color: SHARE_CHART_COLORS[i % SHARE_CHART_COLORS.length],
            weight: 2,
            opacity: 0.45,
            dashArray: '5 6',
          }).addTo(map);
        }
      });

      // 节点：真实经纬度圆点，悬停弹出名称/时间/天/类别
      const dayColor = (i) => SHARE_CHART_COLORS[dayKeys.indexOf(i) % SHARE_CHART_COLORS.length];
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
            + `<div class="font-semibold">${idx + 1}. ${window.escapeHtml(s.title)}</div>`
            + `<div class="opacity-70 mt-0.5">`
            + `${s.time ? s.time + ' · ' : ''}第${s.day}天 · ${SHARE_CATEGORY_LABEL[s.category] || s.category}`
            + `</div></div>`,
            { direction: 'top', offset: L.point(0, -10), opacity: 1 }
          );
      });

      const latlngs = spots.map((s) => [s.lat, s.lng]);
      map.fitBounds(L.latLngBounds(latlngs).pad(0.15), { maxZoom: 15 });
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
