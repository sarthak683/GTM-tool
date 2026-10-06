// Run with Playwright available on NODE_PATH against the built local frontend.
// Synthetic API responses only: no CRM records or credentials are used.
const { chromium, expect } = require('playwright/test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const screenshots = process.env.OPPORTUNITIES_SCREENSHOT_DIR;
  if (screenshots) fs.mkdirSync(screenshots, { recursive: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.addInitScript(() => localStorage.setItem('beacon_token', 'synthetic-test-only'));
    const company = { id: 'account-test', name: 'IBS Software Services', domain: 'ibsplc.com', account_status: 'in_pipeline', enrichment_cache: {}, tech_stack: {}, created_at: '2026-04-27T12:30:00Z', updated_at: '2026-09-30T06:01:00Z' };
    const user = { id: 'test-user', name: 'Test AE', email: 'test@example.invalid', role: 'ae', is_active: true };
    const sdr = { ...user, id: 'test-sdr', name: 'Test SDR', role: 'sdr' };
    const stages = [{ id: 'demo_done', label: 'Demo Done', group: 'active', color: '#2563eb' }, { id: 'msa_review', label: 'MSA Review', group: 'active', color: '#6d28d9' }, { id: 'closed_won', label: 'Closed Won', group: 'closed', color: '#16a34a' }];
    const base = { company_id: company.id, company_name: company.name, assigned_to_id: user.id, sdr_id: sdr.id, value: 50000, currency_code: 'USD', close_date_est: '2026-12-01', next_step: 'Review implementation scope', priority: 'normal', health: 'green', days_in_stage: 3, stakeholder_count: 0, contact_count: 0, is_marketing_lead: false, pipeline_type: 'deal', tags: [], qualification: {}, created_at: '2026-09-30T06:01:00Z', updated_at: '2026-09-30T06:01:00Z' };
    const sample = [{ ...base, id: 'deal-1', name: 'IBS Software', stage: 'demo_done' }, { ...base, id: 'deal-2', name: 'IBS Software – Paid Pilot', stage: 'msa_review', currency_code: 'INR', value: 100000 }];
    let rows = sample;
    let failList = false;
    let failDetail = false;
    const calls = [];
    const writes = [];
    await context.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      calls.push(url);
      if (route.request().method() !== 'GET') writes.push(path);
      let data = [];
      if (path === '/api/v1/auth/me') data = user;
      else if (path === '/api/v1/auth/users/all') data = [user, sdr];
      else if (path === `/api/v1/account-sourcing/companies/${company.id}`) data = company;
      else if (path === '/api/v1/settings/deal-stages') data = { stages };
      else if (path === '/api/v1/deals/') {
        assert.equal(url.searchParams.get('company_id'), company.id, 'Every page must be scoped to this account');
        if (failList) return route.fulfill({ status: 500, json: { detail: 'Opportunities temporarily unavailable' } });
        const skip = Number(url.searchParams.get('skip'));
        const limit = Number(url.searchParams.get('limit'));
        data = { items: rows.slice(skip, skip + limit), total: rows.length, page: skip / limit + 1, size: limit, pages: Math.ceil(rows.length / limit) };
      } else if (/^\/api\/v1\/deals\/deal-\d+$/.test(path)) {
        if (failDetail) return route.fulfill({ status: 403, json: { detail: 'You cannot access this deal' } });
        data = rows.find(deal => deal.id === path.split('/').pop());
      } else if (path.endsWith('/tasks')) data = { items: [], total: 0 };
      else if (path.endsWith('/sync-status')) data = {};
      await route.fulfill({ json: data });
    });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const errors = [];
    const consoleErrors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {
      // The explicit error-state checks below deliberately return 500/403.
      if (message.type() === 'error' && !/Failed to load resource: the server responded with a status of (500|403)/.test(message.text())) consoleErrors.push(message.text());
    });
    const pageUrl = `${process.env.OPPORTUNITIES_TEST_URL || 'http://localhost:8080'}/account-sourcing/${company.id}`;
    await page.goto(pageUrl);
    const open = page.getByRole('button', { name: 'Opportunities', exact: true });
    await open.waitFor();
    assert(!calls.some(url => url.pathname === '/api/v1/deals/'), 'Do not fetch deals before the button is opened');
    const opportunitiesBox = await open.boundingBox();
    const tasksBox = await page.getByRole('button', { name: 'Tasks', exact: true }).boundingBox();
    assert(opportunitiesBox.y < tasksBox.y, 'Opportunities must be above Tasks');
    await open.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('heading', { name: 'Opportunities (2)' })).toBeVisible();
    for (const deal of sample) await expect(dialog.getByRole('heading', { name: deal.name, exact: true })).toBeVisible();
    await expect(dialog.getByText('$50,000', { exact: true })).toBeVisible();
    await expect(dialog.getByText('₹100,000', { exact: true })).toBeVisible();
    for (const card of await dialog.locator('article').all()) {
      await expect(card.locator('dl')).toContainText('Test AE');
      await expect(card.locator('dl')).toContainText('Test SDR');
      await expect(card.locator('dl')).not.toContainText('Unassigned');
    }
    if (screenshots) await page.screenshot({ path: `${screenshots}/desktop.png` });
    await dialog.getByRole('button', { name: /View full deal\s*: IBS Software$/ }).click();
    await expect(page.getByRole('heading', { name: 'IBS Software', exact: true })).toBeVisible();
    assert.equal(page.url(), pageUrl, 'Full details must keep the account page open');
    for (const tab of ['Overview', 'MEDDPICC', 'Activity', 'Timeline', 'Tasks', 'Emails', 'Documents']) {
      await expect(page.getByRole('button', { name: new RegExp(`^${tab}( \\(\\d+\\))?$`) }).last()).toBeVisible();
    }
    await page.getByRole('button', { name: 'Documents', exact: true }).click();
    await page.locator('div[style*="z-index: 51"] button:has(svg.lucide-x)').first().click();
    await expect(dialog.getByRole('heading', { name: 'Opportunities (2)' })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    const bounds = await dialog.boundingBox();
    assert(bounds.x >= 0 && bounds.x + bounds.width <= 390 && bounds.y >= 0 && bounds.y + bounds.height <= 845, 'Mobile dialog must fit viewport');
    assert(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth), 'Mobile dialog must not overflow horizontally');
    if (screenshots) await page.screenshot({ path: `${screenshots}/mobile.png` });
    for (let i = 0; i < 6; i++) await page.keyboard.press('Tab');
    assert(await dialog.evaluate(el => el.contains(document.activeElement)), 'Keyboard focus must stay inside the modal');
    await dialog.getByRole('button', { name: /View full deal\s*: IBS Software – Paid Pilot$/ }).click();
    await expect(page.getByRole('heading', { name: 'IBS Software – Paid Pilot', exact: true })).toBeVisible();
    const drawer = page.locator('div[style*="z-index: 51"]');
    await drawer.evaluate(el => Promise.all(el.getAnimations().map(animation => animation.finished)));
    const drawerBounds = await drawer.boundingBox();
    if (screenshots) await page.screenshot({ path: `${screenshots}/mobile-deal.png` });
    assert(drawerBounds.x >= 0 && drawerBounds.x + drawerBounds.width <= 390 && drawerBounds.y >= 0 && drawerBounds.y + drawerBounds.height <= 845, `Mobile deal drawer must fit viewport: ${JSON.stringify(drawerBounds)}`);
    assert(await drawer.evaluate(el => el.contains(document.elementFromPoint(195, 810))), 'Mobile navigation must not cover the deal drawer');
    await page.locator('div[style*="z-index: 51"] button:has(svg.lucide-x)').first().click();
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
    await open.waitFor();

    rows = [{ ...sample[0], assigned_to_id: null, sdr_id: 'inactive-user' }];
    await open.click();
    await expect(dialog.getByRole('heading', { name: 'Opportunities (1)' })).toBeVisible();
    await expect(dialog.locator('dl > div').filter({ has: page.getByText('Owner', { exact: true }) }).locator('dd')).toHaveText('Unassigned');
    await expect(dialog.locator('dl > div').filter({ has: page.getByText('SDR', { exact: true }) }).locator('dd')).toHaveText('Assigned user unavailable');
    await dialog.getByRole('button', { name: 'Close opportunities' }).click();
    await open.waitFor();

    rows = [];
    await open.click();
    await expect(dialog.getByText('No opportunities yet')).toBeVisible();
    await dialog.getByRole('button', { name: 'Close opportunities' }).click();
    await open.waitFor();
    failList = true;
    await open.click();
    await expect(dialog.getByRole('alert')).toContainText('Opportunities temporarily unavailable');
    failList = false;
    rows = sample;
    await dialog.getByRole('button', { name: 'Try again' }).click();
    await expect(dialog.getByRole('heading', { name: 'Opportunities (2)' })).toBeVisible();
    failDetail = true;
    await dialog.getByRole('button', { name: /View full deal\s*: IBS Software$/ }).click();
    await expect(dialog.getByRole('alert')).toContainText('You cannot access this deal');
    failDetail = false;
    await dialog.getByRole('button', { name: 'Close opportunities' }).click();
    await open.waitFor();

    rows = Array.from({ length: 501 }, (_, i) => ({ ...base, id: `deal-${i + 1}`, name: `Opportunity ${i + 1}`, stage: i === 500 ? 'closed_won' : 'demo_done' }));
    await open.click();
    await expect(dialog.getByRole('heading', { name: 'Opportunities (501)' })).toBeVisible();
    await expect(dialog.getByRole('heading', { name: 'Opportunity 501', exact: true })).toBeAttached();
    await expect(dialog.getByText('Closed Won', { exact: true })).toBeAttached();
    assert(calls.some(url => url.pathname === '/api/v1/deals/' && url.searchParams.get('skip') === '500'), 'Fetch the second page too');
    assert.deepEqual(writes, [], 'Browsing must not mutate CRM records');
    assert.deepEqual(errors, [], 'No browser runtime errors');
    assert.deepEqual(consoleErrors, [], 'No unexpected browser console errors');
    console.log(JSON.stringify({ result: 'passed', viewportChecks: ['1440x900', '390x844'], checks: ['account-scoped deals', 'full details in account page', 'both sample deals', 'empty state', 'error and retry', 'access error', 'closed opportunities', '501 deals across pages', 'no writes', 'no runtime errors'] }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
