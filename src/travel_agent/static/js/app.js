// 全局工具函数
window.formatClock = (t) => t ? t.slice(0, 5) : '·';

window.escapeHtml = (s) => {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
};
