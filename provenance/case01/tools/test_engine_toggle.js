// 用"假 DOM"直接跑模板里的 toggleSandboxWorld(),验证按引擎显隐的逻辑。
// 比截图更可靠:截图只能看当前那一次,这个把三种引擎状态都断言一遍。
// 用法: node case01/tools/test_engine_toggle.js [模板路径]
const fs = require('fs');
const path = require('path');

const tpl = process.argv[2] ||
  path.join(__dirname, '..', '..', '..', 'mavis', 'config_tool', 'templates', 'scenario.html');
const html = fs.readFileSync(tpl, 'utf8');

const m = html.match(/function toggleSandboxWorld\(\)\{[\s\S]*?\n   \}/);
if (!m) { console.error('模板里找不到 toggleSandboxWorld()'); process.exit(1); }
const fn = m[0];

function run(engine) {
  const els = {
    'sandbox-world-section': { style: { display: '?' } },
    'roles-section': { style: { display: '?' } },
    'eval-only-section': { style: { display: '?' } },
    'engine-field-hint': { textContent: '' },
    'engine': { value: engine },
  };
  const document = { getElementById: (id) => els[id] || null };
  // 注意:光声明函数不会执行 —— 要在同一作用域里调用一次
  new Function('document', fn + '\ntoggleSandboxWorld();')(document);
  return {
    eval: els['eval-only-section'].style.display,
    roles: els['roles-section'].style.display,
    sbx: els['sandbox-world-section'].style.display,
    hint: els['engine-field-hint'].textContent,
  };
}

const cases = [
  ['sandbox-value', { eval: 'none', roles: 'none', sbx: '' }, '沙盒:三段收起、沙盒段出现'],
  ['experiment-eval', { eval: '', roles: '', sbx: 'none' }, '实验评估:角色+三段出现、沙盒段收起'],
  ['', { eval: '', roles: '', sbx: 'none' }, '未选引擎:按非沙盒处理'],
];

let bad = 0;
for (const [engine, want, desc] of cases) {
  const got = run(engine);
  const ok = got.eval === want.eval && got.roles === want.roles && got.sbx === want.sbx;
  const hintOk = engine === 'sandbox-value' ? got.hint.includes('已隐藏') : got.hint === '';
  console.log(`${ok && hintOk ? 'PASS' : 'FAIL'}  ${desc}  engine='${engine || '(空)'}'  ` +
    `eval=${got.eval || '""'} roles=${got.roles || '""'} sbx=${got.sbx || '""'} hint=${got.hint ? '有' : '无'}`);
  if (!ok || !hintOk) bad++;
}
console.log(bad ? `${bad} 条不符合预期` : '全部通过');
process.exit(bad ? 1 : 0);
