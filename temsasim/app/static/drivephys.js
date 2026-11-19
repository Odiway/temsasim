/* Free drive: the road and the live vehicle model.

   The live model is deliberately simple -- one longitudinal force balance,
   a steady-state cornering bicycle for steering, steady thermal loads -- so it can run in
   the browser at frame rate. It uses the selected vehicle's own parameters
   (from /api/drive/setup), so the bus pulls, climbs and regenerates like that
   vehicle. Its numbers are a live estimate only: when the drive is over, the
   recorded speed, grade, surface and door trace goes back to the server and
   the full model computes the real energy.

   No three.js here, so this file can be checked with plain node. */

export const LANES = 3, LANE_W = 3.5, MEDIAN = 1.2;
export const ROAD_HALF = MEDIAN / 2 + LANES * LANE_W + 0.5;   // 11.6 m: six lanes, a median, shoulders
export const WALK_W = 5.0;
export const EDGE = ROAD_HALF + WALK_W;                        // outer edge of the pavement
export const WHEELBASE = 6.0;                                  // front axle 3.55 m, rear -2.45 m (sim3d: buildBus); the vehicle's own is used when its suspension file gives one
const AY_DRIVER = 0.3 * 9.81;                                  // lateral acceleration a bus driver uses in a hard turn [m/s2]
const BUS_HALF_W = 1.29;
const G = 9.81;
const STEP = 2;                                                // metres between road samples
const SMOOTH = 30;                                             // vertical-curve length at grade changes [m]
export const REVERSE_MAX = 10 / 3.6;                           // reverse speed limit, as on a city bus [m/s]

export const PRESETS = {
  bulvar: [{ len: 1500, grade: 0, surface: "asfalt" }],
  tepe: [{ len: 300, grade: 0, surface: "asfalt" }, { len: 500, grade: 6, surface: "asfalt" },
         { len: 200, grade: 0, surface: "asfalt" }, { len: 500, grade: -6, surface: "asfalt" },
         { len: 300, grade: 0, surface: "asfalt" }],
  karma: [{ len: 300, grade: 0, surface: "yeni_asfalt" }, { len: 300, grade: 2, surface: "eski_asfalt" },
          { len: 300, grade: 4, surface: "parke" }, { len: 250, grade: -3, surface: "arnavut" },
          { len: 300, grade: 0, surface: "stabilize" }, { len: 300, grade: -2, surface: "asfalt" }],
};

/* ---------------------------------------------------------------- road */
export function buildRoad(segs, surfaces) {
  const factor = Object.fromEntries((surfaces || []).map(s => [s.key, s.factor]));
  const clean = segs.map(s => ({ len: Math.max(20, +s.len || 0), grade: Math.max(-25, Math.min(25, +s.grade || 0)),
                                 surface: s.surface || "asfalt" }));
  const starts = [];
  let L = 0;
  for (const s of clean) { starts.push(L); L += s.len; }
  const segAt = d => {
    let k = 0;
    while (k < clean.length - 1 && d >= starts[k + 1]) k++;
    return k;
  };
  // grade sampled every metre, smoothed over a vertical curve, then integrated
  const n1 = Math.ceil(L) + 1, raw = new Float32Array(n1);
  for (let i = 0; i < n1; i++) raw[i] = clean[segAt(i)].grade;
  const half = SMOOTH / 2, gm = new Float32Array(n1);
  for (let i = 0; i < n1; i++) {
    let sum = 0, cnt = 0;
    for (let j = Math.max(0, i - half); j <= Math.min(n1 - 1, i + half); j++) { sum += raw[j]; cnt++; }
    gm[i] = sum / cnt;
  }
  const n = Math.ceil(L / STEP) + 1;
  const E = new Float32Array(n), Gp = new Float32Array(n);
  let e = 0;
  for (let i = 0; i < n; i++) {
    const d = Math.min(i * STEP, n1 - 1);
    Gp[i] = gm[Math.round(d)];
    E[i] = e;
    for (let j = 0; j < STEP; j++) e += gm[Math.min(n1 - 1, Math.round(d) + j)] / 100;
  }
  const sample = (A, d) => {
    const f = Math.max(0, Math.min(n - 1.001, d / STEP)), i = Math.floor(f), t = f - i;
    return A[i] + (A[i + 1] - A[i]) * t;
  };
  const emin = Math.min(...E), emax = Math.max(...E);
  return {
    segs: clean, starts, L, n, step: STEP, E, emin, emax,
    elev: d => sample(E, d),
    grade: d => sample(Gp, d),                                  // percent, along +s
    seg: d => clean[segAt(Math.max(0, Math.min(L, d)))],
    segIndex: d => segAt(Math.max(0, Math.min(L, d))),
    factor: d => factor[clean[segAt(Math.max(0, Math.min(L, d)))].surface] ?? 1,
  };
}

/* The straight test road as a "world" for the driver: a 2D plane where x runs
   along the road and z across it. The city (citymap.js) is another world. */
export function roadWorld(road) {
  return {
    start: { x: 25, z: 1.9 + LANE_W, psi: 0 }, steerLock: 0.6,
    height: x => road.elev(x),
    gradePct: (x, z, psi) => road.grade(x) * Math.cos(psi),
    factor: x => road.factor(x),
    constrain(st, prev, vNew, dt) {
      // kerbs and the end barriers
      const lim = ROAD_HALF - BUS_HALF_W - 0.1;
      st.scrape = false; st.atEnd = false; st.kerb = false; st.blocked = false;
      if (Math.abs(st.lat) > lim) {
        st.lat = Math.sign(st.lat) * lim; st.psi *= 0.6; vNew *= 1 - 1.2 * dt; st.scrape = Math.abs(vNew) > 0.3;
      }
      if (st.s > road.L - 8) { st.s = road.L - 8; vNew = 0; st.atEnd = true; }
      if (st.s < 8) { st.s = 8; vNew = 0; st.atEnd = true; }
      return vNew;
    },
  };
}

/* ---------------------------------------------------------------- vehicle */
/* st.s and st.lat are the bus centre's world x and z (on the test road, the
   distance along it and the offset across it). */
export function createDriver(P, world, { passengers = 0 } = {}) {
  let mass = P.mass_kg + passengers * P.passenger_mass_kg;
  let mTot = mass + (P.m_eq_kg || 0);
  let pax = passengers;
  const etaDl = P.driveline_eff, etaM = Math.max(0.6, P.motor.eff * 0.96);
  const r = P.wheel_r_m;
  const mot = P.motor;
  const auxAt = v => {
    const V = P.aux_v_mps, W = P.aux_w;
    if (v <= V[0]) return W[0];
    for (let i = 1; i < V.length; i++) if (v <= V[i]) return W[i - 1] + (W[i] - W[i - 1]) * (v - V[i - 1]) / (V[i] - V[i - 1]);
    return W[W.length - 1];
  };
  const tMax = w => {
    if (w > mot.max_speed_rads) return 0;
    const wb = mot.peak_power_w / mot.peak_torque_nm;
    return w <= wb ? mot.peak_torque_nm : mot.peak_power_w / w;
  };

  const st = {};
  function reset() {
    Object.assign(st, {
      t: 0, s: world.start.x, lat: world.start.z, psi: world.start.psi, v: 0, a: 0, gear: 0,
      thr: 0, brk: 0, steer: 0, doors: 0, dir: 1, delta: 0, ay: 0, roll: 0, pitch: 0,
      soc: P.battery.soc_start, dist: 0,
      e_batt: 0, e_trac: 0, e_regen: 0, e_hvac: 0, e_aux: 0, e_thermal: 0, e_fric: 0,
      p_batt: 0, p_motor: 0, p_crank: 0, p_aux: 0, p_fric: 0, cruise: null, scrape: false, atEnd: false, kerb: false, blocked: false,
      trace: { t: [], v: [], grade_pct: [], surface: [], doors: [], pax: [] }, nextRec: 0,
    });
  }
  reset();

  /* input: {up, down, left, right} booleans; dt in seconds of real time */
  function step(input, dtFrame) {
    const sub = Math.max(1, Math.ceil(dtFrame / (1 / 120))), dt = dtFrame / sub;
    for (let k = 0; k < sub; k++) one(input, dt);
  }

  function one(inp, dt) {
    // pedals and wheel ease in and out like a driver's foot and hands
    const ease = (x, target, up, down) => x + Math.max(-down * dt, Math.min(up * dt, target - x));
    // cruise control: a proportional foot on the pedals; the driver's own
    // brake, the doors or reverse cancel it, the throttle overrides it
    if (st.cruise != null && (inp.down || st.doors || st.dir < 0)) st.cruise = null;
    let thrT = inp.up && !st.doors ? 1 : 0, brkT = inp.down ? 1 : 0;
    if (st.cruise != null && !inp.up) {
      const e = st.cruise - Math.abs(st.v);
      thrT = Math.max(0, Math.min(1, 0.25 + 0.45 * e));
      brkT = e < -1.0 ? Math.min(0.5, -(e + 1.0) * 0.15) : 0;
    }
    st.thr = ease(st.thr, thrT, st.cruise != null ? 3 : 1.6, 4);
    st.brk = ease(st.brk, brkT, 3, 6);
    st.steer = ease(st.steer, (inp.right ? 1 : 0) - (inp.left ? 1 : 0), 1.4, 2.2);

    // v is signed: + forward along the bus's heading, - in reverse
    const v = st.v, sp = Math.abs(v), dir = st.dir;
    const motion = v > 0.01 ? 1 : v < -0.01 ? -1 : 0;
    // single-speed or multi-speed: shift on the driveline's own speeds
    const R = P.ratios;
    if (R.length > 1) {
      if (sp > P.shift_up_mps && st.gear < R.length - 1) st.gear++;
      else if (sp < P.shift_down_mps && st.gear > 0) st.gear--;
    }
    const ratio = R[st.gear];
    const w = sp / r * ratio;

    const gradePct = world.gradePct(st.s, st.lat, st.psi);       // along the bus's nose
    const th = Math.atan(gradePct / 100);
    const kSurf = world.factor(st.s, st.lat);
    let fRoll = 0;
    if (sp > 0.01) {
      if (P.coastdown) {
        const [A, B, , mRef] = P.coastdown;
        fRoll = (A * mass / (mRef || mass) + B * sp) * kSurf;
      } else fRoll = mass * G * Math.cos(th) * (P.rolling_c0 + P.rolling_c1 * sp) * kSurf;
    }
    const fAero = P.coastdown ? P.coastdown[2] * (P.rho / P.coastdown[4]) * sp * sp : 0.5 * P.rho * P.cda_m2 * sp * sp;
    const fGrade = mass * G * Math.sin(th);                      // + pulls the bus backwards

    const pAux = auxAt(sp), pHvac = P.hvac_w, pTherm = P.pack_thermal_w;
    // traction in the selected direction, limited by the motor envelope and the pack
    let fDrive = st.thr * tMax(w) * ratio * etaDl / r;
    const pDisMax = Math.max(0, P.battery.max_discharge_w - pAux - pHvac - pTherm);
    if (sp > 0.1 && fDrive * sp / (etaDl * etaM) > pDisMax) fDrive = pDisMax * etaDl * etaM / sp;
    if (st.soc <= P.battery.soc_min) fDrive = 0;
    if (dir < 0 && sp > REVERSE_MAX * 0.85) fDrive *= Math.max(0, (REVERSE_MAX - sp) / (REVERSE_MAX * 0.15));   // reverse governor
    // braking: the motor takes what it can as regen, friction brakes the rest
    let fReq = st.brk * mTot * 4.5;
    // lift-off: an electric bus regenerates gently, a diesel is slowed by the
    // engine with the fuel cut; neither is the friction brake
    const fLift = (!inp.up && !inp.down && sp > 1) ? mTot * 0.25 : 0;
    fReq += fLift;
    if (st.doors) fReq += mTot * 3;                                          // door brake: no rolling off with doors open
    if (dir < 0 && sp > REVERSE_MAX + 0.1) fReq += mTot * 3 * Math.min(1, (sp - REVERSE_MAX - 0.1) / 0.5);   // and it brakes, downhill too
    let fRegenMax = sp > 0.5 ? Math.min(tMax(w) * ratio / r, mot.max_regen_power_w / sp) : 0;
    const pChgMax = P.battery.max_charge_w + pAux + pHvac + pTherm;
    if (sp > 0.5) fRegenMax = Math.min(fRegenMax, pChgMax / (sp * etaDl * etaM));
    const fRegen = Math.min(fReq, fRegenMax), fFric = fReq - fRegen;

    // resistances and brakes oppose the motion; at a standstill they only hold
    const opp = motion || dir;
    let a = (dir * fDrive - opp * (fRegen + fFric + fRoll + fAero) - fGrade) / mTot;
    let vNew = v + a * dt;
    if (motion !== 0 && Math.sign(vNew) !== motion) vNew = 0;                // brakes stop the bus, never reverse it
    if (motion === 0) {
      // pulling away: only in the selected direction; otherwise the hill hold keeps it still
      if (Math.sign(vNew) !== dir) vNew = 0;
    }
    a = (vNew - v) / dt;

    // steering: the steady-state cornering equation delta = L/R + K ay (Gillespie Eq. 6-16), so the
    // path curvature for a road-wheel angle is tan(delta) / (L + K v^2): the same lock turns the bus
    // less and less as speed rises. The driver never asks for more lateral acceleration than a bus is
    // driven with, the tyres can hold, or the body can take before the inner wheels lift.
    const HD = P.handling || {};
    const wb = HD.wheelbase_m || WHEELBASE, kUs = HD.understeer_rad_per_mps2 != null ? HD.understeer_rad_per_mps2 : 2.0 / 57.3 / G;
    const ayMax = Math.min(AY_DRIVER, 0.85 * (HD.friction || 0.8) * G, 0.9 * (HD.rollover_g || 0.45) * G);
    const lenEff = wb + kUs * vNew * vNew;
    let kappa = Math.tan(st.steer * world.steerLock) / lenEff;
    const ayAsk = vNew * vNew * kappa;
    if (Math.abs(ayAsk) > ayMax) kappa *= ayMax / Math.abs(ayAsk);
    st.delta = Math.atan(kappa * lenEff);                       // the road-wheel angle that is actually used
    st.ay = vNew * vNew * kappa;
    // the body leans out of the turn and pitches with the acceleration, with the lag of its roll mode
    const lag = Math.min(1, dt * 2 * Math.PI * (HD.roll_hz || 0.8));
    st.roll = (st.roll || 0) + ((HD.roll_deg_per_g || 0) * st.ay / G - (st.roll || 0)) * lag;
    st.pitch = (st.pitch || 0) + ((a < 0 ? (HD.dive_deg_per_g || 0) : (HD.squat_deg_per_g || 0)) * -a / G - (st.pitch || 0)) * lag;
    const prev = { s: st.s, lat: st.lat, psi: st.psi };
    st.psi += vNew * kappa * dt;
    st.s += vNew * Math.cos(st.psi) * dt;
    st.lat += vNew * Math.sin(st.psi) * dt;
    // kerbs, buildings, barriers: the world decides
    vNew = world.constrain(st, prev, vNew, dt);

    const pMotor = fDrive * sp / (etaDl * etaM) - fRegen * sp * etaDl * etaM;
    const pBatt = pMotor + pAux + pHvac + pTherm;
    st.v = vNew; st.a = a; st.t += dt; st.dist += Math.abs(vNew) * dt;
    st.soc -= pBatt * dt / (P.battery.nominal_kwh * 3.6e6);
    st.soc = Math.max(0, Math.min(1, st.soc));
    const fFricReal = P.powertrain === "diesel" ? Math.max(0, fFric - fLift) : fFric;
    st.p_batt = pBatt; st.p_motor = pMotor; st.p_aux = pAux; st.p_fric = fFricReal * sp;
    // diesel: the crankshaft power the engine actually produces (the "motor"
    // figure is fuel power, because the live model's machine is the whole engine)
    st.p_crank = P.powertrain === "diesel" ? fDrive * sp / etaDl + (pMotor > 0 ? pAux : 0) : pMotor;
    st.p_engbrake = P.powertrain === "diesel" ? Math.min(fFric, fLift) * sp : 0;
    st.e_batt += pBatt * dt; st.e_aux += pAux * dt; st.e_hvac += pHvac * dt; st.e_thermal += pTherm * dt;
    if (pMotor > 0) st.e_trac += pMotor * dt; else st.e_regen -= pMotor * dt;
    st.e_fric += fFricReal * sp * dt;
    st.gradePct = gradePct; st.kSurf = kSurf;

    // the record the full model replays, every half second. The model drives
    // forwards only, so a reversing bus is recorded as its speed with the grade
    // it meets in the direction it actually moves -- the same forces and energy.
    if (st.t >= st.nextRec) {
      const T = st.trace, md = vNew < -0.01 ? -1 : 1;
      T.t.push(+st.t.toFixed(2)); T.v.push(+Math.abs(st.v).toFixed(3)); T.grade_pct.push(+(gradePct * md).toFixed(3));
      T.surface.push(kSurf); T.doors.push(st.doors ? 1 : 0); T.pax.push(pax);
      st.nextRec += 0.5;
    }
  }

  /* cruise control at the current speed (or off) */
  function toggleCruise() { if (st.cruise != null || Math.abs(st.v) < 2 || st.dir < 0) { st.cruise = null; return null; } st.cruise = Math.abs(st.v); return st.cruise; }
  function toggleDoors() { if (Math.abs(st.v) < 0.5) st.doors = st.doors ? 0 : 1; return st.doors; }
  /* D <-> R, only at a standstill, as on the bus's push-button selector */
  function toggleReverse() { if (Math.abs(st.v) > 0.3) return null; st.dir = -st.dir; st.thr = 0; return st.dir; }

  /* people get on and off at stops: the mass the motor moves changes with them */
  function setPassengers(n) { pax = Math.max(0, n); mass = P.mass_kg + pax * P.passenger_mass_kg; mTot = mass + (P.m_eq_kg || 0); }

  return { st, step, reset, toggleDoors, toggleReverse, toggleCruise, setPassengers, get mass() { return mass; }, get pax() { return pax; } };
}
