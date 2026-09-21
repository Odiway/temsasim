/* Traffic in the 3D scene: junctions, signal heads, and the other vehicles.

   What the bus experiences is taken verbatim from the model's record
   (play.traffic): every red-light queue has exactly the number of cars the
   model counted, and they pull away on the model's own timing, so the bus
   leaves right behind the last of them. Stop-and-go platoons move with the bus
   because they are the reason it stops. Signal colours come from traffic.js,
   the tested mirror of the Python timing.

   The oncoming lane and the cross streets are ambient -- they do not affect
   the bus -- but they obey the same lights, so no car ever crosses on red and
   no cross traffic is left in the junction when the bus gets green. */

import * as THREE from "three";
import { lightState, redWindow } from "/static/traffic.js";

const CAR_L = 4.3, BUS_HALF = 6.0;
const PALETTE = ["#cfd2d6", "#8e959c", "#4b535b", "#e8e5de", "#9fb0bc", "#b9a894", "#6f8276", "#a3a6aa", "#5d6f82"].map(c => new THREE.Color(c));
const MAX_CARS = 220;

export function createTraffic(scene, { at, rightOf, mats }) {
  const group = new THREE.Group();
  scene.add(group);
  let path = null, signals = [], data = null, level = "yok";

  /* ---------------- car instancing: body, cabin, head and tail light bars ---------------- */
  const bodyGeo = new THREE.BoxGeometry(CAR_L, 0.72, 1.78); bodyGeo.translate(0, 0.62, 0);
  const cabGeo = new THREE.BoxGeometry(2.3, 0.6, 1.6); cabGeo.translate(-0.25, 1.28, 0);
  const headGeo = new THREE.BoxGeometry(0.05, 0.14, 1.4); headGeo.translate(CAR_L / 2 + 0.02, 0.72, 0);
  const tailGeo = new THREE.BoxGeometry(0.05, 0.14, 1.5); tailGeo.translate(-CAR_L / 2 - 0.02, 0.8, 0);
  const bodyMat = new THREE.MeshStandardMaterial({ roughness: 0.55, metalness: 0.1 });
  const cabMat = new THREE.MeshStandardMaterial({ color: 0x2c3a46, roughness: 0.2, metalness: 0.3 });
  const headMat = new THREE.MeshStandardMaterial({ color: 0xf2efe6, emissive: 0xfff1cc, emissiveIntensity: 0.2 });
  const tailMat = new THREE.MeshStandardMaterial({ color: 0x6b1414, emissive: 0xff2a1a, emissiveIntensity: 0.3 });
  const bodies = new THREE.InstancedMesh(bodyGeo, bodyMat, MAX_CARS);
  const cabs = new THREE.InstancedMesh(cabGeo, cabMat, MAX_CARS);
  const heads = new THREE.InstancedMesh(headGeo, headMat, MAX_CARS);
  const tails = new THREE.InstancedMesh(tailGeo, tailMat, MAX_CARS);
  for (const m of [bodies, cabs]) { m.castShadow = true; m.receiveShadow = true; }
  for (let i = 0; i < MAX_CARS; i++) bodies.setColorAt(i, PALETTE[(i * 7 + 3) % PALETTE.length]);
  for (const m of [bodies, cabs, heads, tails]) { m.count = 0; m.frustumCulled = false; group.add(m); }
  const dummy = new THREE.Object3D();
  let nCars = 0;
  const putCar = (pos, yaw, pitch, colourSeed) => {
    if (nCars >= MAX_CARS) return;
    dummy.position.copy(pos); dummy.rotation.set(0, yaw, pitch, "YZX"); dummy.scale.setScalar(1); dummy.updateMatrix();
    for (const m of [bodies, cabs, heads, tails]) m.setMatrixAt(nCars, dummy.matrix);
    bodies.setColorAt(nCars, PALETTE[Math.abs(colourSeed | 0) % PALETTE.length]);
    nCars++;
  };
  // a car on the main road: d is its centre, lateral + is right of the bus's heading
  const onRoad = (d, lateral, reverse, seed) => {
    const p = at(path, d), r = rightOf(p.th);
    const pos = new THREE.Vector3(p.x + r.x * lateral, p.y + 0.02, p.z + r.z * lateral);
    const pitch = Math.atan(Math.max(-0.12, Math.min(0.12, p.slope))) * (reverse ? -1 : 1);
    putCar(pos, -p.th + (reverse ? Math.PI : 0), pitch, seed);
  };
  // a car on a cross street at signal g: p along the cross street (+ = right of the bus), lane offset along the main road
  const onCross = (g, p, laneFwd, dir, seed) => {
    const c = at(path, g.d), r = rightOf(c.th), f = new THREE.Vector3(Math.cos(c.th), 0, Math.sin(c.th));
    const pos = new THREE.Vector3(c.x + r.x * p + f.x * laneFwd, c.y + 0.02, c.z + r.z * p + f.z * laneFwd);
    const yaw = -(c.th + (dir > 0 ? Math.PI / 2 : -Math.PI / 2));
    putCar(pos, yaw, 0, seed);
  };

  /* ---------------- junction furniture ---------------- */
  let furniture = null, heads3 = [];
  const lampMat = {
    red: [new THREE.MeshStandardMaterial({ color: 0x3a1010, roughness: 0.4 }), new THREE.MeshStandardMaterial({ color: 0xff3b2f, emissive: 0xff2a1a, emissiveIntensity: 2.4, toneMapped: false })],
    amber: [new THREE.MeshStandardMaterial({ color: 0x3a2a08, roughness: 0.4 }), new THREE.MeshStandardMaterial({ color: 0xffb000, emissive: 0xffa000, emissiveIntensity: 2.4, toneMapped: false })],
    green: [new THREE.MeshStandardMaterial({ color: 0x0c2a16, roughness: 0.4 }), new THREE.MeshStandardMaterial({ color: 0x2fd27a, emissive: 0x1fcf6a, emissiveIntensity: 2.4, toneMapped: false })],
  };
  const headBoxMat = new THREE.MeshStandardMaterial({ color: 0x23282d, roughness: 0.6 });

  function quads(items, y) {
    // items: [d0, d1, lat0, lat1]; one geometry for all of them
    const pos = [], idx = [];
    for (const [d0, d1, l0, l1] of items) {
      const a = at(path, d0), b = at(path, d1), ra = rightOf(a.th), rb = rightOf(b.th);
      const k = pos.length / 3;
      pos.push(a.x + ra.x * l0, a.y + y, a.z + ra.z * l0, a.x + ra.x * l1, a.y + y, a.z + ra.z * l1,
               b.x + rb.x * l0, b.y + y, b.z + rb.z * l0, b.x + rb.x * l1, b.y + y, b.z + rb.z * l1);
      idx.push(k, k + 1, k + 2, k + 1, k + 3, k + 2);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3)); g.setIndex(idx); g.computeVertexNormals();
    const m = new THREE.Mesh(g, mats.line); m.receiveShadow = true;
    return m;
  }

  function buildJunctions() {
    if (furniture) { group.remove(furniture); furniture.traverse(o => o.geometry && o.geometry.dispose()); }
    furniture = new THREE.Group(); heads3 = [];
    if (!signals.length) { group.add(furniture); return; }
    const marks = [];
    for (const g of signals) {
      marks.push([g.d - 0.45, g.d - 0.05, 0.15, 3.7]);                       // stop line, bus direction
      marks.push([g.d + 8.05, g.d + 8.45, -3.7, -0.15]);                     // stop line, oncoming
      for (let k = -3.4; k < 3.5; k += 1.0) marks.push([g.d + 1.6, g.d + 6.4, k, k + 0.5]);   // zebra
    }
    furniture.add(quads(marks, 0.04));
    const crossGeo = new THREE.PlaneGeometry(170, 8.2);
    const poleGeo = new THREE.CylinderGeometry(0.07, 0.09, 3.4, 8); poleGeo.translate(0, 1.7, 0);
    const boxGeo = new THREE.BoxGeometry(0.34, 1.02, 0.36);
    const lampGeo = new THREE.SphereGeometry(0.11, 12, 8);
    for (const g of signals) {
      const c = at(path, g.d + 4.2), r = rightOf(c.th);
      const street = new THREE.Mesh(crossGeo, mats.road);
      street.rotation.set(-Math.PI / 2, 0, -c.th + Math.PI / 2);
      street.position.set(c.x, c.y + 0.015, c.z); street.receiveShadow = true;
      furniture.add(street);
      // one head per direction, facing the traffic it controls
      for (const [dd, lat, face] of [[g.d - 0.8, 4.5, 0], [g.d + 8.8, -4.5, Math.PI]]) {
        const p = at(path, dd), rr = rightOf(p.th);
        const base = new THREE.Vector3(p.x + rr.x * lat, p.y + 0.16, p.z + rr.z * lat);
        const pole = new THREE.Mesh(poleGeo, mats.pole); pole.position.copy(base); pole.castShadow = true; furniture.add(pole);
        const head = new THREE.Group(); head.position.set(base.x, base.y + 3.9, base.z); head.rotation.y = -p.th + face;
        const box = new THREE.Mesh(boxGeo, headBoxMat); box.castShadow = true; head.add(box);
        const lamps = {};
        [["red", 0.3], ["amber", 0], ["green", -0.3]].forEach(([col, y]) => {
          const l = new THREE.Mesh(lampGeo, lampMat[col][0]); l.position.set(-0.19, y, 0); head.add(l); lamps[col] = l;
        });
        furniture.add(head);
        heads3.push({ g, lamps, state: null });
      }
    }
    group.add(furniture);
  }

  /* ---------------- oncoming lane: a small car-following model ---------------- */
  let onc = [], oncT = null;
  function resetOncoming(t) {
    const perKm = (data && data.oncoming_per_km) || 0;
    const n = Math.min(140, Math.round(perKm * path.L / 1000));
    onc = [];
    for (let i = 0; i < n; i++) onc.push({ u: (i + 0.37 * ((i * 7919) % 11) / 11) * path.L / Math.max(n, 1), v: 8, seed: i * 5 + 1 });
    oncT = t;
  }
  function stepOncoming(t) {
    if (!onc.length) return;
    let dt = t - oncT;
    if (dt < 0 || dt > 40) { resetOncoming(t); dt = 0; }
    oncT = t;
    const vFree = 13.9 * ({ akici: 0.95, orta: 0.8, yogun: 0.6, sikisik: 0.42 }[level] || 0.9);
    const sub = Math.max(1, Math.ceil(dt / 0.5)), h = dt / sub;
    for (let k = 0; k < sub; k++) {
      const tk = t - dt + (k + 1) * h;
      onc.sort((a, b) => a.u - b.u);                          // index i-1 is the car ahead (they drive toward smaller u)
      for (let i = 0; i < onc.length; i++) {
        const c = onc[i];
        let want = vFree;
        if (i > 0) want = Math.min(want, Math.max(0, (c.u - onc[i - 1].u - CAR_L - 2.5) / 1.1));
        // the next junction in their direction; their stop line is on its far side
        for (const g of signals) {
          const line = g.d + 8.6, dist = c.u - CAR_L / 2 - line;
          if (dist < -0.5 || dist > 90) continue;
          const st = lightState(g, tk);
          if (st === "red" || (st === "amber" && dist > c.v * c.v / 6)) want = Math.min(want, Math.sqrt(2 * 2.5 * Math.max(0, dist - 0.3)));
        }
        c.v = Math.max(0, c.v + Math.max(-3 * h, Math.min(1.8 * h, want - c.v)));
        c.u -= c.v * h;
        if (c.u < -20) { c.u += path.L + 40; c.v = vFree; }
      }
    }
  }

  /* ---------------- per frame ---------------- */
  let leader = null, lastT = null;
  function update(st) {
    if (!path) return;
    nCars = 0;
    const t = st.t ?? performance.now() / 1000, busFront = st.d || 0;
    let dts = lastT == null ? 0 : t - lastT;
    if (dts < 0 || dts > 30) { dts = 0; leader = null; }        // a seek, not motion
    lastT = t;

    // signal heads: only those near the bus change material
    for (const h of heads3) {
      if (Math.abs(h.g.d - busFront) > 700) continue;
      const s = data && data.signals_active === false ? "green" : lightState(h.g, t);
      if (s !== h.state) {
        for (const col of ["red", "amber", "green"]) h.lamps[col].material = lampMat[col][col === s ? 1 : 0];
        h.state = s;
      }
    }
    if (!data || data.traffic === "yok") { flush(); return; }

    // 1) red-light queues, exactly as the model counted them
    let busLaneBusy = false;
    for (const e of data.signal_stops || []) {
      if (busFront < e.d_signal - 260 || busFront > e.d_signal + 5) continue;
      if (t < e.t0 - 120 || t > e.t_go + 25) continue;          // another lap's visit
      const tg = e.t_green;
      for (let k = 0; k < e.queue; k++) {
        const rest = e.d_signal - 1.0 - k * data.queue_spacing_m - CAR_L / 2;
        const td = tg + data.reaction_s + data.discharge_s_per_car * k;
        const tau = Math.max(0, t - td);
        const move = tau < 6 ? 0.9 * tau * tau : 32.4 + 10.8 * (tau - 6);   // 1.8 m/s^2 up to 10.8 m/s
        if (move > 160) continue;
        onRoad(rest + move, 1.9, false, k + 13 * e.i);
      }
      busLaneBusy = true;
    }
    // 2) stop-and-go platoon: the cars the bus is stuck behind move with it
    for (const j of data.jams || []) {
      if (t < j.t0 - 25 || t > j.t1 + 25) continue;
      const inside = t <= j.t1;
      const ref = inside ? Math.max(busFront, j.d) : busFront;
      const away = inside ? 0 : 0.75 * (t - j.t1) * (t - j.t1);
      for (let k = 0; k < 5; k++) onRoad(ref + 3.5 + CAR_L / 2 + k * 7.0 + away, 1.9, false, 31 + k + j.d);
      busLaneBusy = true;
    }
    // 3) a car ahead in free flow (heavier traffic only), released near stops and lights
    const want = ["orta", "yogun", "sikisik"].includes(level) && !busLaneBusy && (st.v || 0) > 5 && !st.doors &&
      clearAhead(busFront, t);
    if (want && !leader) leader = { d: busFront + 140, v: (st.v || 0) / 3.6 };
    if (leader) {
      const vb = (st.v || 0) / 3.6, gapWant = 12 + 1.6 * vb;
      if (want) {
        const gap = leader.d - busFront;
        leader.v = vb + Math.max(-3, Math.min(3, (gap - gapWant) * -0.25));
      } else leader.v = Math.min(leader.v + 1.5 * dts, 12);
      leader.d = Math.max(leader.d + leader.v * dts, busFront + gapWant * 0.6);
      if (!want && leader.d - busFront > 170) leader = null;
      else if (leader) onRoad(leader.d, 1.9, false, 77);
    }
    // 4) oncoming lane
    stepOncoming(t);
    for (const c of onc) if (Math.abs(c.u - busFront) < 450) onRoad(c.u, -1.9, true, c.seed);
    // 5) cross traffic, only while the bus has red, and never late in that red
    const spacing = { akici: 9, orta: 5.5, yogun: 3.8, sikisik: 3.0 }[level] || 8;
    for (const g of signals) {
      if (Math.abs(g.d - busFront) > 260) continue;
      const [r0, r1] = redWindow(g, t);
      for (let dir = -1; dir <= 1; dir += 2) {
        for (let ts = r0 + 2 + (dir > 0 ? 0 : spacing / 2); ts <= r1 - 8; ts += spacing) {
          const p = -75 + 11 * (t - ts);
          if (p < -75 || p > 75) continue;
          onCross(g, dir * p, 4.2 + dir * 1.9, dir, (ts * 13) | 0);
        }
      }
    }
    flush();
  }

  function clearAhead(front, t) {
    for (const g of signals) {
      const dd = g.d - front;
      if (dd > 0 && dd < 90 && lightState(g, t) !== "green") return false;
    }
    return true;
  }

  function flush() {
    for (const m of [bodies, cabs, heads, tails]) { m.count = nCars; m.instanceMatrix.needsUpdate = true; }
    if (bodies.instanceColor) bodies.instanceColor.needsUpdate = true;
  }

  return {
    setPath(p, sigs) { path = p; signals = (sigs || []).map(s => ({ ...s })); buildJunctions(); onc = []; leader = null; },
    setData(d) { data = d; level = (d && d.traffic) || "yok"; leader = null; if (path) resetOncoming(0); },
    setNight(on) { headMat.emissiveIntensity = on ? 2.5 : 0.2; tailMat.emissiveIntensity = on ? 2.2 : 0.3; },
    nearSignal(d, margin) { return signals.some(g => Math.abs(g.d + 4 - d) < margin); },
    update,
    dispose() { scene.remove(group); group.traverse(o => o.geometry && o.geometry.dispose()); },
  };
}
