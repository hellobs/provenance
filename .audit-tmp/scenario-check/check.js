const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({
    executablePath: 'C:\\Users\\rui\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe',
    headless: true
  });
  const page = await browser.newPage({ viewport: { width: 1100, height: 1400 } });
  const out = {};
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const visible = async (sel, forceWait) => {
    // return whether element is visually displayed
    const r = await page.evaluate((sel2) => {
      const el = document.querySelector(sel2);
      if (!el) return { exists: false, display: null };
      const cs = getComputedStyle(el);
      return { exists: true, display: cs.display, rect: el.getBoundingClientRect().height };
    }, sel);
    return r;
  };

  // wait for network idle a bit
  await page.goto('http://127.0.0.1:8060/scenario', { waitUntil: 'networkidle' });
  await sleep(600);

  // Wait for asset options to load
  await page.waitForFunction(() => {
    const sel = document.querySelector('#asset_story');
    return sel && sel.options.length > 1;
  }, { timeout: 8000 }).catch(()=>{});
  await sleep(400);

  // ---- Step 2: default engine experiment-eval ----
  out.engine_default = await page.evaluate(() => document.getElementById('engine').value);
  out.roles_visible_default = await visible('#roles-section');
  out.sandbox_visible_default = await visible('#sandbox-world-section');

  // screenshot experiment-eval state
  await page.screenshot({ path: 'd:/zzr/provenance/.audit-tmp/scenario-check/state_experiment_eval.png' });

  // ---- Inspect asset pickers (fields) ----
  const assetInfo = await page.evaluate(() => {
    const keys = ['asset_story','asset_relationships','asset_maze','asset_governance','asset_agents'];
    const picks = {};
    keys.forEach(k => {
      const sel = document.querySelector('#'+k);
      picks[k] = { tag: sel.tagName, isSelect: sel.tagName==='SELECT', isTextInput: sel.tagName==='INPUT'&&sel.type==='text' };
      let opts = [];
      if (sel && sel.options) opts = Array.from(sel.options).map(o=>o.value);
      picks[k].options = opts;
      picks[k].optionCount = opts.length;
    });
    return picks;
  });

  // ---- Step 3: switch engine to sandbox-value ----
  await page.selectOption('#engine', 'sandbox-value');
  await sleep(300);
  out.engine_sandbox = await page.evaluate(() => document.getElementById('engine').value);
  out.roles_visible_sandbox = await visible('#roles-section');
  out.sandbox_visible_sandbox = await visible('#sandbox-world-section');

  // screenshot sandbox-value state
  await page.screenshot({ path: 'd:/zzr/provenance/.audit-tmp/scenario-check/state_sandbox_value.png' });

  // ---- Step 4/5: select assets, fill fields, preview ----
  await page.selectOption('#asset_story', 'case00/scenario/story.json');
  await page.selectOption('#asset_agents', 'frontend/static/assets/village/agents');
  await page.fill('#value_tendency', '{"governance":{"AI Advisor":{"Compliance Rigor":0.4}}}');
  await page.fill('#sandbox_params', '{"percept":{"mode":"box"}}');
  await sleep(200);

  const filled = await page.evaluate(() => ({
    story: document.getElementById('asset_story').value,
    agents: document.getElementById('asset_agents').value,
    value_tendency: document.getElementById('value_tendency').value,
    sandbox_params: document.getElementById('sandbox_params').value
  }));

  await page.evaluate(() => {
    // confirm no top-level custom key check by triggering preview
  });
  const [resp] = await Promise.all([
    page.waitForResponse(r => r.url().includes('/api/scenario/preview'), { timeout: 8000 }),
    page.click('text=预览并校验')
  ]);
  await sleep(600);
  const resultText = await page.textContent('#result');
  const resultVisible = await page.evaluate(() => document.getElementById('result').style.display);

  // ---- Step 6: switch back to experiment-eval ----
  await page.selectOption('#engine', 'experiment-eval');
  await sleep(300);
  out.engine_back = await page.evaluate(() => document.getElementById('engine').value);
  out.roles_visible_back = await visible('#roles-section');
  out.sandbox_visible_back = await visible('#sandbox-world-section');

  out.filled = filled;
  out.assetInfo = assetInfo;
  out.resultText = resultText;
  out.resultVisible = resultVisible;

  // preview response raw
  let previewJson = null;
  try { const r = await resp.json(); previewJson = r; } catch(e) { previewJson = null; }
  out.previewJson = previewJson;

  console.log(JSON.stringify(out, null, 2));
  await browser.close();
})().catch(e => { console.error('ERROR:', e.message); process.exit(1); });