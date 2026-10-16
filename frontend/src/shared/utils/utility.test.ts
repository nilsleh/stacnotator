import { describe, expect, it } from 'vitest';

import {
  formatDuration,
  formatSliceLabel,
  formatWindowLabel,
  parseEmailList,
  orthogonalizePolygonRing,
  searchUsers,
  squareBuilding,
} from './utility';

describe('formatDuration', () => {
  it('reads an unmeasured duration as a dash rather than zero', () => {
    expect(formatDuration(null)).toBe('-');
    expect(formatDuration(undefined)).toBe('-');
  });

  it('shows seconds under a minute', () => {
    expect(formatDuration(0)).toBe('0s');
    expect(formatDuration(45)).toBe('45s');
  });

  it('shows minutes and seconds under an hour', () => {
    expect(formatDuration(200)).toBe('3m 20s');
    expect(formatDuration(180)).toBe('3m');
  });

  it('drops seconds once past an hour', () => {
    expect(formatDuration(4320)).toBe('1h 12m');
    expect(formatDuration(3600)).toBe('1h');
  });
});

describe('formatSliceLabel', () => {
  it('labels a weekly slice with its inclusive start and end days', () => {
    expect(formatSliceLabel('2026-05-01', '2026-05-07', 'weeks', 0)).toBe('May 1-7');
  });

  it('labels a weekly slice spanning a month boundary', () => {
    expect(formatSliceLabel('2026-05-29', '2026-06-04', 'weeks', 0)).toBe('May 29 - Jun 4');
  });

  it('labels a monthly slice by its month', () => {
    expect(formatSliceLabel('2026-05-01', '2026-05-31', 'months', 0)).toBe('May');
  });

  it('labels a single-day slice', () => {
    expect(formatSliceLabel('2026-05-01', '2026-05-01', 'days', 0)).toBe('May 1');
  });

  it('accepts the compact YYYYMMDD form', () => {
    expect(formatSliceLabel('20260501', '20260507', 'weeks', 0)).toBe('May 1-7');
  });
});

describe('formatWindowLabel', () => {
  it('labels a monthly window by month and year', () => {
    expect(formatWindowLabel('2026-05-01', '2026-05-31', 'months')).toBe('May 2026');
  });

  it('labels a weekly window with its inclusive day range', () => {
    expect(formatWindowLabel('2026-05-01', '2026-05-07', 'weeks')).toBe('May 1-7, 2026');
  });

  it('labels a window spanning several months of one year', () => {
    expect(formatWindowLabel('2026-05-01', '2026-07-31', 'months')).toBe('May-Jul 2026');
  });
});

describe('searchUsers', () => {
  const user = (display_name: string, email: string) => ({ display_name, email });

  it('ranks prefix matches before substring matches', () => {
    const users = [user('jwagner', 'jwagner@example.com'), user('wagnerj', 'wagnerj@example.com')];

    const result = searchUsers(users, (u) => u, 'wag');

    expect(result.map((u) => u.display_name)).toEqual(['wagnerj', 'jwagner']);
  });

  it('ranks word-prefix matches in the name between full-prefix and substring matches', () => {
    const users = [
      user('awagstaff', 'awagstaff@example.com'),
      user('Jonas Wagner', 'jwagner@example.com'),
      user('wagnerj', 'wagnerj@example.com'),
    ];

    const result = searchUsers(users, (u) => u, 'wag');

    expect(result.map((u) => u.display_name)).toEqual(['wagnerj', 'Jonas Wagner', 'awagstaff']);
  });

  it('keeps the incoming order within a rank tier', () => {
    const users = [user('wagner-a', 'a@example.com'), user('wagner-b', 'b@example.com')];

    const result = searchUsers(users, (u) => u, 'wagner');

    expect(result.map((u) => u.display_name)).toEqual(['wagner-a', 'wagner-b']);
  });

  it('matches case-insensitively against name and email', () => {
    const users = [user('Alice', 'alice@example.com'), user('Bob', 'wagner@example.com')];

    expect(searchUsers(users, (u) => u, 'WAG').map((u) => u.display_name)).toEqual(['Bob']);
  });

  it('drops non-matching users', () => {
    const users = [user('Alice', 'alice@example.com')];

    expect(searchUsers(users, (u) => u, 'zzz')).toEqual([]);
  });

  it('returns everyone unchanged for an empty or whitespace query', () => {
    const users = [user('Bob', 'bob@example.com'), user('Alice', 'alice@example.com')];

    expect(searchUsers(users, (u) => u, '')).toEqual(users);
    expect(searchUsers(users, (u) => u, '   ')).toEqual(users);
  });

  it('reads the user through the accessor', () => {
    const items = [
      { user: user('jwagner', 'jwagner@example.com') },
      { user: user('wagnerj', 'wagnerj@example.com') },
    ];

    const result = searchUsers(items, (item) => item.user, 'wag');

    expect(result.map((item) => item.user.display_name)).toEqual(['wagnerj', 'jwagner']);
  });
});

describe('parseEmailList', () => {
  it('splits on commas, semicolons, and whitespace', () => {
    expect(parseEmailList('a@x.io, b@x.io;c@x.io\nd@x.io').emails).toEqual([
      'a@x.io',
      'b@x.io',
      'c@x.io',
      'd@x.io',
    ]);
  });

  it('dedupes case-insensitively, keeping the first spelling', () => {
    expect(parseEmailList('A@x.io a@x.io').emails).toEqual(['A@x.io']);
  });

  it('separates entries that cannot be an address', () => {
    const { emails, invalid } = parseEmailList('ok@x.io nope, also@bad');
    expect(emails).toEqual(['ok@x.io']);
    expect(invalid).toEqual(['nope', 'also@bad']);
  });

  it('ignores empty input', () => {
    expect(parseEmailList('  \n ')).toEqual({ emails: [], invalid: [] });
  });
});

/** |cos| of every corner angle, in local metres-ish coordinates. */
const cornerCosines = (closedRing: [number, number][]) => {
  const ring = closedRing.slice(0, -1);
  const cosLat = Math.cos(((ring.reduce((sum, p) => sum + p[1], 0) / ring.length) * Math.PI) / 180);
  return ring.map((p, i) => {
    const prev = ring[(i + ring.length - 1) % ring.length];
    const next = ring[(i + 1) % ring.length];
    const a = [(prev[0] - p[0]) * cosLat, prev[1] - p[1]];
    const b = [(next[0] - p[0]) * cosLat, next[1] - p[1]];
    return Math.abs(
      (a[0] * b[0] + a[1] * b[1]) / (Math.hypot(a[0], a[1]) * Math.hypot(b[0], b[1]))
    );
  });
};

describe('orthogonalizePolygonRing', () => {
  it('squares a skewed quadrilateral and keeps the ring closed', () => {
    const ring = orthogonalizePolygonRing([
      [10, 50],
      [10.0011, 50.00002],
      [10.00108, 50.0007],
      [9.99998, 50.00068],
      [10, 50],
    ]);
    expect(ring).toHaveLength(5);
    expect(ring[0]).toEqual(ring[4]);
    for (const cosine of cornerCosines(ring)) expect(cosine).toBeLessThan(1e-6);
  });

  it('makes every corner perpendicular or collinear on a multi-vertex footprint', () => {
    const ring = orthogonalizePolygonRing([
      [0, 0],
      [0.0003, 0.00005],
      [0.0007, -0.00004],
      [0.001, 0],
      [0.001, 0.001],
      [0, 0.001],
      [0, 0],
    ]);
    for (const cosine of cornerCosines(ring)) {
      expect(Math.min(cosine, 1 - cosine)).toBeLessThan(1e-6);
    }
  });

  it('leaves degenerate rings alone', () => {
    const line: [number, number][] = [
      [0, 0],
      [1, 1],
    ];
    expect(orthogonalizePolygonRing(line)).toBe(line);
  });
});

describe('squareBuilding', () => {
  it('squares polygons and passes other geometry through', () => {
    const point: GeoJSON.Geometry = { type: 'Point', coordinates: [1, 2] };
    expect(squareBuilding(point)).toBe(point);
    const squared = squareBuilding({
      type: 'Polygon',
      coordinates: [
        [
          [10, 50],
          [10.0011, 50.00002],
          [10.00108, 50.0007],
          [9.99998, 50.00068],
          [10, 50],
        ],
      ],
    });
    expect(squared.type).toBe('Polygon');
    const ring = (squared as GeoJSON.Polygon).coordinates[0] as [number, number][];
    for (const cosine of cornerCosines(ring)) expect(cosine).toBeLessThan(1e-6);
  });
});
