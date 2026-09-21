/* 3D playback stage.

   Purely visual: it never computes physics. The controller in app.js feeds it
   one interpolated simulation state per frame, and the stage places the bus,
   spins the wheels, opens the doors and lights the energy flow accordingly.

   Design: a pale architectural-model city (buildings, trees and stops in quiet
   off-whites) so that the only saturated things on screen are the bus and its
   energy -- blue while the pack discharges, green while regen charges it -- and
   the amber dot-matrix destination sign it shares with the page header.

   The road's elevation is the route's real elevation. Its plan-view curves are
   illustrative: the route files carry distance and height, not map coordinates. */

import * as THREE from "three";
import { OrbitControls } from "/static/vendor/three/OrbitControls.js";
import { createTraffic } from "/static/traffic3d.js";

const STEP = 10;             // metres between path samples
const LANE = 1.9;            // bus lane centre, right of the road centreline
const ROAD_HALF = 3.8;
const BLUE = new THREE.Color("#3987e5");
const GREEN = new THREE.Color("#2fbf71");
const AMBER = "#ffb000";

/* ------------------------------------------------------------ path */
function buildPath(profile) {
  const L = Math.max(profile.length_m, STEP * 3);
  const n = Math.ceil(L / STEP) + 2;
  const X = new Float32Array(n), Y = new Float32Array(n), Z = new Float32Array(n), H = new Float32Array(n);
  const D = profile.d, E = profile.elev;
  const emin = Math.min(...E);
  const elevAt = d => {
    if (d <= D[0]) return E[0];
    if (d >= D[D.length - 1]) return E[E.length - 1];
    let lo = 0, hi = D.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (D[m] <= d) lo = m; else hi = m; }
    const t = (d - D[lo]) / (D[hi] - D[lo] || 1);
    return E[lo] + t * (E[hi] - E[lo]);
  };
  let x = 0, z = 0;
  for (let i = 0; i < n; i++) {
    const d = i * STEP;
    const th = 0.55 * Math.sin(d / 820) + 0.28 * Math.sin(d / 300 + 1.1) + 0.1 * Math.sin(d / 140 + 0.3);
    if (i > 0) { x += Math.cos(H[i - 1]) * STEP; z += Math.sin(H[i - 1]) * STEP; }
    X[i] = x; Z[i] = z; H[i] = th; Y[i] = elevAt(d) - emin;
  }
  return { X, Y, Z, H, n, L };
}

function at(p, d) {
  const f = Math.min(Math.max(d / STEP, 0), p.n - 1.001);
  const i = Math.floor(f), t = f - i;
  const lerp = (A) => A[i] + (A[i + 1] - A[i]) * t;
  const th = Math.atan2(p.Z[i + 1] - p.Z[i], p.X[i + 1] - p.X[i]);
  return { x: lerp(p.X), y: lerp(p.Y), z: lerp(p.Z), th, slope: (p.Y[i + 1] - p.Y[i]) / STEP };
}
const rightOf = th => new THREE.Vector3(-Math.sin(th), 0, Math.cos(th));

/* A ribbon offset from the centreline between off0 and off1 (metres, +right). */
function ribbon(p, off0, off1, y0, y1, material, { from = 0, to = null, absoluteY1 = null } = {}) {
  const i0 = Math.max(0, Math.floor(from / STEP)), i1 = Math.min(p.n - 1, to == null ? p.n - 1 : Math.ceil(to / STEP));
  const cnt = i1 - i0 + 1;
  if (cnt < 2) return null;
  const pos = new Float32Array(cnt * 2 * 3);
  const idx = [];
  for (let k = 0; k < cnt; k++) {
    const i = i0 + k;
    const th = i < p.n - 1 ? Math.atan2(p.Z[i + 1] - p.Z[i], p.X[i + 1] - p.X[i]) : Math.atan2(p.Z[i] - p.Z[i - 1], p.X[i] - p.X[i - 1]);
    const r = rightOf(th);
    pos.set([p.X[i] + r.x * off0, p.Y[i] + y0, p.Z[i] + r.z * off0], k * 6);
    pos.set([p.X[i] + r.x * off1, absoluteY1 != null ? absoluteY1 : p.Y[i] + y1, p.Z[i] + r.z * off1], k * 6 + 3);
    if (k < cnt - 1) {
      const a = k * 2, b = a + 1, c = a + 2, d = a + 3;
      // (b-a) x (c-a) must point up so the ribbon is visible from above
      if (off1 > off0) idx.push(a, b, c, b, d, c); else idx.push(a, c, b, b, c, d);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  const m = new THREE.Mesh(g, material);
  m.receiveShadow = true;
  return m;
}

/* Dashed centre line as a single geometry: one draw call for the whole route. */
function dashes(p, halfW, y, dash, gap, material) {
  const pos = [], idx = [];
  for (let d = 0; d < p.L - dash; d += dash + gap) {
    const a = at(p, d), b = at(p, d + dash);
    const ra = rightOf(a.th), rb = rightOf(b.th);
    const k = pos.length / 3;
    pos.push(a.x - ra.x * halfW, a.y + y, a.z - ra.z * halfW, a.x + ra.x * halfW, a.y + y, a.z + ra.z * halfW,
             b.x - rb.x * halfW, b.y + y, b.z - rb.z * halfW, b.x + rb.x * halfW, b.y + y, b.z + rb.z * halfW);
    idx.push(k, k + 1, k + 2, k + 1, k + 3, k + 2);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx); g.computeVertexNormals();
  const m = new THREE.Mesh(g, material); m.receiveShadow = true;
  return m;
}

/* ------------------------------------------------------------ dot-matrix texture */
function dotTexture(raster, text, { minCols = 0, pitch = 10 } = {}) {
  const ROWS = 12;
  const cols = raster ? raster(text.toLocaleUpperCase("tr-TR")) : [];
  const n = Math.max(minCols, cols.length + 4);
  const c = document.createElement("canvas");
  c.width = n * pitch; c.height = ROWS * pitch;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#121110"; ctx.fillRect(0, 0, c.width, c.height);
  const x0 = Math.floor((n - cols.length) / 2);
  for (let x = 0; x < n; x++) for (let y = 0; y < ROWS; y++) {
    const col = cols[x - x0] || 0;
    ctx.fillStyle = (col >> y) & 1 ? AMBER : "#2a2318";
    ctx.beginPath(); ctx.arc(x * pitch + pitch / 2, y * pitch + pitch / 2, pitch * 0.36, 0, Math.PI * 2); ctx.fill();
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

/* ------------------------------------------------------------ the bus */
function buildBus(raster, doorsWanted = 3) {
  const g = new THREE.Group();
  const M = (o) => new THREE.MeshStandardMaterial(o);
  const paint = M({ color: 0xffffff, roughness: 0.3, metalness: 0.08 });
  const dark = M({ color: 0x2a3037, roughness: 0.7 });
  const skirtMat = M({ color: 0x80888f, roughness: 0.6 });
  // tinted glazing, not black: without an environment map, metalness only darkens
  const glass = M({ color: 0x3f5a6c, roughness: 0.14, metalness: 0.15 });
  const doorGlass = M({ color: 0x55738a, roughness: 0.14, metalness: 0.15 });
  const rubber = M({ color: 0x141414, roughness: 0.92 });
  const rim = M({ color: 0xbfc4c9, roughness: 0.3, metalness: 0.65 });
  const packMat = M({ color: 0x3a424b, roughness: 0.5, metalness: 0.3 });
  const hvacMat = M({ color: 0xd9dcdf, roughness: 0.55 });
  const glowMat = M({ color: 0x0d1116, emissive: BLUE.clone(), emissiveIntensity: 0 });
  const headMat = M({ color: 0xffffff, emissive: 0xfff4d6, emissiveIntensity: 0.6 });
  const tailMat = M({ color: 0x5a0d0d, emissive: 0xff2a1a, emissiveIntensity: 0.5 });
  const add = (geo, mat, x, y, z, cast = true) => { const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); m.castShadow = cast; m.receiveShadow = true; g.add(m); return m; };

  // body: a side profile extruded across the width, rounded at the roof
  const s = new THREE.Shape();
  const L2 = 6, H0 = 0.3, H1 = 3.12, r = 0.5;
  s.moveTo(-L2, H0); s.lineTo(L2 - 0.18, H0); s.lineTo(L2, H0 + 0.28);
  s.lineTo(L2, H1 - r); s.quadraticCurveTo(L2, H1, L2 - r, H1);
  s.lineTo(-L2 + 0.3, H1); s.quadraticCurveTo(-L2, H1, -L2, H1 - 0.3); s.lineTo(-L2, H0);
  const body = new THREE.ExtrudeGeometry(s, { depth: 2.46, bevelEnabled: true, bevelThickness: 0.045, bevelSize: 0.045, bevelSegments: 2, curveSegments: 10 });
  body.translate(0, 0, -1.23);
  add(body, paint, 0, 0, 0);

  add(new THREE.BoxGeometry(12.06, 0.4, 2.58), skirtMat, 0, 0.52, 0);             // skirt
  for (const z of [-1.285, 1.285]) {                                               // window band
    add(new THREE.BoxGeometry(10.2, 1.08, 0.02), glass, -0.35, 2.06, z, false);
    for (let x = -5.2; x <= 4.8; x += 1.62) add(new THREE.BoxGeometry(0.12, 1.08, 0.03), paint, x, 2.06, z * 1.001, false);
  }
  add(new THREE.BoxGeometry(0.02, 1.72, 2.3), glass, 6.05, 1.92, 0, false);        // windscreen
  add(new THREE.BoxGeometry(0.02, 0.85, 2.0), glass, -6.05, 2.3, 0, false);        // rear window

  // destination signs: front and near-side, amber dot matrix
  const signMat = new THREE.MeshBasicMaterial({ map: null, toneMapped: false });
  const front = new THREE.Mesh(new THREE.PlaneGeometry(2.05, 0.28), signMat);
  front.position.set(6.065, 2.83, 0); front.rotation.y = Math.PI / 2; g.add(front);
  const side = new THREE.Mesh(new THREE.PlaneGeometry(1.5, 0.22), signMat);
  side.position.set(3.95, 2.45, 1.3); g.add(side);

  // doors on the kerb (right) side: two plug-and-slide leaves each
  const doorX = [4.95, 0.55, -4.35].slice(0, Math.max(1, Math.min(3, doorsWanted)));
  const leaves = [];
  for (const cx of doorX) {
    add(new THREE.BoxGeometry(1.3, 2.26, 0.02), dark, cx, 1.43, 1.279, false);     // door aperture
    for (const side of [-1, 1]) {
      const leaf = add(new THREE.BoxGeometry(0.6, 2.18, 0.05), doorGlass, cx + side * 0.31, 1.42, 1.3, false);
      leaves.push({ mesh: leaf, cx, side });
    }
  }

  // wheels (rear shown single, as seen from outside)
  const wheels = [];
  const tire = new THREE.CylinderGeometry(0.5, 0.5, 0.34, 28); tire.rotateX(Math.PI / 2);
  const hub = new THREE.CylinderGeometry(0.3, 0.3, 0.36, 18); hub.rotateX(Math.PI / 2);
  const spoke = new THREE.BoxGeometry(0.5, 0.07, 0.37);
  for (const x of [3.55, -2.45]) for (const z of [-1.12, 1.12]) {
    const w = new THREE.Group(); w.position.set(x, 0.5, z);
    const t = new THREE.Mesh(tire, rubber); t.castShadow = true; w.add(t);
    w.add(new THREE.Mesh(hub, rim));
    const sp = new THREE.Mesh(spoke, dark); w.add(sp);
    const sp2 = sp.clone(); sp2.rotation.z = Math.PI / 2; w.add(sp2);
    g.add(w); wheels.push(w);
  }

  // roof: traction pack with its glow strip, and the HVAC unit
  for (const x of [3.3, 1.25, -0.8]) {
    add(new THREE.BoxGeometry(1.9, 0.3, 2.0), packMat, x, 3.28, 0);
    add(new THREE.BoxGeometry(1.92, 0.045, 2.02), glowMat, x, 3.45, 0, false);
  }
  add(new THREE.BoxGeometry(2.8, 0.36, 2.1), hvacMat, -3.75, 3.31, 0);
  for (const x of [-4.45, -3.05]) {
    const fan = new THREE.CylinderGeometry(0.4, 0.4, 0.05, 20);
    add(fan, dark, x, 3.51, 0, false);
  }
  for (const z of [-0.85, 0.85]) add(new THREE.BoxGeometry(0.03, 0.16, 0.42), headMat, 6.06, 0.8, z, false);
  for (const z of [-1.0, 1.0]) add(new THREE.BoxGeometry(0.03, 0.34, 0.18), tailMat, -6.06, 1.05, z, false);

  // energy particles: roof pack -> along the roof -> down the rear face -> axle
  const flowCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(3.9, 3.62, 0), new THREE.Vector3(-1.8, 3.62, 0), new THREE.Vector3(-5.4, 3.55, 0),
    new THREE.Vector3(-6.14, 3.1, 0), new THREE.Vector3(-6.14, 1.35, 0), new THREE.Vector3(-6.0, 0.55, 0),
    new THREE.Vector3(-2.45, 0.35, 0)]);
  const hvacCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(-0.6, 3.52, 0.65), new THREE.Vector3(-2.2, 3.54, 0.65), new THREE.Vector3(-3.0, 3.56, 0.65)]);
  const pGeo = new THREE.SphereGeometry(0.1, 10, 8);
  const flowMat = new THREE.MeshBasicMaterial({ color: BLUE, toneMapped: false, transparent: true, opacity: 0.95 });
  const hvacFlowMat = new THREE.MeshBasicMaterial({ color: new THREE.Color("#eb6834"), toneMapped: false, transparent: true, opacity: 0.9 });
  const flow = Array.from({ length: 34 }, () => { const m = new THREE.Mesh(pGeo, flowMat); g.add(m); return m; });
  const hvacFlow = Array.from({ length: 8 }, () => { const m = new THREE.Mesh(pGeo, hvacFlowMat); m.scale.setScalar(0.6); g.add(m); return m; });

  const headlight = new THREE.SpotLight(0xfff1d0, 0, 90, Math.PI / 7, 0.5, 1.2);
  headlight.position.set(6.2, 1.0, 0); headlight.target.position.set(40, -1.5, 0);
  g.add(headlight, headlight.target);

  return { group: g, signMat, leaves, wheels, glowMat, tailMat, headMat, flow, flowCurve, flowMat,
           hvacFlow, hvacCurve, headlight, doorOpen: 0, phase: 0, hvacPhase: 0,
           setSign(tex) { signMat.map = tex; signMat.needsUpdate = true; } };
}

/* ------------------------------------------------------------ the stage */
export function createStage(host, { raster } = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.02;
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.5, 5000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enabled = false; controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.47; controls.minDistance = 8; controls.maxDistance = 900;

  const hemi = new THREE.HemisphereLight(0xffffff, 0x8a8f86, 1.25);
  const sun = new THREE.DirectionalLight(0xffffff, 2.1);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -55, right: 55, top: 55, bottom: -55, near: 1, far: 400 });
  sun.shadow.bias = -0.0005; sun.shadow.normalBias = 0.02;
  scene.add(hemi, sun, sun.target);

  const bus = buildBus(raster);
  scene.add(bus.group);
  let traffic = null;
  let world = null, path = null, env = { ambient: 20, solar: 0, night: false };
  let camMode = "chase", lastBusPos = new THREE.Vector3(), snow = null, lamps = null;

  const mats = {
    road: new THREE.MeshStandardMaterial({ color: 0x33383d, roughness: 0.95 }),
    line: new THREE.MeshStandardMaterial({ color: 0xe9e8e0, roughness: 0.8 }),
    kerb: new THREE.MeshStandardMaterial({ color: 0xc9cbc6, roughness: 0.9 }),
    ground: new THREE.MeshStandardMaterial({ color: 0xdfe2da, roughness: 1 }),
    groundFar: new THREE.MeshStandardMaterial({ color: 0xd6d9d0, roughness: 1 }),
    building: new THREE.MeshStandardMaterial({ roughness: 0.9, metalness: 0.0 }),
    trunk: new THREE.MeshStandardMaterial({ color: 0x9a9184, roughness: 1 }),
    crown: new THREE.MeshStandardMaterial({ color: 0xb9ccb0, roughness: 0.95, flatShading: true }),
    shelter: new THREE.MeshStandardMaterial({ color: 0xf4f4f1, roughness: 0.6 }),
    shelterGlass: new THREE.MeshStandardMaterial({ color: 0xa8c2d6, roughness: 0.1, metalness: 0.3, transparent: true, opacity: 0.45 }),
    pole: new THREE.MeshStandardMaterial({ color: 0x8c9196, roughness: 0.5, metalness: 0.4 }),
    lamp: new THREE.MeshStandardMaterial({ color: 0xfff1c8, emissive: 0xffd88a, emissiveIntensity: 0 }),
  };

  function clearWorld() {
    if (!world) return;
    scene.remove(world);
    world.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material && o.material.map) o.material.map.dispose(); });
    world = null;
  }

  function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

  function setProfile(profile, meta = {}) {
    clearWorld();
    path = buildPath(profile);
    if (!traffic) traffic = createTraffic(scene, { at, rightOf, mats });
    traffic.setPath(path, profile.signals || []);
    const nearJunction = (d, m) => traffic.nearSignal(d, m);
    world = new THREE.Group();
    const add = m => { if (m) world.add(m); return m; };

    // road, markings, kerbs, verge and falling-away ground
    add(ribbon(path, -ROAD_HALF, ROAD_HALF, 0.02, 0.02, mats.road));
    for (const o of [-3.55, 3.55]) add(ribbon(path, o - 0.07, o + 0.07, 0.035, 0.035, mats.line));
    add(dashes(path, 0.08, 0.035, 4, 8, mats.line));
    for (const sgn of [-1, 1]) {
      add(ribbon(path, sgn * ROAD_HALF, sgn * 7.5, 0.16, 0.16, mats.kerb));
      add(ribbon(path, sgn * 7.5, sgn * 42, 0.16, -0.8, mats.ground));
      add(ribbon(path, sgn * 42, sgn * 260, -0.8, 0, mats.groundFar, { absoluteY1: -14 }));
    }
    const plane = new THREE.Mesh(new THREE.PlaneGeometry(20000, 20000), mats.groundFar);
    plane.rotation.x = -Math.PI / 2; plane.position.y = -14; plane.receiveShadow = true;
    add(plane);

    const rand = rng(1234 + Math.round(path.L));
    const stops = profile.stops || [];
    const nearStop = d => stops.some(s => Math.abs(s - d) < 16);

    // buildings: an architectural-model city in quiet off-whites
    const bPlaces = [];
    for (const sgn of [-1, 1]) {
      for (let d = 20 + rand() * 20; d < path.L - 20; d += 24 + rand() * 20) {
        if (rand() < 0.12 || nearJunction(d, 22)) continue;
        bPlaces.push({ d, off: sgn * (19 + rand() * 12), w: 10 + rand() * 12, dep: 9 + rand() * 10, h: 6 + Math.pow(rand(), 1.6) * 34 });
      }
    }
    const bGeo = new THREE.BoxGeometry(1, 1, 1); bGeo.translate(0, 0.5, 0);
    const bMesh = new THREE.InstancedMesh(bGeo, mats.building, bPlaces.length);
    bMesh.castShadow = true; bMesh.receiveShadow = true;
    const palette = ["#ebe8e1", "#e2ded5", "#d7dadd", "#e6e1d8", "#dcdfe1", "#efece6"].map(c => new THREE.Color(c));
    const dummy = new THREE.Object3D();
    bPlaces.forEach((b, k) => {
      const p = at(path, b.d), r = rightOf(p.th);
      dummy.position.set(p.x + r.x * b.off, p.y - 0.8, p.z + r.z * b.off);
      dummy.rotation.set(0, -p.th, 0);
      dummy.scale.set(b.w, b.h, b.dep);
      dummy.updateMatrix(); bMesh.setMatrixAt(k, dummy.matrix);
      bMesh.setColorAt(k, palette[k % palette.length]);
    });
    add(bMesh);

    // street trees along the kerb
    const tPlaces = [];
    for (const sgn of [-1, 1]) for (let d = 10; d < path.L - 10; d += 16 + rand() * 10) {
      if (sgn > 0 && nearStop(d)) continue;
      if (rand() < 0.2 || nearJunction(d, 12)) continue;
      tPlaces.push({ d, off: sgn * (9.5 + rand() * 1.5), s: 0.8 + rand() * 0.5 });
    }
    const trunkGeo = new THREE.CylinderGeometry(0.16, 0.2, 2.6, 6); trunkGeo.translate(0, 1.3, 0);
    const crownGeo = new THREE.IcosahedronGeometry(1.8, 0); crownGeo.translate(0, 3.9, 0);
    const trunks = new THREE.InstancedMesh(trunkGeo, mats.trunk, tPlaces.length);
    const crowns = new THREE.InstancedMesh(crownGeo, mats.crown, tPlaces.length);
    crowns.castShadow = true; trunks.castShadow = true;
    tPlaces.forEach((t, k) => {
      const p = at(path, t.d), r = rightOf(p.th);
      dummy.position.set(p.x + r.x * t.off, p.y + 0.16, p.z + r.z * t.off);
      dummy.rotation.set(0, rand() * 6.28, 0); dummy.scale.setScalar(t.s); dummy.updateMatrix();
      trunks.setMatrixAt(k, dummy.matrix); crowns.setMatrixAt(k, dummy.matrix);
    });
    add(trunks); add(crowns);

    // street lamps (lit at night)
    const lPlaces = [];
    for (let d = 18; d < path.L - 10; d += 38) for (const sgn of [-1, 1]) {
      const dd = d + (sgn > 0 ? 19 : 0);
      if (!nearJunction(dd, 10)) lPlaces.push({ d: dd, off: sgn * 8.2 });
    }
    const poleGeo = new THREE.CylinderGeometry(0.06, 0.08, 6.5, 6); poleGeo.translate(0, 3.25, 0);
    const headGeo = new THREE.BoxGeometry(0.9, 0.12, 0.35); headGeo.translate(-0.3, 6.5, 0);
    const poles = new THREE.InstancedMesh(poleGeo, mats.pole, lPlaces.length);
    lamps = new THREE.InstancedMesh(headGeo, mats.lamp, lPlaces.length);
    lPlaces.forEach((l, k) => {
      const p = at(path, l.d), r = rightOf(p.th);
      dummy.position.set(p.x + r.x * l.off, p.y + 0.16, p.z + r.z * l.off);
      dummy.rotation.set(0, -p.th + (l.off > 0 ? Math.PI / 2 : -Math.PI / 2), 0); dummy.scale.setScalar(1); dummy.updateMatrix();
      poles.setMatrixAt(k, dummy.matrix); lamps.setMatrixAt(k, dummy.matrix);
    });
    add(poles); add(lamps);

    // bus stops: shelter, bench and an amber stop sign
    const shelterBack = new THREE.BoxGeometry(3.6, 2.3, 0.06);
    const shelterRoof = new THREE.BoxGeometry(3.9, 0.1, 1.7);
    const shelterSide = new THREE.BoxGeometry(0.06, 2.3, 1.5);
    const bench = new THREE.BoxGeometry(2.4, 0.08, 0.45);
    const signPole = new THREE.CylinderGeometry(0.05, 0.05, 3.0, 6);
    stops.forEach((sd, k) => {
      const p = at(path, sd), r = rightOf(p.th);
      const grp = new THREE.Group();
      grp.position.set(p.x + r.x * 6.2, p.y + 0.16, p.z + r.z * 6.2);
      grp.rotation.set(0, -p.th, 0);
      const part = (geo, mat, x, y, z) => { const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); m.castShadow = true; m.receiveShadow = true; grp.add(m); };
      part(shelterBack, mats.shelterGlass, 0, 1.15, 0.8);
      part(shelterRoof, mats.shelter, 0, 2.35, 0.1);
      part(shelterSide, mats.shelterGlass, -1.78, 1.15, 0.05);
      part(bench, mats.shelter, 0, 0.5, 0.55);
      part(signPole, mats.pole, 2.5, 1.5, -0.5);
      const plate = new THREE.Mesh(new THREE.PlaneGeometry(0.95, 0.34),
        new THREE.MeshBasicMaterial({ map: dotTexture(raster, String(k + 1), { minCols: 11, pitch: 8 }), toneMapped: false, side: THREE.DoubleSide }));
      plate.position.set(2.5, 2.8, -0.5); grp.add(plate);
      world.add(grp);
    });

    scene.add(world);
    bus.setSign(dotTexture(raster, meta.sign || profile.name || "", { minCols: 40, pitch: 8 }));
    applyEnv();
    update({ d: 0, v: 0, p_batt: 0, p_motor: 0, p_hvac: 0, doors: 0 }, 0, true);
  }

  /* ---------------------------------------------------------- weather */
  function applyEnv() {
    const cold = env.ambient < 1, hot = env.ambient > 28, night = !!env.night;
    let sky = new THREE.Color("#d8e6f1"), fogNear = 180, fogFar = 1100;
    let sunCol = new THREE.Color("#ffffff"), sunI = 2.1, hemiI = 1.25;
    let ground = "#dfe2da", far = "#d4d8ce", crown = "#b9ccb0";
    if (cold) { sky = new THREE.Color("#e3e8ed"); fogNear = 90; fogFar = 650; ground = "#f2f5f7"; far = "#e9eef1"; crown = "#e4ebe8"; sunI = 1.4; hemiI = 1.45; }
    if (hot) { sky = new THREE.Color("#efe3cc"); sunCol = new THREE.Color("#ffe2b8"); sunI = 2.6; ground = "#e3dccd"; far = "#dcd3c1"; crown = "#c3c89e"; }
    if (night) { sky = new THREE.Color("#0b1320"); fogNear = 60; fogFar = 520; sunCol = new THREE.Color("#8fa7ff"); sunI = 0.25; hemiI = 0.28; ground = cold ? "#8d97a3" : "#3b4046"; far = cold ? "#7d8792" : "#2e3237"; crown = "#56645a"; }
    scene.background = sky;
    scene.fog = new THREE.Fog(sky, fogNear, fogFar);
    sun.color.copy(sunCol); sun.intensity = sunI;
    hemi.intensity = hemiI; hemi.color.set(night ? "#6f7fa8" : "#ffffff");
    hemi.groundColor.set(night ? "#1a1d22" : "#8a8f86");
    mats.ground.color.set(ground); mats.groundFar.color.set(far); mats.crown.color.set(crown);
    mats.kerb.color.set(cold && !night ? "#e6eaec" : night ? "#5b6066" : "#c9cbc6");
    mats.building.color.set(night ? "#7b8189" : "#ffffff");
    mats.lamp.emissiveIntensity = night ? 3.2 : 0;
    bus.headlight.intensity = night ? 90 : 0;
    if (traffic) traffic.setNight(night);
    bus.headMat.emissiveIntensity = night ? 3 : 0.6;
    renderer.toneMappingExposure = night ? 1.25 : 1.02;
    if (cold && !snow) snow = makeSnow();
    if (snow) snow.visible = cold;
  }

  function makeSnow() {
    const N = 2600, pos = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) { pos[i * 3] = (Math.random() - 0.5) * 160; pos[i * 3 + 1] = Math.random() * 70; pos[i * 3 + 2] = (Math.random() - 0.5) * 160; }
    const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const pts = new THREE.Points(g, new THREE.PointsMaterial({ color: 0xffffff, size: 0.28, transparent: true, opacity: 0.9, depthWrite: false }));
    scene.add(pts);
    return pts;
  }

  /* ---------------------------------------------------------- per-frame */
  const tmpV = new THREE.Vector3(), look = new THREE.Vector3(), camTarget = new THREE.Vector3();
  let firstCam = true;

  function update(st, dt, snap = false) {
    if (!path) return;
    // The model's position is the front bumper; the bus mesh is centred, so
    // draw it 6 m back. Otherwise it would nose into queues and overshoot stops.
    const p = at(path, (st.d || 0) - 6.0), r = rightOf(p.th);
    const pos = new THREE.Vector3(p.x + r.x * LANE, p.y + 0.16, p.z + r.z * LANE);
    bus.group.position.copy(pos);
    bus.group.rotation.set(0, -p.th, Math.atan(Math.max(-0.12, Math.min(0.12, p.slope))), "YZX");
    for (const w of bus.wheels) w.rotation.z = -(st.d || 0) / 0.5;

    // doors ease open and shut over about half a second
    const target = st.doors > 0.5 ? 1 : 0;
    bus.doorOpen += (target - bus.doorOpen) * (snap ? 1 : Math.min(1, dt * 5));
    for (const lf of bus.leaves) {
      lf.mesh.position.x = lf.cx + lf.side * (0.31 + 0.5 * bus.doorOpen);
      lf.mesh.position.z = 1.3 + 0.2 * bus.doorOpen;
    }

    // pack glow and energy particles follow the battery and motor power
    const pb = st.p_batt || 0, pm = st.p_motor || 0;
    const regen = pm < -2;
    const col = regen ? GREEN : BLUE;
    bus.glowMat.emissive.copy(col);
    bus.glowMat.emissiveIntensity = Math.min(2.2, Math.abs(pb) / 70);
    bus.flowMat.color.copy(col);
    const flowOn = Math.abs(pm) > 3;
    const rate = (regen ? -1 : 1) * (0.12 + Math.min(1.1, Math.abs(pm) / 160));
    bus.phase = (bus.phase + dt * rate + 10) % 1;
    bus.flow.forEach((m, i) => {
      m.visible = flowOn;
      if (flowOn) m.position.copy(bus.flowCurve.getPointAt(((i / bus.flow.length) + bus.phase) % 1));
    });
    const hvOn = (st.p_hvac || 0) > 1;
    bus.hvacPhase = (bus.hvacPhase + dt * (0.2 + Math.min(0.8, (st.p_hvac || 0) / 20))) % 1;
    bus.hvacFlow.forEach((m, i) => {
      m.visible = hvOn;
      if (hvOn) m.position.copy(bus.hvacCurve.getPointAt(((i / bus.hvacFlow.length) + bus.hvacPhase) % 1));
    });
    bus.tailMat.emissiveIntensity = (regen || (st.accel || 0) < -0.3) ? 3 : 0.5;

    // sun and its shadow camera travel with the bus
    sun.position.set(pos.x + 45, pos.y + 70, pos.z + 30);
    sun.target.position.copy(pos);

    if (snow && snow.visible) {
      const a = snow.geometry.attributes.position;
      for (let i = 0; i < a.count; i++) {
        let y = a.getY(i) - dt * 4.5;
        if (y < 0) y += 70;
        a.setY(i, y);
      }
      a.needsUpdate = true;
      snow.position.set(camera.position.x, Math.max(pos.y - 5, 0), camera.position.z);
    }

    moveCamera(pos, p.th, dt, snap || firstCam);
    firstCam = false;
  }

  function moveCamera(pos, th, dt, snap) {
    const fwd = new THREE.Vector3(Math.cos(th), 0, Math.sin(th)), right = rightOf(th);
    if (camMode === "free") {
      tmpV.subVectors(pos, lastBusPos);
      camera.position.add(tmpV); controls.target.add(tmpV);
      controls.update(); lastBusPos.copy(pos);
      return;
    }
    let want;
    if (camMode === "side") {
      // Past the tree line (lateral 13.5 m) and above the crowns (about 6.3 m).
      want = pos.clone().addScaledVector(right, 11.6).addScaledVector(fwd, 17); want.y += 6.8;
      look.copy(pos).addScaledVector(fwd, -1.5); look.y += 1.5;
    } else if (camMode === "top") {
      want = pos.clone().addScaledVector(fwd, -45); want.y += 240;
      look.copy(pos).addScaledVector(fwd, 20);
    } else {
      // Stay over the carriageway: kerbside lamps (8.2 m) and trees (9.5 m+) would clip.
      want = pos.clone().addScaledVector(fwd, -24).addScaledVector(right, 2); want.y += 9;
      look.copy(pos).addScaledVector(fwd, 9); look.y += 1.4;
    }
    const k = snap ? 1 : 1 - Math.exp(-dt * 3.2);
    camera.position.lerp(want, k);
    camTarget.lerp(look, k);
    if (snap) camTarget.copy(look);
    camera.lookAt(camTarget);
    lastBusPos.copy(pos);
  }

  function setCameraMode(mode) {
    camMode = mode;
    controls.enabled = mode === "free";
    if (mode === "free") { controls.target.copy(bus.group.position); controls.target.y += 1.5; controls.update(); }
  }

  function setEnv(e) { env = { ...env, ...e }; applyEnv(); }

  // Panels float over the canvas. Shift the projection so the bus sits in the
  // middle of the area the panels leave free, not the middle of the window.
  let viewShift = 0;
  const applyView = () => {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    renderer.domElement.style.width = "100%"; renderer.domElement.style.height = "100%";
    camera.aspect = w / h;
    if (viewShift) camera.setViewOffset(w, h, viewShift, 0, w, h); else camera.clearViewOffset();
    camera.updateProjectionMatrix();
  };
  const setViewShift = px => { viewShift = Math.round(px); applyView(); };
  const ro = new ResizeObserver(applyView);
  ro.observe(host);

  function frame(state, dt) {
    update(state, dt);
    if (traffic) traffic.update(state);
    renderer.render(scene, camera);
  }
  function setTraffic(data) { if (traffic) traffic.setData(data); }

  function dispose() {
    ro.disconnect(); controls.dispose(); clearWorld();
    if (traffic) traffic.dispose();
    scene.traverse(o => { if (o.geometry) o.geometry.dispose(); });
    renderer.dispose(); renderer.domElement.remove();
  }

  return { setProfile, setEnv, setCameraMode, setViewShift, setTraffic, frame, dispose, get mode() { return camMode; } };
}

export function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(window.WebGLRenderingContext && (c.getContext("webgl2") || c.getContext("webgl")));
  } catch { return false; }
}
