/* The city-bus mission drawn: streets that follow the hill, rounded kerbs,
   lane and stop markings, zebra crossings, blocks of shops and flats, trees
   and lamps, signal heads that change with citymap's timing, bus stops with
   people waiting, the line's route painted along its lane, a beacon over the
   next stop, and the traffic.

   Everything is built in world x/z and then draped onto the terrain, so the
   road, kerbs and markings always sit on the same ground. */

import * as THREE from "three";
import { addFacadeWindows, buildStreetLife } from "/static/streets3d.js";
import { dotTexture } from "/static/sim3d.js";
import { B, N, H, WALK, R, SPAN, DIRS, STOPLINE, LANE_OUT } from "/static/citymap.js";

const CAR_PALETTE = ["#cfd2d6", "#8e959c", "#4b535b", "#e8e5de", "#9fb0bc", "#b9a894", "#6f8276", "#a3a6aa", "#5d6f82", "#8a3f3a", "#2f5a6e"].map(c => new THREE.Color(c));
const CLOTHES = ["#5d6f82", "#8e959c", "#b9a894", "#6f8276", "#3f4750", "#a87e6a", "#d9d4ca", "#7a6a88", "#9b4f4f", "#4f6f9b"].map(c => new THREE.Color(c));
const inRange = (i, j) => i >= 0 && j >= 0 && i < N && j < N;
const rightV = d => [-DIRS[d][1], DIRS[d][0]];

export function buildCity3d(city, { mats, surfMat, raster }) {
  const group = new THREE.Group();
  const hgt = city.height;
  const add = m => { group.add(m); return m; };

  /* ---------------- geometry helpers ---------------- */
  // a strip between two paired polylines [[x,z],...], draped at offset y
  function strip(a, b, y, mat, { uvTile = 0 } = {}) {
    const pos = [], uv = [], idx = [];
    for (let k = 0; k < a.length; k++) {
      pos.push(a[k][0], hgt(a[k][0], a[k][1]) + y, a[k][1], b[k][0], hgt(b[k][0], b[k][1]) + y, b[k][1]);
      if (uvTile) uv.push(a[k][0] / uvTile, a[k][1] / uvTile, b[k][0] / uvTile, b[k][1] / uvTile);
      if (k < a.length - 1) { const n = k * 2; idx.push(n, n + 1, n + 2, n + 1, n + 3, n + 2); }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    if (uvTile) g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
    g.setIndex(idx); g.computeVertexNormals();
    // keep the faces pointing up whichever way the polylines run
    const nrm = g.attributes.normal;
    if (nrm.count && nrm.getY(0) < 0) { for (let k = 0; k < idx.length; k += 3) [idx[k + 1], idx[k + 2]] = [idx[k + 2], idx[k + 1]]; g.setIndex(idx); g.computeVertexNormals(); }
    const m = new THREE.Mesh(g, mat); m.receiveShadow = true;
    return m;
  }
  const line = (p, q, n) => Array.from({ length: n + 1 }, (_, k) => [p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n]);
  const off = (pts, v, k) => pts.map(p => [p[0] + v[0] * k, p[1] + v[1] * k]);
  // a batch of flat quads (markings) in one geometry: each quad is 4 [x,z] corners
  function quads(list, y, mat) {
    const pos = [], idx = [];
    for (const q of list) {
      const n = pos.length / 3;
      for (const [x, z] of q) pos.push(x, hgt(x, z) + y, z);
      idx.push(n, n + 2, n + 1, n, n + 3, n + 2);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3)); g.setIndex(idx); g.computeVertexNormals();
    const m = new THREE.Mesh(g, mat); m.receiveShadow = true;
    return m;
  }
  // a rectangle along direction d: from u0 to u1 along, l0 to l1 to the right, measured from point p
  const rect = (p, d, u0, u1, l0, l1) => {
    const f = DIRS[d], r = rightV(d), P = (u, l) => [p[0] + f[0] * u + r[0] * l, p[1] + f[1] * u + r[1] * l];
    return [P(u0, l0), P(u1, l0), P(u1, l1), P(u0, l1)];
  };

  /* ---------------- ground ---------------- */
  {
    const g = new THREE.PlaneGeometry(SPAN + 500, SPAN + 500, 80, 80);
    g.rotateX(-Math.PI / 2); g.translate(SPAN / 2, 0, SPAN / 2);
    const p = g.attributes.position;
    for (let k = 0; k < p.count; k++) p.setY(k, hgt(p.getX(k), p.getZ(k)) - 0.05);
    g.computeVertexNormals();
    const m = new THREE.Mesh(g, mats.ground); m.receiveShadow = true; add(m);
  }

  /* ---------------- streets: carriageway, corners, pavements ---------------- */
  const nodeP = (i, j) => [i * B, j * B];
  const legsEW = [], legsNS = [];
  for (let j = 0; j < N; j++) for (let i = 0; i < N - 1; i++) legsEW.push([i, j, 0]);
  for (let i = 0; i < N; i++) for (let j = 0; j < N - 1; j++) legsNS.push([i, j, 1]);
  for (const [i, j, d] of [...legsEW, ...legsNS]) {
    const a = nodeP(i, j), b = nodeP(i + DIRS[d][0], j + DIRS[d][1]), r = rightV(d);
    const c = line(a, b, Math.ceil(B / 4));
    add(strip(off(c, r, -H), off(c, r, H), 0.02, surfMat(city.legSurface(i, j, d)), { uvTile: 4 }));
  }
  for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
    const p = nodeP(i, j);
    add(strip(line([p[0] - H, p[1] - H], [p[0] + H, p[1] - H], 4), line([p[0] - H, p[1] + H], [p[0] + H, p[1] + H], 4), 0.021, surfMat("asfalt"), { uvTile: 4 }));
    // street ends at the edge of town: a pavement cap
    for (let d = 0; d < 4; d++) {
      if (inRange(i + DIRS[d][0], j + DIRS[d][1]) || !inRange(i - DIRS[d][0], j - DIRS[d][1])) continue;
      const q = rect(p, d, H, H + WALK, -H - WALK, H + WALK);
      add(strip([q[0], q[1]], [q[3], q[2]], 0.16, mats.kerb));
    }
    // rounded corners: asphalt fan out to the kerb arc, pavement ring behind it
    for (const [sx, sz] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      if (!inRange(i + sx, j) || !inRange(i, j + sz)) continue;
      const cx = p[0] + sx * (H + R), cz = p[1] + sz * (H + R), corner = [p[0] + sx * H, p[1] + sz * H];
      // the kerb arc, tangent to both street edges, on the side facing the junction
      const arc = rr => Array.from({ length: 9 }, (_, k) => {
        const t = Math.PI / 2 * k / 8;
        return [cx - sx * rr * Math.cos(t), cz - sz * rr * Math.sin(t)];
      });
      const outer = arc(R);
      add(strip(outer.map(() => corner), outer, 0.021, surfMat("asfalt")));
      add(strip(arc(R), arc(R - WALK), 0.16, mats.kerb));
    }
  }
  // straight pavements between the corners
  for (const [i, j, d] of [...legsEW, ...legsNS]) {
    const a = nodeP(i, j), b = nodeP(i + DIRS[d][0], j + DIRS[d][1]), f = DIRS[d];
    for (const side of [-1, 1]) {
      const r = rightV(d).map(v => v * side);
      const perpAt = (ni, nj) => inRange(ni + r[0], nj + r[1]);         // a street leaves this node on this side
      // stop at the corner arcs; run on through a junction with no street on this side; close the dead ends
      const s0 = perpAt(i, j) ? H + R : inRange(i - f[0], j - f[1]) ? 0 : -H;
      const s1 = perpAt(i + f[0], j + f[1]) ? H + R : inRange(i + 2 * f[0], j + 2 * f[1]) ? 0 : -H;
      const p0 = [a[0] + f[0] * s0, a[1] + f[1] * s0], p1 = [b[0] - f[0] * s1, b[1] - f[1] * s1];
      const c = line(p0, p1, Math.max(1, Math.ceil((B - s0 - s1) / 6)));
      add(strip(off(c, r, H), off(c, r, H + WALK), 0.16, mats.kerb));
      // kerb face so the step reads
      add(strip(off(c, r, H), off(c, r, H + 0.02), 0.0, mats.kerb));
    }
  }

  /* ---------------- markings ---------------- */
  const marks = [], yellow = [];
  for (const [i, j, d0] of [...legsEW, ...legsNS]) for (const d of [d0, (d0 + 2) % 4]) {
    const from = d === d0 ? nodeP(i, j) : nodeP(i + DIRS[d0][0], j + DIRS[d0][1]);
    const u0 = STOPLINE, u1 = B - STOPLINE;
    // centre line (one half each way), lane dashes, edge line
    marks.push(rect(from, d, u0, u1, 0.06, 0.18));
    for (let u = u0 + 2; u < u1 - 3; u += 9) marks.push(rect(from, d, u, u + 3, 3.58, 3.72));
    marks.push(rect(from, d, u0, u1, H - 0.38, H - 0.24));
    // stop line before the junction, zebra beyond it
    const ni = (d === d0 ? i + DIRS[d0][0] : i), nj = (d === d0 ? j + DIRS[d0][1] : j);
    if (city.signalised(ni, nj)) marks.push(rect(from, d, u1 - 0.45, u1, 0.2, H - 0.3));
    for (let l = -H + 0.6; l < H - 0.4; l += 1.1) marks.push(rect(from, d, u1 + 0.8, u1 + 3.8, l, l + 0.55));
  }
  add(quads(marks, 0.035, mats.line));
  // bus stop boxes on the outer lane
  const stopMat = new THREE.MeshStandardMaterial({ color: 0xf2b705, roughness: 0.7 });
  for (const s of city.route.stops) {
    const from = nodeP(s.i, s.j);
    for (const [a, b, l0, l1] of [[s.u - 9, s.u + 9, 3.7, 3.9], [s.u - 9, s.u + 9, H - 0.6, H - 0.4], [s.u - 9, s.u - 8.8, 3.7, H - 0.4], [s.u + 8.8, s.u + 9, 3.7, H - 0.4]])
      yellow.push(rect(from, s.d, a, b, l0, l1));
    for (let k = -8; k < 8; k += 2) yellow.push(rect(from, s.d, s.u + k, s.u + k + 0.25, 3.9, H - 0.6));
  }
  add(quads(yellow, 0.04, stopMat));

  /* ---------------- the line's route along its lane ---------------- */
  const ribbonMat = new THREE.MeshBasicMaterial({ color: 0xffb000, transparent: true, opacity: 0.32, depthWrite: false });
  {
    const P = city.route.path, a = [], b = [];
    for (let k = 0; k < P.length; k++) {
      const q = P[Math.min(k + 1, P.length - 1)], p = P[Math.max(0, k - 1)];
      const hx = q[0] - p[0], hz = q[1] - p[1], L = Math.hypot(hx, hz) || 1, rx = -hz / L, rz = hx / L;
      a.push([P[k][0] - rx * 0.35, P[k][1] - rz * 0.35]); b.push([P[k][0] + rx * 0.35, P[k][1] + rz * 0.35]);
    }
    const m = strip(a, b, 0.05, ribbonMat); m.renderOrder = 1; add(m);
  }

  /* ---------------- blocks: flats over shops on the east-west streets ---------------- */
  const rand = (s => () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296))(9001);
  const bPlaces = [], lPlaces = [], blds = [];
  const legPaths = [];
  for (const [i, j] of legsEW) {
    const k = legPaths.length;
    legPaths.push({ x0: i * B, z0: j * B });
    for (const side of [-1, 1]) {
      // which side has a block: not beyond the edge of town
      if (!inRange(i, j + side) && !(j + side === -1 || j + side === N)) continue;
      let u = 14 + rand() * 2;
      while (u < B - 24) {
        const w = Math.min(B - 14 - u, 13 + rand() * 13);
        if (w < 9) break;
        const dep = 22 + rand() * 26, ht = 9 + Math.pow(rand(), 1.4) * (j === 2 || j === 3 ? 34 : 22);
        bPlaces.push({ d: k * 1000 + u + w / 2, off: side * (14 + dep / 2), w, dep, h: ht });
        blds.push({ x: i * B + u + w / 2, z: j * B + side * (14 + dep / 2), w, dep, h: ht });
        u += w + 0.6;
      }
    }
    for (let u = 22; u < B - 22; u += 34) for (const side of [-1, 1]) lPlaces.push({ d: k * 1000 + u + (side > 0 ? 17 : 0), off: side * (H + 0.7) });
  }
  // the street-life helper walks a "path": here every east-west leg, keyed by d = leg * 1000 + metres
  const at = (_, d) => { const k = Math.floor(d / 1000), u = d - k * 1000, L = legPaths[Math.min(k, legPaths.length - 1)];
    const x = L.x0 + u, z = L.z0; return { x, y: hgt(x, z), z, th: 0, slope: 0 }; };
  const rightOf = th => new THREE.Vector3(-Math.sin(th), 0, Math.cos(th));
  const bGeo = new THREE.BoxGeometry(1, 1, 1); bGeo.translate(0, 0.5, 0);
  const bMesh = new THREE.InstancedMesh(bGeo, mats.building, blds.length);
  bMesh.castShadow = true; bMesh.receiveShadow = true;
  const palette = ["#ebe8e1", "#e2ded5", "#d7dadd", "#e6e1d8", "#dcdfe1", "#efece6", "#e9ddcf", "#d9e0e3"].map(c => new THREE.Color(c));
  const dummy = new THREE.Object3D();
  blds.forEach((b, k) => {
    dummy.position.set(b.x, Math.min(hgt(b.x - b.w / 2, b.z), hgt(b.x + b.w / 2, b.z)) - 1.2, b.z); dummy.rotation.set(0, 0, 0);
    dummy.scale.set(b.w, b.h + 1.2, b.dep); dummy.updateMatrix(); bMesh.setMatrixAt(k, dummy.matrix);
    bMesh.setColorAt(k, palette[k % palette.length]);
  });
  add(bMesh);
  buildStreetLife(group, { path: null, at, rightOf, bPlaces, lPlaces, rand, mats, edge: H + WALK });

  // trees along the north-south streets (their sides are the blocks' gable walls), lamps everywhere
  const trees = [], lamps = [];
  for (const [i, j] of legsNS) for (let u = H + R + 6; u < B - H - R - 4; u += 16) for (const side of [-1, 1]) {
    if (!inRange(i + side, j) && i + side !== -1 && i + side !== N) continue;
    trees.push([i * B + side * (H + WALK - 1.2), j * B + u]);
  }
  for (const [i, j] of legsNS) for (let u = 24; u < B - 20; u += 36) for (const side of [-1, 1]) lamps.push([i * B + side * (H + 0.7), j * B + u + (side > 0 ? 18 : 0), side > 0 ? Math.PI : 0]);
  for (const l of lPlaces) { const p = at(null, l.d); lamps.push([p.x, p.z + l.off, l.off > 0 ? Math.PI / 2 : -Math.PI / 2]); }
  {
    const trunkGeo = new THREE.CylinderGeometry(0.16, 0.2, 2.6, 6); trunkGeo.translate(0, 1.3, 0);
    const crownGeo = new THREE.IcosahedronGeometry(1.8, 0); crownGeo.translate(0, 3.9, 0);
    const tr = new THREE.InstancedMesh(trunkGeo, mats.trunk, trees.length), cr = new THREE.InstancedMesh(crownGeo, mats.crown, trees.length);
    cr.castShadow = true;
    trees.forEach(([x, z], k) => { dummy.position.set(x, hgt(x, z) + 0.16, z); dummy.rotation.set(0, rand() * 6, 0); dummy.scale.setScalar(0.85 + rand() * 0.4); dummy.updateMatrix(); tr.setMatrixAt(k, dummy.matrix); cr.setMatrixAt(k, dummy.matrix); });
    add(tr); add(cr);
    const poleGeo = new THREE.CylinderGeometry(0.07, 0.09, 7.5, 6); poleGeo.translate(0, 3.75, 0);
    const headGeo = new THREE.BoxGeometry(1.8, 0.14, 0.4); headGeo.translate(-0.8, 7.5, 0);
    const pl = new THREE.InstancedMesh(poleGeo, mats.pole, lamps.length), hd = new THREE.InstancedMesh(headGeo, mats.lamp, lamps.length);
    lamps.forEach(([x, z, rot], k) => { dummy.position.set(x, hgt(x, z) + 0.16, z); dummy.rotation.set(0, rot, 0); dummy.scale.setScalar(1); dummy.updateMatrix(); pl.setMatrixAt(k, dummy.matrix); hd.setMatrixAt(k, dummy.matrix); });
    add(pl); add(hd);
  }

  /* ---------------- signals: one head per approach, lamps recoloured every frame ---------------- */
  const heads = [];
  for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
    if (!city.signalised(i, j)) continue;
    for (let d = 0; d < 4; d++) {
      if (!inRange(i - DIRS[d][0], j - DIRS[d][1])) continue;     // nobody approaches from beyond the edge
      const r = rightV(d), p = nodeP(i, j);
      // on the pavement just behind the corner arc, before the junction on the right
      const x = p[0] - DIRS[d][0] * (H + R - 1) + r[0] * (H + 1.0), z = p[1] - DIRS[d][1] * (H + R - 1) + r[1] * (H + 1.0);
      heads.push({ i, j, axis: DIRS[d][1] === 0 ? "h" : "v", x, z, y: hgt(x, z) + 0.16, face: -Math.atan2(-DIRS[d][1], -DIRS[d][0]) });
    }
  }
  const sigPoleGeo = new THREE.CylinderGeometry(0.07, 0.09, 3.6, 8); sigPoleGeo.translate(0, 1.8, 0);
  const boxGeo = new THREE.BoxGeometry(0.36, 1.05, 0.38); boxGeo.translate(0, 4.1, 0);
  const sp = new THREE.InstancedMesh(sigPoleGeo, mats.pole, heads.length), sb = new THREE.InstancedMesh(boxGeo, new THREE.MeshStandardMaterial({ color: 0x23282d, roughness: 0.6 }), heads.length);
  const lampGeo = new THREE.SphereGeometry(0.12, 10, 8);
  const lampMat = new THREE.MeshBasicMaterial({ toneMapped: false });
  const sl = new THREE.InstancedMesh(lampGeo, lampMat, heads.length * 3);
  const ON = { red: new THREE.Color(3, 0.5, 0.35), amber: new THREE.Color(3, 1.9, 0.2), green: new THREE.Color(0.4, 2.6, 1.0) };
  const OFF = { red: new THREE.Color(0.22, 0.06, 0.06), amber: new THREE.Color(0.22, 0.16, 0.04), green: new THREE.Color(0.05, 0.16, 0.09) };
  heads.forEach((hd, k) => {
    dummy.position.set(hd.x, hd.y, hd.z); dummy.rotation.set(0, hd.face, 0); dummy.scale.setScalar(1); dummy.updateMatrix();
    sp.setMatrixAt(k, dummy.matrix); sb.setMatrixAt(k, dummy.matrix);
    ["red", "amber", "green"].forEach((col, n) => {
      const o = new THREE.Object3D(); o.position.set(0.2, 4.44 - n * 0.34, 0);
      o.updateMatrix(); sl.setMatrixAt(k * 3 + n, new THREE.Matrix4().multiplyMatrices(dummy.matrix, o.matrix));
      sl.setColorAt(k * 3 + n, OFF[col]);
    });
  });
  sp.castShadow = true; add(sp); add(sb); add(sl);

  /* ---------------- stops: shelter, pole with the line number, people ---------------- */
  const shelterMat = new THREE.MeshStandardMaterial({ color: 0xf4f4f1, roughness: 0.6 });
  const glassMat = new THREE.MeshStandardMaterial({ color: 0xa8c2d6, roughness: 0.1, metalness: 0.3, transparent: true, opacity: 0.45 });
  const plateMat = new THREE.MeshBasicMaterial({ map: dotTexture(raster, city.route.code, { minCols: 12, pitch: 8 }), toneMapped: false, side: THREE.DoubleSide });
  const stopSpots = city.route.stops.map(s => {
    const p = nodeP(s.i, s.j), f = DIRS[s.d], r = rightV(s.d);
    const x = p[0] + f[0] * s.u + r[0] * (H + 2.4), z = p[1] + f[1] * s.u + r[1] * (H + 2.4);
    const g = new THREE.Group(); g.position.set(x, hgt(x, z) + 0.16, z); g.rotation.y = -Math.atan2(f[1], f[0]);
    const part = (geo, mat, px, py, pz) => { const m = new THREE.Mesh(geo, mat); m.position.set(px, py, pz); m.castShadow = true; g.add(m); };
    part(new THREE.BoxGeometry(4.2, 2.3, 0.06), glassMat, 0, 1.15, 0.9);
    part(new THREE.BoxGeometry(4.5, 0.1, 1.8), shelterMat, 0, 2.35, 0.2);
    part(new THREE.BoxGeometry(2.6, 0.08, 0.45), shelterMat, 0, 0.5, 0.6);
    part(new THREE.CylinderGeometry(0.05, 0.05, 3.0, 6), mats.pole, 3.0, 1.5, -0.6);
    const plate = new THREE.Mesh(new THREE.PlaneGeometry(1.0, 0.36), plateMat); plate.position.set(3.0, 2.8, -0.6); plate.rotation.y = Math.PI / 2; g.add(plate);
    add(g);
    return { s, x, z, f, r };
  });

  // people: small instanced figures; waiting crowds and those who just got off
  const MAXP = 160;
  const torsoGeo = new THREE.CapsuleGeometry(0.18, 0.5, 3, 8); torsoGeo.translate(0, 1.12, 0);
  const headGeo2 = new THREE.SphereGeometry(0.12, 10, 8); headGeo2.translate(0, 1.66, 0);
  const pT = new THREE.InstancedMesh(torsoGeo, new THREE.MeshStandardMaterial({ roughness: 0.85 }), MAXP);
  const pH = new THREE.InstancedMesh(headGeo2, new THREE.MeshStandardMaterial({ color: 0xd9b394, roughness: 0.8 }), MAXP);
  pT.setColorAt(0, CLOTHES[0]);
  for (const m of [pT, pH]) { m.castShadow = true; m.count = 0; m.frustumCulled = false; add(m); }

  // the beacon over the next stop
  const beacon = new THREE.Group();
  const beamMat = new THREE.MeshBasicMaterial({ color: 0xffb000, transparent: true, opacity: 0.22, depthWrite: false });
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(1.4, 1.4, 26, 20, 1, true), beamMat); beam.position.y = 13; beacon.add(beam);
  const chev = new THREE.Mesh(new THREE.ConeGeometry(1.1, 1.8, 4), new THREE.MeshBasicMaterial({ color: 0xffb000, toneMapped: false }));
  chev.rotation.x = Math.PI; chev.position.y = 8; beacon.add(chev);
  add(beacon);

  /* ---------------- cars ---------------- */
  const MAXC = 80;
  const cBody = new THREE.BoxGeometry(4.3, 0.72, 1.78); cBody.translate(0, 0.62, 0);
  const cCab = new THREE.BoxGeometry(2.3, 0.6, 1.6); cCab.translate(-0.25, 1.28, 0);
  const cTail = new THREE.BoxGeometry(0.05, 0.14, 1.5); cTail.translate(-2.17, 0.8, 0);
  const cHead = new THREE.BoxGeometry(0.05, 0.14, 1.4); cHead.translate(2.17, 0.72, 0);
  const tailMat = new THREE.MeshStandardMaterial({ color: 0x6b1414, emissive: 0xff2a1a, emissiveIntensity: 0.3 });
  const headMat = new THREE.MeshStandardMaterial({ color: 0xf2efe6, emissive: 0xfff1cc, emissiveIntensity: 0.2 });
  const cars = [new THREE.InstancedMesh(cBody, new THREE.MeshStandardMaterial({ roughness: 0.55, metalness: 0.1 }), MAXC),
                new THREE.InstancedMesh(cCab, new THREE.MeshStandardMaterial({ color: 0x2c3a46, roughness: 0.2, metalness: 0.3 }), MAXC),
                new THREE.InstancedMesh(cTail, tailMat, MAXC), new THREE.InstancedMesh(cHead, headMat, MAXC)];
  for (let k = 0; k < MAXC; k++) cars[0].setColorAt(k, CAR_PALETTE[(k * 7 + 3) % CAR_PALETTE.length]);
  for (const m of cars) { m.castShadow = true; m.count = 0; m.frustumCulled = false; add(m); }

  /* ---------------- per frame ---------------- */
  let tAcc = 0;
  function update({ t, mission, night }, dt) {
    tAcc += dt;
    // lights
    heads.forEach((hd, k) => {
      const st = city.light(hd.i, hd.j, hd.axis, t);
      ["red", "amber", "green"].forEach((col, n) => sl.setColorAt(k * 3 + n, col === st ? ON[col] : OFF[col]));
    });
    sl.instanceColor.needsUpdate = true;
    // cars
    city.cars.forEach((c, k) => {
      dummy.position.set(c.x, hgt(c.x, c.z) + 0.02, c.z); dummy.rotation.set(0, -c.h, 0); dummy.scale.setScalar(1); dummy.updateMatrix();
      for (const m of cars) m.setMatrixAt(k, dummy.matrix);
    });
    for (const m of cars) { m.count = city.cars.length; m.instanceMatrix.needsUpdate = true; }
    tailMat.emissiveIntensity = night ? 2.2 : 0.3; headMat.emissiveIntensity = night ? 2.5 : 0.2;
    // people at the stops: those still waiting, and those who just got off
    let n = 0;
    const put = (x, z, yaw, seed) => {
      if (n >= MAXP) return;
      dummy.position.set(x, hgt(x, z) + 0.16, z); dummy.rotation.set(0, yaw, 0); dummy.scale.setScalar(0.92 + (seed % 5) * 0.03); dummy.updateMatrix();
      pT.setMatrixAt(n, dummy.matrix); pH.setMatrixAt(n, dummy.matrix); pT.setColorAt(n, CLOTHES[seed % CLOTHES.length]); n++;
    };
    stopSpots.forEach(({ s, x, z, f, r }, k) => {
      const ms = mission ? mission.stops[k] : null;
      const b = mission && mission.next === k && mission.boarding ? mission.boarding : null;
      const waiting = !ms ? 3 : ms.state === "todo" ? ms.board : ms.state === "serving" && b ? b.board - b.dB : 0;
      for (let p = 0; p < waiting; p++) {
        const a = (p % 4) - 1.5, row = Math.floor(p / 4);
        put(x - f[0] * a * 0.9 - r[0] * (0.9 + row * 0.7), z - f[1] * a * 0.9 - r[1] * (0.9 + row * 0.7), -Math.atan2(-r[1], -r[0]), k * 31 + p);
      }
      if (ms && (ms.state === "served" || ms.state === "serving") && ms.alighted) {
        const since = tAcc - ms.alightedAt;
        if (since < 25) for (let p = 0; p < ms.alighted; p++) {
          const walk = Math.min(since * 1.2, 18) * (p % 2 ? 1 : -1);
          put(x + f[0] * (walk - 6 + p * 0.7) + r[0] * 0.2, z + f[1] * (walk - 6 + p * 0.7) + r[1] * 0.2, -Math.atan2(f[1] * (p % 2 ? 1 : -1), f[0] * (p % 2 ? 1 : -1)), k * 57 + p);
        }
      }
      if (ms && b && ms.alighted !== b.dA) { ms.alighted = b.dA; ms.alightedAt = tAcc; }
    });
    pT.count = pH.count = n;
    pT.instanceMatrix.needsUpdate = pH.instanceMatrix.needsUpdate = true;
    if (pT.instanceColor) pT.instanceColor.needsUpdate = true;
    // beacon over the next stop
    const nx = mission && !mission.done ? stopSpots[mission.next] : null;
    beacon.visible = !!nx;
    if (nx) {
      const s = nx.s, p = nodeP(s.i, s.j), f = DIRS[s.d], r = rightV(s.d);
      const x = p[0] + f[0] * s.u + r[0] * LANE_OUT, z = p[1] + f[1] * s.u + r[1] * LANE_OUT;
      beacon.position.set(x, hgt(x, z), z);
      chev.position.y = 7.5 + Math.sin(tAcc * 2.4) * 0.6; chev.rotation.y += dt * 1.5;
      beamMat.opacity = 0.16 + 0.08 * Math.sin(tAcc * 3);
    }
  }

  return { group, update };
}
