/* City-bus mission: the city, its traffic and the line the driver must run.

   A grid town of 6 x 6 junctions on a gentle hill. Four-lane streets with
   rounded corners, signals at every junction with three or more arms, cars
   that drive on the right, keep their distance, stop at red and turn at
   junctions. A bus line is a fixed sequence of streets with stops; the
   mission is to serve every stop in order -- stop at the kerb, open the
   doors, let people off and on, close the doors -- and reach the terminal.

   Pure logic, no three.js: city3d.js draws it, drive.js runs it, and node can
   test it. Coordinates: x east, z south, metres; heading psi = atan2(dz, dx),
   so the right-hand side of a heading is (-sin psi, cos psi). */

export const B = 130;                 // junction spacing
export const N = 6;                   // junctions per side
export const H = 7.5;                 // half carriageway: two 3.5 m lanes each way
export const WALK = 4.5;              // pavement width
export const R = 9;                   // kerb radius at the corners
export const LANE_IN = 1.9, LANE_OUT = 5.4;
export const STOPLINE = 13.5;         // stop line distance from the junction centre
export const SPAN = (N - 1) * B;      // 650 m
export const DIRS = [[1, 0], [0, 1], [-1, 0], [0, -1]];          // E, S, W, N
export const DIR_NAMES = ["doğu", "güney", "batı", "kuzey"];
const CYCLE = 44;                     // signal cycle [s]

export const ROW_NAMES = ["Sahil Yolu", "Cumhuriyet Caddesi", "Atatürk Bulvarı", "İstiklal Caddesi", "Çarşı Sokağı", "Okul Sokağı"];
export const COL_NAMES = ["Liman Caddesi", "Fırın Sokağı", "Belediye Caddesi", "Gazi Bulvarı", "Pazar Caddesi", "Lale Sokağı"];

/* Bus lines: turning points on the grid; the legs between are filled in. */
export const LINES = {
  "19K": { code: "19K", name: "Liman – Okul Terminali", via: [[0, 1], [3, 1], [3, 3], [1, 3], [1, 5], [5, 5]],
           stops: ["Liman", "Cumhuriyet Meydanı", "Gazi Hastanesi", "Kültür Merkezi", "Fırın Sokağı", "Lise", "Spor Salonu", "Okul Terminali"] },
  "42": { code: "42", name: "Lale – Çarşı", via: [[5, 0], [5, 2], [2, 2], [2, 4], [0, 4]],
          stops: ["Lale Sokağı", "Atatürk Parkı", "Belediye", "Pazar Yeri", "Eski Çarşı"] },
};

const SURF = { "h:0": "eski_asfalt", "h:2": "yeni_asfalt", "v:1": "parke", "h:4:0": "arnavut", "h:4:1": "arnavut" };

export function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const inRange = (i, j) => i >= 0 && j >= 0 && i < N && j < N;
export const headingOf = d => Math.atan2(DIRS[d][1], DIRS[d][0]);
const rev = d => (d + 2) % 4;
const turnOf = (dIn, dOut) => (dOut - dIn + 4) % 4;      // 0 straight, 1 right, 2 back, 3 left

/* ================================================================ city */
export function buildCity({ hill = 12, surfaces = [], line = "19K", traffic = "orta", seed = 7 } = {}) {
  const factorOf = Object.fromEntries(surfaces.map(s => [s.key, s.factor]));
  const labelOf = Object.fromEntries(surfaces.map(s => [s.key, s.label]));

  // terrain: a hill in the middle of town, tilted a little toward the sea in the north
  const height = (x, z) => {
    const cx = clamp(x / SPAN, -0.2, 1.2), cz = clamp(z / SPAN, -0.2, 1.2);
    return hill * Math.sin(Math.PI * cx) ** 2 * (0.55 + 0.45 * Math.cos(Math.PI * (cz - 0.35))) + hill * 0.25 * cz;
  };
  const gradePct = (x, z, psi) => {
    const c = Math.cos(psi), s = Math.sin(psi);
    return (height(x + c, z + s) - height(x - c, z - s)) / 2 * 100;
  };

  const degree = (i, j) => DIRS.filter(([dx, dz]) => inRange(i + dx, j + dz)).length;
  const signalised = (i, j) => degree(i, j) >= 3;
  const offset = (i, j) => (i * 13 + j * 7) % CYCLE;
  /* light for traffic moving along axis "h" (east-west) or "v" (north-south) */
  const light = (i, j, axis, t) => {
    if (!signalised(i, j)) return "green";
    const ph = ((t + offset(i, j)) % CYCLE + CYCLE) % CYCLE;
    if (axis === "h") return ph < 18 ? "green" : ph < 21 ? "amber" : "red";
    return ph >= 22 && ph < 40 ? "green" : ph >= 40 && ph < 43 ? "amber" : "red";
  };

  /* what is under a point: road, pavement, or something solid (a building, the edge of town) */
  function locate(x, z) {
    const i = clamp(Math.round(x / B), 0, N - 1), j = clamp(Math.round(z / B), 0, N - 1);
    const dx = x - i * B, dz = z - j * B, adx = Math.abs(dx), adz = Math.abs(dz);
    const xIn = e => x >= -e && x <= SPAN + e, zIn = e => z >= -e && z <= SPAN + e;
    const onV = adx <= H && zIn(H), onH = adz <= H && xIn(H);
    let road = onV || onH;
    const ax = adx - H, az = adz - H;
    const corner = ax > 0 && az > 0 && ax < R && az < R && inRange(i + Math.sign(dx), j) && inRange(i, j + Math.sign(dz));
    const fromArc = corner ? Math.hypot(R - ax, R - az) : 0;
    if (!road && corner && fromArc >= R) road = true;
    if (road) return { kind: "road", i, j, dx, dz, onV, onH };
    const walk = (adx <= H + WALK && zIn(H + WALK)) || (adz <= H + WALK && xIn(H + WALK)) || (corner && fromArc >= R - WALK);
    return { kind: walk ? "walk" : "solid", i, j, dx, dz };
  }

  const surfaceKey = (x, z) => {
    const L = locate(x, z);
    if (L.kind !== "road" || (L.onV && L.onH)) return "asfalt";
    if (L.onH) { const k = clamp(Math.floor(x / B), 0, N - 2); return SURF[`h:${L.j}:${k}`] || SURF[`h:${L.j}`] || "asfalt"; }
    const k = clamp(Math.floor(z / B), 0, N - 2); return SURF[`v:${L.i}:${k}`] || SURF[`v:${L.i}`] || "asfalt";
  };
  const legSurface = (i, j, d) => {
    const [dx, dz] = DIRS[d], i2 = i + dx, j2 = j + dz;
    if (dz === 0) { const k = Math.min(i, i2); return SURF[`h:${j}:${k}`] || SURF[`h:${j}`] || "asfalt"; }
    const k = Math.min(j, j2); return SURF[`v:${i}:${k}`] || SURF[`v:${i}`] || "asfalt";
  };
  const factor = (x, z) => factorOf[surfaceKey(x, z)] ?? 1;
  const streetName = (i, j, d) => (DIRS[d][1] === 0 ? ROW_NAMES[j] : COL_NAMES[i]);

  /* the bus's footprint must stay off buildings; the pavement is allowed but noted */
  const FOOT = [[6, -1.27], [6, 1.27], [0, -1.27], [0, 1.27], [-6, -1.27], [-6, 1.27]];
  const footprint = (x, z, psi) => {
    const c = Math.cos(psi), s = Math.sin(psi);
    return FOOT.map(([a, b]) => [x + a * c - b * s, z + a * s + b * c]);
  };
  // the wheels decide whether the bus is on the kerb; the body may overhang it, as on a real bus
  const WHEELS = [[3.55, -1.15], [3.55, 1.15], [-2.45, -1.1], [-2.45, 1.1]];
  const wheelsAt = (x, z, psi) => {
    const c = Math.cos(psi), s = Math.sin(psi);
    return WHEELS.map(([a, b]) => [x + a * c - b * s, z + a * s + b * c]);
  };
  function constrain(st, prev, vNew, dt) {
    st.scrape = false; st.atEnd = false; st.kerb = false; st.blocked = false;
    if (footprint(st.s, st.lat, st.psi).some(([px, pz]) => locate(px, pz).kind === "solid")) {
      st.s = prev.s; st.lat = prev.lat; st.psi = prev.psi; st.blocked = Math.abs(vNew) > 0.2;
      return 0;
    }
    if (wheelsAt(st.s, st.lat, st.psi).some(([px, pz]) => locate(px, pz).kind !== "road")) { st.kerb = true; vNew *= 1 - 0.9 * dt; }
    return vNew;
  }

  /* which street a point is on, in which direction, how far along and how far right of centre */
  function legAt(x, z, psi) {
    const L = locate(x, z);
    if (L.kind === "solid") return null;
    const c = Math.cos(psi), s = Math.sin(psi);
    const alongH = Math.abs(c) >= Math.abs(s);
    const nearH = Math.abs(L.dz) <= H + WALK && x >= -H && x <= SPAN + H;
    const nearV = Math.abs(L.dx) <= H + WALK && z >= -H && z <= SPAN + H;
    let axisH = alongH ? nearH || !nearV : !(nearV || !nearH);
    if (!nearH && !nearV) return null;
    if (axisH) {
      const d = c >= 0 ? 0 : 2, k = clamp(Math.floor(x / B), 0, N - 2);
      const i0 = d === 0 ? k : k + 1, j0 = L.j;
      const u = (x - i0 * B) * DIRS[d][0];
      const right = (z - j0 * B) * (d === 0 ? 1 : -1);
      return { i: i0, j: j0, d, u, right, key: `${i0},${j0},${d}` };
    }
    const d = s >= 0 ? 1 : 3, k = clamp(Math.floor(z / B), 0, N - 2);
    const j0 = d === 1 ? k : k + 1, i0 = L.i;
    const u = (z - j0 * B) * DIRS[d][1];
    const right = (x - i0 * B) * (d === 1 ? -1 : 1);
    return { i: i0, j: j0, d, u, right, key: `${i0},${j0},${d}` };
  }

  /* ---------------- lanes and turns, shared by cars and the route ribbon ---------------- */
  const laneOff = lane => (lane ? LANE_OUT : LANE_IN);
  const nodeP = (i, j) => [i * B, j * B];
  const add = (p, q, k = 1) => [p[0] + q[0] * k, p[1] + q[1] * k];
  const rightV = d => [-DIRS[d][1], DIRS[d][0]];
  function streetPiece(i, j, d, lane) {
    const a = add(add(nodeP(i, j), DIRS[d], STOPLINE), rightV(d), laneOff(lane));
    const b = add(add(nodeP(i + DIRS[d][0], j + DIRS[d][1]), DIRS[d], -STOPLINE), rightV(d), laneOff(lane));
    return poly([a, b]);
  }
  function turnPiece(i, j, dIn, laneIn, dOut, laneOut) {
    const p0 = add(add(nodeP(i, j), DIRS[dIn], -STOPLINE), rightV(dIn), laneOff(laneIn));
    const p2 = add(add(nodeP(i, j), DIRS[dOut], STOPLINE), rightV(dOut), laneOff(laneOut));
    let p1;
    if (dIn === dOut) p1 = [(p0[0] + p2[0]) / 2, (p0[1] + p2[1]) / 2];
    else { const a = (p2[0] - p0[0]) * DIRS[dIn][0] + (p2[1] - p0[1]) * DIRS[dIn][1]; p1 = add(p0, DIRS[dIn], a); }
    const pts = [];
    for (let k = 0; k <= 14; k++) {
      const t = k / 14, m = 1 - t;
      pts.push([m * m * p0[0] + 2 * m * t * p1[0] + t * t * p2[0], m * m * p0[1] + 2 * m * t * p1[1] + t * t * p2[1]]);
    }
    return poly(pts);
  }

  /* ---------------- the line ---------------- */
  const L0 = LINES[line] || LINES["19K"];
  const legs = [];
  for (let k = 0; k < L0.via.length - 1; k++) {
    let [i, j] = L0.via[k];
    const [i2, j2] = L0.via[k + 1];
    const d = i2 > i ? 0 : i2 < i ? 2 : j2 > j ? 1 : 3;
    while (i !== i2 || j !== j2) { legs.push({ i, j, d }); i += DIRS[d][0]; j += DIRS[d][1]; }
  }
  // stops: every other leg from the second, the terminal on the last
  const stops = [];
  const stopLegs = legs.map((_, k) => k).filter(k => k > 0 && k % 2 === 1);
  if (stopLegs[stopLegs.length - 1] !== legs.length - 1) stopLegs.push(legs.length - 1);
  const names = L0.stops;
  stopLegs.slice(0, names.length).forEach((k, n) => {
    const lg = legs[k], last = k === legs.length - 1;
    stops.push({ ...lg, leg: k, u: B * (last ? 0.62 : 0.55), name: names[Math.min(n, names.length - 1)], terminal: last });
  });
  stops[stops.length - 1].terminal = true; stops[stops.length - 1].name = names[names.length - 1];
  const route = { code: L0.code, name: L0.name, legs, stops,
    start: (() => { const lg = legs[0], [x, z] = add(add(nodeP(lg.i, lg.j), DIRS[lg.d], 24), rightV(lg.d), LANE_OUT); return { x, z, psi: headingOf(lg.d) }; })() };
  // the ribbon the driver follows: the outer lane through every leg and turn
  route.path = [];
  legs.forEach((lg, k) => {
    route.path.push(...streetPiece(lg.i, lg.j, lg.d, 1).pts);
    const nx = legs[k + 1];
    if (nx) route.path.push(...turnPiece(lg.i + DIRS[lg.d][0], lg.j + DIRS[lg.d][1], lg.d, 1, nx.d, 1).pts.slice(1, -1));
  });

  /* ---------------- traffic ---------------- */
  const rand = rng(seed * 7919 + 13);
  const nCars = { yok: 0, az: 16, orta: 34, yogun: 60 }[traffic] ?? 34;
  const cars = [];
  const allLegs = [];
  for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) for (let d = 0; d < 4; d++)
    if (inRange(i + DIRS[d][0], j + DIRS[d][1])) allLegs.push([i, j, d]);
  for (let n = 0; n < nCars; n++) {
    for (let tries = 0; tries < 20; tries++) {
      const [i, j, d] = allLegs[Math.floor(rand() * allLegs.length)], lane = rand() < 0.6 ? 1 : 0;
      const piece = streetPiece(i, j, d, lane), u = rand() * piece.len;
      const p = piece.at(u);
      if (Math.hypot(p.x - route.start.x, p.z - route.start.z) < 60) continue;
      if (cars.some(c => Math.hypot(c.x - p.x, c.z - p.z) < 12)) continue;
      cars.push({ id: n, piece, u, stage: "street", i, j, d, lane, v: 8 + rand() * 4, x: p.x, z: p.z, h: p.h,
                  col: n, wait: 0, ghost: 0, nextD: null, stunned: 0 });
      break;
    }
  }
  function chooseNext(c) {
    const ni = c.i + DIRS[c.d][0], nj = c.j + DIRS[c.d][1];
    const opts = [0, 1, 3].map(tt => (c.d + tt) % 4).filter(d => inRange(ni + DIRS[d][0], nj + DIRS[d][1]));
    // cars go straight or right; a left turn across oncoming traffic only where nothing else is possible
    const pref = opts.filter(d => turnOf(c.d, d) !== 3 && (turnOf(c.d, d) === 0 || c.lane === 1));
    const pool = pref.length ? pref : opts;
    const straight = pool.find(d => d === c.d);
    return straight != null && rand() < 0.62 ? straight : pool[Math.floor(rand() * pool.length)];
  }
  function stepCars(dt, t, busPts) {
    for (const c of cars) {
      const p = c.piece.at(c.u), hx = Math.cos(p.h), hz = Math.sin(p.h);
      let gap = 60;
      if (c.stunned > 0) { c.stunned -= dt; gap = 0; }
      // the car or bus ahead in this lane
      const look = (ox, oz, len) => {
        const rx = ox - p.x, rz = oz - p.z, f = rx * hx + rz * hz, l = -rx * hz + rz * hx;
        if (f > 0 && f < 45 && Math.abs(l) < 1.7) gap = Math.min(gap, f - len);
      };
      if (c.ghost <= 0) for (const o of cars) if (o !== c) look(o.x, o.z, 4.6);
      for (const [bx, bz] of busPts) look(bx, bz, 2.4);
      // the light at the end of this street
      let redGap = 60;
      if (c.stage === "street") {
        const rem = c.piece.len - c.u, axis = DIRS[c.d][1] === 0 ? "h" : "v";
        const st = light(c.i + DIRS[c.d][0], c.j + DIRS[c.d][1], axis, t);
        if (st === "red" || (st === "amber" && rem > c.v * c.v / 6 + 3)) redGap = rem + 0.5;
      }
      const vmax = c.stage === "turn" ? (turnOf(c.d, c.nextD ?? c.d) === 0 ? 11 : 5.5) : 12.5;
      const want = Math.min(vmax, Math.sqrt(2 * 3.5 * Math.max(0, gap - 2.5)), Math.sqrt(2 * 3.5 * Math.max(0, redGap - 0.5)));
      c.v = Math.max(0, c.v + clamp((want - c.v) * 1.6, -7, 2.2) * dt);
      // patience: a car stuck behind another for long (not at a light, not behind the bus) eases through
      const stuck = c.v < 0.1 && redGap > 30 && c.stunned <= 0;
      c.wait = stuck ? c.wait + dt : 0;
      if (c.wait > 14) { c.ghost = 3; c.wait = 0; }
      if (c.ghost > 0) c.ghost -= dt;
      c.u += c.v * dt;
      while (c.u >= c.piece.len) {
        c.u -= c.piece.len;
        if (c.stage === "street") {
          c.nextD = chooseNext(c);
          const ni = c.i + DIRS[c.d][0], nj = c.j + DIRS[c.d][1];
          c.piece = turnPiece(ni, nj, c.d, c.lane, c.nextD, c.lane); c.stage = "turn"; c.i = ni; c.j = nj;
        } else {
          c.d = c.nextD; c.nextD = null;
          c.piece = streetPiece(c.i, c.j, c.d, c.lane); c.stage = "street";
        }
      }
      const q = c.piece.at(c.u);
      c.x = q.x; c.z = q.z; c.h = q.h;
    }
  }
  /* cars the bus is touching: a car's corners inside the bus, or the bus's points inside a car */
  function collisions(x, z, psi) {
    const out = [], c0 = Math.cos(psi), s0 = Math.sin(psi);
    const inBus = (px, pz) => { const rx = px - x, rz = pz - z; return Math.abs(rx * c0 + rz * s0) < 6.1 && Math.abs(-rx * s0 + rz * c0) < 1.35; };
    const busPts = footprint(x, z, psi);
    for (const c of cars) {
      const hc = Math.cos(c.h), hs = Math.sin(c.h);
      const corners = [[2.15, 0.9], [2.15, -0.9], [-2.15, 0.9], [-2.15, -0.9]].map(([a, b]) => [c.x + a * hc - b * hs, c.z + a * hs + b * hc]);
      const inCar = ([px, pz]) => { const rx = px - c.x, rz = pz - c.z; return Math.abs(rx * hc + rz * hs) < 2.2 && Math.abs(-rx * hs + rz * hc) < 0.95; };
      if (corners.some(([px, pz]) => inBus(px, pz)) || busPts.some(inCar)) out.push(c);
    }
    return out;
  }
  /* after a knock: the car stops for a moment and the bus is set back off it */
  function resolveCollisions(st) {
    const hits = collisions(st.s, st.lat, st.psi);
    if (!hits.length) return 0;
    for (const c of hits) { c.v = 0; c.stunned = 2.5; }
    const back = st.v >= 0 ? -1 : 1;
    st.s += Math.cos(st.psi) * 0.6 * back; st.lat += Math.sin(st.psi) * 0.6 * back; st.v = 0;
    return hits.length;
  }

  /* ---------------- guidance: shortest way to the next stop, no U-turns ---------------- */
  function routeTo(from, stop) {
    // BFS over (node, heading) from the junction ahead of the bus
    const start = { i: from.i + DIRS[from.d][0], j: from.j + DIRS[from.d][1], d: from.d };
    const key = s => `${s.i},${s.j},${s.d}`;
    const prevOf = new Map([[key(start), null]]);
    const q = [start];
    while (q.length) {
      const s = q.shift();
      if (s.i === stop.i && s.j === stop.j && s.d !== rev(stop.d)) {
        const moves = [];
        for (let k = key(s), cur = s; cur; ) { const p = prevOf.get(k); if (!p) break; moves.unshift(cur.d); cur = p; k = key(p); }
        moves.push(stop.d);
        return moves;                      // directions to take at each junction from the one ahead
      }
      for (const d of [0, 1, 2, 3]) {
        if (d === rev(s.d)) continue;
        const ni = s.i + DIRS[d][0], nj = s.j + DIRS[d][1];
        if (!inRange(ni, nj)) continue;
        const n = { i: ni, j: nj, d };
        if (!prevOf.has(key(n))) { prevOf.set(key(n), s); q.push(n); }
      }
    }
    return null;
  }

  return { B, N, H, WALK, R, SPAN, height, gradePct, factor, surfaceKey, legSurface, labelOf, locate, light, signalised,
           constrain, legAt, footprint, streetName, streetPiece, turnPiece, route, cars, stepCars, collisions, resolveCollisions, routeTo,
           start: route.start, steerLock: 0.72 };
}

/* a polyline with arc-length lookup: at(u) -> {x, z, h} */
function poly(pts) {
  const cum = [0];
  for (let k = 1; k < pts.length; k++) cum.push(cum[k - 1] + Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]));
  const len = cum[cum.length - 1];
  return { pts, cum, len, at(u) {
    u = clamp(u, 0, len);
    let k = 1;
    while (k < pts.length - 1 && cum[k] < u) k++;
    const a = pts[k - 1], b = pts[k], f = (u - cum[k - 1]) / ((cum[k] - cum[k - 1]) || 1);
    return { x: a[0] + (b[0] - a[0]) * f, z: a[1] + (b[1] - a[1]) * f, h: Math.atan2(b[1] - a[1], b[0] - a[0]) };
  } };
}

/* ================================================================ mission */
const PEN = { missed: 15, red: 10, crash: 20, kerb: 2, doors: 5, left: 5 };

export function createMission(city, { startPax = 20, seed = 11 } = {}) {
  const r = rng(seed);
  const stops = city.route.stops.map((s, k) => ({ ...s, k, state: "todo",
    board: s.terminal ? 0 : 2 + Math.floor(r() * 7), alightWant: s.terminal ? 999 : Math.floor(r() * 6) }));
  const M = { stops, next: 0, pax: startPax, penalties: [], events: [], phase: "driving", t: 0, done: false,
              boarding: null, lastRed: null, kerbT: -99, crashT: -99, wrongDoorT: -99, instruction: "", distNext: null };
  const pen = (kind, text) => { M.penalties.push({ kind, text, pts: PEN[kind], t: M.t }); M.events.push({ kind: "pen", text, pts: PEN[kind] }); };
  M.score = () => Math.max(0, 100 - M.penalties.reduce((a, p) => a + p.pts, 0));

  /* called every frame with the driver's state; returns passenger count changes via M.pax */
  M.update = (st, dt, t, doorsOpen) => {
    M.t += dt;
    if (M.done) return;
    const psi = st.psi, fx = st.s + Math.cos(psi) * 6, fz = st.lat + Math.sin(psi) * 6;
    const doorX = st.s + Math.cos(psi) * 4.95, doorZ = st.lat + Math.sin(psi) * 4.95;
    const legC = city.legAt(st.s, st.lat, psi), legF = city.legAt(fx, fz, psi);
    const stop = stops[M.next];

    // red light: the front crosses a stop line while its light is red
    if (legF) {
      const rem = B - STOPLINE - legF.u;
      const key = legF.key;
      if (M.lastRed && M.lastRed.key === key && M.lastRed.rem > 0 && rem <= 0 && st.v > 0.5) {
        const axis = DIRS[legF.d][1] === 0 ? "h" : "v";
        if (city.light(legF.i + DIRS[legF.d][0], legF.j + DIRS[legF.d][1], axis, t) === "red") pen("red", "Kırmızı ışıkta geçtiniz");
      }
      M.lastRed = { key, rem };
    }
    if (st.kerb && M.t - M.kerbT > 4) { pen("kerb", "Kaldırıma çıktınız"); M.kerbT = M.t; }

    // the stop: zone, doors, people
    const legD = city.legAt(doorX, doorZ, psi);
    const inZone = stop && legD && legD.i === stop.i && legD.j === stop.j && legD.d === stop.d &&
      Math.abs(legD.u - stop.u) < 8 && legC && legC.right > 3.4 && legC.right < 8.2;
    M.atStop = !!inZone && Math.abs(st.v) < 0.3;

    if (M.phase === "driving") {
      if (doorsOpen && M.atStop) {
        const alight = Math.min(stop.alightWant, M.pax);
        M.boarding = { alight, board: stop.board, done: 0, dA: 0, dB: 0, t: 0 };
        M.phase = "boarding"; stop.state = "serving";
      } else if (doorsOpen && M.t - M.wrongDoorT > 6) { pen("doors", "Durak dışında kapı açtınız"); M.wrongDoorT = M.t; }
      // passing the stop without serving it
      if (stop && legD && legD.i === stop.i && legD.j === stop.j && legD.d === stop.d && legD.u > stop.u + 22 && !stop.terminal) {
        stop.state = "missed"; pen("missed", `${stop.name} durağını kaçırdınız`); M.next++;
      }
    } else if (M.phase === "boarding") {
      const b = M.boarding;
      b.t += dt;
      // middle and rear doors let people off while the front door lets people on
      const offNow = Math.min(b.alight, Math.floor(b.t / 1.1));
      const onNow = Math.min(b.board, Math.floor(Math.max(0, b.t - 1.0) / 2.0));
      M.pax += (offNow - b.dA) * -1 + (onNow - b.dB); b.dA = offNow; b.dB = onNow;
      if (offNow >= b.alight && onNow >= b.board) {
        if (stop.terminal) { stop.state = "served"; M.phase = "finished"; M.done = true; M.events.push({ kind: "done" }); }
        else M.phase = "close";
      }
      if (!doorsOpen && M.phase === "boarding") {
        const left = b.board - b.dB;
        if (left > 0) pen("left", `${left} yolcu durakta kaldı`);
        stop.state = "served"; M.next++; M.phase = "driving"; M.boarding = null;
      }
    } else if (M.phase === "close") {
      if (!doorsOpen) { stop.state = "served"; M.next++; M.phase = "driving"; M.boarding = null; }
    }

    // guidance to the next stop
    const tgt = stops[M.next];
    M.guide = null;
    if (tgt && legC) {
      if (legC.i === tgt.i && legC.j === tgt.j && legC.d === tgt.d) {
        M.guide = { kind: "stop", dist: Math.max(0, tgt.u - (legD ? legD.u : legC.u)), street: city.streetName(tgt.i, tgt.j, tgt.d) };
      } else {
        const moves = city.routeTo(legC, tgt);
        if (moves && moves.length) {
          const turn = turnOf(legC.d, moves[0]);
          const ni = legC.i + DIRS[legC.d][0], nj = legC.j + DIRS[legC.d][1];
          M.guide = { kind: "turn", turn, dist: Math.max(0, B - legC.u), street: city.streetName(ni, nj, moves[0]), moves, from: legC };
        }
      }
    }
  };

  M.collide = hit => {
    if (hit && M.t - M.crashT > 3) { pen("crash", "Bir araca çarptınız"); M.crashT = M.t; }
  };
  return M;
}

/* a world for drivephys: the city */
export function cityWorld(city) {
  return {
    start: city.start, steerLock: city.steerLock,
    height: city.height,
    gradePct: (x, z, psi) => city.gradePct(x, z, psi),
    factor: (x, z) => city.factor(x, z),
    constrain: city.constrain,
  };
}
