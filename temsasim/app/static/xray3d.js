/* X-ray views of the bus: the shell fades to a wireframe ghost and one of two
   insides shows through, coloured and labelled from the live model state.

   - "cabin": seats, the passengers the model is carrying (st.pax), and a heat
     map of the floor. The model's cabin is one zone with one temperature
     (st.cabin), so the map is that temperature everywhere -- except that while
     doors are open, outside air at the ambient temperature is drawn spreading
     in from each doorway. That spread is illustrative; the cabin temperature
     it fades into is the model's.
   - "powertrain": roof battery packs coloured by pack temperature, orange HV
     cables, inverter, the central motor on the rear axle, the auxiliary box
     and the friction brakes, each glowing with the power the model puts
     through it this instant.

   Purely visual, like the rest of the stage: it reads the state, never writes it. */

import * as THREE from "three";

const BLUE = new THREE.Color("#3987e5"), GREEN = new THREE.Color("#2fbf71"), HEAT = new THREE.Color("#eb6834");
const DOORS = [4.95, 0.55, -4.35];           // door centres along the bus (sim3d: buildBus)
const PASSENGER_W = 80;                      // sensible heat per passenger (components/hvac.py default)

/* Temperature colour scale shared by the floor map, the packs and the legend. */
export const TEMP_STOPS = [[0, "#2f6fd6"], [12, "#3fa7d6"], [18, "#4cc38a"], [23, "#8fd16a"], [27, "#f2c14e"], [33, "#e8743b"], [40, "#d03b3b"]]
  .map(([t, c]) => [t, new THREE.Color(c)]);
export function tempColor(t, out = new THREE.Color()) {
  if (!(t > TEMP_STOPS[0][0])) return out.copy(TEMP_STOPS[0][1]);
  for (let i = 1; i < TEMP_STOPS.length; i++) {
    const [t1, c1] = TEMP_STOPS[i];
    if (t <= t1) { const [t0, c0] = TEMP_STOPS[i - 1]; return out.copy(c0).lerp(c1, (t - t0) / (t1 - t0)); }
  }
  return out.copy(TEMP_STOPS[TEMP_STOPS.length - 1][1]);
}

const nf = (x, d = 0) => (x ?? 0).toLocaleString("tr-TR", { minimumFractionDigits: d, maximumFractionDigits: d });

export function createXray(bus, host) {
  let mode = "off";

  /* ---------------- the shell: remember every material, fade them on demand ---------------- */
  const skip = new Set([...bus.flow, ...bus.hvacFlow]);
  const shellMeshes = [], shellMats = new Map();
  bus.group.traverse(o => {
    if (!o.isMesh || skip.has(o)) return;
    shellMeshes.push({ mesh: o, cast: o.castShadow });
    for (const m of [].concat(o.material)) if (!shellMats.has(m)) shellMats.set(m, { transparent: m.transparent, opacity: m.opacity, depthWrite: m.depthWrite });
  });
  const wheelMats = new Set();
  for (const w of bus.wheels) w.traverse(o => { if (o.isMesh) wheelMats.add(o.material); });

  // a wireframe outline of the body so the bus still reads as a bus
  const ghost = new THREE.Group(); ghost.visible = false; bus.group.add(ghost);
  const lineMat = new THREE.LineBasicMaterial({ color: 0x9fd3ff, transparent: true, opacity: 0.75, toneMapped: false });
  bus.group.traverse(o => {
    if (o.isMesh && o.geometry.type === "ExtrudeGeometry") {
      const l = new THREE.LineSegments(new THREE.EdgesGeometry(o.geometry, 25), lineMat);
      l.position.copy(o.position); ghost.add(l);
    }
  });

  function fadeShell(on) {
    for (const [m, orig] of shellMats) {
      if (on) {
        m.transparent = true; m.depthWrite = false;
        m.opacity = wheelMats.has(m) ? (mode === "powertrain" ? 0.45 : 0.15) : 0.07;
      } else Object.assign(m, orig);
      m.needsUpdate = true;
    }
    for (const s of shellMeshes) s.mesh.castShadow = on ? false : s.cast;
  }

  const M = o => new THREE.MeshStandardMaterial(o);
  const add = (grp, geo, mat, x, y, z) => { const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); grp.add(m); return m; };

  /* ---------------- cabin ---------------- */
  const cabin = new THREE.Group(); cabin.visible = false; bus.group.add(cabin);
  const FLOOR_Y = 0.48, CX0 = -5.85, CX1 = 5.75, CZ = 1.18;

  // floor heat map: a grid whose vertex colours are the temperature field
  const floorGeo = new THREE.PlaneGeometry(CX1 - CX0, 2 * CZ, 58, 12);
  floorGeo.rotateX(-Math.PI / 2); floorGeo.translate((CX0 + CX1) / 2, FLOOR_Y, 0);
  floorGeo.setAttribute("color", new THREE.BufferAttribute(new Float32Array(floorGeo.attributes.position.count * 3), 3));
  const floor = new THREE.Mesh(floorGeo, new THREE.MeshBasicMaterial({ vertexColors: true, toneMapped: false, transparent: true, opacity: 0.92 }));
  cabin.add(floor);
  // the cabin air as a faint tinted volume
  const airMat = new THREE.MeshBasicMaterial({ color: 0x8fd16a, transparent: true, opacity: 0.1, depthWrite: false, toneMapped: false });
  add(cabin, new THREE.BoxGeometry(CX1 - CX0, 2.5, 2 * CZ), airMat, (CX0 + CX1) / 2, FLOOR_Y + 1.25, 0);

  // seats: forward-facing pairs, none in the door bays; a rear bench across the width
  const seatSlots = [];
  for (let x = 3.9; x > -5.0; x -= 0.82) {
    for (const z of [-0.93, -0.5]) seatSlots.push([x, z]);
    if (DOORS.every(dx => Math.abs(x - dx) > 1.05)) for (const z of [0.5, 0.93]) seatSlots.push([x, z]);
  }
  for (const z of [-0.94, -0.47, 0, 0.47, 0.94]) seatSlots.push([-5.5, z]);
  const standSlots = [];
  for (let x = 3.6; x > -4.8; x -= 0.62) standSlots.push([x, (standSlots.length % 2 ? 0.08 : -0.08)]);
  for (const dx of DOORS) for (const [ox, z] of [[-0.35, 0.75], [0.35, 0.75], [0, 0.35]]) standSlots.push([dx + ox, z]);

  const seatMat = M({ color: 0x46525e, roughness: 0.7 });
  const cushionGeo = new THREE.BoxGeometry(0.44, 0.09, 0.4);
  const backGeo = new THREE.BoxGeometry(0.07, 0.55, 0.4);
  const cushions = new THREE.InstancedMesh(cushionGeo, seatMat, seatSlots.length);
  const backs = new THREE.InstancedMesh(backGeo, seatMat, seatSlots.length);
  const dm = new THREE.Object3D();
  seatSlots.forEach(([x, z], i) => {
    dm.position.set(x, FLOOR_Y + 0.42, z); dm.updateMatrix(); cushions.setMatrixAt(i, dm.matrix);
    dm.position.set(x - 0.22, FLOOR_Y + 0.72, z); dm.updateMatrix(); backs.setMatrixAt(i, dm.matrix);
  });
  cabin.add(cushions, backs);
  // the driver
  add(cabin, cushionGeo, seatMat, 4.95, FLOOR_Y + 0.62, -0.75);

  // passengers: seated first, then standing; warm-coloured because they are heat sources
  const paxMat = M({ color: 0xf0a35e, roughness: 0.6, emissive: 0x6b2e08, emissiveIntensity: 0.35 });
  const torsoGeo = new THREE.CapsuleGeometry(0.16, 0.3, 3, 8);
  const headGeo = new THREE.SphereGeometry(0.105, 10, 8);
  const legGeo = new THREE.BoxGeometry(0.42, 0.13, 0.3);           // thighs, seated
  const standLegGeo = new THREE.BoxGeometry(0.16, 0.8, 0.28);
  const nSlots = seatSlots.length + standSlots.length;
  const torsos = new THREE.InstancedMesh(torsoGeo, paxMat, nSlots + 1);
  const heads = new THREE.InstancedMesh(headGeo, paxMat, nSlots + 1);
  const thighs = new THREE.InstancedMesh(legGeo, paxMat, seatSlots.length + 1);
  const shins = new THREE.InstancedMesh(standLegGeo, paxMat, standSlots.length + 1);
  for (const m of [torsos, heads, thighs, shins]) { m.count = 0; cabin.add(m); }
  function placePassengers(n) {
    let k = 0, kt = 0, ks = 0;
    for (let i = 0; i < seatSlots.length && k < n; i++, k++) {
      const [x, z] = seatSlots[i];
      dm.position.set(x - 0.06, FLOOR_Y + 0.82, z); dm.updateMatrix(); torsos.setMatrixAt(k, dm.matrix);
      dm.position.set(x - 0.04, FLOOR_Y + 1.2, z); dm.updateMatrix(); heads.setMatrixAt(k, dm.matrix);
      dm.position.set(x + 0.14, FLOOR_Y + 0.52, z); dm.updateMatrix(); thighs.setMatrixAt(kt++, dm.matrix);
    }
    for (let i = 0; i < standSlots.length && k < n; i++, k++) {
      const [x, z] = standSlots[i];
      dm.position.set(x, FLOOR_Y + 0.4, z); dm.updateMatrix(); shins.setMatrixAt(ks++, dm.matrix);
      dm.position.set(x, FLOOR_Y + 1.12, z); dm.updateMatrix(); torsos.setMatrixAt(k, dm.matrix);
      dm.position.set(x, FLOOR_Y + 1.52, z); dm.updateMatrix(); heads.setMatrixAt(k, dm.matrix);
    }
    torsos.count = heads.count = k; thighs.count = kt; shins.count = ks;
    for (const m of [torsos, heads, thighs, shins]) m.instanceMatrix.needsUpdate = true;
    return k;
  }
  // roof HVAC unit, lit while the cabin's thermal management draws power
  const hvacMat = M({ color: 0xd9dcdf, emissive: 0xeb6834, emissiveIntensity: 0, transparent: true, opacity: 0.9 });
  add(cabin, new THREE.BoxGeometry(4.34, 0.38, 2.08), hvacMat, -3.80, 3.30, 0);

  /* ---------------- powertrain ---------------- */
  const pt = new THREE.Group(); pt.visible = false; bus.group.add(pt);
  const packMats = [];
  for (const x of [3.3, 1.25, -0.8]) {
    const m = M({ color: 0x8fd16a, roughness: 0.45, metalness: 0.2, emissive: 0x3987e5, emissiveIntensity: 0 });
    packMats.push(m);
    add(pt, new THREE.BoxGeometry(1.94, 0.34, 2.04), m, x, 3.28, 0);
    // module seams so a pack reads as a stack of modules
    for (const dz of [-0.5, 0, 0.5]) add(pt, new THREE.BoxGeometry(1.96, 0.36, 0.02), M({ color: 0x2a3138 }), x, 3.28, dz);
  }
  const cableMat = M({ color: 0xf08a24, roughness: 0.5, emissive: 0xff7a00, emissiveIntensity: 0.2 });
  const tube = pts => new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts.map(p => new THREE.Vector3(...p))), 40, 0.045, 6, false);
  for (const dz of [-0.82, -0.7]) {
    pt.add(new THREE.Mesh(tube([[-0.8, 3.15, dz], [-4.5, 3.12, dz], [-5.7, 3.0, dz], [-5.82, 2.4, dz], [-5.82, 1.6, dz], [-5.45, 1.3, dz + 0.2]]), cableMat));
    pt.add(new THREE.Mesh(tube([[-4.95, 1.1, dz + 0.3], [-4.4, 0.75, dz * 0.5], [-3.8, 0.58, dz * 0.2]]), cableMat));
  }
  const invMat = M({ color: 0x5a6470, roughness: 0.5, metalness: 0.4, emissive: 0x3987e5, emissiveIntensity: 0 });
  add(pt, new THREE.BoxGeometry(0.56, 0.5, 0.72), invMat, -5.2, 1.25, -0.5);
  const motorMat = M({ color: 0x9aa3ad, roughness: 0.35, metalness: 0.6, emissive: 0x3987e5, emissiveIntensity: 0 });
  const motor = add(pt, new THREE.CylinderGeometry(0.3, 0.3, 0.78, 20), motorMat, -3.4, 0.55, 0); motor.rotation.z = Math.PI / 2;
  const steel = M({ color: 0x7c858e, roughness: 0.4, metalness: 0.6 });
  const shaft = add(pt, new THREE.CylinderGeometry(0.06, 0.06, 0.6, 10), steel, -2.75, 0.52, 0); shaft.rotation.z = Math.PI / 2;
  add(pt, new THREE.SphereGeometry(0.25, 16, 12), steel, -2.45, 0.5, 0);
  const axle = add(pt, new THREE.CylinderGeometry(0.075, 0.075, 1.95, 10), steel, -2.45, 0.5, 0); axle.rotation.x = Math.PI / 2;
  const auxMat = M({ color: 0x8b9298, roughness: 0.5, emissive: 0xffb000, emissiveIntensity: 0 });
  add(pt, new THREE.BoxGeometry(0.5, 0.45, 0.6), auxMat, -5.2, 1.0, 0.55);
  // friction brake discs: red when the model sends braking energy to them instead of the pack
  const brakeMat = M({ color: 0x55595e, roughness: 0.4, metalness: 0.6, emissive: 0xff3b1a, emissiveIntensity: 0 });
  for (const [x, z] of [[3.55, 0.86], [3.55, -0.86], [-2.45, 0.68], [-2.45, -0.68]]) {
    const d = add(pt, new THREE.CylinderGeometry(0.3, 0.3, 0.05, 20), brakeMat, x, 0.5, z); d.rotation.x = Math.PI / 2;
  }

  /* ---------------- labels and legend (HTML over the canvas) ---------------- */
  const layer = document.createElement("div"); layer.className = "xr-layer"; layer.hidden = true;
  const legend = document.createElement("div"); legend.className = "xr-legend"; legend.hidden = true;
  const grad = TEMP_STOPS.map(([t, c]) => `#${c.getHexString()} ${(t / 40 * 100).toFixed(0)}%`).join(", ");
  legend.innerHTML = `<div class="xr-legend-t">Kabin sıcaklığı</div><div class="xr-bar" style="background:linear-gradient(90deg, ${grad})"><i></i></div>` +
    `<div class="xr-ticks"><span>0 °C</span><span>10</span><span>20</span><span>30</span><span>40 °C</span></div>` +
    `<div class="xr-note">Model kabini tek bölge olarak hesaplar; kapı ağızlarındaki dış hava yayılımı temsilidir.</div>`;
  const marker = legend.querySelector("i");
  host.append(layer, legend);
  const labels = [];
  const label = (modeName, x, y, z, text) => {
    const el = document.createElement("div"); el.className = "xr-label"; layer.append(el);
    labels.push({ mode: modeName, pos: new THREE.Vector3(x, y, z), text, el, last: "" });
  };
  label("cabin", 0.6, 3.5, 0, s => `Kabin ${nf(s.cabin, 1)} °C`);
  label("cabin", -3.8, 3.9, 0, s => `Kabin içi termal yönetim ${nf(s.p_hvac, 1)} kW`);
  label("cabin", 2.6, 2.4, -0.6, s => `${nf(s.pax)} yolcu · vücut ısısı ≈ ${nf((s.pax || 0) * PASSENGER_W / 1000, 1)} kW`);
  label("cabin", 0.55, 2.6, 1.4, (s, e) => bus.doorOpen > 0.3 ? `Kapılar açık · dış hava ${nf(e.ambient, 0)} °C` : null);
  label("powertrain", 1.25, 3.95, 0, s => `Batarya · SOC %${nf(s.soc, 1)} · ${nf(s.pack, 1)} °C`);
  label("powertrain", 1.25, 3.62, 0, s => {
    const p = s.p_batt || 0;
    return Math.abs(p) < 0.5 ? "bekliyor" : p > 0 ? `${nf(p, 0)} kW veriyor` : `${nf(-p, 0)} kW şarj oluyor`;
  });
  label("powertrain", -0.8, 3.95, -0.9, s => (s.p_thermal || 0) > 0.2 ? `Yüksek voltaj ısıtma ${nf(s.p_thermal, 1)} kW` : null);
  label("powertrain", -5.2, 1.85, -0.5, () => "İnvertör");
  label("powertrain", -3.4, 1.15, 0, s => {
    const p = s.p_motor || 0;
    return Math.abs(p) < 2 ? "Motor · boşta" : p > 0 ? `Motor · çekiş ${nf(p, 0)} kW` : `Motor · rejeneratif fren ${nf(-p, 0)} kW`;
  });
  label("powertrain", -5.2, 1.55, 0.9, s => `Yardımcı sistemler ${nf(s.p_aux, 1)} kW`);
  label("powertrain", 3.55, 1.3, 0.9, s => (s.regen_lost || 0) > 1 ? `Sürtünmeli fren ${nf(s.regen_lost, 0)} kW` : null);

  /* ---------------- per frame ---------------- */
  const tmpC = new THREE.Color(), cabC = new THREE.Color(), ambC = new THREE.Color(), v = new THREE.Vector3();
  function update(st, env, camera) {
    if (mode === "off") return;
    const cab = st.cabin ?? env.ambient ?? 20, amb = env.ambient ?? 20;
    if (mode === "cabin") {
      const open = bus.doorOpen;
      const pos = floorGeo.attributes.position, col = floorGeo.attributes.color;
      tempColor(cab, cabC); tempColor(amb, ambC);
      for (let i = 0; i < pos.count; i++) {
        let w = 0;
        if (open > 0.01) for (const dx of DOORS) {
          const r = Math.hypot(pos.getX(i) - dx, (pos.getZ(i) - CZ) * 1.2);
          w = Math.max(w, Math.exp(-r / 1.6));
        }
        tmpC.copy(cabC).lerp(ambC, Math.min(1, w * open * 1.1));
        col.setXYZ(i, tmpC.r, tmpC.g, tmpC.b);
      }
      col.needsUpdate = true;
      airMat.color.copy(cabC);
      placePassengers(Math.round(st.pax || 0));
      hvacMat.emissiveIntensity = Math.min(2, (st.p_hvac || 0) / 6);
      marker.style.left = `${Math.max(0, Math.min(100, cab / 40 * 100))}%`;
    } else {
      const pb = st.p_batt || 0, pm = st.p_motor || 0, regen = pm < -2;
      for (const m of packMats) {
        tempColor(st.pack ?? 20, m.color);
        m.emissive.copy((st.p_thermal || 0) > 0.2 ? HEAT : regen ? GREEN : BLUE);
        m.emissiveIntensity = Math.min(0.9, Math.abs(pb) / 220 + ((st.p_thermal || 0) > 0.2 ? 0.4 : 0));
      }
      cableMat.emissiveIntensity = 0.2 + Math.min(2, Math.abs(pb) / 90);
      for (const m of [invMat, motorMat]) { m.emissive.copy(regen ? GREEN : BLUE); m.emissiveIntensity = Math.min(2, Math.abs(pm) / 90); }
      auxMat.emissiveIntensity = Math.min(1.5, (st.p_aux || 0) / 8);
      brakeMat.emissiveIntensity = Math.min(3, (st.regen_lost || 0) / 25);
    }
    // labels follow their anchors on screen
    bus.group.updateMatrixWorld(); camera.updateMatrixWorld();
    const w = host.clientWidth, h = host.clientHeight, shown = [];
    for (const l of labels) {
      const txt = l.mode === mode ? l.text(st, env) : null;
      if (!txt) { l.el.hidden = true; continue; }
      v.copy(l.pos).applyMatrix4(bus.group.matrixWorld).project(camera);
      if (v.z > 1 || Math.abs(v.x) > 1.2 || Math.abs(v.y) > 1.2) { l.el.hidden = true; continue; }
      l.el.hidden = false;
      if (txt !== l.last) { l.el.textContent = txt; l.last = txt; l.w = l.el.offsetWidth; l.h = l.el.offsetHeight; }
      l.x = (v.x + 1) / 2 * w; l.y = (1 - v.y) / 2 * h;
      shown.push(l);
    }
    // nudge colliding labels upward so none hides another
    shown.sort((a, b) => b.y - a.y);
    const placed = [];
    for (const l of shown) {
      for (let moved = true; moved;) {
        moved = false;
        for (const o of placed) if (Math.abs(l.x - o.x) < (l.w + o.w) / 2 + 4 && l.y > o.y - o.h - 3 && l.y - l.h < o.y + 3) { l.y = o.y - o.h - 4; moved = true; }
      }
      placed.push(l);
      l.el.style.transform = `translate(${l.x.toFixed(1)}px, ${l.y.toFixed(1)}px) translate(-50%, -100%)`;
    }
  }

  function setMode(m) {
    if (m === mode) return;
    if (mode !== "off") fadeShell(false);
    mode = m;
    const on = m !== "off";
    if (on) fadeShell(true);
    ghost.visible = on;
    cabin.visible = m === "cabin";
    pt.visible = m === "powertrain";
    layer.hidden = !on;
    legend.hidden = m !== "cabin";
  }

  return { setMode, update, get mode() { return mode; },
           dispose() { layer.remove(); legend.remove(); } };
}
