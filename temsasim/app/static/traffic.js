/* Traffic-light timing shared by the 3D view.
   Line-for-line mirror of temsasim/traffic.py::Signal.state -- a test runs both
   and compares them, so the light the viewer sees is the light the model obeyed. */

export function lightState(sig, t) {
  const ph = ((t + sig.offset) % sig.cycle + sig.cycle) % sig.cycle;
  if (ph < sig.green) return "green";
  if (ph < sig.green + sig.amber) return "amber";
  return "red";
}

/* The red window (for the bus's direction) that contains t, or the last one
   before it: [start, end]. Cross traffic moves during it. */
export function redWindow(sig, t) {
  const ph = ((t + sig.offset) % sig.cycle + sig.cycle) % sig.cycle;
  const redStart = sig.green + sig.amber, redLen = sig.cycle - redStart;
  if (ph >= redStart) { const r0 = t - (ph - redStart); return [r0, r0 + redLen]; }
  const r1 = t - ph;
  return [r1 - redLen, r1];
}
