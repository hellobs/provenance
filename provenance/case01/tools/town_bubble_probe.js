// 小镇页头顶气泡「聊天记录 + 打字机」的行为自查(2026-10-09)。
//
// 需求原文:"主角头上顶着那个要直接是聊天记录,要有打字机的效果"。
// 之前气泡显示的是"角色名: 行动文本"(纯文本、一次性全显、无打字机)。
//
// 本脚本**不连浏览器**:把渲染好的小镇页 HTML 里的主 <script> 抽出来,
// 在最小 DOM/Phaser 桩里真跑一遍,直接调 setBubble / tickBubbles / setActionBubble,
// 断言**行为**(逐字吐字、吐满即停、超长截断、新句覆盖、行动文本不抢镜)。
// 光查语法抓不到这些 —— 逻辑错了照样能过 `new Function`。
//
// 用法:node case01/tools/town_bubble_probe.js --page-file <渲染好的 index.html>
// 退出码:0=全部通过;1=有断言失败或抛异常。
const fs = require('fs');
const vm = require('vm');

const argv = process.argv.slice(2);
let pageFile = '';
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--page-file') { pageFile = argv[++i]; }
}
if (!pageFile) {
  console.error('用法:node town_bubble_probe.js --page-file <index.html>');
  process.exit(1);
}
const html = fs.readFileSync(pageFile, 'utf8');
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
const main = scripts.find((s) => s.includes('function setBubble'));
if (!main) {
  console.error('FAIL: 页面脚本里找不到 setBubble —— 气泡改动可能没进渲染页');
  process.exit(1);
}

const sandbox = {
  console, Date, Math, JSON, Object, Array, String, Number,
  document: { getElementById: () => ({ innerHTML: '', style: {}, scrollTop: 0, scrollHeight: 0, textContent: '' }) },
  window: {}, setTimeout, clearTimeout,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// 顶层脚本会继续调 Phaser(未提供),抛在函数定义之后,忽略即可。
try { vm.runInContext(main, sandbox, { filename: 'main.js' }); } catch (e) { /* 见上 */ }

let pass = 0, fail = 0;
const ok = (cond, msg) => { (cond ? pass++ : fail++); console.log((cond ? 'PASS ' : 'FAIL ') + msg); };

if (typeof sandbox.setBubble !== 'function' || typeof sandbox.tickBubbles !== 'function'
    || typeof sandbox.setActionBubble !== 'function') {
  console.error('FAIL: setBubble / tickBubbles / setActionBubble 未全部定义');
  process.exit(1);
}

// 假的气泡文本对象,塞进页面的 pronunciatios 表
const mk = () => ({ _t: '', setText(v) { this._t = v; } });
sandbox.pronunciatios['B'] = mk();
sandbox.pronunciatios['C'] = mk();

// 1) 存全文
sandbox.setBubble('B', 'Ethan Lin', '你好世界');
ok(sandbox.bubble_full['B'] === '你好世界', 'setBubble 存全文');

// 2) 逐字:每次强制吐一字
sandbox.bubble_type_acc = 999;
sandbox.tickBubbles(16.67);
ok(sandbox.pronunciatios['B']._t === 'Ethan Lin：你', '打字机第 1 字');
sandbox.bubble_type_acc = 999;
sandbox.tickBubbles(16.67);
ok(sandbox.pronunciatios['B']._t === 'Ethan Lin：你好', '打字机第 2 字');

// 3) 吐满即停
for (let i = 0; i < 10; i++) { sandbox.bubble_type_acc = 999; sandbox.tickBubbles(16.67); }
ok(sandbox.pronunciatios['B']._t === 'Ethan Lin：你好世界', '吐满即停');

// 4) 超长截断(上限值随需求会调,所以断言"确实截了 + 带省略号",不写死具体字数)
sandbox.setBubble('B', 'Ethan Lin', 'x'.repeat(500));
ok(sandbox.bubble_full['B'].length < 500 && /…$/.test(sandbox.bubble_full['B']),
   '超长截断并以省略号收尾,实得长度 ' + sandbox.bubble_full['B'].length);

// 5) 新句覆盖,并从 0 重新吐
sandbox.setBubble('B', 'Ethan Lin', '新的一句');
ok(sandbox.bubble_full['B'] === '新的一句' && sandbox.bubble_shown['B'] === 0,
   '新句覆盖并从 0 开始');

// 6) 有对话时,行动文本不抢镜
sandbox.setActionBubble('B', '走动中');
ok(sandbox.bubble_full['B'] === '新的一句', '有对话时行动文本不覆盖');

// 7) 无对话时,行动文本占位且直接全显(不打字机)
sandbox.setActionBubble('C', '发呆');
ok(sandbox.bubble_full['C'] === '发呆' && sandbox.bubble_shown['C'] === 2,
   '无对话时行动文本占位且直接全显');

// 8) 一次只念一句(2026-10-10 用户:"Ethan 打字机效果说完之后,AI Investor 才应该
//    开始打字机,这才拟真")。原来 `tickBubbles` 把所有角色一起吐 ⇒ 两人同时打字。
sandbox.setBubble('B', 'Ethan Lin', '一二三四五');
sandbox.setBubble('C', 'Investment AI', 'ABCDEFG');
let both = 0, bDoneAt = -1, cStartAt = -1;
let lastB = 0, lastC = 0;
for (let i = 0; i < 40; i++) {
  sandbox.bubble_type_acc = 999;
  sandbox.tickBubbles(16.67);
  const bLen = sandbox.pronunciatios['B']._t.length, cLen = sandbox.pronunciatios['C']._t.length;
  // 念完的句子**保留**在气泡里,所以"有文字"不等于"正在打字";要看长度是否在增长
  if (bLen > lastB && cLen > lastC) both++;
  lastB = bLen; lastC = cLen;
  if (bDoneAt < 0 && sandbox.pronunciatios['B']._t.indexOf('一二三四五') > 0) bDoneAt = i;
  if (cStartAt < 0 && cLen > 0) cStartAt = i;
}
ok(both === 0, '同一时刻不应有两句同时在打字,实得同增的 tick 数=' + both);
ok(bDoneAt >= 0 && cStartAt > bDoneAt,
   'AI 那句要等 Ethan 念完才开口,实得 Ethan 念完于第 ' + bDoneAt + ' tick,AI 开口于第 ' + cStartAt + ' tick');
ok(sandbox.pronunciatios['B']._t === '',
   '换人开口时上一位的框要清空(同一时刻只留一个框),实得残留 ' + JSON.stringify(sandbox.pronunciatios['B']._t));

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
