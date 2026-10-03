// 把 config_tool 页面里的内联 JS 抽出来做 node --check(揪语法错误)。
// 用法:
//   node tools/check_inline_js.js <url>          # 从运行中的服务拉(如 http://127.0.0.1:8060/scenario)
//   node tools/check_inline_js.js <file.html>    # 直接检查本地模板(服务没起时用,2026-10-03 加)
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const target = process.argv[2];
if (!target) {
  console.error('用法: check_inline_js.js <url 或 .html 文件路径>');
  process.exit(2);
}

function checkHtml(b) {
    const blocks = [...b.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)]
      .map((m) => m[1]).filter((s) => s.trim());
    console.log(`内联 script 块: ${blocks.length} 个,共 ${blocks.join('\n').length} 字符`);
    const tmp = path.join(os.tmpdir(), 'cfg_inline.js');
    let bad = 0;
    blocks.forEach((code, i) => {
      fs.writeFileSync(tmp, code, 'utf8');
      try {
        execFileSync(process.execPath, ['--check', tmp], { stdio: 'pipe' });
        console.log(`  块 ${i + 1}: OK (${code.length} 字符)`);
      } catch (e) {
        bad++;
        console.log(`  块 ${i + 1}: 语法错误 → ${String(e.stderr || e.message).split('\n').slice(0, 4).join(' | ')}`);
      }
    });
    process.exit(bad ? 1 : 0);
}

if (/^https?:\/\//.test(target)) {
  http.get(target, (r) => {
    let b = '';
    r.on('data', (d) => b += d);
    r.on('end', () => checkHtml(b));
  }).on('error', (e) => { console.error('拉页面失败:', e.message); process.exit(2); });
} else {
  // 离线模式:参数不是 URL 就当本地文件 —— 体检时 8060 按要求是关着的,
  // 旧版只会去连端口,连不上就报失败,和"JS 真有语法错"无法区分(误报)。
  // 模板里的 Jinja 在服务端才渲染,语法检查前先替成合法 JS 占位:
  //   {{ x | tojson }} → null(输出本就是 JS 字面量,null 能过 parse);
  //   {% ... %} → ; / {# ... #} 删除(当前模板没有,防御性处理)。
  try {
    let html = fs.readFileSync(target, 'utf8')
      .replace(/\{\{[\s\S]*?\}\}/g, 'null')
      .replace(/\{#[\s\S]*?#\}/g, '')
      .replace(/\{%[\s\S]*?%\}/g, ';');
    checkHtml(html);
  } catch (e) {
    console.error('读模板失败:', e.message);
    process.exit(2);
  }
}
