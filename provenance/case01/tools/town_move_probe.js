// 小镇页移动/避让的**行为**自查(2026-10-09)。
//
// 用户反馈原文:"有走路但是被前面的人挡住，从而一直在一个地方走"。
// 这段逻辑活在 `frontend/templates/main_script.html` 的内联 JS 里,
// `tests/frontend_smoke.js` **只查语法** —— 逻辑写错照样 green。
//
// 本脚本**不连浏览器**:把渲染好的小镇页 HTML 里的主 <script> 抽出来,在最小桩里
// 真跑 `moveAgent()` + `update()` 若干帧,断言"该走的人走到了、停下的人不再原地踏步"。
//
// 关键几何(与页面一致,勿改):tile=32px、每帧步长 0.8px、hitbox 30x40。
// 两个角色被后端钉在同一格时(强制交互节点就是这么干的),
// "推离"与"走向目标"是两股相向的力 —— 一旦二者每帧抵消,角色位置不动但走路动画照播。
//
// 用法:node case01/tools/town_move_probe.js --page-file <渲染好的 index.html>
// 退出码:0=全部通过;1=有断言失败或抛异常。
const fs = require('fs');
const vm = require('vm');

const argv = process.argv.slice(2);
let pageFile = '';
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--page-file') { pageFile = argv[++i]; }
}
if (!pageFile) {
  console.error('用法:node town_move_probe.js --page-file <index.html>');
  process.exit(1);
}
const html = fs.readFileSync(pageFile, 'utf8');
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
const main = scripts.find((s) => s.includes('function moveAgent') && s.includes('function update'));
if (!main) {
  console.error('FAIL: 页面脚本里找不到 moveAgent/update —— 移动改动可能没进渲染页');
  process.exit(1);
}

function mkSprite(name) {
  return {
    name: name,
    body: { x: 0, y: 0 },
    x: 0, y: 0, width: 90, height: 30, text: '',
    anims: {
      isPlaying: false,
      play() { this.isPlaying = true; },
      stop() { this.isPlaying = false; },
    },
    setTexture() {},
    setOrigin() {},
    setVisible() {},
    setText(v) { this.text = v; },
  };
}

// 最小 DOM 桩:任何 id 都返回同一个假元素(顶层脚本要读它的属性/挂事件)。
function mkEl() {
  return {
    id: '', innerHTML: '', textContent: '', value: '', style: {},
    scrollTop: 0, scrollHeight: 0, className: '',
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    getAttribute: () => null, setAttribute() {}, removeAttribute() {},
    addEventListener() {}, removeEventListener() {}, appendChild() {}, remove() {},
    querySelector: () => null, querySelectorAll: () => [], focus() {}, click() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }),
  };
}
const sandbox = {
  console, Date, Math, JSON, Object, Array, String, Number, Boolean,
  document: {
    getElementById: () => mkEl(),
    querySelector: () => mkEl(),
    querySelectorAll: () => [],
    createElement: () => mkEl(),
    addEventListener() {},
    body: mkEl(),
  },
  window: {}, setTimeout, clearTimeout,
  // Phaser 桩:顶层会 `new Phaser.Game(config)`;这里只要它别抛,
  // 后面被测的 moveAgent/update 都是纯函数式逻辑,不依赖真实引擎。
  Phaser: {
    AUTO: 0,
    Scale: { NONE: 0 },
    Game: function () { return { scene: {}, destroy() {} }; },
  },
};
sandbox.window.addEventListener = () => {};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// 顶层脚本末尾会继续调 Phaser(未提供),抛在函数定义之后,忽略即可。
try { vm.runInContext(main, sandbox, { filename: 'main.js' }); }
catch (e) { if (process.env.PROBE_DEBUG) console.error('[probe] 顶层脚本中断:' + e.message); }

const run = (expr) => vm.runInContext(expr, sandbox);

// ---- 把页面切到 live 模式,并补齐 create() 里才会有的运行期对象 ----
run('live_mode = true; sim_finished = false; sim_error = "";');
run('colLayer = { width: 27, height: 24, getTileAt: function () { return { collide: false }; } };');
run('cursors = { left: {isDown:false}, right: {isDown:false}, up: {isDown:false}, down: {isDown:false} };');
run('player = { body: { x:0, y:0, setVelocity(){}, setVelocityX(){}, setVelocityY(){} } };');
run('delete all_movement; all_movement = {};');

let pass = 0, fail = 0;
const ok = (cond, msg) => { (cond ? pass++ : fail++); console.log((cond ? 'PASS ' : 'FAIL ') + msg); };

function setup(names) {
  run('personas = {}; pronunciatios = {}; movement_target = {}; pre_anims_direction_dict = {}; bubble_seq = {}; bubble_seq_counter = 0;');
  for (const n of names) {
    sandbox.personas[n] = mkSprite(n);
    sandbox.personas[n].body.x = 0;
    sandbox.personas[n].body.y = 0;
    // 真实页面里新角色 displayWidth=40、scaleY=scaleX ⇒ 画出来就是 40x40
    sandbox.personas[n].displayWidth = 40;
    sandbox.personas[n].displayHeight = 40;
    sandbox.pronunciatios[n] = mkSprite(n + '-bubble');
  }
}

// 给某个角色挂一句气泡(只设尺寸与文本 —— 真实页面里这两个由 Phaser Text 量出来)
// seq = 说话序号,决定两个气泡左右摆放的先后(先说的靠左)
function bubbleBox(name, w, h, seq) {
  const t = sandbox.pronunciatios[name];
  t.width = w;
  t.height = h;
  t.text = 'x';
  if (seq != null) sandbox.bubble_seq[name] = seq;
}

const charRect = (n) => {
  const p = sandbox.personas[n];
  return { x: p.body.x, y: p.body.y, w: p.displayWidth, h: p.displayHeight };
};
const bubbleRect = (n) => {
  const t = sandbox.pronunciatios[n];
  return { x: t.x - t.width / 2, y: t.y - t.height, w: t.width, h: t.height };
};
const rectOverlap = (a, b) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

function place(name, tx, ty) {
  sandbox.personas[name].body.x = tx * 32;
  sandbox.personas[name].body.y = ty * 32;
}

function tick(n) {
  for (let i = 0; i < n; i++) {
    run('update(0, 16.67);');
    if (process.env.PROBE_TRACE && i % (+process.env.PROBE_TRACE || 40) === 0) {
      console.log('  f' + i + ' ' + run(
        'JSON.stringify(Object.keys(personas).map(function (n) {' +
        '  var q = movement_target[n];' +
        '  return [Math.round(personas[n].body.x * 10) / 10,' +
        '          Math.round(personas[n].body.y * 10) / 10,' +
        '          personas[n].anims.isPlaying ? "walk" : "idle",' +
        '          q ? q.length : 0];' +
        '}))'));
    }
  }
}

const pos = (n) => [sandbox.personas[n].body.x, sandbox.personas[n].body.y];
const near = (a, b, tol) => Math.abs(a[0] - b[0]) <= tol && Math.abs(a[1] - b[1]) <= tol;
const walking = (n) => sandbox.personas[n].anims.isPlaying;

// ============================================================
// 复现:"一人已在目标格、另一人正走向该格"
// 后端在强制交互节点把两人钉到同一格,前端收到的就是这种形状:
//   停住的那个 path=[]、行走的那个 path=[..., 目标格]
// ============================================================
setup(['Investment AI', 'Ethan Lin']);   // 顺序与页面一致(roles 顺序)
place('Investment AI', 10, 6);           // 已在目标格,path=[] → 立即停住
place('Ethan Lin', 10, 9);
run('moveAgent("Investment AI", [10,6], "act", "loc", "", []);');
run('moveAgent("Ethan Lin", [10,6], "act", "loc", "", [[10,9],[10,8],[10,7],[10,6]]);');

tick(400);   // 400 帧 ≈ 6.7 秒,足够走完 3 格(每格 40 帧)
const ethanPos = pos('Ethan Lin');
ok(near(ethanPos, [320, 192], 34),
   '走向同伴所在格的 Ethan 应停在目标格附近,实得 ' + JSON.stringify(ethanPos));
ok(!walking('Ethan Lin'), '走完的 Ethan 不应还在原地踏步(anims.isPlaying=' + walking('Ethan Lin') + ')');

// 再跑 200 帧:位置必须稳定,不能出现"每帧动一点、永远走不到"的极限环
const before = pos('Ethan Lin');
tick(200);
ok(near(pos('Ethan Lin'), before, 1),
   '再跑 200 帧位置应保持不变,实得 ' + JSON.stringify(pos('Ethan Lin')) + ' 从 ' + JSON.stringify(before));
ok(!walking('Ethan Lin'), '稳态下 Ethan 不应继续播走路动画');

// ============================================================
// 同上,但**角色顺序反过来**(roles 顺序决定谁在分离逻辑里让位):
// 行走的 Ethan 排在前面(数组下标 0)= 分离逻辑里的"让位方"。
// 若他每前进一步都被"推离"原样推回去,就会永远走不到 —— 这正是
// "有走路但被前面的人挡住、一直在同一个地方走"。
// ============================================================
setup(['Ethan Lin', 'Investment AI']);
place('Investment AI', 10, 6);
place('Ethan Lin', 10, 9);
run('moveAgent("Investment AI", [10,6], "act", "loc", "", []);');
run('moveAgent("Ethan Lin", [10,6], "act", "loc", "", [[10,9],[10,8],[10,7],[10,6]]);');
tick(400);
ok(near(pos('Ethan Lin'), [320, 192], 34),
   '角色顺序反过来时,Ethan 同样应走到目标格,实得 ' + JSON.stringify(pos('Ethan Lin')));
ok(!walking('Ethan Lin'), '角色顺序反过来时,Ethan 走完也不应还在原地踏步');

// ============================================================
// 两人都在走同一条路到同一格(后端把两人钉到同一目标):
// 到达后都要停住,且两人不能叠在同一个像素
// ============================================================
setup(['Investment AI', 'Ethan Lin']);
place('Investment AI', 10, 6);
place('Ethan Lin', 10, 6);
const samePath = [[10,6],[10,7],[10,8],[10,9],[11,9],[12,9],[13,9],[14,9],[15,9],[16,9]];
run('moveAgent("Investment AI", [16,9], "act", "loc", "", ' + JSON.stringify(samePath) + ');');
run('moveAgent("Ethan Lin", [16,9], "act", "loc", "", ' + JSON.stringify(samePath) + ');');
tick(1200);
const aiP = pos('Investment AI'), ethP = pos('Ethan Lin');
ok(near(aiP, [512, 288], 40) && near(ethP, [512, 288], 40),
   '两人都应到达 [16,9] 附近,实得 AI=' + JSON.stringify(aiP) + ' Ethan=' + JSON.stringify(ethP));
ok(!walking('Investment AI') && !walking('Ethan Lin'), '到达后两人都不应还在踏步');
ok(Math.hypot(aiP[0] - ethP[0], aiP[1] - ethP[1]) > 20,
   '同格的两人应被错开、不叠在同一像素,实得间距 ' + Math.hypot(aiP[0] - ethP[0], aiP[1] - ethP[1]).toFixed(1));

// ============================================================
// 相向而行:一个朝对方走、对方也朝它走(用户反馈"反向的撞,
// 导致有个人一直要在原地或者反过来走路")
// ============================================================
setup(['Investment AI', 'Ethan Lin']);
place('Investment AI', 4, 5);
place('Ethan Lin', 9, 5);
run('moveAgent("Investment AI", [11,5], "act", "loc", "", [[5,5],[6,5],[7,5],[8,5],[9,5],[10,5],[11,5]]);');
run('moveAgent("Ethan Lin", [2,5], "act", "loc", "", [[9,5],[8,5],[7,5],[6,5],[5,5],[4,5],[3,5],[2,5]]);');
// 记录两人的 x 轨迹,看有没有出现"净位移为负"(反着走)
const trail = { 'Investment AI': [], 'Ethan Lin': [] };
for (let f = 0; f < 700; f++) {
  run('update(0, 16.67);');
  if (f % 25 === 0) {
    trail['Investment AI'].push(sandbox.personas['Investment AI'].body.x);
    trail['Ethan Lin'].push(sandbox.personas['Ethan Lin'].body.x);
  }
}
const mono = (arr, sign) => arr.every((v, i) => i === 0 || sign * (v - arr[i - 1]) >= -0.001);
ok(mono(trail['Investment AI'], +1),
   '向右走的 Investment AI 不应被推得反着走,轨迹 ' + JSON.stringify(trail['Investment AI']));
ok(mono(trail['Ethan Lin'], -1),
   '向左走的 Ethan 不应被推得反着走,轨迹 ' + JSON.stringify(trail['Ethan Lin']));
ok(near(pos('Investment AI'), [352, 160], 34) && near(pos('Ethan Lin'), [64, 160], 34),
   '相向而行后两人仍各自到达目标,实得 AI=' + JSON.stringify(pos('Investment AI')) +
   ' Ethan=' + JSON.stringify(pos('Ethan Lin')));
ok(!walking('Investment AI') && !walking('Ethan Lin'), '相向而行结束后两人都 idle');

// ============================================================
// 气泡必须挂在角色**正上方**(2026-10-09 用户硬性要求)
// 锚点 origin(0.5,1):(x,y) 是气泡底边中点 ⇒ x 该对准角色绘制范围的中线,y 该在顶边之上。
// ============================================================
setup(['Investment AI', 'Ethan Lin']);
place('Investment AI', 10, 6);
place('Ethan Lin', 10, 9);
bubbleBox('Investment AI', 128, 66, 2);
bubbleBox('Ethan Lin', 134, 42, 1);
tick(1);
const aiBody = sandbox.personas['Investment AI'].body;
const aiBub = sandbox.pronunciatios['Investment AI'];
ok(Math.abs(aiBub.x - (aiBody.x + 20)) < 0.01,
   '气泡水平居中于角色绘制范围,实得 x=' + aiBub.x + ' 角色中线=' + (aiBody.x + 20));
ok(aiBub.y <= aiBody.y, '气泡在角色上方(y 是底边),实得 y=' + aiBub.y + ' body.y=' + aiBody.y);

// ============================================================
// "不要遮挡人" + "两个框左右撇开"(2026-10-10 用户反馈)
// 两个人上下相邻站着、同时都在说话时:两个气泡必须左右分开,
// 而且**先说的靠左**(Ethan 先问、AI 后答 ⇒ 从左往右读就是聊天次序)。
// ============================================================
setup(['Investment AI', 'Ethan Lin']);
place('Investment AI', 10, 6);   // 上
place('Ethan Lin', 10, 7);       // 下(只差一格)
bubbleBox('Investment AI', 128, 66, 2);   // AI 后说
bubbleBox('Ethan Lin', 134, 42, 1);       // Ethan 先说
tick(1);
let covered = [];
for (const b of ['Investment AI', 'Ethan Lin']) {
  for (const c of ['Investment AI', 'Ethan Lin']) {
    if (rectOverlap(bubbleRect(b), charRect(c))) covered.push(b + ' 的气泡压住 ' + c);
  }
}
ok(covered.length === 0, '任何气泡都不许压住任何角色,实得:' + JSON.stringify(covered));
ok(rectOverlap(bubbleRect('Investment AI'), bubbleRect('Ethan Lin')) === false,
   '两个气泡之间也不重叠');
const ethB = bubbleRect('Ethan Lin'), aiB = bubbleRect('Investment AI');
// 聊天次序:摆位策略改成"优先偏上方"之后,两个框可能是左右排,也可能是上下排
// (各自都在本人正上方时)。两种摆法都要满足"先说的 Ethan 在前"。
ok(ethB.x + ethB.w <= aiB.x || ethB.y + ethB.h <= aiB.y,
   '先说的 Ethan 在前(要么整块在 AI 左边,要么整块在 AI 上边),实得 Ethan=' +
   JSON.stringify(ethB) + ' AI=' + JSON.stringify(aiB));
// 摆位策略:优先"偏上方"(留在本人附近),不是一路抬到另一个人头上。
const ethHome = charRect('Ethan Lin').x + charRect('Ethan Lin').w / 2;
ok(Math.abs((ethB.x + ethB.w / 2) - ethHome) < 200,
   'Ethan 的气泡仍留在本人附近(未漂到 AI 头顶),实得气泡中线=' +
   (ethB.x + ethB.w / 2) + ' 本人中线=' + ethHome);

// ============================================================
// 回归:目标格在墙里时,不能"位置不动、腿一直走"
// ============================================================
setup(['AI Advisor']);
place('AI Advisor', 5, 5);
run('colLayer = { width: 27, height: 24, getTileAt: function (tx, ty) { return { collide: tx >= 6 }; } };');
run('moveAgent("AI Advisor", [9,5], "act", "loc", "", [[6,5],[7,5],[8,5],[9,5]]);');
tick(200);
ok(near(pos('AI Advisor'), [160, 160], 34), '撞墙时应停在墙前,实得 ' + JSON.stringify(pos('AI Advisor')));
ok(!walking('AI Advisor'), '撞墙走不动时不应继续播走路动画(原地踏步)');

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
