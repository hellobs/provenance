#!/usr/bin/env node
/**
 * 前端 JS 冒烟测试(无浏览器、无运行服务依赖)
 *
 * 输入:由 pytest(tests/test_live_api.py::TestPageRender)渲染的 HTML 文件
 * 或由 tests/render_pages.py 生成的 tests/_pages/*.html
 *
 * 验证每个页面:内联 <script> 拼接后 JS 语法正确(new Function 解析)
 *
 * 用法:
 *   node tests/frontend_smoke.js [页面目录]
 * 默认 tests/_pages/
 */
const fs = require("fs");
const path = require("path");

const dir = process.argv[2] || path.join(__dirname, "_pages");
if (!fs.existsSync(dir)) {
  console.log("SKIP: 页面目录不存在", dir);
  process.exit(0); // 无页面时不算失败(CI 中由 pytest 保证渲染)
}

const pages = fs.readdirSync(dir).filter((f) => f.endsWith(".html"));
if (!pages.length) {
  console.log("SKIP: 目录无 HTML 页面", dir);
  process.exit(0);
}

let failures = 0;
for (const f of pages) {
  const html = fs.readFileSync(path.join(dir, f), "utf8");
  const scripts = html.match(/<script[^>]*>([\s\S]*?)<\/script>/g) || [];
  let code = "";
  for (const s of scripts) {
    const inner = s.replace(/^<script[^>]*>/, "").replace(/<\/script>$/, "");
    if (inner.trim()) code += inner + "\n";
  }
  try {
    new Function(code); // 只解析不执行:函数体内未定义全局不会报错,语法错误会抛
    console.log("PASS", f);
  } catch (e) {
    console.log("FAIL", f, "→", e.message);
    failures++;
  }
}
if (failures) {
  console.log("\n" + failures + " 个页面 JS 失败");
  process.exit(1);
}
console.log("\n全部页面 JS 通过");
