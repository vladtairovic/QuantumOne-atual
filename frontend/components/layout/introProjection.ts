import contours from "./introOutline.json";

// Mid-plane contours sampled from the supplied Q1 mesh. This renderer projects the
// extrusion in software on devices where a WebGL context cannot be created.
export function createProjectedIntro(canvas: HTMLCanvasElement) {
  const ctx = canvas.getContext("2d", { alpha: true });
  if (!ctx) throw new Error("Canvas indisponível.");
  const pen = ctx;
  const clamp = (n: number) => Math.max(0, Math.min(1, n));
  const ease = (n: number) => { const t = clamp(n); return t * t * (3 - 2 * t); };
  let width = 0, height = 0;

  return {
    render(seconds: number) {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (!w || !h) return;
      if (w !== width || h !== height) {
        width = w; height = h;
        const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
        canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
        pen.setTransform(canvas.width / w, 0, 0, canvas.height / h, 0, 0);
      }
      const t = Math.min(seconds, 4.05);
      const travel = ease(t / 4.05), bend = Math.sin(travel * Math.PI);
      const x = w * (.18 * (1 - travel) + .5 * travel + .25 * bend);
      const y = h * (.63 * (1 - travel) + .5 * travel - .28 * bend);
      const scale = Math.min(w * .62, h * .59) * (.32 + .68 * travel);
      const rotation = -.65 + ease((t - .6) / 3.45) * (2 * Math.PI + .51);
      const tilt = -.08 + .24 * bend;
      const co = Math.cos(rotation), si = Math.sin(rotation);
      const build = ease(t / 1.52);
      type Point = [number, number];
      function project(px: number, py: number, pz: number): Point {
        const depth = -si * px + co * pz;
        const f = 3.7 / (3.7 - depth);
        return [x + (co * px + si * pz) * f * scale, y - (py * Math.cos(tilt) - depth * Math.sin(tilt)) * f * scale];
      }
      function trace(rings: Point[][]) {
        const path = new Path2D();
        for (const ring of rings) {
          path.moveTo(...ring[0]);
          for (let i = 1; i < ring.length; i++) path.lineTo(...ring[i]);
          path.closePath();
        }
        return path;
      }
      pen.fillStyle = "#080a15"; pen.fillRect(0, 0, w, h);
      const horizon = h * .43;
      pen.strokeStyle = "#26213b"; pen.lineWidth = 1;
      pen.beginPath();
      for (let i = -10; i <= 10; i++) {
        pen.moveTo(w * .5 + i * 9, horizon);
        pen.lineTo(w * .5 + (i + .12 * bend) * w * .22, h);
      }
      for (let i = 0; i < 11; i++) {
        const yy = horizon + Math.pow((i + t * .18) / 11, 2.4) * (h - horizon);
        pen.moveTo(0, yy); pen.lineTo(w, yy);
      }
      pen.stroke();
      const symbols = ["01", "{ }", "H", "CX", "π/2", "|0⟩", "RZ"];
      pen.font = "12px monospace";
      for (let i = 0; i < 65; i++) {
        const z = ((i * .618 + .027 * t) % 1);
        const xx = w / 2 + (Math.sin(i * 277.3) * w * .74) * (.24 + z);
        const yy = horizon + (Math.cos(i * 117.9) * h * .62) * (.24 + z);
        pen.fillStyle = i % 5 ? "#53476b" : "#9189ad";
        if (i % 8) pen.fillRect(xx, yy, 1 + 1.1 * z, 1 + 1.1 * z);
        else pen.fillText(symbols[i % symbols.length], xx, yy);
      }
      const near = co >= 0 ? .073 : -.073;
      const faces = contours.map(ring => ring.map(([px, py]) => project(px, py, near)));
      const backs = contours.map(ring => ring.map(([px, py]) => project(px, py, -near)));
      const face = trace(faces);
      pen.save();
      pen.beginPath(); pen.rect(0, y - scale * .7, w, scale * 1.4 * build); pen.clip();
      for (let r = 0; r < contours.length; r++) {
        for (let i = 0; i < faces[r].length; i++) {
          const j = (i + 1) % faces[r].length;
          const [a,b] = faces[r][i], [c,d] = faces[r][j];
          const reflect = (1 + Math.sin(Math.atan2(d-b, c-a) + t * 1.3)) / 2;
          pen.fillStyle = `hsl(263 55% ${19 + 41 * reflect}%)`;
          pen.fill(trace([[faces[r][i], backs[r][i], backs[r][j], faces[r][j]]]));
        }
      }
      const streak = x + scale * (.40 * Math.sin(t * 2.1) - .08);
      const metal = pen.createLinearGradient(streak - scale * .65, y - scale * .35, streak + scale * .65, y + scale * .35);
      for (const [stop,color] of [[0,"#29144b"],[.26,"#593293"],[.48,"#8f67c9"],[.55,"#efe7ff"],[.59,"#ad89df"],[.72,"#584184"],[1,"#382355"]] as const) metal.addColorStop(stop,color);
      pen.fillStyle = metal; pen.fill(face,"evenodd");
      pen.strokeStyle = "#bf9eef"; pen.lineWidth = 1.4; pen.stroke(face);
      pen.restore();
      if (build < 1) {
        const scan = y + scale * (build * 1.4 - .7);
        pen.save(); pen.clip(face,"evenodd"); pen.strokeStyle = "#cffbff"; pen.lineWidth = 3;
        pen.beginPath(); pen.moveTo(x - scale,scan); pen.lineTo(x + scale,scan); pen.stroke(); pen.restore();
        pen.fillStyle = "#c3b2ef";
        for (let i = 0; i < 21; i++) {
          const phase = (i / 21 + t * .8) % 1, spread = (1 - build) * (1 - phase);
          pen.fillText(symbols[i % symbols.length], x + Math.cos(i * 4.6) * scale * (.47 + spread), scan - spread * h * .36);
        }
      }
      if (seconds > 4.05) {
        const open = ease((seconds - 4.05) / .95);
        const anchor = project(.355, .10, near);
        const magnify = .05 + open * open * (28 * Math.max(w,h) / scale);
        pen.save(); pen.globalCompositeOperation = "destination-out";
        pen.translate(anchor[0],anchor[1]); pen.scale(magnify,magnify); pen.translate(-anchor[0],-anchor[1]);
        pen.fillStyle = "#000"; pen.fill(face,"evenodd"); pen.restore();
      }
    },
    dispose() { canvas.width = canvas.height = 1; },
  };
}
