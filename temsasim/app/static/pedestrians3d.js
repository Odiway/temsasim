/* Pedestrians in the 3D scene.

   Purely visual, like the rest of the stage. Two kinds:

   - Passengers at the stops. Every door opening in the model's record
     (play.timeline.doors) is one stop visit. People wait at the shelter as the
     bus comes in, walk to the front door once it opens and are gone by the
     time it shuts; others step out of the middle and rear doors and walk off.
     The net change in headcount is the model's own occupancy (timeline.pax);
     the two people who board and two who alight on top of that are only there
     so a stop never looks empty -- they cancel and change nothing.

   - Walkers on the pavements. Ambient, fewer at night and in harsh weather.
     They obey the bus's own signal phase where they cross a side street, and
     never step out late in the green, so cross traffic never meets them. */

import * as THREE from "three";
import { lightState } from "/static/traffic.js";

const MAX = 360;
const WALK = 1.35;              // m/s
const STRIDE = 1.5;             // metres per full leg cycle
const CROSS0 = 0.1, CROSS1 = 8.3;   // side street extent past the stop line (traffic3d: centre +4.2, 8.2 wide)
const DOORS = [-1.05, -5.45, -10.35];   // door centres behind the bus's front bumper (sim3d: buildBus)
const DOOR_LAT = 3.45;          // just outside the open door leaves
const TURNOVER = 2;             // illustrative boarders/alighters that cancel out
const CLOTHES = ["#5d6f82", "#8e959c", "#b9a894", "#6f8276", "#3f4750", "#a87e6a", "#d9d4ca", "#7a6a88", "#9b4f4f", "#4f6f9b"].map(c => new THREE.Color(c));
const TROUSERS = ["#2f353b", "#3e4a5a", "#575049", "#6b6f73", "#24282c"].map(c => new THREE.Color(c));
const SKIN = ["#e8c9a8", "#c99f7c", "#8d6448", "#f0d6bf", "#b07f5a"].map(c => new THREE.Color(c));

const hash = n => { let x = (n | 0) * 2654435761 >>> 0; x ^= x >>> 15; x = Math.imul(x, 2246822519) >>> 0; x ^= x >>> 13; return (x >>> 0) / 4294967296; };
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));

export function createPedestrians(scene, { at, rightOf }) {
  const group = new THREE.Group();
  scene.add(group);

  /* ---------------- one low-poly figure, instanced: torso, head, legs, arms ---------------- */
  const torsoGeo = new THREE.CapsuleGeometry(0.18, 0.36, 3, 8); torsoGeo.scale(0.72, 1, 1); torsoGeo.translate(0, 1.18, 0);
  const headGeo = new THREE.SphereGeometry(0.115, 10, 8); headGeo.translate(0, 1.63, 0);
  const legGeo = new THREE.BoxGeometry(0.13, 0.84, 0.13); legGeo.translate(0, -0.42, 0);
  const armGeo = new THREE.BoxGeometry(0.085, 0.6, 0.085); armGeo.translate(0, -0.3, 0);
  const mat = () => new THREE.MeshStandardMaterial({ roughness: 0.85 });
  const torsos = new THREE.InstancedMesh(torsoGeo, mat(), MAX);
  const heads = new THREE.InstancedMesh(headGeo, mat(), MAX);
  const legs = new THREE.InstancedMesh(legGeo, mat(), MAX * 2);
  const arms = new THREE.InstancedMesh(armGeo, mat(), MAX * 2);
  for (const m of [torsos, heads, legs, arms]) {
    m.castShadow = true; m.receiveShadow = true; m.frustumCulled = false; m.count = 0;
    m.setColorAt(0, CLOTHES[0]);          // allocates instanceColor
    group.add(m);
  }

  const base = new THREE.Matrix4(), limb = new THREE.Matrix4(), out = new THREE.Matrix4();
  const q = new THREE.Quaternion(), yAxis = new THREE.Vector3(0, 1, 0);
  const P = new THREE.Vector3(), S = new THREE.Vector3();
  let n = 0;

  /* A figure at path distance u, lateral offset lat (+ right of the bus's
     heading), heading along (du, dlat) in path coordinates; `walked` drives
     the gait, and a figure standing still has walked = null. */
  function put(u, lat, du, dlat, walked, seed) {
    if (n >= MAX) return;
    const c = at(path, u), r = rightOf(c.th);
    const fx = Math.cos(c.th), fz = Math.sin(c.th);
    const y = c.y + (Math.abs(lat) > 3.8 ? 0.16 : 0.02);
    P.set(c.x + r.x * lat, y, c.z + r.z * lat);
    const dx = fx * du + r.x * dlat, dz = fz * du + r.z * dlat;
    q.setFromAxisAngle(yAxis, -Math.atan2(dz, dx));
    S.setScalar(0.9 + 0.18 * hash(seed * 3 + 1));
    const swing = walked == null ? 0 : 0.42 * Math.sin(walked * 2 * Math.PI / STRIDE);
    if (walked != null) P.y += 0.025 * Math.abs(Math.cos(walked * 2 * Math.PI / STRIDE));
    base.compose(P, q, S);
    torsos.setMatrixAt(n, base); heads.setMatrixAt(n, base);
    for (const side of [0, 1]) {
      const s = side ? 1 : -1;
      limb.makeRotationZ(s * swing).setPosition(0, 0.88, s * 0.095);
      legs.setMatrixAt(n * 2 + side, out.multiplyMatrices(base, limb));
      limb.makeRotationZ(-s * swing * 0.8).setPosition(0, 1.44, s * 0.235);
      arms.setMatrixAt(n * 2 + side, out.multiplyMatrices(base, limb));
    }
    const shirt = CLOTHES[Math.floor(hash(seed * 7 + 2) * CLOTHES.length)];
    torsos.setColorAt(n, shirt);
    heads.setColorAt(n, SKIN[Math.floor(hash(seed * 11 + 3) * SKIN.length)]);
    const trousers = TROUSERS[Math.floor(hash(seed * 13 + 4) * TROUSERS.length)];
    legs.setColorAt(n * 2, trousers); legs.setColorAt(n * 2 + 1, trousers);
    arms.setColorAt(n * 2, shirt); arms.setColorAt(n * 2 + 1, shirt);
    n++;
  }

  /* ---------------- state ---------------- */
  let path = null, signals = [], stops = [], visits = [], signalsOn = true;
  let density = 1;

  const nearestStop = d => {
    let best = null;
    for (const s of stops) if (best == null || Math.abs(s - d) < Math.abs(best - d)) best = s;
    return best != null && Math.abs(best - d) < 25 ? best : null;
  };

  /* Where boarders stand: between the kerb and the shelter (sim3d puts it at 6.2 m). */
  const waitSpot = (centre, i, seed) => ({
    u: centre - 1.6 + ((i * 0.83 + hash(seed) * 0.5) % 3.4),
    lat: 4.35 + 0.9 * hash(seed + 17),
  });

  function setVisits(tl) {
    visits = [];
    if (!tl || !tl.doors) return;
    const D = tl.doors, T = tl.t, X = tl.d, pax = tl.pax || [];
    let prevPax = pax.length ? pax[0] : 0;
    for (let i = 0; i < D.length; i++) {
      if (!(D[i] > 0.5) || (i > 0 && D[i - 1] > 0.5)) continue;
      let j = i;
      while (j + 1 < D.length && D[j + 1] > 0.5) j++;
      const after = pax.length ? pax[Math.min(j + 1, pax.length - 1)] : prevPax;
      const net = Math.round(after - prevPax);
      const turn = clamp(Math.round(prevPax) - Math.max(0, -net), 0, TURNOVER);
      const nA = Math.min(12, Math.max(0, -net) + turn);
      const nB = Math.min(12, Math.max(0, net) + turn);
      const front = X[i], shelter = nearestStop(front);
      visits.push({ t0: T[i], t1: T[Math.min(j + 1, T.length - 1)], front, centre: shelter ?? front - 1.0, nA, nB, k: visits.length });
      prevPax = after;
      i = j;
    }
  }

  function drawVisit(v, t) {
    const dwell = Math.max(2, v.t1 - v.t0);
    // boarders: wait, then walk to the front door and step in before it shuts
    const sp = clamp((dwell - 3) / Math.max(v.nB, 1), 0.5, 1.8);
    const door = { u: v.front + DOORS[0], lat: DOOR_LAT };
    for (let i = 0; i < v.nB; i++) {
      const seed = v.k * 97 + i;
      const w = waitSpot(v.centre, i, seed);
      const tb = v.t0 + 0.7 + i * sp;
      if (t < tb) { put(w.u, w.lat, 0, -1, null, seed); continue; }       // facing the road
      const du = door.u - w.u, dl = door.lat - w.lat, len = Math.hypot(du, dl) || 1;
      const s = (t - tb) * WALK;
      if (s >= len) continue;                                            // aboard
      put(w.u + du * s / len, w.lat + dl * s / len, du, dl, s, seed);
    }
    // alighters: out of the middle and rear doors, onto the pavement, away
    for (let j = 0; j < v.nA; j++) {
      const seed = v.k * 97 + 50 + j;
      const du0 = v.front + DOORS[1 + (j % 2)];
      const ta = v.t0 + 0.6 + Math.floor(j / 2) * 1.3;
      if (t < ta) continue;
      const s = (t - ta) * WALK;
      const step = 0.9 + 0.4 * hash(seed);                               // out to the walking line
      if (s < step) { put(du0, DOOR_LAT + s, 0, 1, s, seed); continue; }
      const dir = hash(seed + 5) < 0.5 ? -1 : 1, along = s - step;
      if (along > 22) continue;                                          // out of the scene
      put(du0 + dir * along, DOOR_LAT + step, dir, 0, s, seed);
    }
  }

  /* With no run loaded, a few people simply wait at each shelter. */
  function drawIdleStops(busFront) {
    for (let k = 0; k < stops.length; k++) {
      if (Math.abs(stops[k] - busFront) > 400) continue;
      const cnt = 1 + Math.floor(hash(k * 31 + 7) * 3);
      for (let i = 0; i < cnt; i++) { const w = waitSpot(stops[k], i, k * 97 + i); put(w.u, w.lat, 0, -1, null, k * 97 + i); }
    }
  }

  /* ---------------- pavement walkers: a small stateful model ---------------- */
  let walkers = [], wT = null;
  function resetWalkers(t) {
    walkers = [];
    if (!path) return;
    const perSide = Math.round(18 * density * path.L / 1000);
    for (const side of [-1, 1]) for (let i = 0; i < Math.min(perSide, 900); i++) {
      const seed = 5000 + i * 2 + (side > 0 ? 1 : 0);
      // the right pavement has the shelters on it: walk behind them
      const lat = side > 0 ? 5.7 + 1.5 * hash(seed + 1) : -(4.3 + 3.0 * hash(seed + 1));
      walkers.push({ u: hash(seed + 2) * path.L, dir: hash(seed + 3) < 0.5 ? -1 : 1, lat, side,
                     v: WALK * (0.8 + 0.4 * hash(seed + 4)), gap: 0.3 + 1.4 * hash(seed + 6), walked: hash(seed) * STRIDE, seed });
    }
    wT = t;
  }
  // a walker may only start across a side street with time to finish before cross traffic moves
  const mayCross = (g, t) => {
    if (!signalsOn) return true;
    const ph = ((t + g.offset) % g.cycle + g.cycle) % g.cycle;
    return ph < g.green - 6.5;
  };
  function stepWalkers(t) {
    if (!walkers.length) return;
    let dt = t - wT;
    if (dt < 0 || dt > 40) { resetWalkers(t); dt = 0; }
    wT = t;
    const sub = Math.max(1, Math.ceil(dt / 0.4)), h = dt / sub;
    for (let k = 0; k < sub; k++) {
      const tk = t - dt + (k + 1) * h;
      for (const w of walkers) {
        let v = w.v;
        for (const g of signals) {
          const edge = w.dir > 0 ? g.d + CROSS0 : g.d + CROSS1, dist = (edge - w.u) * w.dir;
          if (dist > w.gap - 0.1 && dist < w.gap + 0.8 && !mayCross(g, tk)) { v = 0; break; }
        }
        w.u += w.dir * v * h; w.walked += v * h; w.moving = v > 0;
        if (w.u < 0) w.u += path.L; else if (w.u > path.L) w.u -= path.L;
      }
    }
  }
  // right-side walkers step out to the back of the pavement past a shelter
  const walkerLat = w => {
    if (w.side < 0) return w.lat;
    const s = nearestStop(w.u);
    if (s == null) return w.lat;
    const b = clamp((Math.abs(w.u - s) - 2.6) / 3, 0, 1);
    return 7.3 + (w.lat - 7.3) * b;
  };

  /* ---------------- per frame ---------------- */
  function update(st) {
    if (!path) return;
    n = 0;
    const t = st.t ?? performance.now() / 1000, busFront = st.d || 0;
    if (visits.length) {
      for (const v of visits) if (t >= v.t0 - 150 && t <= v.t1 + 25 && Math.abs(v.front - busFront) < 400) drawVisit(v, t);
    } else drawIdleStops(busFront);
    stepWalkers(t);
    for (const w of walkers) {
      if (Math.abs(w.u - busFront) > 320) continue;
      const lat = walkerLat(w);
      put(w.u, lat, w.dir, 0, w.moving === false ? null : w.walked, w.seed);
    }
    for (const m of [torsos, heads]) m.count = n;
    for (const m of [legs, arms]) m.count = n * 2;
    for (const m of [torsos, heads, legs, arms]) { m.instanceMatrix.needsUpdate = true; m.instanceColor.needsUpdate = true; }
  }

  return {
    setPath(p, sigs, stopList) { path = p; signals = sigs || []; stops = stopList || []; visits = []; resetWalkers(wT ?? 0); },
    setVisits,
    setSignalsActive(on) { signalsOn = on; },
    setEnv({ ambient = 20, night = false } = {}) {
      const d = (night ? 0.3 : 1) * (ambient < 1 ? 0.55 : ambient > 30 ? 0.7 : 1);
      if (d !== density) { density = d; if (path) resetWalkers(wT ?? 0); }
    },
    update,
    dispose() { scene.remove(group); group.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); }); },
  };
}
