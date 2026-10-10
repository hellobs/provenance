// 小镇页头顶气泡「聊天记录 + 打字机」的行为自查(2026-10-09)。
//
// 需求原文:"主角头上顶着那个要直接是聊天记录,要有打字机的效果"。
// 之前气泡显示的是"角色名: 行动文本"(纯文本、一次性全显、无打字机)。
//
// 本脚本**不连浏览器**:把渲染好的小镇页 HTML 里的主 <script> 抽出来,
// 在最小 DOM/Phaser 桩里真跑一遍,直接调 setBubble / tickBubbles / setActionBubble,
// 断言**行为**(逐字吐字、吐满即停、超长分段续说、换人清框、新句覆盖、行动文本不抢镜)。
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

// 2) 逐字:每次强制吐一字。
//    第一拍只"开新段"(把框清空,用户要求"先留着原本的两秒,然后清空气泡框"),
//    第二拍才开始吐第一个字。
sandbox.bubble_type_acc = 999;
sandbox.tickBubbles(16.67);
ok(sandbox.pronunciatios['B']._t === '', '开段那一拍先把气泡清空(不接着上一段留字)');
sandbox.bubble_type_acc = 999;
sandbox.tickBubbles(16.67);
ok(sandbox.pronunciatios['B']._t === 'Ethan Lin：你', '打字机第 1 字');
sandbox.bubble_type_acc = 999;
sandbox.tickBubbles(16.67);
ok(sandbox.pronunciatios['B']._t === 'Ethan Lin：你好', '打字机第 2 字');

// 3) 吐满即停
for (let i = 0; i < 10; i++) { sandbox.bubble_type_acc = 999; sandbox.tickBubbles(16.67); }
ok(sandbox.pronunciatios['B']._t === 'Ethan Lin：你好世界', '吐满即停');

// 4) 超长文本:**不丢弃**,切成多段排队接着说(2026-10-10 用户要求)
//    "先留着原本的两秒,然后清空气泡框,把还要输出的文本说出来"
sandbox.setBubble('B', 'Ethan Lin', 'x'.repeat(500));
const segs = sandbox.bubble_queue.filter((q) => q.name === 'B');
ok(segs.length >= 4, '500 字应被切成多段排队,实得段数 ' + segs.length);
const joined = segs.map((q) => q.text).join('');
ok(joined.replace(/\s+/g, '') === 'x'.repeat(500),
   '各段拼起来必须等于原文(一个字都不丢),实得 ' + joined.replace(/\s+/g, '').length + ' 字');
// 断点必须在**句末标点**或**空格**上,绝不把一个单词劈开
// (2026-10-10 用户截图出现过 "manu | facturer.")
const en = 'The company officially confirmed validation talks. However no specific order value or contract has been announced. The market rumour of a one hundred twenty billion yuan order is not supported by evidence. ';
sandbox.setBubble('B', 'Ethan Lin', en.repeat(3));
const esegs = sandbox.bubble_queue.filter((q) => q.name === 'B');
let bad = 0;
for (let i = 1; i < esegs.length; i++) {
  const prev = esegs[i - 1].text, cur = esegs[i].text;
  const at = en.repeat(3).indexOf(prev) + prev.length;
  if (!/[\s。！？.!?]/.test(en.repeat(3)[at - 1] || '')) bad++;
}
ok(bad === 0, '每段都断在空格或句末标点上(不劈开单词),实得劈开 ' + bad + ' 处');
ok(esegs.slice(0, -1).every((q) => /[。！？.!?]$/.test(q.text)),
   '有句子可断时应优先断在句末(下一段正好是句子开头),实得各段末字符 ' +
   esegs.map((q) => q.text.slice(-1)).join(''));
ok(segs.slice(0, -1).every((q) => q.text.length === segs[0].text.length),
   '除最后一段外每段都是满的上限长度,实得各段 ' + segs.map((q) => q.text.length).join('/'));

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
// 前面的块留下了 active 段,这里先清干净,各块互不串味
sandbox.bubble_queue.length = 0;
sandbox.bubble_active = ''; sandbox.bubble_active_text = ''; sandbox.bubble_active_sp = '';
sandbox.bubble_rest_acc = 0; sandbox.bubble_rest_need = 0;
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
