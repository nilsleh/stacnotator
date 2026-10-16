// TODO: should refactor at some point into semantically meaningfull files. Currently just a dump

export interface LatLon {
  lat: number;
  lon: number;
}

const MONTH_ABBREV = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/**
 * Capitalize the first letter of a string
 * @param str - The string to capitalize
 * @returns The string with the first letter capitalized
 * @example capitalizeFirst('hello') // Returns 'Hello'
 */
export const capitalizeFirst = (str: string): string => {
  if (!str) return str;
  return str.charAt(0).toUpperCase() + str.slice(1);
};

type SearchableUser = { display_name?: string | null; email?: string | null };

const userMatchRank = (user: SearchableUser, query: string): number => {
  const name = (user.display_name ?? '').toLowerCase();
  const email = (user.email ?? '').toLowerCase();
  if (name.startsWith(query) || email.startsWith(query)) return 0;
  if (name.split(/\s+/).some((word) => word.startsWith(query))) return 1;
  if (name.includes(query) || email.includes(query)) return 2;
  return -1;
};

/**
 * Case-insensitive user search over display name and, where the viewer is
 * given it, email. Results are ranked: full prefix matches first, then
 * name-word prefixes, then substring matches, preserving the incoming order
 * within each tier. An empty query returns all items unchanged.
 */
export const searchUsers = <T>(
  items: T[],
  getUser: (item: T) => SearchableUser,
  rawQuery: string
): T[] => {
  const query = rawQuery.trim().toLowerCase();
  if (!query) return items;
  return items
    .map((item) => ({ item, rank: userMatchRank(getUser(item), query) }))
    .filter(({ rank }) => rank >= 0)
    .sort((a, b) => a.rank - b.rank)
    .map(({ item }) => item);
};

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface ParsedEmails {
  emails: string[];
  invalid: string[];
}

/** Splits pasted text on commas/semicolons/whitespace, dedupes
 *  case-insensitively and separates entries that cannot be an address. */
export const parseEmailList = (raw: string): ParsedEmails => {
  const seen = new Set<string>();
  const emails: string[] = [];
  const invalid: string[] = [];

  for (const token of raw.split(/[\s,;]+/)) {
    if (!token) continue;
    const key = token.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    if (EMAIL_PATTERN.test(token)) emails.push(token);
    else invalid.push(token);
  }

  return { emails, invalid };
};

/**
 * Extract latitude and longitude from a WKT POINT string
 * Supports both 2D and 3D (POINT Z) formats
 * @param wkt - Well-Known Text string in POINT format
 * @returns Object with lat and lon properties, or null if parsing fails
 * @example
 * extractLatLonFromWKT('POINT(-122.4194 37.7749)')
 * // Returns { lat: 37.7749, lon: -122.4194 }
 */
export const extractLatLonFromWKT = (wkt: string): LatLon | null => {
  if (!wkt) return null;

  const normalized = wkt.trim().toUpperCase();
  const match = normalized.match(/^POINT(?:\s+Z)?\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)/);

  if (!match) return null;

  const lon = Number(match[1]);
  const lat = Number(match[2]);

  if (Number.isNaN(lat) || Number.isNaN(lon)) {
    return null;
  }

  return { lat, lon };
};

/**
 * Parse a date string (YYYYMMDD or YYYY-MM-DD format).
 *
 * Builds a local-midnight date so the local getters callers use to format it read
 * back the same calendar day. `new Date('2026-05-01')` would parse as UTC midnight
 * and read back as Apr 30 anywhere west of UTC.
 */
const parseDate = (dateStr: string): Date => {
  const match = /^(\d{4})-?(\d{2})-?(\d{2})/.exec(dateStr);
  if (match) {
    const [, year, month, day] = match;
    return new Date(Number(year), Number(month) - 1, Number(day));
  }
  return new Date(dateStr);
};

/**
 * Format a slice label. `endDate` is inclusive, as stored.
 */
export const formatSliceLabel = (
  startDate: string,
  endDate: string,
  slicingUnit: string | null,
  _sliceIndex: number
): string => {
  const start = parseDate(startDate);
  const end = parseDate(endDate);

  const displayEnd = end < start ? start : end;

  const startMonth = start.getMonth();
  const endMonth = displayEnd.getMonth();
  const startDay = start.getDate();
  const endDay = displayEnd.getDate();
  const startYear = start.getFullYear();
  const endYear = displayEnd.getFullYear();

  const unit = slicingUnit?.toLowerCase() || 'days';

  switch (unit) {
    case 'month':
    case 'months':
      if (startMonth === endMonth && startYear === endYear) {
        return MONTH_ABBREV[startMonth];
      }
      if (startYear === endYear) {
        return `${MONTH_ABBREV[startMonth]}-${MONTH_ABBREV[endMonth]}`;
      }
      return `${MONTH_ABBREV[startMonth]} '${String(startYear).slice(2)}`;

    case 'week':
    case 'weeks':
      if (startMonth === endMonth) {
        return `${MONTH_ABBREV[startMonth]} ${startDay}-${endDay}`;
      }
      return `${MONTH_ABBREV[startMonth]} ${startDay} - ${MONTH_ABBREV[endMonth]} ${endDay}`;

    case 'day':
    case 'days':
    default:
      if (startMonth === endMonth && startDay === endDay) {
        return `${MONTH_ABBREV[startMonth]} ${startDay}`;
      }
      if (startMonth === endMonth) {
        return `${MONTH_ABBREV[startMonth]} ${startDay}-${endDay}`;
      }
      return `${MONTH_ABBREV[startMonth]} ${startDay} - ${MONTH_ABBREV[endMonth]} ${endDay}`;
  }
};

/**
 * Format a window label. `endDate` is inclusive, as stored.
 */
export const formatWindowLabel = (
  startDate: string,
  endDate: string,
  windowUnit: string | null
): string => {
  const start = parseDate(startDate);
  const end = parseDate(endDate);

  const displayEnd = end < start ? start : end;

  const startMonth = start.getMonth();
  const endMonth = displayEnd.getMonth();
  const startDay = start.getDate();
  const endDay = displayEnd.getDate();
  const startYear = start.getFullYear();
  const endYear = displayEnd.getFullYear();

  const unit = windowUnit?.toLowerCase() || 'months';

  switch (unit) {
    case 'month':
    case 'months':
      if (startMonth === endMonth && startYear === endYear) {
        return `${MONTH_ABBREV[startMonth]} ${startYear}`;
      }
      if (startYear === endYear) {
        return `${MONTH_ABBREV[startMonth]}-${MONTH_ABBREV[endMonth]} ${startYear}`;
      }
      return `${MONTH_ABBREV[startMonth]} ${startYear} - ${MONTH_ABBREV[endMonth]} ${endYear}`;

    case 'week':
    case 'weeks':
    case 'day':
    case 'days':
    default:
      if (startYear === endYear) {
        if (startMonth === endMonth) {
          if (startDay === endDay) {
            return `${MONTH_ABBREV[startMonth]} ${startDay}, ${startYear}`;
          }
          return `${MONTH_ABBREV[startMonth]} ${startDay}-${endDay}, ${startYear}`;
        }
        return `${MONTH_ABBREV[startMonth]} ${startDay} - ${MONTH_ABBREV[endMonth]} ${endDay}, ${startYear}`;
      }
      return `${MONTH_ABBREV[startMonth]} ${startDay}, ${startYear} - ${MONTH_ABBREV[endMonth]} ${endDay}, ${endYear}`;
  }
};

/**
 * Convert YYYYMM to YYYY-MM
 */
export const yyyymmToInputMonth = (yyyymm: string): string => {
  if (!yyyymm || yyyymm.length !== 6) return '';

  const year = yyyymm.substring(0, 4);
  const month = yyyymm.substring(4, 6);

  return `${year}-${month}`;
};

/**
 * Convert YYYY-MM to YYYYMM
 */
export const inputMonthToYYYYMM = (inputMonth: string): string => {
  if (!inputMonth) return '';
  return inputMonth.replace('-', '');
};

/**
 * Extract the centroid (lat/lon) from any WKT geometry string.
 * Supports POINT, LINESTRING, and POLYGON.
 * For POINT: returns the point itself.
 * For LINESTRING: returns the average of all vertices.
 * For POLYGON: returns the average of the exterior ring vertices.
 *
 * @param wkt - Well-Known Text geometry string
 * @returns Object with lat and lon properties, or null if parsing fails
 */
export const extractCentroidFromWKT = (wkt: string): LatLon | null => {
  if (!wkt) return null;
  const normalized = wkt.trim().toUpperCase();

  // POINT
  const pointMatch = normalized.match(
    /^POINT(?:\s+Z)?\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)/
  );
  if (pointMatch) {
    return { lon: parseFloat(pointMatch[1]), lat: parseFloat(pointMatch[2]) };
  }

  // LINESTRING - average of all vertices
  const lineMatch = normalized.match(/^LINESTRING\s*\((.+)\)$/);
  if (lineMatch) {
    const pairs = lineMatch[1].split(',').map((p) => p.trim().split(/\s+/).map(Number));
    if (pairs.length === 0) return null;
    const sumLon = pairs.reduce((s, p) => s + p[0], 0);
    const sumLat = pairs.reduce((s, p) => s + p[1], 0);
    return { lon: sumLon / pairs.length, lat: sumLat / pairs.length };
  }

  // POLYGON - average of exterior ring vertices (skip closing vertex)
  const polyMatch = normalized.match(/^POLYGON\s*\(\((.+?)\)/);
  if (polyMatch) {
    const pairs = polyMatch[1].split(',').map((p) => p.trim().split(/\s+/).map(Number));
    // Drop last vertex if it duplicates the first (closed ring)
    const ring =
      pairs.length > 1 &&
      pairs[0][0] === pairs[pairs.length - 1][0] &&
      pairs[0][1] === pairs[pairs.length - 1][1]
        ? pairs.slice(0, -1)
        : pairs;
    if (ring.length === 0) return null;
    const sumLon = ring.reduce((s, p) => s + p[0], 0);
    const sumLat = ring.reduce((s, p) => s + p[1], 0);
    return { lon: sumLon / ring.length, lat: sumLat / ring.length };
  }

  // MULTIPOLYGON - centroid of the first polygon's exterior ring
  const multiPolyMatch = normalized.match(/^MULTIPOLYGON\s*\(\(\((.+?)\)/);
  if (multiPolyMatch) {
    const pairs = multiPolyMatch[1].split(',').map((p) => p.trim().split(/\s+/).map(Number));
    const ring =
      pairs.length > 1 &&
      pairs[0][0] === pairs[pairs.length - 1][0] &&
      pairs[0][1] === pairs[pairs.length - 1][1]
        ? pairs.slice(0, -1)
        : pairs;
    if (ring.length === 0) return null;
    const sumLon = ring.reduce((s, p) => s + p[0], 0);
    const sumLat = ring.reduce((s, p) => s + p[1], 0);
    return { lon: sumLon / ring.length, lat: sumLat / ring.length };
  }

  // Fallback: try extractLatLonFromWKT for anything else
  return extractLatLonFromWKT(wkt);
};

/** Compact duration for the annotation-time columns: "45s", "3m 20s", "1h 12m".
 *  Null means never measured, which reads as an em-free dash rather than 0. */
export const formatDuration = (seconds: number | null | undefined): string => {
  if (seconds == null) return '-';
  if (seconds < 60) return `${Math.round(seconds)}s`;

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    const rest = Math.round(seconds % 60);
    return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  }

  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes ? `${hours}h ${restMinutes}m` : `${hours}h`;
};

/**
 * Orthogonalize a polygon ring so building corners are squared to 90-degree
 * angles along the dominant edge orientation.
 */
export const orthogonalizePolygonRing = (coords: [number, number][]): [number, number][] => {
  if (!coords || coords.length < 3) return coords;

  const isClosed =
    coords.length > 1 &&
    Math.abs(coords[0][0] - coords[coords.length - 1][0]) < 1e-12 &&
    Math.abs(coords[0][1] - coords[coords.length - 1][1]) < 1e-12;
  const pts = isClosed ? coords.slice(0, -1) : [...coords];
  const n = pts.length;
  if (n < 3) return coords;

  const cx = pts.reduce((s, p) => s + p[0], 0) / n;
  const cy = pts.reduce((s, p) => s + p[1], 0) / n;
  const cosLat = Math.cos((cy * Math.PI) / 180) || 1;

  // Project into local isotropic planar coordinates around centroid
  const localPts = pts.map(([lon, lat]) => ({
    x: (lon - cx) * cosLat,
    y: lat - cy,
  }));

  // Compute length-weighted dominant orientation modulo pi/2
  let sumCos4 = 0;
  let sumSin4 = 0;
  for (let i = 0; i < n; i++) {
    const p0 = localPts[i];
    const p1 = localPts[(i + 1) % n];
    const dx = p1.x - p0.x;
    const dy = p1.y - p0.y;
    const len = Math.hypot(dx, dy);
    if (len > 1e-12) {
      const angle = Math.atan2(dy, dx);
      sumCos4 += len * Math.cos(4 * angle);
      sumSin4 += len * Math.sin(4 * angle);
    }
  }
  const dominantAngle = 0.25 * Math.atan2(sumSin4, sumCos4);
  const cosA = Math.cos(-dominantAngle);
  const sinA = Math.sin(-dominantAngle);

  // Rotate into dominant-axis frame (u, v)
  const rotated = localPts.map(({ x, y }) => ({
    u: x * cosA - y * sinA,
    v: x * sinA + y * cosA,
  }));

  // Classify each edge as horizontal ('H', constant v) or vertical ('V', constant u)
  const edges = rotated.map((p0, i) => {
    const p1 = rotated[(i + 1) % n];
    const du = p1.u - p0.u;
    const dv = p1.v - p0.v;
    const isHorizontal = Math.abs(du) >= Math.abs(dv);
    return {
      axis: isHorizontal ? ('H' as const) : ('V' as const),
      uMid: 0.5 * (p0.u + p1.u),
      vMid: 0.5 * (p0.v + p1.v),
    };
  });

  const strictlyAlternating = edges.every((e, i) => e.axis !== edges[(i + 1) % n].axis);

  let orthoUV: { u: number; v: number }[];

  if (strictlyAlternating) {
    orthoUV = rotated.map((_, i) => {
      const prevEdge = edges[(i - 1 + n) % n];
      const nextEdge = edges[i];
      const u = prevEdge.axis === 'V' ? prevEdge.uMid : nextEdge.uMid;
      const v = prevEdge.axis === 'H' ? prevEdge.vMid : nextEdge.vMid;
      return { u, v };
    });
  } else if (n === 4) {
    // Oriented minimum bounding rectangle preserving winding & start corner
    const minU = Math.min(...rotated.map((p) => p.u));
    const maxU = Math.max(...rotated.map((p) => p.u));
    const minV = Math.min(...rotated.map((p) => p.v));
    const maxV = Math.max(...rotated.map((p) => p.v));

    let signedArea = 0;
    for (let i = 0; i < 4; i++) {
      const p0 = rotated[i];
      const p1 = rotated[(i + 1) % 4];
      signedArea += p0.u * p1.v - p1.u * p0.v;
    }
    const ccwRect = [
      { u: minU, v: minV },
      { u: maxU, v: minV },
      { u: maxU, v: maxV },
      { u: minU, v: maxV },
    ];
    const rect = signedArea >= 0 ? ccwRect : [...ccwRect].reverse();

    // Rotate corner array so index 0 is closest to rotated[0]
    let bestIdx = 0;
    let bestDist = Infinity;
    for (let i = 0; i < 4; i++) {
      const d = Math.hypot(rect[i].u - rotated[0].u, rect[i].v - rotated[0].v);
      if (d < bestDist) {
        bestDist = d;
        bestIdx = i;
      }
    }
    orthoUV = [0, 1, 2, 3].map((k) => rect[(bestIdx + k) % 4]);
  } else {
    orthoUV = rotated.map((point) => ({ ...point }));
    // Consecutive same-axis edges must share one coordinate, including at ring closure.
    for (const axis of ['H', 'V'] as const) {
      const coordinate = axis === 'H' ? 'v' : 'u';
      const visited = new Set<number>();
      for (let start = 0; start < n; start++) {
        if (visited.has(start)) continue;
        const component: number[] = [];
        const pending = [start];
        while (pending.length) {
          const index = pending.pop()!;
          if (visited.has(index)) continue;
          visited.add(index);
          component.push(index);
          const previous = (index - 1 + n) % n;
          if (edges[index].axis === axis) pending.push((index + 1) % n);
          if (edges[previous].axis === axis) pending.push(previous);
        }
        const average =
          component.reduce((sum, index) => sum + rotated[index][coordinate], 0) / component.length;
        for (const index of component) orthoUV[index][coordinate] = average;
      }
    }
  }

  // Rotate back by +dominantAngle and convert to WGS84 [lon, lat]
  const cosB = Math.cos(dominantAngle);
  const sinB = Math.sin(dominantAngle);
  const outRing: [number, number][] = orthoUV.map(({ u, v }) => {
    const x = u * cosB - v * sinB;
    const y = u * sinB + v * cosB;
    return [cx + x / cosLat, cy + y];
  });

  outRing.push([outRing[0][0], outRing[0][1]]);
  return outRing;
};

/** A drawn building with its corners squared; any other geometry unchanged. */
export const squareBuilding = (geometry: GeoJSON.Geometry): GeoJSON.Geometry =>
  geometry.type === 'Polygon' && geometry.coordinates.length > 0
    ? {
        type: 'Polygon',
        coordinates: [
          orthogonalizePolygonRing(geometry.coordinates[0] as [number, number][]),
          ...geometry.coordinates.slice(1),
        ],
      }
    : geometry;
