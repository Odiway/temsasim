/* Air exchange at the open doors.

   While a door is open, outside air flows in low and cabin air flows out high
   -- the stack effect that makes door openings expensive in winter (the model
   books it as q_doors). Each stream is a ribbon of soft particles coloured by
   its own temperature: icy blue when cold, near-white when mild, orange when
   hot. Cold incoming air sinks toward the floor, hot incoming air rises. The
   streams are stronger the larger the gap between outside and cabin.

   Visual only: the strength follows the model's ambient and cabin
   temperatures and the door state, but the shape of the flow is illustrative. */

import * as THREE from "three";

const IN_N = 84, OUT_N = 40;
const FLANK = 1.29;                         // bus flank, half the body width

const AIR_STOPS = [[-10, "#5aa0ff"], [5, "#9fd0ff"], [16, "#e6f1fa"], [22, "#f6f3ee"], [28, "#ffd2a8"], [40, "#ff8a4a"]]
  .map(([t, c]) => [t, new THREE.Color(c)]);
export function airColor(t, out = new THREE.Color()) {
  if (!(t > AIR_STOPS[0][0])) return out.copy(AIR_STOPS[0][1]);
  for (let i = 1; i < AIR_STOPS.length; i++) {
    const [t1, c1] = AIR_STOPS[i];
    if (t <= t1) { const [t0, c0] = AIR_STOPS[i - 1]; return out.copy(c0).lerp(c1, (t - t0) / (t1 - t0)); }
  }
  return out.copy(AIR_STOPS[AIR_STOPS.length - 1][1]);
}

const hash = n => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };

export function createDoorAir(bus, doorX, host) {
  const per = IN_N + OUT_N, N = per * doorX.length;
  const pos = new Float32Array(N * 3), col = new Float32Array(N * 4), size = new Float32Array(N);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("aCol", new THREE.BufferAttribute(col, 4));
  geo.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
  const mat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { uScale: { value: 300 } },
    vertexShader: `
      attribute vec4 aCol; attribute float aSize; varying vec4 vCol; uniform float uScale;
      void main() {
        vCol = aCol;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = aSize * uScale / -mv.z;
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      varying vec4 vCol;
      void main() {
        float d = length(gl_PointCoord - 0.5);
        float a = vCol.a * smoothstep(0.5, 0.05, d);
        if (a < 0.01) discard;
        gl_FragColor = vec4(vCol.rgb, a);
      }`,
  });
  const points = new THREE.Points(geo, mat);
  points.frustumCulled = false; points.visible = false; points.renderOrder = 2;
  bus.group.add(points);

  // one plain-language tag over the middle door, in the normal view as well
  const tag = document.createElement("div");
  tag.className = "air-tag"; tag.hidden = true;
  host.append(tag);
  const anchor = new THREE.Vector3(doorX[Math.min(1, doorX.length - 1)], 3.3, FLANK + 0.6), v = new THREE.Vector3();

  // fixed per-particle randomness
  const R = Array.from({ length: per }, (_, i) => [hash(i), hash(i + 0.37), hash(i + 0.71)]);
  const cIn = new THREE.Color(), cOut = new THREE.Color();
  let phase = 0;

  function update(dt, doorOpen, ambient, cabin, heightScale = 1, camera = null) {
    if (doorOpen < 0.02) { points.visible = false; tag.hidden = true; return; }
    points.visible = true;
    mat.uniforms.uScale.value = heightScale;
    const gap = ambient - cabin;
    const k = doorOpen * Math.min(1, 0.3 + Math.abs(gap) / 14);   // mild days still show a gentle exchange
    const buoy = Math.max(-1, Math.min(1, gap / 15));              // <0: cold air sinks, >0: hot air rises
    airColor(ambient, cIn); airColor(cabin, cOut);
    phase = (phase + dt * 0.42) % 1;
    let n = 0;
    for (const dx of doorX) {
      for (let i = 0; i < per; i++, n++) {
        const [a, b, c] = R[i], inflow = i < IN_N;
        const u = ((inflow ? i / IN_N : (i - IN_N) / OUT_N) + phase * (inflow ? 1 : 0.8) + a * 0.05) % 1;
        let x, y, z;
        if (inflow) {
          // from 1.4 m outside the door to 1.5 m inside, fanning out along the aisle
          x = dx + (a - 0.5) * (1.1 + u * 1.8);
          z = FLANK + 1.4 - u * 2.9;
          y = 0.55 + b * 1.15 + buoy * 0.9 * u * u;
        } else {
          // cabin air leaving under the door head and lifting away outside
          x = dx + (a - 0.5) * 1.2;
          z = FLANK - 1.0 + u * 2.6;
          y = 1.95 + b * 0.6 + 0.35 * u * u;
        }
        pos[n * 3] = x; pos[n * 3 + 1] = Math.max(0.42, y); pos[n * 3 + 2] = z;
        const cc = inflow ? cIn : cOut;
        col[n * 4] = cc.r; col[n * 4 + 1] = cc.g; col[n * 4 + 2] = cc.b;
        col[n * 4 + 3] = Math.min(1, k * Math.sin(Math.PI * u) * (inflow ? 1.15 : 0.75));
        size[n] = (inflow ? 0.24 : 0.17) * (0.7 + c * 0.6) * (1 + u * 0.9);
      }
    }
    geo.attributes.position.needsUpdate = true;
    geo.attributes.aCol.needsUpdate = true;
    geo.attributes.aSize.needsUpdate = true;

    if (camera && doorOpen > 0.5) {
      // "cold" and "hot" in the everyday sense, not merely cooler or warmer than the cabin
      const word = ambient < 12 && gap < -3 ? "Soğuk" : ambient > 26 && gap > 3 ? "Sıcak" : "Dış";
      const t = `${word} hava giriyor · ${Math.round(ambient).toLocaleString("tr-TR")} °C → kabin ${cabin.toLocaleString("tr-TR", { maximumFractionDigits: 1 })} °C`;
      if (tag.textContent !== t) tag.textContent = t;
      tag.style.setProperty("--air", `#${cIn.getHexString()}`);
      bus.group.updateMatrixWorld(); camera.updateMatrixWorld();
      v.copy(anchor).applyMatrix4(bus.group.matrixWorld).project(camera);
      tag.hidden = v.z > 1 || Math.abs(v.x) > 1.1 || Math.abs(v.y) > 1.1;
      tag.style.transform = `translate(${((v.x + 1) / 2 * host.clientWidth).toFixed(1)}px, ${((1 - v.y) / 2 * host.clientHeight).toFixed(1)}px) translate(-50%, -100%)`;
    } else tag.hidden = true;
  }

  return { update, points, dispose() { tag.remove(); } };
}
