/* Free-drive stage: a wide straight boulevard whose grade and surface come
   from the road editor, and the bus driven from the keyboard.

   Six lanes and a painted median, so there is room to weave, overtake an
   imaginary queue or swing round in a U-turn. Each section of road wears its
   own surface (new asphalt to gravel), and a roadside board at every change
   says what is coming. Shops, trees and lamps line both sides as on the route
   roads. Purely visual: drivephys.js moves the bus, this file only draws it. */

import * as THREE from "three";
import { OrbitControls } from "/static/vendor/three/OrbitControls.js";
import { buildBus, animateBus, dotTexture } from "/static/sim3d.js";
import { createXray } from "/static/xray3d.js";
import { createDoorAir } from "/static/doorair3d.js";
import { addFacadeWindows, buildStreetLife } from "/static/streets3d.js";
import { ROAD_HALF, EDGE, LANES, LANE_W, MEDIAN } from "/static/drivephys.js";

const at = (road, d) => {
  const y = road.elev(d), g = road.grade(d);
  return { x: d, y, z: 0, th: 0, slope: g / 100 };
};
const rightOf = () => new THREE.Vector3(0, 0, 1);

/* ---------------------------------------------------------------- surfaces */
function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

function surfaceTexture(key) {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const x = c.getContext("2d"), r = rng(key.length * 977 + key.charCodeAt(0));
  const speck = (base, spread, n, size = 2) => {
    x.fillStyle = base; x.fillRect(0, 0, 256, 256);
    for (let i = 0; i < n; i++) {
      const k = (r() - 0.5) * spread;
      x.fillStyle = `rgba(${k > 0 ? 255 : 0},${k > 0 ? 255 : 0},${k > 0 ? 255 : 0},${Math.abs(k)})`;
      x.fillRect(r() * 256, r() * 256, size, size);
    }
  };
  if (key === "yeni_asfalt") speck("#24282c", 0.12, 2500, 1.5);
  else if (key === "asfalt") speck("#33383d", 0.16, 3000);
  else if (key === "beton") {
    speck("#a3a5a1", 0.1, 1800);
    x.strokeStyle = "rgba(60,62,60,.55)"; x.lineWidth = 2;
    x.beginPath(); x.moveTo(0, 1); x.lineTo(256, 1); x.moveTo(1, 0); x.lineTo(1, 256); x.stroke();
  } else if (key === "eski_asfalt") {
    speck("#4a4e52", 0.2, 3500);
    for (let i = 0; i < 4; i++) {                                   // patches
      x.fillStyle = `rgba(20,22,24,${0.35 + r() * 0.2})`;
      x.fillRect(r() * 200, r() * 200, 30 + r() * 60, 20 + r() * 50);
    }
    x.strokeStyle = "rgba(15,15,15,.7)"; x.lineWidth = 1.5;     // cracks
    for (let i = 0; i < 7; i++) {
      let px = r() * 256, py = r() * 256;
      x.beginPath(); x.moveTo(px, py);
      for (let j = 0; j < 6; j++) { px += (r() - 0.5) * 50; py += (r() - 0.5) * 50; x.lineTo(px, py); }
      x.stroke();
    }
  } else if (key === "parke") {
    x.fillStyle = "#5d5850"; x.fillRect(0, 0, 256, 256);
    const bw = 32, bh = 16;
    for (let row = 0; row < 256 / bh; row++) for (let col = -1; col < 256 / bw + 1; col++) {
      const ox = (row % 2) * bw / 2, v = 120 + r() * 30;
      x.fillStyle = `rgb(${v},${v - 6},${v - 14})`;
      x.fillRect(col * bw + ox + 1, row * bh + 1, bw - 2, bh - 2);
    }
  } else if (key === "arnavut") {
    x.fillStyle = "#3e3a35"; x.fillRect(0, 0, 256, 256);
    for (let gy = 0; gy < 256; gy += 21) for (let gx = 0; gx < 256; gx += 21) {
      const v = 95 + r() * 55, rx = 8 + r() * 3, ry = 7 + r() * 3;
      x.fillStyle = `rgb(${v},${v - 8},${v - 18})`;
      x.beginPath(); x.ellipse(gx + 10 + (r() - 0.5) * 4, gy + 10 + (r() - 0.5) * 4, rx, ry, r() * 3, 0, Math.PI * 2); x.fill();
    }
  } else {                                                          // stabilize
    speck("#9c8c70", 0.35, 6000, 2.5);
    for (let i = 0; i < 25; i++) { x.fillStyle = "rgba(70,60,45,.35)"; x.beginPath(); x.arc(r() * 256, r() * 256, 3 + r() * 9, 0, 7); x.fill(); }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace; t.wrapS = t.wrapT = THREE.RepeatWrapping; t.anisotropy = 8;
  return t;
}
const TILE = { yeni_asfalt: 4, asfalt: 4, beton: 5, eski_asfalt: 6, parke: 2.4, arnavut: 2.2, stabilize: 4 };
const MARKED = new Set(["yeni_asfalt", "asfalt", "beton", "eski_asfalt"]);

/* A strip of road between d0 and d1 and laterals l0..l1, following the elevation. */
function strip(road, d0, d1, l0, l1, y, mat, tile = 0) {
  const pos = [], uv = [], idx = [];
  const n = Math.max(1, Math.ceil((d1 - d0) / road.step));
  for (let k = 0; k <= n; k++) {
    const d = d0 + (d1 - d0) * k / n, e = road.elev(d) + y;
    pos.push(d, e, l0, d, e, l1);
    if (tile) uv.push(d / tile, l0 / tile, d / tile, l1 / tile);
    if (k < n) { const a = k * 2; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  if (tile) g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx); g.computeVertexNormals();
  const m = new THREE.Mesh(g, mat); m.receiveShadow = true;
  return m;
}

function boardTexture(lines, bg = "#1d4f8c") {
  const c = document.createElement("canvas"); c.width = 512; c.height = 256;
  const x = c.getContext("2d");
  x.fillStyle = bg; x.fillRect(0, 0, 512, 256);
  x.strokeStyle = "#f2f2f2"; x.lineWidth = 10; x.strokeRect(10, 10, 492, 236);
  x.fillStyle = "#ffffff"; x.textAlign = "center"; x.textBaseline = "middle";
  x.font = "700 92px 'Segoe UI', Arial, sans-serif"; x.fillText(lines[0], 256, 98, 470);
  x.font = "600 48px 'Segoe UI', Arial, sans-serif"; x.fillText(lines[1] || "", 256, 190, 470);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
  return t;
}

/* ---------------------------------------------------------------- stage */
export function createDriveStage(host, { raster } = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  renderer.shadowMap.enabled = true;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(48, 1, 0.3, 6000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enabled = false; controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.49; controls.minDistance = 6; controls.maxDistance = 600;

  const hemi = new THREE.HemisphereLight(0xffffff, 0x8a8f86, 1.25);
  const sun = new THREE.DirectionalLight(0xffffff, 2.1);
  sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -60, right: 60, top: 60, bottom: -60, near: 1, far: 400 });
  sun.shadow.bias = -0.0005; sun.shadow.normalBias = 0.02;
  scene.add(hemi, sun, sun.target);

  const bus = buildBus(raster);
  scene.add(bus.group);
  const xray = createXray(bus, host);
  bus.setSign(dotTexture(raster, "SERBEST SÜRÜŞ", { minCols: 40, pitch: 8 }));
  // reversing lamps beside the tail lights, lit in R
  const revMat = new THREE.MeshStandardMaterial({ color: 0xf4f4f0, emissive: 0xffffff, emissiveIntensity: 0 });
  for (const z of [-0.86, 0.86]) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.16, 0.42), revMat);
    m.position.set(-6.12, 1.08, z); bus.group.add(m);
  }
  const doorAir = createDoorAir(bus, bus.doorX, host);

  const mats = {
    line: new THREE.MeshStandardMaterial({ color: 0xeceae2, roughness: 0.8 }),
    kerb: new THREE.MeshStandardMaterial({ color: 0xc9cbc6, roughness: 0.9 }),
    ground: new THREE.MeshStandardMaterial({ color: 0xdfe2da, roughness: 1 }),
    groundFar: new THREE.MeshStandardMaterial({ color: 0xd6d9d0, roughness: 1 }),
    building: new THREE.MeshStandardMaterial({ roughness: 0.9 }),
    trunk: new THREE.MeshStandardMaterial({ color: 0x9a9184, roughness: 1 }),
    crown: new THREE.MeshStandardMaterial({ color: 0xb9ccb0, roughness: 0.95, flatShading: true }),
    pole: new THREE.MeshStandardMaterial({ color: 0x8c9196, roughness: 0.5, metalness: 0.4 }),
    lamp: new THREE.MeshStandardMaterial({ color: 0xfff1c8, emissive: 0xffd88a, emissiveIntensity: 0 }),
    paving: new THREE.MeshStandardMaterial({ color: 0xd8d4cb, roughness: 0.95 }),
    shopGlass: new THREE.MeshStandardMaterial({ color: 0x3a4a56, roughness: 0.15, metalness: 0.3, emissive: 0xffd59a, emissiveIntensity: 0 }),
    shopFrame: new THREE.MeshStandardMaterial({ color: 0x3b4148, roughness: 0.6 }),
    awning: new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.85, side: THREE.DoubleSide }),
    furniture: new THREE.MeshStandardMaterial({ color: 0x6b6258, roughness: 0.8 }),
    bin: new THREE.MeshStandardMaterial({ color: 0x4d5a52, roughness: 0.6, metalness: 0.2 }),
    hatch: new THREE.MeshStandardMaterial({ color: 0xd9d6cc, roughness: 0.85 }),
    barrier: new THREE.MeshStandardMaterial({ color: 0xd23b2f, roughness: 0.6 }),
    white: new THREE.MeshStandardMaterial({ color: 0xf2f2f0, roughness: 0.6 }),
  };
  const nightU = { value: 0 };
  addFacadeWindows(mats.building, nightU);
  const surfMats = {};
  const surfMat = key => surfMats[key] || (surfMats[key] = new THREE.MeshStandardMaterial({ map: surfaceTexture(key), roughness: 0.92 }));

  let world = null, road = null, env = { ambient: 20, night: false };
  let camMode = "chase", firstCam = true;

  function clearWorld() {
    if (!world) return;
    scene.remove(world);
    world.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material && o.material.map && !Object.values(surfMats).includes(o.material)) o.material.map.dispose(); });
    world = null;
  }

  function setRoad(r) {
    clearWorld();
    road = r;
    world = new THREE.Group();
    const add = m => { world.add(m); return m; };
    const L = road.L;

    // carriageway, one strip per section in its own surface
    road.segs.forEach((s, k) => {
      const d0 = road.starts[k], d1 = d0 + s.len;
      add(strip(road, d0, d1, -ROAD_HALF, ROAD_HALF, 0.02, surfMat(s.surface), TILE[s.surface] || 4));
      if (!MARKED.has(s.surface)) return;
      // lane dashes, edge lines and the painted median: one geometry per section
      const pos = [], idx = [];
      const quad = (a, b, l0, l1) => {
        const ea = road.elev(a) + 0.035, eb = road.elev(b) + 0.035, n = pos.length / 3;
        pos.push(a, ea, l0, a, ea, l1, b, eb, l0, b, eb, l1); idx.push(n, n + 1, n + 2, n + 1, n + 3, n + 2);
      };
      const edge = ROAD_HALF - 0.45, med = MEDIAN / 2;
      for (let a = d0; a < d1 - 0.5; a += 10) {
        const b = Math.min(d1, a + 10);
        for (const sg of [-1, 1]) {
          quad(a, b, sg * edge - 0.08, sg * edge + 0.08);
          quad(a, b, sg * med - 0.08, sg * med + 0.08);
          for (let ln = 1; ln < LANES; ln++) { const o = sg * (med + ln * LANE_W); if (b - a > 4) quad(a, a + 4, o - 0.07, o + 0.07); }
        }
        quad(a + 1, a + 2.2, -med + 0.1, med - 0.1);                 // median hatching
        quad(a + 6, a + 7.2, -med + 0.1, med - 0.1);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3)); g.setIndex(idx); g.computeVertexNormals();
      const m = new THREE.Mesh(g, mats.line); m.receiveShadow = true; add(m);
    });

    // pavements, verges, ground falling away
    for (const sg of [-1, 1]) {
      const a = sg > 0 ? ROAD_HALF : -EDGE, b = sg > 0 ? EDGE : -ROAD_HALF;
      add(strip(road, -40, L + 40, a, b, 0.16, mats.kerb));
      const c = sg > 0 ? EDGE : -70, dd = sg > 0 ? 70 : -EDGE;
      add(strip(road, -40, L + 40, c, dd, 0.15, mats.ground));
    }
    add(strip(road, -40, 0, -ROAD_HALF, ROAD_HALF, 0.02, surfMat(road.segs[0].surface), 4));
    add(strip(road, L, L + 40, -ROAD_HALF, ROAD_HALF, 0.02, surfMat(road.segs[road.segs.length - 1].surface), 4));
    const plane = new THREE.Mesh(new THREE.PlaneGeometry(20000, 20000), mats.groundFar);
    plane.rotation.x = -Math.PI / 2; plane.position.y = road.emin - 1.5; plane.receiveShadow = true;
    add(plane);

    // end barriers across the whole road
    for (const d of [4, L - 4]) {
      const e = road.elev(d);
      for (let l = -ROAD_HALF + 0.6; l < ROAD_HALF; l += 2.4) {
        const b = new THREE.Mesh(new THREE.BoxGeometry(0.3, 1.0, 2.0), (Math.round(l / 2.4) % 2) ? mats.barrier : mats.white);
        b.position.set(d, e + 0.5, l + 1.0); b.castShadow = true; add(b);
      }
    }

    // buildings with shops, trees, lamps: the same city as on the route roads
    const rand = rng(4242 + Math.round(L));
    const bPlaces = [];
    for (const sg of [-1, 1]) for (let d = 15 + rand() * 15; d < L - 15; d += 24 + rand() * 18) {
      if (rand() < 0.1) continue;
      const dep = 9 + rand() * 10;
      bPlaces.push({ d, off: sg * (EDGE + 3 + rand() * 7 + dep / 2), w: 10 + rand() * 12, dep, h: 6 + Math.pow(rand(), 1.6) * 34 });
    }
    const bGeo = new THREE.BoxGeometry(1, 1, 1); bGeo.translate(0, 0.5, 0);
    const bMesh = new THREE.InstancedMesh(bGeo, mats.building, bPlaces.length);
    bMesh.castShadow = true; bMesh.receiveShadow = true;
    const palette = ["#ebe8e1", "#e2ded5", "#d7dadd", "#e6e1d8", "#dcdfe1", "#efece6"].map(c => new THREE.Color(c));
    const dummy = new THREE.Object3D();
    bPlaces.forEach((b, k) => {
      dummy.position.set(b.d, road.elev(b.d) - 0.8, b.off); dummy.rotation.set(0, 0, 0);
      dummy.scale.set(b.w, b.h, b.dep); dummy.updateMatrix(); bMesh.setMatrixAt(k, dummy.matrix);
      bMesh.setColorAt(k, palette[k % palette.length]);
    });
    add(bMesh);
    const tPlaces = [];
    for (const sg of [-1, 1]) for (let d = 12; d < L - 8; d += 14 + rand() * 6) tPlaces.push({ d, off: sg * (EDGE - 1.3) });
    const trunkGeo = new THREE.CylinderGeometry(0.16, 0.2, 2.6, 6); trunkGeo.translate(0, 1.3, 0);
    const crownGeo = new THREE.IcosahedronGeometry(1.8, 0); crownGeo.translate(0, 3.9, 0);
    const trunks = new THREE.InstancedMesh(trunkGeo, mats.trunk, tPlaces.length);
    const crowns = new THREE.InstancedMesh(crownGeo, mats.crown, tPlaces.length);
    crowns.castShadow = true;
    tPlaces.forEach((t, k) => {
      dummy.position.set(t.d, road.elev(t.d) + 0.16, t.off); dummy.rotation.set(0, rand() * 6.28, 0);
      dummy.scale.setScalar(0.85 + rand() * 0.4); dummy.updateMatrix();
      trunks.setMatrixAt(k, dummy.matrix); crowns.setMatrixAt(k, dummy.matrix);
    });
    add(trunks); add(crowns);
    const lPlaces = [];
    for (let d = 20; d < L - 10; d += 36) for (const sg of [-1, 1]) lPlaces.push({ d: d + (sg > 0 ? 18 : 0), off: sg * (ROAD_HALF + 0.7) });
    const poleGeo = new THREE.CylinderGeometry(0.07, 0.09, 8.5, 6); poleGeo.translate(0, 4.25, 0);
    const headGeo = new THREE.BoxGeometry(2.2, 0.14, 0.4); headGeo.translate(-1.0, 8.5, 0);
    const poles = new THREE.InstancedMesh(poleGeo, mats.pole, lPlaces.length);
    const lamps = new THREE.InstancedMesh(headGeo, mats.lamp, lPlaces.length);
    lPlaces.forEach((l, k) => {
      dummy.position.set(l.d, road.elev(l.d) + 0.16, l.off);
      dummy.rotation.set(0, l.off > 0 ? Math.PI / 2 : -Math.PI / 2, 0); dummy.scale.setScalar(1); dummy.updateMatrix();
      poles.setMatrixAt(k, dummy.matrix); lamps.setMatrixAt(k, dummy.matrix);
    });
    add(poles); add(lamps);
    buildStreetLife(world, { path: road, at, rightOf, bPlaces, lPlaces, rand, mats, edge: EDGE });

    // a board at every change of grade or surface, on both sides
    road.segs.forEach((s, k) => {
      const prev = road.segs[k - 1];
      if (prev && prev.grade === s.grade && prev.surface === s.surface) return;
      const lab = (road.surfaceLabel && road.surfaceLabel(s.surface)) || s.surface;
      const g = s.grade === 0 ? "DÜZ" : `%${Math.abs(s.grade).toLocaleString("tr-TR")} ${s.grade > 0 ? "YOKUŞ" : "İNİŞ"}`;
      const tex = boardTexture([g, lab], s.grade > 0 ? "#8c4a1d" : s.grade < 0 ? "#1d5a8c" : "#2f5f45");
      for (const [dd, off, rot] of [[road.starts[k] - 25, ROAD_HALF + 1.6, -Math.PI / 2], [road.starts[k] + 25, -(ROAD_HALF + 1.6), Math.PI / 2]]) {
        if (dd < 2 || dd > L - 2) continue;
        const e = road.elev(dd) + 0.16;
        const post = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 3.2, 6), mats.pole);
        post.position.set(dd, e + 1.6, off); add(post);
        const board = new THREE.Mesh(new THREE.PlaneGeometry(2.4, 1.2), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, side: THREE.DoubleSide }));
        board.position.set(dd, e + 3.4, off); board.rotation.y = rot; add(board);
      }
    });

    scene.add(world);
    applyEnv();
    firstCam = true;
  }

  /* ---------------------------------------------------------- weather */
  function applyEnv() {
    const cold = env.ambient < 1, hot = env.ambient > 28, night = !!env.night;
    let sky = new THREE.Color("#d8e6f1"), sunI = 2.1, hemiI = 1.25, ground = "#dfe2da", far = "#d4d8ce";
    if (cold) { sky = new THREE.Color("#e3e8ed"); ground = "#f2f5f7"; far = "#e9eef1"; sunI = 1.4; hemiI = 1.45; }
    if (hot) { sky = new THREE.Color("#efe3cc"); sunI = 2.6; ground = "#e3dccd"; far = "#dcd3c1"; }
    if (night) { sky = new THREE.Color("#0b1320"); sunI = 0.25; hemiI = 0.3; ground = "#3b4046"; far = "#2e3237"; }
    scene.background = sky; scene.fog = new THREE.Fog(sky, night ? 80 : 220, night ? 600 : 1400);
    sun.intensity = sunI; hemi.intensity = hemiI;
    hemi.color.set(night ? "#6f7fa8" : "#ffffff"); hemi.groundColor.set(night ? "#1a1d22" : "#8a8f86");
    mats.ground.color.set(ground); mats.groundFar.color.set(far);
    mats.building.color.set(night ? "#7b8189" : "#ffffff");
    mats.kerb.color.set(night ? "#5b6066" : cold ? "#e6eaec" : "#c9cbc6");
    mats.lamp.emissiveIntensity = night ? 3.2 : 0;
    mats.shopGlass.emissiveIntensity = night ? 0.5 : 0;
    nightU.value = night ? 1 : 0;
    bus.headMat.emissiveIntensity = night ? 3 : 0.6;
    bus.headlight.intensity = night ? 120 : 0;
    renderer.toneMappingExposure = night ? 1.25 : 1.02;
  }
  function setEnv(e) { env = { ...env, ...e }; applyEnv(); }

  /* ---------------------------------------------------------- per frame */
  const look = new THREE.Vector3(), camTarget = new THREE.Vector3(), lastBus = new THREE.Vector3(), tmp = new THREE.Vector3();
  let spin = 0;
  function frame(D, live, dt) {
    if (!road) return;
    const e = road.elev(D.s), slope = road.grade(D.s) / 100 * Math.cos(D.psi);
    bus.group.position.set(D.s, e + 0.02, D.lat);
    bus.group.rotation.set(0, -D.psi, Math.atan(slope), "YZX");
    spin += D.v * dt / 0.5;
    const steerAng = D.steer * 0.55 / (1 + D.v / 7);
    bus.wheels.forEach((w, i) => w.rotation.set(0, i < 2 ? -steerAng : 0, -spin, "YZX"));
    animateBus(bus, live, dt);
    revMat.emissiveIntensity = D.dir < 0 ? 3 : 0;
    const pxScale = renderer.domElement.height / (2 * Math.tan(camera.fov * Math.PI / 360));
    doorAir.update(dt, bus.doorOpen, env.ambient, live.cabin ?? env.ambient, pxScale, camera);

    const pos = bus.group.position;
    sun.position.set(pos.x + 45, pos.y + 70, pos.z + 30); sun.target.position.copy(pos);
    moveCamera(pos, D.psi, dt);
    xray.update(live, env, camera);
    renderer.render(scene, camera);
  }

  function moveCamera(pos, psi, dt) {
    const fwd = new THREE.Vector3(Math.cos(psi), 0, Math.sin(psi)), right = new THREE.Vector3(-Math.sin(psi), 0, Math.cos(psi));
    if (camMode === "free") {
      tmp.subVectors(pos, lastBus); camera.position.add(tmp); controls.target.add(tmp);
      controls.update(); lastBus.copy(pos); return;
    }
    let want, k;
    if (camMode === "cockpit") {
      // the driver's eye height, just ahead of the windscreen so the glass does not block the view
      want = pos.clone().addScaledVector(fwd, 6.35).addScaledVector(right, -0.6); want.y += 2.3;
      look.copy(want).addScaledVector(fwd, 30); look.y -= 1.6;
      camera.position.copy(want); camTarget.copy(look); camera.lookAt(camTarget); lastBus.copy(pos); return;
    }
    if (camMode === "side") { want = pos.clone().addScaledVector(right, 8.5).addScaledVector(fwd, 7); want.y += 3.6; look.copy(pos); look.y += 1.4; }   // inside the tree line
    else if (camMode === "top") { want = pos.clone().addScaledVector(fwd, -30); want.y += 160; look.copy(pos).addScaledVector(fwd, 25); }
    else { want = pos.clone().addScaledVector(fwd, -22); want.y += 8; look.copy(pos).addScaledVector(fwd, 10); look.y += 1.4; }
    k = firstCam ? 1 : 1 - Math.exp(-dt * 3.5);
    camera.position.lerp(want, k); camTarget.lerp(look, k);
    if (firstCam) camTarget.copy(look);
    firstCam = false;
    camera.lookAt(camTarget); lastBus.copy(pos);
  }
  function setCameraMode(m) {
    camMode = m; controls.enabled = m === "free";
    if (m === "free") {
      // start from a three-quarter view behind the bus, then the mouse takes over
      const p = bus.group.position, yaw = -bus.group.rotation.y;
      camera.position.set(p.x - 20 * Math.cos(yaw) - 10 * Math.sin(yaw), p.y + 9, p.z - 20 * Math.sin(yaw) + 10 * Math.cos(yaw));
      controls.target.copy(p); controls.target.y += 1.5; controls.update(); lastBus.copy(p);
    }
    firstCam = m !== "free";
  }

  const applyView = () => {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    renderer.domElement.style.width = "100%"; renderer.domElement.style.height = "100%";
    camera.aspect = w / h; camera.updateProjectionMatrix();
  };
  const ro = new ResizeObserver(applyView); ro.observe(host);

  function dispose() {
    ro.disconnect(); controls.dispose(); clearWorld(); xray.dispose(); doorAir.dispose();
    scene.traverse(o => { if (o.geometry) o.geometry.dispose(); });
    renderer.dispose(); renderer.domElement.remove();
  }

  return { setRoad, setEnv, frame, setCameraMode, setXray: m => xray.setMode(m), setSign: tex => bus.setSign(tex), dispose,
           get mode() { return camMode; } };
}
