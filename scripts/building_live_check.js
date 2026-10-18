// Live browser check of building labeling against the running local stack (not mocked).
//
//   node scripts/building_live_check.js <projectId> <campaignId>
//
// Use a freshly seeded building campaign whose scenes are untouched, e.g.
//   python scripts/seed_building_datasets.py --only sn1_rio --reset --confirm-reset DELETE-ANNOTATIONS
// It draws two buildings (one squared), reloads, re-scores, deletes one, completes the
// scene, marks the next one empty, checks the panel, the server and the export, takes the
// first scene's outcome off again (Draft), then finishes everything, hands in, checks the
// scenes are locked, and withdraws.
// Headless Chromium from frontend/node_modules (playwright-core); screenshot in data/.
const path = require('path');
const { chromium } = require(path.join(__dirname, '..', 'frontend', 'node_modules', 'playwright-core'));

const [PROJECT, CID] = process.argv.slice(2).map(Number);
if (!PROJECT || !CID) {
  console.error('usage: node scripts/building_live_check.js <projectId> <campaignId>');
  process.exit(2);
}
const URL = `${process.env.APP_URL ?? 'http://localhost:5173'}/projects/${PROJECT}/campaigns/${CID}/annotate`;
const API = process.env.API_URL ?? 'http://localhost:8000/api';
const checks = [];
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  checks.push(ok);
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}: ${JSON.stringify(got)}${ok ? '' : ` want ${JSON.stringify(want)}`}`);
};
const api = async (p) => (await fetch(API + p, { headers: { Authorization: 'Bearer local' } })).json();

async function open(page) {
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 180000 });
  await page.waitForSelector('[data-testid=scene-shapes]', { timeout: 180000 });
  await page.getByText('Skip Tour').first().click({ timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(2500);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await open(page);
  const scene = () => page.getByTestId('scene-shapes').getAttribute('data-scene-task-id');
  const shapes = () => page.locator('[data-testid=scene-shape]').count();
  const firstScene = await scene();
  check('authoritative hidden', await page.getByRole('button', { name: 'Submit authoritative' }).count(), 0);
  check('complete disabled with no buildings', await page.getByTestId('scene-complete').isDisabled(), true);

  const box = await page.locator('[data-crosshair-lon]').first().boundingBox();
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  await page.getByTestId('scene-tool-draw').click();
  const draw = async (dx, dy, s, key, square) => {
    for (const [x, y] of [[dx - s, dy - s], [dx + s, dy - s + 4], [dx + s - 3, dy + s]]) {
      await page.mouse.click(cx + x, cy + y);
      await page.waitForTimeout(150);
    }
    await page.mouse.dblclick(cx + dx - s, cy + dy + s);
    await page.waitForSelector('[data-testid=scene-score-prompt]', { timeout: 5000 });
    if (square) await page.keyboard.press('q');
    await page.keyboard.press(key);
    await page.waitForSelector('[data-testid=scene-score-prompt]', { state: 'detached', timeout: 5000 });
  };
  await draw(-60, -30, 20, '4', true);
  await draw(60, 30, 15, '3', false);
  for (let i = 0; i < 50 && (await shapes()) < 2; i++) await page.waitForTimeout(200);
  check('two buildings listed', await shapes(), 2);
  check('scene shows draft', await page.getByTestId('scene-outcome').innerText(), 'Draft');

  await open(page);
  check('same scene after reload', await scene(), firstScene);
  check('two buildings after reload', await shapes(), 2);
  const confidences = await page.locator('[data-testid=scene-shape]').evaluateAll((els) => els.map((e) => e.dataset.confidence));
  // Both saves run in the background, so either may get the lower id.
  check('scores persisted', [...confidences].sort(), ['3', '4']);

  // Edit: re-score the second building.
  await page.locator('[data-testid=scene-shape]').nth(1).locator('[data-testid=scene-shape-confidence][data-level="5"]').click();
  for (let i = 0; i < 30; i++) {
    const c = await page.locator('[data-testid=scene-shape]').nth(1).getAttribute('data-confidence');
    if (c === '5') break;
    await page.waitForTimeout(200);
  }
  check('edited score', await page.locator('[data-testid=scene-shape]').nth(1).getAttribute('data-confidence'), '5');

  // Delete the first.
  await page.locator('[data-testid=scene-shape]').first().getByTestId('scene-shape-delete').click();
  for (let i = 0; i < 30 && (await shapes()) !== 1; i++) await page.waitForTimeout(200);
  check('one building left', await shapes(), 1);

  await page.getByTestId('scene-complete').click();
  for (let i = 0; i < 30 && (await scene()) === firstScene; i++) await page.waitForTimeout(200);
  const second = await scene();
  check('moved to another scene', second !== firstScene, true);
  check('mark empty enabled', await page.getByTestId('scene-empty').isDisabled(), false);
  await page.getByTestId('scene-empty').click();
  for (let i = 0; i < 30 && (await scene()) === second; i++) await page.waitForTimeout(200);
  await page.waitForTimeout(1500);
  const prog = await page.getByTestId('scene-progress').evaluate((e) => ({ ...e.dataset }));
  const total = (await api(`/campaigns/${CID}/buildings/progress`)).scenes.length;
  check('panel counts', [prog.complete, prog.empty, prog.todo], ['1', '1', String(total - 2)]);
  await page.screenshot({ path: path.join(__dirname, '..', 'data', `building_live_check_${CID}.png`) });

  const server = await api(`/campaigns/${CID}/buildings/progress`);
  check('server states', server.scenes.map((s) => s.state), (() => {
    const st = server.scenes.map(() => 'todo');
    st[server.scenes.findIndex((s) => String(s.task_id) === firstScene)] = 'complete';
    st[server.scenes.findIndex((s) => String(s.task_id) === second)] = 'empty';
    return st;
  })());
  const fc = await api(`/campaigns/${CID}/export-annotations-geojson`);
  const buildings = fc.features.filter((f) => f.properties.stacnotator_origin_task_id != null);
  check('export: one building in the first scene', buildings.map((f) => [String(f.properties.stacnotator_origin_task_id), f.properties.stacnotator_confidence, f.geometry.type]), [[firstScene, 5, 'Polygon']]);
  check('export: no references', fc.features.filter((f) => f.properties.stacnotator_source_id != null).length, 0);

  // Back to the completed scene and take its outcome off: it becomes a draft again.
  await page.getByTestId('scene-cycle-all').click();
  for (let i = 0; i < 10 && (await scene()) !== firstScene; i++) {
    await page.keyboard.press('w');
    await page.waitForTimeout(400);
  }
  await page.getByTestId('scene-draft').click();
  for (let i = 0; i < 30 && (await page.getByTestId('scene-outcome').textContent()) !== 'Draft'; i++) {
    await page.waitForTimeout(200);
  }
  check('draft clears the outcome', await page.getByTestId('scene-outcome').textContent(), 'Draft');
  const afterDraft = await api(`/campaigns/${CID}/buildings/progress`);
  check('server sees the draft', afterDraft.scenes.find((s) => String(s.task_id) === firstScene)?.state, 'draft');

  // Finish every scene, hand in: the scenes lock until the hand-in is withdrawn.
  await page.getByTestId('scene-cycle-remaining').click();
  await page.getByTestId('scene-complete').click();
  for (let i = 0; i < 30 && (await scene()) === firstScene; i++) await page.waitForTimeout(200);
  for (let i = 0; i < total - 2; i++) {
    const at = await scene();
    await page.getByTestId('scene-empty').click();
    for (let j = 0; j < 30 && (await scene()) === at && !(await page.getByTestId('scene-all-done').count()); j++) {
      await page.waitForTimeout(200);
    }
  }
  await page.getByTestId('scene-hand-in').click();
  await page.waitForSelector('[data-testid=scene-handed-in]', { timeout: 10000 });
  check('locked notice', await page.getByTestId('scene-locked').count(), 1);
  check('locked controls', [
    await page.getByTestId('scene-skip').isDisabled(),
    await page.getByTestId('scene-tool-draw').isDisabled(),
  ], [true, true]);
  await page.getByTestId('scene-withdraw').click();
  await page.waitForSelector('[data-testid=scene-handed-in]', { state: 'detached', timeout: 10000 });
  check('unlocked after withdraw', [
    await page.getByTestId('scene-locked').count(),
    await page.getByTestId('scene-skip').isDisabled(),
  ], [0, false]);
  check('no page errors', errors, []);
  console.log(`${checks.filter(Boolean).length}/${checks.length} passed`);
  await browser.close();
  process.exit(checks.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
