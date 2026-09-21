// 把 config_tool 页面里的内联 JS 抽出来做 node --check(揪语法错误)。
// 用法: node tools/check_inline_js.js <url>
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const url = process.argv[2];
http.get(url, (r) => {
  let b = '';
  r.on('data', (d) => b += d);
  r.on('end', () => {
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
  });
}).on('error', (e) => { console.error('拉页面失败:', e.message); process.exit(2); });
