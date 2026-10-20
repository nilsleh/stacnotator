import { expect, test } from '@playwright/test';
import { MOCK_CAMPAIGN, MOCK_USER } from './fixtures/mock-data';

test('scene-fit renders carry precise transforms, fresh edits and recoverable tile status', async ({
  page,
}) => {
  await page.exposeFunction('stacnotatorToken', () => 'stac-agent-render-test');
  const campaign = {
    ...MOCK_CAMPAIGN,
    time_series: [],
    basemaps: [],
    settings: {
      ...MOCK_CAMPAIGN.settings,
      building_scenes: { building_label_id: 1, complete_label_id: 2, empty_label_id: 3 },
    },
  };
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/detailed')) {
        expect(route.request().headers().authorization).toBe('Bearer stac-agent-render-test');
        await route.fulfill({ json: campaign });
      } else if (path.endsWith('/me')) {
        await route.fulfill({ json: MOCK_USER });
      } else if (path.endsWith('/tiler-token')) {
        await route.fulfill({ json: { expires_in: 60 } });
      } else {
        await route.fulfill({ status: 404, json: { detail: 'Unmocked API request' } });
      }
    }
  );
  const tile = await page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 256;
    const ctx = canvas.getContext('2d')!;
    ctx.fillStyle = '#657957';
    ctx.fillRect(0, 0, 256, 256);
    return canvas.toDataURL().split(',')[1];
  });
  let failTiles = false;
  await page.route('https://tiles.example.com/**', async (route) => {
    await route.fulfill({
      status: failTiles ? 404 : 200,
      contentType: 'image/png',
      headers: { 'Access-Control-Allow-Origin': '*' },
      body: failTiles ? '' : Buffer.from(tile, 'base64'),
    });
  });
  await page.goto('/agent-render?campaign=42');
  await expect(page.getByText('Agent render page - ready')).toBeVisible();

  const render = (cellPx: number, edited = false) =>
    page.evaluate(
      async ({ cellPx, edited }) => {
        const task = {
          task_id: 7,
          lat: 0.0005,
          lon: 0.0005,
          geometry_wkt: 'POLYGON((0 0,0.001 0,0.001 0.001,0 0.001,0 0))',
          buildings: [
            {
              building_id: 9,
              confidence: 4,
              geometry_wkt: edited
                ? 'POLYGON((0.0006 0.0006,0.0008 0.0006,0.0008 0.0008,0.0006 0.0006))'
                : 'POLYGON((0.0002 0.0002,0.0004 0.0002,0.0004 0.0004,0.0002 0.0002))',
            },
          ],
        };
        const result = await window.stacnotatorAgent!.render(
          task,
          [{ cells: [{ slice_id: 101 }], columns: 1, cell_px: cellPx, fit_scene: true }],
          { max_edge_px: 512, max_megapixels: 1 }
        );
        const image = result[0];
        if (!('image_base64' in image)) throw new Error(image.error);
        return image;
      },
      { cellPx, edited }
    );

  const first = await render(512);
  expect(first.meta.status).toBe('ready');
  const cells = first.meta.cells as {
    rect: number[];
    pixel_to_map: number[];
    status: string;
    projection: string;
    coverage: { missing_pixels: number };
  }[];
  const cell = cells[0];
  expect(cell.projection).toBe('EPSG:3857');
  expect(cell.rect[1]).toBeGreaterThan(0);
  expect(cell.coverage.missing_pixels).toBe(0);
  for (const [lon, lat] of [
    [0, 0],
    [0.001, 0.001],
  ]) {
    const x = ((lon * Math.PI) / 180) * 6378137;
    const y = Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) * 6378137;
    const [a, , c, , e, f] = cell.pixel_to_map;
    expect((x - c) / a).toBeGreaterThanOrEqual(cell.rect[0]);
    expect((x - c) / a).toBeLessThanOrEqual(cell.rect[0] + cell.rect[2]);
    expect((y - f) / e).toBeGreaterThanOrEqual(cell.rect[1]);
    expect((y - f) / e).toBeLessThanOrEqual(cell.rect[1] + cell.rect[3]);
  }
  expect((await render(512, true)).image_base64).not.toBe(first.image_base64);
  failTiles = true;
  expect((await render(384)).meta.status).toBe('partial');
  failTiles = false;
  expect((await render(384)).meta.status).toBe('ready');
});
