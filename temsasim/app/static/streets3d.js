/* Street life for the 3D scene: shopfronts on the ground floors, windows on
   the floors above, a paved forecourt between the pavement and the shops, and
   street furniture (benches, bins, café tables).

   Purely decorative and procedural. Shop names are generic trades, never
   brands. Colours stay muted so the bus and its energy remain the most
   saturated things on screen. Everything is instanced, a handful of draw
   calls for the whole route. */

import * as THREE from "three";

const SHOPS = [
  ["FIRIN", "#8a5a3c"], ["ECZANE", "#2f7a5a"], ["KAFE", "#5b4636"], ["MARKET", "#3d5f86"],
  ["KIRTASİYE", "#6a5a8a"], ["BERBER", "#3c4652"], ["LOKANTA", "#8a3f3a"], ["ÇİÇEKÇİ", "#5f7a3e"],
  ["KUAFÖR", "#7a4a6a"], ["OPTİK", "#2f5a6e"], ["TERZİ", "#6e6248"], ["BÜFE", "#9a6a2a"],
  ["KASAP", "#7a3434"], ["MANAV", "#4f7a34"], ["KİTAPÇI", "#4a4a6a"], ["TATLICI", "#9a5a5a"],
];
const AWNINGS = ["#b8664a", "#6f8a5a", "#4f6a8a", "#c49a4a", "#8a5a6a", "#5a7a7a", "#a8a49a"].map(c => new THREE.Color(c));
const FOOD = new Set(["KAFE", "LOKANTA", "FIRIN", "TATLICI"]);
const GROUND = 0.17;        // forecourt top above the road centreline

function signTexture(text, bg) {
  const c = document.createElement("canvas");
  c.width = 768; c.height = 128;
  const ctx = c.getContext("2d");
  ctx.fillStyle = bg; ctx.fillRect(0, 0, 768, 128);
  ctx.strokeStyle = "rgba(255,255,255,.22)"; ctx.lineWidth = 6; ctx.strokeRect(6, 6, 756, 116);
  ctx.fillStyle = "#f6f2ea";
  ctx.font = "600 72px 'Segoe UI', Arial, sans-serif";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(text, 384, 68, 700);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
  return t;
}

/* Windows on the upper floors of the instanced buildings, drawn in the shader
   from each instance's own size so they never stretch. At night a share of
   them light up. */
export function addFacadeWindows(material, night) {
  material.onBeforeCompile = sh => {
    sh.uniforms.uNight = night;
    sh.vertexShader = sh.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vBp;\nvarying vec3 vBn;\nvarying float vBs;")
      .replace("#include <begin_vertex>", `#include <begin_vertex>
#ifdef USE_INSTANCING
  vBp = position * vec3(length(instanceMatrix[0].xyz), length(instanceMatrix[1].xyz), length(instanceMatrix[2].xyz));
  // kept small: the hash below magnifies any interpolation error in a large seed into speckle
  vBs = floor(fract(instanceMatrix[3].x * 0.0131 + instanceMatrix[3].z * 0.0377) * 64.0);
#else
  vBp = position; vBs = 0.0;
#endif
  vBn = normal;`);
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", `#include <common>
varying vec3 vBp;
varying vec3 vBn;
varying float vBs;
uniform float uNight;
float bHash(vec3 p) { return fract(sin(dot(p, vec3(12.9898, 78.233, 37.719))) * 43758.5453); }`)
      .replace("#include <emissivemap_fragment>", `#include <emissivemap_fragment>
  // the ground floor (shops) ends about 4.6 m above the building's sunken base
  if (abs(vBn.y) < 0.5 && vBp.y > 4.8) {
    float u = abs(vBn.x) > 0.5 ? vBp.z : vBp.x;
    float fy = vBp.y - 4.8;
    vec2 cell = vec2(floor(u / 2.6), floor(fy / 3.1));
    vec2 f = vec2(fract(u / 2.6), fract(fy / 3.1));
    float win = step(0.22, f.x) * step(f.x, 0.78) * step(0.22, f.y) * step(f.y, 0.72);
    // whole numbers only: interpolated inputs differ in the last bit per pixel, which the hash turns into noise
    float lit = step(0.5, bHash(vec3(cell, floor(vBs + 0.5) + floor(vBn.x * 3.0 + 0.5) * 7.0 + floor(vBn.z * 5.0 + 0.5) * 3.0)));
    // far away a window is smaller than a pixel: fade to the average instead of shimmering
    float aa = clamp(1.6 - 2.0 * max(fwidth(u) / 2.6, fwidth(fy) / 3.1) * 8.0, 0.0, 1.0);
    win = mix(0.28, win, aa); lit = mix(0.5, lit, aa);
    diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.32, 0.39, 0.46), win * 0.8);
    totalEmissiveRadiance += win * lit * uNight * vec3(1.0, 0.78, 0.48) * 0.4;
  }`);
  };
  material.needsUpdate = true;
}

/* bPlaces: the buildings ({d, off, w, dep}); lPlaces: the street lamps ({d, off}). */
export function buildStreetLife(world, { path, at, rightOf, bPlaces, lPlaces, rand, mats, edge = 7.5 }) {
  const dummy = new THREE.Object3D();
  const unitPlane = new THREE.PlaneGeometry(1, 1);
  const unitBox = new THREE.BoxGeometry(1, 1, 1);

  // one shopfront per bay: 1 bay up to 15 m of frontage, 2 above
  const fronts = [], cafes = [];
  for (const b of bPlaces) {
    const sgn = Math.sign(b.off), L = Math.abs(b.off) - b.dep / 2;
    const nShops = b.w > 15 ? 2 : 1, bay = b.w / nShops;
    const p = at(path, b.d), r = rightOf(p.th), f = new THREE.Vector3(Math.cos(p.th), 0, Math.sin(p.th));
    // forecourt paving from the pavement edge to the shopfront
    fronts.push({ kind: "pave", p, r, f, sgn, L, w: b.w + 1.5 });
    for (let j = 0; j < nShops; j++) {
      const x = -b.w / 2 + bay * (j + 0.5);
      const shop = SHOPS[Math.floor(rand() * SHOPS.length)];
      fronts.push({ kind: "shop", p, r, f, sgn, L, x, bay, shop, awning: AWNINGS[Math.floor(rand() * AWNINGS.length)] });
      if (FOOD.has(shop[0]) && L - 2.2 > edge + 4.1) cafes.push({ p, r, f, sgn, lat: L - 2.2, x });
    }
  }
  const shops = fronts.filter(o => o.kind === "shop"), paves = fronts.filter(o => o.kind === "pave");

  // place helper: a facade frame at shop o, x along the facade, y up, z out toward the road
  const facade = (o, x, y, z) => {
    const lat = o.sgn * (o.L - z);
    dummy.position.set(o.p.x + o.f.x * x + o.r.x * lat, o.p.y + y, o.p.z + o.f.z * x + o.r.z * lat);
    dummy.rotation.set(0, -o.p.th + (o.sgn > 0 ? Math.PI : 0), 0);
  };

  const pave = new THREE.InstancedMesh(unitBox, mats.paving, paves.length);
  pave.receiveShadow = true;
  paves.forEach((o, k) => {
    const lat = o.sgn * (edge + o.L) / 2;
    dummy.position.set(o.p.x + o.r.x * lat, o.p.y + GROUND - 0.2, o.p.z + o.r.z * lat);
    dummy.rotation.set(0, -o.p.th, 0);
    dummy.scale.set(o.w, 0.4, Math.max(0.1, o.L - edge));
    dummy.updateMatrix(); pave.setMatrixAt(k, dummy.matrix);
  });
  world.add(pave);

  // vitrines, the frame around them, awnings, and the sign boards
  const glass = new THREE.InstancedMesh(unitPlane, mats.shopGlass, shops.length);
  const frame = new THREE.InstancedMesh(unitPlane, mats.shopFrame, shops.length);
  const door = new THREE.InstancedMesh(unitPlane, mats.shopFrame, shops.length);
  const awn = new THREE.InstancedMesh(unitBox, mats.awning, shops.length);
  awn.castShadow = true;
  shops.forEach((o, k) => {
    const w = o.bay - 0.7;
    facade(o, o.x, GROUND + 1.85, 0.02); dummy.scale.set(w + 0.3, 3.7, 1); dummy.updateMatrix(); frame.setMatrixAt(k, dummy.matrix);
    facade(o, o.x, GROUND + 1.35, 0.04); dummy.scale.set(w, 2.5, 1); dummy.updateMatrix(); glass.setMatrixAt(k, dummy.matrix);
    facade(o, o.x + w / 2 - 0.75, GROUND + 1.1, 0.05); dummy.scale.set(1.0, 2.2, 1); dummy.updateMatrix(); door.setMatrixAt(k, dummy.matrix);
    facade(o, o.x, GROUND + 2.92, 0.7); dummy.rotation.set(0.3, dummy.rotation.y, 0, "YXZ"); dummy.scale.set(w, 0.06, 1.4); dummy.updateMatrix(); awn.setMatrixAt(k, dummy.matrix);
    awn.setColorAt(k, o.awning);
    dummy.rotation.order = "XYZ";
  });
  world.add(frame, glass, door, awn);

  const byName = new Map();
  shops.forEach(o => { if (!byName.has(o.shop[0])) byName.set(o.shop[0], []); byName.get(o.shop[0]).push(o); });
  for (const [name, list] of byName) {
    const bg = list[0].shop[1];
    const m = new THREE.MeshBasicMaterial({ map: signTexture(name, bg), toneMapped: false });
    const sign = new THREE.InstancedMesh(unitPlane, m, list.length);
    list.forEach((o, k) => {
      facade(o, o.x, GROUND + 3.55, 0.06); dummy.scale.set(Math.min(o.bay - 1.0, 4.2), 0.7, 1); dummy.updateMatrix(); sign.setMatrixAt(k, dummy.matrix);
    });
    world.add(sign);
  }

  // café tables with parasols in front of the food shops
  const tables = [];
  for (const c of cafes) for (const dx of [-1.6, 1.6]) tables.push({ ...c, x: c.x + dx });
  const tableGeo = new THREE.CylinderGeometry(0.4, 0.4, 0.05, 12); tableGeo.translate(0, 0.75, 0);
  const legGeo = new THREE.CylinderGeometry(0.04, 0.04, 2.3, 6); legGeo.translate(0, 1.15, 0);
  const paraGeo = new THREE.ConeGeometry(1.25, 0.45, 8, 1, true); paraGeo.translate(0, 2.35, 0);
  const chairGeo = new THREE.BoxGeometry(0.4, 0.45, 0.4); chairGeo.translate(0, 0.22, 0);
  const tMesh = new THREE.InstancedMesh(tableGeo, mats.furniture, tables.length);
  const lMesh = new THREE.InstancedMesh(legGeo, mats.pole, tables.length);
  const pMesh = new THREE.InstancedMesh(paraGeo, mats.awning, tables.length);
  const cMesh = new THREE.InstancedMesh(chairGeo, mats.furniture, tables.length * 2);
  pMesh.castShadow = true;
  tables.forEach((t, k) => {
    const lat = t.sgn * t.lat;
    const pos = new THREE.Vector3(t.p.x + t.f.x * t.x + t.r.x * lat, t.p.y + GROUND, t.p.z + t.f.z * t.x + t.r.z * lat);
    dummy.position.copy(pos); dummy.rotation.set(0, -t.p.th, 0); dummy.scale.setScalar(1); dummy.updateMatrix();
    tMesh.setMatrixAt(k, dummy.matrix); lMesh.setMatrixAt(k, dummy.matrix); pMesh.setMatrixAt(k, dummy.matrix);
    pMesh.setColorAt(k, AWNINGS[k % AWNINGS.length]);
    for (const s of [0, 1]) {
      dummy.position.copy(pos).addScaledVector(t.f, s ? 0.7 : -0.7); dummy.updateMatrix(); cMesh.setMatrixAt(k * 2 + s, dummy.matrix);
    }
  });
  world.add(tMesh, lMesh, pMesh, cMesh);

  // a bin by every lamp, and a bench every other lamp, on the pavement's outer edge
  const binGeo = new THREE.CylinderGeometry(0.24, 0.21, 0.85, 10); binGeo.translate(0, 0.42, 0);
  const seatGeo = new THREE.BoxGeometry(1.7, 0.08, 0.45); seatGeo.translate(0, 0.46, 0);
  const backGeo = new THREE.BoxGeometry(1.7, 0.42, 0.06); backGeo.translate(0, 0.72, 0.21);
  const bins = new THREE.InstancedMesh(binGeo, mats.bin, lPlaces.length);
  const benchPlaces = lPlaces.filter((_, k) => k % 2 === 0);
  const seats = new THREE.InstancedMesh(seatGeo, mats.furniture, benchPlaces.length);
  const backs = new THREE.InstancedMesh(backGeo, mats.furniture, benchPlaces.length);
  for (const m of [bins, seats, backs]) m.castShadow = true;
  lPlaces.forEach((l, k) => {
    const p = at(path, l.d + 1.1), r = rightOf(p.th), lat = Math.sign(l.off) * (edge + 0.4);
    dummy.position.set(p.x + r.x * lat, p.y + 0.16, p.z + r.z * lat); dummy.rotation.set(0, 0, 0); dummy.scale.setScalar(1); dummy.updateMatrix();
    bins.setMatrixAt(k, dummy.matrix);
  });
  benchPlaces.forEach((l, k) => {
    const p = at(path, l.d + 9), r = rightOf(p.th), lat = Math.sign(l.off) * (edge + 1.1);
    dummy.position.set(p.x + r.x * lat, p.y + 0.16, p.z + r.z * lat);
    dummy.rotation.set(0, -p.th + (l.off > 0 ? 0 : Math.PI), 0); dummy.updateMatrix();
    seats.setMatrixAt(k, dummy.matrix); backs.setMatrixAt(k, dummy.matrix);
  });
  world.add(bins, seats, backs);
}
