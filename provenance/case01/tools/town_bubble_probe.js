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
  // 场景角色及其出生坐标:预置房间按"≤3 人一间房 / >3 人 KNN 三三分组"用它。
  persona_names: { Ethan: {x: 40, y: 60}, AI: {x: 46, y: 64}, C: {x: 300, y: 400} },
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

function resetBubbles() {
  sandbox.bubble_group_keys.length = 0;
  for (const k in sandbox.bubble_groups) delete sandbox.bubble_groups[k];
  sandbox.bubble_rr = 0;
  sandbox.bubble_shown = {}; sandbox.bubble_full = {}; sandbox.bubble_speaker = {};
  sandbox.bubble_spoken = {};
  sandbox.bubble_seeded = false;
  sandbox.bubble_last_speaker = ''; sandbox.bubble_partner = {};
}

function allLines(name) {
  const out = [];
  for (const k of sandbox.bubble_group_keys) {
    for (const q of (sandbox.bubble_groups[k] || {}).lines || []) {
      if (q.name === name) out.push(q);
    }
  }
  return out;
}

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
ok(sandbox.pronunciatios['B']._t === 'Ethan Lin：\n你', '打字机第 1 字(气泡带"名字："前缀)');
sandbox.bubble_type_acc = 999;
sandbox.tickBubbles(16.67);
ok(sandbox.pronunciatios['B']._t === 'Ethan Lin：\n你好', '打字机第 2 字');

// 3) 吐满即停
for (let i = 0; i < 10; i++) { sandbox.bubble_type_acc = 999; sandbox.tickBubbles(16.67); }
ok(sandbox.pronunciatios['B']._t === 'Ethan Lin：\n你好世界', '吐满即停');

// 4) 超长文本:**不丢弃**,切成多段排队接着说(2026-10-10 用户要求)
//    "先留着原本的两秒,然后清空气泡框,把还要输出的文本说出来"
// 用**真句子**(带句号)而不是一串没有空格也没有句号的 x ——
// 切段规则是"整句优先":没有句末可断时才退到空格/硬切(2026-10-10 用户:
// "没说完的不要只是一个词,至少也是一句话吧")。
const SENTENCES = 'The company officially confirmed validation talks with an international manufacturer. '
  + 'However no specific order value or contract has been announced so far. '
  + 'The market rumour of a one hundred twenty billion yuan order is not supported by evidence. '
  + 'I cannot confirm the rumour and therefore do not recommend buying now. ';
const full = SENTENCES.repeat(3);
sandbox.setBubble('B', 'Ethan Lin', full);
const segs = allLines('B');
const joined = segs.map((q) => q.text).join(' ');
ok(joined.replace(/\s+/g, '') === full.replace(/\s+/g, ''),
   '各段拼起来必须等于原文(一个字都不丢),实得 ' +
   joined.replace(/\s+/g, '').length + ' / ' + full.replace(/\s+/g, '').length + ' 字');
ok(segs.length >= 2, '长文本应被切成多段排队,实得段数 ' + segs.length);
ok(segs.slice(0, -1).every((q) => /[。！？.!?]$/.test(q.text)),
   '除最后一段外,每段都必须断在**句末**(下一段正好是句子开头),实得各段末字符 ' +
   segs.map((q) => q.text.slice(-1)).join(''));
ok(segs.every((q) => !/\s$/.test(q.text)),
   '段尾不留悬空空格(断点处的空白在切段时吃掉)');

// 4b) 同一角色又开口时,**正在念的那一段不能从头重打**
//     (2026-10-10 用户:"说话说到一半突然来了属于它自己的新的一段话,他会突然打断,
//      然后重复原来说的一般的话之后再继续往下说")
resetBubbles();
sandbox.setBubble('B', 'Ethan Lin', '第一句话很长很长很长很长很长很长很长很长很长。');
for (let i = 0; i < 6; i++) { sandbox.bubble_type_acc = 999; sandbox.tickBubbles(16.67); }
const beforeNew = sandbox.pronunciatios['B']._t;
const shownBefore = sandbox.bubble_shown['B'];
sandbox.setBubble('B', 'Ethan Lin', '这是同一个人新的一段话。');
ok(sandbox.bubble_shown['B'] === shownBefore,
   '新台词不得把正在念的那一段进度清零(否则会从头重复),实得 ' +
   shownBefore + ' -> ' + sandbox.bubble_shown['B']);
for (let i = 0; i < 3; i++) { sandbox.bubble_type_acc = 999; sandbox.tickBubbles(16.67); }
ok(sandbox.pronunciatios['B']._t.length > beforeNew.length
   && sandbox.pronunciatios['B']._t.indexOf(beforeNew) === 0,
   '新台词到达后,正在念的那一段应当**接着往下念**,实得 "' +
   sandbox.pronunciatios['B']._t + '" 接在 "' + beforeNew + '" 之后');

// 4c) 价格里的**小数点不是句号**(2026-10-10 用户反馈"$47.30 被拆掉了")
resetBubbles();
const MONEY = 'The closing price was $47.30 on Tuesday. It then moved to $48.05. ';
sandbox.setBubble('B', 'Ethan Lin', MONEY.repeat(4));
const mseg = allLines('B');
ok(mseg.every((q) => !/\$\d+\.?$/.test(q.text)),
   '任何一段都不能以 "$47." 这样半个价格收尾(小数点被当句号),实得各段末: ' +
   mseg.map((q) => q.text.slice(-6)).join(' | '));
ok(mseg.map((q) => q.text).join(' ').includes('$47.30')
   && mseg.map((q) => q.text).join(' ').includes('$48.05'),
   '两个价格都必须完整出现在某一段里');

// 4c2) 回归:第一句没有对话对象可配,会自己开一间单人房;第二句才配上对。
//      两间房 ⇒ 同一段对话的两个人同时显示(用户 2026-10-10 截图)。
//      期望:配出成对房间时把单人房并进去 ⇒ **全程只有一间房**。
resetBubbles();
sandbox.setBubble('B', 'Ethan Lin', '我先开口。');
sandbox.setBubble('C', 'Investment AI', '你问我答。');
sandbox.setBubble('B', 'Ethan Lin', '我再问一句。');
ok(sandbox.bubble_group_keys.length === 1,
   '两人交替说话全程只应有一间房(单人房要被并入),实得 ' +
   sandbox.bubble_group_keys.length + ' 间: ' + JSON.stringify(sandbox.bubble_group_keys));

// 4c3) **预置房间**(用户规则:≤3 人 ⇒ 全部同一间房;>3 人 ⇒ KNN 三三分组)
//      场景里就 Ethan / AI / C 三个人 ⇒ 应当**只有一间房**,谁都别自己开单人间。
resetBubbles();
sandbox.setBubble('B', 'Ethan Lin', '第一句。');
ok(sandbox.bubble_group_keys.length === 1,
   '≤3 人时预置成**一间**房,实得 ' + sandbox.bubble_group_keys.length + ' 间');
sandbox.setBubble('C', 'Investment AI', '第二句。');
ok(sandbox.bubble_group_keys.length === 1,
   '再来一个人也不该新增房间,实得 ' + sandbox.bubble_group_keys.length + ' 间');

// 4d) **房间之间并行**:B×C 一个房、E×D 另一个房,两房应当**同时**在打字。
//     (房内互斥、房间并行 —— 2026-10-10 用户定的语义)
resetBubbles();
sandbox.pronunciatios['E'] = mk(); sandbox.pronunciatios['D'] = mk();
sandbox.setBubble('B', 'Ethan Lin', '一二三四五', 'C');
sandbox.setBubble('E', '另一人甲', '六七八九十', 'D');
let bothRooms = 0;
for (let i = 0; i < 6; i++) {
  sandbox.bubble_type_acc = 999; sandbox.tickBubbles(16.67);
  const bl = sandbox.pronunciatios['B']._t.length;
  const el = sandbox.pronunciatios['E']._t.length;
  if (bl > 1 && el > 1) bothRooms++;
}
ok(bothRooms >= 3, '两个房间应当并行推进(同一 tick 里两个框都在变长),实得同增 tick 数=' + bothRooms);
const bRooms = sandbox.bubble_group_keys.length;
ok(bRooms === 2, '两段对话应是两个独立房间,实得 ' + bRooms);

// 5) 新句覆盖(没有正在念的段时,新句从头开始吐)
resetBubbles();
sandbox.setBubble('B', 'Ethan Lin', '新的一句');
ok(sandbox.bubble_full['B'] === '新的一句' && sandbox.bubble_shown['B'] === 0,
   '没有正在念的段时,新句从头开始吐');

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
resetBubbles();
// 第四个参数 = 对话对象:把两人放进**同一个房间**。
// 房内一次只念一句(互斥),房与房之间并行 —— 这是 2026-10-10 用户定的语义。
sandbox.setBubble('B', 'Ethan Lin', '一二三四五', 'C');
sandbox.setBubble('C', 'Investment AI', 'ABCDEFG', 'B');
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
