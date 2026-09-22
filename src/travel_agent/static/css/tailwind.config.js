/** Tailwind v3 静态构建配置：替代运行时 CDN tailwind.js（省掉每次页面加载的 JIT 编译）。
 *
 * 构建命令（需要 Node，仅构建时需要；产物 static/css/tailwind.css 提交入库，运行时零 Node 依赖）：
 *   npx -y tailwindcss@3 -c src/travel_agent/static/css/tailwind.config.js \
 *     -i src/travel_agent/static/css/tailwind-input.css \
 *     -o src/travel_agent/static/css/tailwind.css --minify
 *
 * content 覆盖全部模板与前端 JS：动态类名（Alpine :class 三元、plan.js 的 cls 字段）以
 * 字符串字面量出现在这些文件中，Tailwind 会按引号内 token 提取。
 */
module.exports = {
  content: [
    "./src/travel_agent/templates/**/*.html",
    "./src/travel_agent/static/js/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        brand: { 50: "#f2f0ff", 100: "#e7e3ff", 200: "#d2cbff", 400: "#8b7cf6", 500: "#6c5ce7", 600: "#5a4bd6", 700: "#4a3db5" },
        ink: { 900: "#1f2329", 700: "#4e5563", 500: "#8a909e", 300: "#c4c9d2" },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"PingFang SC"', '"Microsoft YaHei"', '"Segoe UI"', 'sans-serif'],
      },
      boxShadow: {
        soft: "0 2px 16px rgba(31,35,41,.06)",
        lift: "0 8px 32px rgba(108,92,231,.12)",
      },
    },
  },
  plugins: [],
};
