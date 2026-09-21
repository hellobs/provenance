// 场景页"先选引擎,再显示该引擎需要的配置"的逻辑守卫(假 DOM 上真跑模板里的函数)。
// 断言三种状态:未选 → 引擎专属内容全不显示且按钮禁用;选 experiment-eval → 角色+三段显示、
// 沙盒段隐藏;选 sandbox-value → 沙盒段显示、其余隐藏。提示行必须给出去向(不静默)。
// 用法: node case01/tools/test_engine_toggle.js [模板路径]
const fs = require('fs');
const path = require('path');

const tpl = process.argv[2] ||
  path.join(__dirname, '..', '..', '..', 'mavis', 'config_tool', 'templates', 'scenario.html');
const html = fs.readFileSync(tpl, 'utf8');

const m = html.match(/function toggleSandboxWorld\(\)\{[\s\S]*?\n   \}/);
if (!m) { console.error('模板里找不到 toggleSandboxWorld()'); process.exit(1); }
const fn = m[0];

// 标记必须真的在模板里(否则"先选引擎"就落空了)
const marked = [...html.matchAll(/data-engines="([^"]+)"/g)].map((x) => x[1]);
const hasEmptyOption = /<option value="">[^<]*请先选择引擎/.test(html);

function mkEl(id, engines) {
  return { id, style: { display: '?' }, _engines: engines || null,
           getAttribute: (k) => (k === 'data-engines' ? this_engines : null),
           disabled: false, title: '' };
}

function run(engineValue) {
  const roles = mkEl('roles-section', 'experiment-eval');
  const evalSec = mkEl('eval-only-section', 'experiment-eval');
  const sbx = mkEl('sandbox-world-section', 'sandbox-value');
  const hint = { textContent: '' };
  const engine = { value: engineValue };
  const btns = [{ disabled: false, title: '' }, { disabled: false, title: '' }];
  const withEngines = [roles, evalSec, sbx];
  withEngines.forEach((el) => { el.getAttribute = (k) => (k === 'data-engines' ? el._engines : null); });

  const document = {
    getElementById: (id) => ({ 'engine': engine, 'engine-field-hint': hint }[id] || null),
    querySelectorAll: (sel) => (sel === '[data-engines]' ? withEngines
      : sel === '[data-needs-engine]' ? btns : []),
  };
  new Function('document', fn + '\ntoggleSandboxWorld();')(document);
  return {
    roles: roles.style.display, evalSec: evalSec.style.display, sbx: sbx.style.display,
    hint: hint.textContent, disabled: btns.map((b) => b.disabled),
  };
}

const cases = [
  ['', { roles: 'none', evalSec: 'none', sbx: 'none' }, '未选引擎:引擎专属内容一律不显示', true],
  ['experiment-eval', { roles: '', evalSec: '', sbx: 'none' }, '实验评估:角色+三段显示、沙盒段隐藏', false],
  ['sandbox-value', { roles: 'none', evalSec: 'none', sbx: '' }, '沙盒:沙盒段显示、其余隐藏', false],
];

let bad = 0;
console.log(`空首项: ${hasEmptyOption ? '有' : '缺'} | data-engines 标记: ${JSON.stringify(marked)}`);
for (const [engine, want, desc, wantDisabled] of cases) {
  const got = run(engine);
  const ok = got.roles === want.roles && got.evalSec === want.evalSec && got.sbx === want.sbx;
  const btnOk = got.disabled.every((d) => d === wantDisabled);
  const hintOk = engine === '' ? got.hint.includes('请先选择引擎') : got.hint.includes(engine);
  const pass = ok && btnOk && hintOk;
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${desc}  engine='${engine || '(空)'}'  ` +
    `roles=${got.roles || '""'} eval=${got.evalSec || '""'} sbx=${got.sbx || '""'} ` +
    `按钮禁用=${JSON.stringify(got.disabled)} hint=${hintOk ? '对' : '不对'}`);
  if (!pass) { bad++; console.log('        hint 实际:', got.hint); }
}
console.log(bad ? `${bad} 条不符合预期` : '全部通过');
process.exit(bad ? 1 : 0);
