// Run with Playwright available on NODE_PATH. All API traffic is fulfilled
// locally with synthetic data; this never signs in or writes CRM records.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const url = process.env.PIPELINE_TEST_URL || 'http://localhost:8080/pipeline';
  const baseline = process.env.PIPELINE_BASELINE === '1';
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.addInitScript(() => {
      localStorage.setItem('beacon_token', 'synthetic-test-only');
      window.testLongTasks = [];
      new PerformanceObserver(list => window.testLongTasks.push(...list.getEntries().map(e => e.duration))).observe({ type: 'longtask', buffered: true });
    });
    const stages = ['reprospect', 'demo_scheduled', 'demo_done', 'qualified_lead', 'poc_agreed', 'poc_wip', 'poc_done', 'commercial_negotiation', 'msa_review', 'closed_won', 'backlog', 'not_a_fit', 'cold', 'closed_lost'];
    const board = Object.fromEntries(stages.map(s => [s, []]));
    for (let i = 0; i < 1000; i++) {
      const stage = stages[i % stages.length];
      board[stage].push({ id: `deal-${i}`, name: `Synthetic Deal ${i}`, stage, company_id: `company-${i}`, company_name: `Synthetic Account ${i}`, value: 50000, currency_code: 'USD', tags: ['Test'], next_step: 'Follow up on the evaluation', assigned_rep_name: 'Test Admin', assigned_to_id: 'test-user', days_in_stage: 12, close_date: '2026-12-01', pipeline_type: 'deal' });
    }
    const apiCalls = [];
    await context.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      apiCalls.push(path);
      let data = [];
      if (path === '/api/v1/auth/me') data = { id: 'test-user', name: 'Test Admin', email: 'test@example.invalid', role: 'admin', is_active: true };
      else if (path === '/api/v1/deals/board') data = board;
      else if (path === '/api/v1/settings/deal-stages') data = { stages: stages.map(id => ({ id, label: id.toUpperCase(), group: ['closed_won','backlog','not_a_fit','cold','closed_lost'].includes(id) ? 'closed' : 'active' })) };
      else if (path === '/api/v1/settings/pipeline-summary') data = {};
      else if (path === '/api/v1/performance/settings') data = { stage_probabilities: {} };
      else if (path === '/api/v1/deals/board/stream') return route.fulfill({ status: 401, body: '' });
      else if (path.startsWith('/api/v1/deals/deal-')) data = Object.values(board).flat().find(d => d.id === path.split('/').pop()) || [];
      return route.fulfill({ json: data });
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const cdp = await context.newCDPSession(page);
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 6 });
    await page.goto(url);
    await page.getByRole('button', { name: 'Synthetic Deal 0', exact: true }).waitFor();
    await page.waitForTimeout(1000);
    const metrics = await page.evaluate(() => ({ nodes: document.querySelectorAll('*').length, cards: document.querySelectorAll('[draggable=true]').length, longTasks: window.testLongTasks, maxLongTask: Math.max(0, ...window.testLongTasks) }));
    if (process.env.PIPELINE_SCREENSHOT_DIR) {
      require('node:fs').mkdirSync(process.env.PIPELINE_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: `${process.env.PIPELINE_SCREENSHOT_DIR}/desktop.png` });
    }
    if (!baseline) {
      assert(metrics.cards < 100, `Too many mounted cards: ${metrics.cards}`);
      assert.equal(await page.locator('.mobile-card').count(), 0);
      const nav = page.getByRole('navigation', { name: 'Card pages' }).first();
      await nav.getByRole('button', { name: 'Next' }).click();
      await page.getByRole('button', { name: 'Synthetic Deal 168', exact: true }).waitFor();
      await page.getByPlaceholder('Search deals...').fill('Synthetic Deal 994');
      await page.getByRole('button', { name: 'Synthetic Deal 994', exact: true }).waitFor();
      await page.getByPlaceholder('Search deals...').fill('');
      await page.getByRole('button', { name: 'Synthetic Deal 0', exact: true }).waitFor();
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByText('Synthetic Deal 0', { exact: true }).waitFor();
      assert.equal(await page.locator('[draggable=true]').count(), 0);
      assert(await page.getByText('Synthetic Account 0', { exact: true }).isVisible());
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
      assert.equal(overflow, false, 'Mobile page overflows horizontally');
      if (process.env.PIPELINE_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.PIPELINE_SCREENSHOT_DIR}/mobile.png` });
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.getByRole('button', { name: 'Synthetic Deal 0', exact: true }).waitFor();
      assert.equal(apiCalls.filter(p => p === '/api/v1/companies/').length, 0, 'Pipeline downloaded the company catalog');
      await page.getByRole('button', { name: 'Synthetic Deal 0', exact: true }).click();
      await page.locator('.tiptap').first().waitFor();
      await page.waitForTimeout(500);
      assert.equal(await page.getByRole('heading', { name: 'Something went wrong' }).count(), 0, 'Opening a deal crashed');
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ url, baseline, cpuSlowdown: 6, syntheticDeals: 1000, ...metrics, assertions: 'passed' }));
    await context.close();
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
