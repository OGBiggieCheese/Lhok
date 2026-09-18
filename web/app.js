/* LHOK — visor del enjambre y banco de ensayos (canvas puro, sin librerías). */
(() => {
  const canvas = document.getElementById("scene");
  const ctx = canvas.getContext("2d");
  const strip = document.getElementById("strip");
  const sctx = strip.getContext("2d");
  const tooltip = document.getElementById("tooltip");
  const $ = id => document.getElementById(id);
  const api = p => fetch(p).catch(() => {});

  const C = {
    ink: "#1c1e21", ink2: "#5f636a", ink3: "#9a9ea5", grid: "#d3d1c6", paper: "#f4f3ee",
    green: "#1b7f4c", blue: "#2a5db0", orange: "#e0561d", darkRed: "#a3271b", gray: "#8b8f95",
    field: "#e7e9d5", veg: "#8aa06f", contour: "#cec8ab", contourIdx: "#ada377",
    water: "#aac3d2", waterLine: "#5f829a",
  };

  let cfg = null, state = null, prev = null, dpr = 1;
  let az = 0, el = 1.15, camDist = 470, focal = 560, userZoom = 1;
  let viewMode = "top", autoRot = false, userRotated = false;
  let dragging = false, moved = false, lastX = 0, lastY = 0;
  let hot = [], selected = null;
  const layers = { terrain: true, mesh: true, trails: true, ghost: true, labels: false };
  let terrain = null;
  const TGT = [0, 0, 0];
  const TOP_PAD = 76;                     // franja de narración
  const sceneH = () => canvas.clientHeight - 34 - TOP_PAD;
  const disp = {};                        // posiciones interpoladas por nodo
  const hist = { t: [], res: [] }, HIST_MAX = 100;
  let restoredAt = -1e9, restoredName = "";
  let demo = null;

  // ---------- geometría ----------
  const d3 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
  function resize() {
    dpr = window.devicePixelRatio || 1;
    canvas.width = canvas.clientWidth * dpr; canvas.height = canvas.clientHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    strip.width = strip.clientWidth * dpr; strip.height = strip.clientHeight * dpr;
    sctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener("resize", resize);

  function project(p) {
    const x = p[0] - TGT[0], y = p[1] - TGT[1], z = p[2] - TGT[2];
    const ca = Math.cos(az), sa = Math.sin(az);
    const x1 = x * ca + y * sa, y1 = -x * sa + y * ca;
    const ce = Math.cos(el), se = Math.sin(el);
    const y2 = y1 * ce - z * se, z2 = y1 * se + z * ce;
    const depth = Math.max(1, camDist + y2), s = focal / depth;
    return { x: canvas.clientWidth / 2 + x1 * s, y: TOP_PAD + sceneH() / 2 - z2 * s, s, depth };
  }
  function line(a, b, color, width, alpha, dash) {
    ctx.strokeStyle = color; ctx.globalAlpha = alpha == null ? 1 : alpha; ctx.lineWidth = width || 1;
    ctx.setLineDash(dash || []); ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    ctx.setLineDash([]); ctx.globalAlpha = 1;
  }
  function arrow(a, b, color, width, dash) {
    line(a, b, color, width, 1, dash);
    const ang = Math.atan2(b.y - a.y, b.x - a.x);
    ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(b.x, b.y);
    ctx.lineTo(b.x - 9 * Math.cos(ang - .4), b.y - 9 * Math.sin(ang - .4));
    ctx.lineTo(b.x - 9 * Math.cos(ang + .4), b.y - 9 * Math.sin(ang + .4)); ctx.closePath(); ctx.fill();
  }
  function polyline3(pts, color, width, alpha, dash) {
    if (!pts || pts.length < 2) return;
    ctx.strokeStyle = color; ctx.globalAlpha = alpha; ctx.lineWidth = width; ctx.setLineDash(dash || []);
    ctx.beginPath();
    pts.forEach((p, i) => { const q = project(p); i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y); });
    ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
  }
  function halo(text, x, y, color, font, align) {
    ctx.font = font || "11px Consolas, monospace"; ctx.textAlign = align || "left";
    ctx.lineWidth = 3.5; ctx.strokeStyle = "rgba(244,243,238,.92)"; ctx.strokeText(text, x, y);
    ctx.fillStyle = color; ctx.fillText(text, x, y); ctx.textAlign = "left";
  }
  function drawDrone(q, size, color, fill) {
    ctx.strokeStyle = color; ctx.lineWidth = 1.5;
    [[-1, -1], [1, -1], [1, 1], [-1, 1]].forEach(([ax, ay]) => {
      ctx.beginPath(); ctx.moveTo(q.x, q.y); ctx.lineTo(q.x + ax * size, q.y + ay * size); ctx.stroke();
      ctx.beginPath(); ctx.arc(q.x + ax * size, q.y + ay * size, size * .5, 0, 7); ctx.fillStyle = C.paper; ctx.fill(); ctx.stroke();
    });
    ctx.beginPath(); ctx.arc(q.x, q.y, size * .55, 0, 7); ctx.fillStyle = fill; ctx.fill(); ctx.stroke();
  }
  function drawDisc(cx, cy, r, color) {
    ctx.beginPath();
    for (let i = 0; i <= 48; i++) { const a = i / 48 * Math.PI * 2, q = project([cx + r * Math.cos(a), cy + r * Math.sin(a), 0]); i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y); }
    ctx.closePath(); ctx.fillStyle = color; ctx.globalAlpha = .08; ctx.fill(); ctx.globalAlpha = 1;
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
    for (let k = -r; k <= r; k += 9) { const h = Math.sqrt(Math.max(0, r * r - k * k)); line(project([cx + k, cy - h, 0]), project([cx + k, cy + h, 0]), color, 1, .3); }
  }

  // ---------- terreno (relieve topográfico, sobre el plano del suelo) ----------
  function mulberry32(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function catmull(pts, samples) {
    const out = [], P = i => pts[Math.max(0, Math.min(pts.length - 1, i))];
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = P(i - 1), p1 = P(i), p2 = P(i + 1), p3 = P(i + 2);
      for (let s = 0; s < samples; s++) {
        const t = s / samples, t2 = t * t, t3 = t2 * t;
        out.push([
          .5 * (2 * p1[0] + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
          .5 * (2 * p1[1] + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3),
        ]);
      }
    }
    out.push(pts[pts.length - 1]);
    return out;
  }
  function buildTerrain(ext) {
    const rng = mulberry32(7);
    const hills = [{ x: -165, y: -125, a: 1.0, s: 78 }, { x: 150, y: 165, a: .72, s: 66 },
                   { x: 195, y: 35, a: .5, s: 46 }, { x: -120, y: 130, a: -.55, s: 40 }];
    const H = (x, y) => { let h = 0; for (const k of hills) { const dx = x - k.x, dy = y - k.y; h += k.a * Math.exp(-(dx * dx + dy * dy) / (2 * k.s * k.s)); } return h + .05 * Math.sin(x / 38) * Math.cos(y / 44); };
    const G = 76, d = 2 * ext / G, grid = [];
    for (let j = 0; j <= G; j++) { grid[j] = []; for (let i = 0; i <= G; i++) grid[j][i] = H(-ext + i * d, -ext + j * d); }
    let hmax = 0; grid.forEach(r => r.forEach(v => { if (v > hmax) hmax = v; }));
    const contours = [];
    for (let l = 1; l <= 6; l++) {
      const lv = hmax * l / 7, segs = [], idx = l % 2 === 0;
      for (let j = 0; j < G; j++) for (let i = 0; i < G; i++) {
        const xl = -ext + i * d, xr = xl + d, yt = -ext + j * d, yb = yt + d;
        const tl = grid[j][i], tr = grid[j][i + 1], br = grid[j + 1][i + 1], bl = grid[j + 1][i];
        const c = (tl > lv ? 8 : 0) | (tr > lv ? 4 : 0) | (br > lv ? 2 : 0) | (bl > lv ? 1 : 0);
        if (c === 0 || c === 15) continue;
        const eT = [xl + d * (lv - tl) / (tr - tl || 1e-6), yt], eR = [xr, yt + d * (lv - tr) / (br - tr || 1e-6)],
              eB = [xl + d * (lv - bl) / (br - bl || 1e-6), yb], eL = [xl, yt + d * (lv - tl) / (bl - tl || 1e-6)];
        const s = (a, b) => segs.push([a[0], a[1], b[0], b[1]]);
        switch (c) {
          case 1: case 14: s(eL, eB); break;
          case 2: case 13: s(eB, eR); break;
          case 3: case 12: s(eL, eR); break;
          case 4: case 11: s(eT, eR); break;
          case 6: case 9: s(eT, eB); break;
          case 7: case 8: s(eT, eL); break;
          case 5: s(eT, eL); s(eB, eR); break;
          case 10: s(eT, eR); s(eB, eL); break;
        }
      }
      contours.push({ segs, idx });
    }
    const river = catmull([[-120, 128], [-55, 150], [35, 152], [120, 128], [170, 62], [212, -45], [235, -160]], 10);
    const lake = []; const lc = [-120, 130];
    for (let a = 0; a <= Math.PI * 2 + .01; a += Math.PI / 18) { const r = 30 + 7 * Math.sin(a * 3 + 1); lake.push([lc[0] + r * Math.cos(a) * 1.25, lc[1] + r * Math.sin(a) * .8]); }
    const patches = []; [[120, -155], [-55, -25], [80, 95]].forEach(c => { for (let k = 0; k < 44; k++) patches.push([c[0] + (rng() - .5) * 78, c[1] + (rng() - .5) * 66, 1.6 + rng() * 2.2]); });
    return { contours, river, lake, patches, hills };
  }
  function drawTerrain() {
    if (!terrain) return;
    const e = cfg.extent;
    ctx.beginPath();
    [[-e, -e], [e, -e], [e, e], [-e, e]].forEach((p, i) => { const q = project([p[0], p[1], 0]); i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y); });
    ctx.closePath(); ctx.fillStyle = C.field; ctx.globalAlpha = .55; ctx.fill(); ctx.globalAlpha = 1;
    ctx.fillStyle = C.veg; ctx.globalAlpha = .42;
    terrain.patches.forEach(p => { const q = project([p[0], p[1], 0]); ctx.beginPath(); ctx.arc(q.x, q.y, Math.max(1, p[2] * q.s), 0, 7); ctx.fill(); });
    ctx.globalAlpha = 1;
    terrain.contours.forEach(L => {
      ctx.strokeStyle = L.idx ? C.contourIdx : C.contour; ctx.lineWidth = L.idx ? 1.1 : .7; ctx.globalAlpha = L.idx ? .8 : .55;
      ctx.beginPath();
      L.segs.forEach(sg => { const a = project([sg[0], sg[1], 0]), b = project([sg[2], sg[3], 0]); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); });
      ctx.stroke(); ctx.globalAlpha = 1;
    });
    ctx.beginPath();
    terrain.lake.forEach((p, i) => { const q = project([p[0], p[1], 0]); i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y); });
    ctx.closePath(); ctx.fillStyle = C.water; ctx.globalAlpha = .6; ctx.fill(); ctx.globalAlpha = 1;
    ctx.strokeStyle = C.waterLine; ctx.lineWidth = 1; ctx.stroke();
    ctx.strokeStyle = C.waterLine; ctx.lineWidth = 2.4; ctx.globalAlpha = .72; ctx.setLineDash([]);
    ctx.beginPath();
    terrain.river.forEach((p, i) => { const q = project([p[0], p[1], 0]); i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y); });
    ctx.stroke(); ctx.globalAlpha = 1;
    const sh = project([terrain.hills[0].x, terrain.hills[0].y, 0]);
    halo("Sierra", sh.x, sh.y, C.contourIdx, "italic 11px Georgia, serif", "center");
    const lk = project([-120, 130, 0]);
    halo("Laguna", lk.x, lk.y, C.waterLine, "italic 10px Georgia, serif", "center");
  }

  // ---------- interpolación de movimiento ----------
  function smooth() {
    if (!state) return;
    state.drones.forEach(d => {
      let s = disp[d.id];
      if (!s || d3(s.t, d.true) > 120) { disp[d.id] = { t: [...d.true], g: [...d.gps] }; return; }
      for (let k = 0; k < 3; k++) { s.t[k] += (d.true[k] - s.t[k]) * .2; s.g[k] += (d.gps[k] - s.g[k]) * .2; }
    });
  }

  // ---------- cámara ----------
  function updateCamera() {
    const W = canvas.clientWidth, H = sceneH();
    let target = null, elGoal = el;
    if (viewMode === "top") {
      // planta: lente larga (poca perspectiva), norte arriba, encuadre fijo sobre la misión
      target = [0, 0, 0]; elGoal = 1.25;
      if (!userRotated && !dragging) az += (0 - az) * .1;
      focal += (2600 - focal) * .12;
      const s = Math.min(W, H) / (2 * 300) * userZoom;
      camDist += (focal / s - camDist) * .12;
    } else if (viewMode === "follow" && state) {
      const d = state.drones.find(x => x.id === selected) || state.drones[0];
      target = disp[d.id] ? disp[d.id].t : d.true; elGoal = .55;
      focal += (560 - focal) * .12;
      camDist += (330 * userZoom - camDist) * .1;
    } else if (state) {
      focal += (560 - focal) * .12;
      const arr = state.drones.filter(d => !d.down && d.status !== "captured");
      const src = arr.length ? arr : state.drones;
      target = [0, 0, 0]; src.forEach(d => { const p = disp[d.id] ? disp[d.id].t : d.true; target[0] += p[0]; target[1] += p[1]; target[2] += p[2]; });
      target = target.map(v => v / src.length); elGoal = .6;
      if (autoRot && !dragging) az += .0012;
      camDist += (470 * userZoom - camDist) * .1;
    }
    if (target) for (let k = 0; k < 3; k++) TGT[k] += (target[k] - TGT[k]) * .06;
    // sólo volver a la inclinación por defecto si el usuario no rotó a mano
    // (una vez que arrastró, la cámara se queda donde la dejó; doble clic resetea)
    if (!dragging && !userRotated) el += (elGoal - el) * .08;
  }

  // ---------- escena ----------
  function render(now) {
    requestAnimationFrame(render);
    if (!cfg) return;
    smooth(); updateCamera();
    const W = canvas.clientWidth, H = canvas.clientHeight;
    ctx.clearRect(0, 0, W, H); hot = [];
    if (layers.terrain) drawTerrain();
    const e = cfg.extent, step = 50;
    for (let g = -e; g <= e; g += step) { line(project([g, -e, 0]), project([g, e, 0]), C.grid, 1, .8); line(project([-e, g, 0]), project([e, g, 0]), C.grid, 1, .8); }
    const n0 = project([-e + 14, e - 50, 0]), n1 = project([-e + 14, e - 14, 0]);
    arrow(n0, n1, C.ink, 1.2); halo("N", n1.x + 6, n1.y + 4, C.ink, "bold 11px Georgia, serif");
    const s0 = project([-e + 14, -e + 14, 0]), s1 = project([-e + 14 + 100, -e + 14, 0]);
    line(s0, s1, C.ink, 1.4); halo("100 m", s1.x + 5, s1.y + 4, C.ink2, "10px Consolas, monospace");
    const path = cfg.path.map(p => [p[0], p[1], cfg.z0]); path.push(path[0]);
    polyline3(path, C.ink3, 1, .7, [6, 5]);
    const pl = project([cfg.path[18][0], cfg.path[18][1], cfg.z0]);
    halo("ruta de la misión", pl.x + 6, pl.y - 6, C.ink3, "italic 10.5px Georgia, serif");
    drawDisc(cfg.trap.x, cfg.trap.y, cfg.trap.r, C.orange);
    const tl = project([cfg.trap.x, cfg.trap.y, 0]);
    halo("ZONA TRAMPA", tl.x, tl.y - 6, C.orange, "bold 11px Georgia, serif", "center");
    halo("adonde el atacante quiere llevar al dron", tl.x, tl.y + 8, C.orange, "italic 9.5px Georgia, serif", "center");
    if (cfg.base) {
      const bq = project([cfg.base.x, cfg.base.y, 0]);
      ctx.strokeStyle = C.ink; ctx.lineWidth = 1.5;
      ctx.strokeRect(bq.x - 6, bq.y - 4, 12, 8);
      ctx.beginPath(); ctx.moveTo(bq.x - 7, bq.y - 4); ctx.lineTo(bq.x, bq.y - 11); ctx.lineTo(bq.x + 7, bq.y - 4); ctx.stroke();
      halo("BASE (GCS)", bq.x, bq.y + 16, C.ink2, "bold 10px Georgia, serif", "center");
    }
    if (!state) return;

    const D = state.drones, pulse = .5 + .5 * Math.sin(now / 350);
    const thr = state.params.vote_thresh, activeN = state.stats.active;
    const P = d => disp[d.id] ? disp[d.id].t : d.true, G = d => disp[d.id] ? disp[d.id].g : d.gps;
    const isActive = d => !d.down && d.status !== "captured";
    const attacked = D.find(d => (d.spoof || d.status === "mitigated") && isActive(d));

    // malla de ranging
    if (layers.mesh) for (let i = 0; i < D.length; i++) {
      if (!isActive(D[i])) continue;
      for (let j = i + 1; j < D.length; j++) {
        if (!isActive(D[j])) continue;
        const hotLink = attacked && (D[i].id === attacked.id || D[j].id === attacked.id);
        line(project(P(D[i])), project(P(D[j])), hotLink ? C.orange : C.ink3, hotLink ? 1.3 : .7, hotLink ? .55 : .3);
      }
    }
    // marcas de voto sobre los enlaces del nodo atacado
    if (attacked && layers.mesh) D.forEach(j => {
      if (j.id === attacked.id || !isActive(j)) return;
      const implied = d3(G(attacked), G(j)), measured = d3(P(attacked), P(j)), dlt = Math.abs(implied - measured);
      const a = project(P(j)), b = project(P(attacked));
      const mx = a.x + (b.x - a.x) * .42, my = a.y + (b.y - a.y) * .42;
      const bad = dlt > thr;
      halo(`${bad ? "✗" : "✓"} ${dlt.toFixed(0)} m`, mx, my + 4, bad ? C.orange : C.ink3, `${bad ? "bold " : ""}10px Consolas, monospace`, "center");
    });
    // arrastre hacia la trampa
    if (attacked && attacked.spoof && attacked.status !== "mitigated") {
      const a = project(P(attacked)), b = project([cfg.trap.x, cfg.trap.y, 0]);
      arrow(a, b, C.orange, 1, [5, 5]);
    }
    // enlaces de comunicaciones: directo a la base, relevo (puente) o aislado
    const R = state.res || {};
    if (cfg.base) {
      const bq = project([cfg.base.x, cfg.base.y, 0]);
      D.forEach(d => {
        if (!isActive(d)) return;
        const q = project(P(d));
        if (d.relay_via != null) {
          const relay = D.find(x => x.id === d.relay_via);
          if (relay) { line(q, project(P(relay)), C.blue, 1.6, .75, [3, 3]); halo("relevo", (q.x + project(P(relay)).x) / 2, (q.y + project(P(relay)).y) / 2 - 4, C.blue, "9px Consolas, monospace", "center"); }
        } else if (d.gcs_link) {
          line(q, bq, C.green, .8, .2);
        } else {
          halo("⚠ sin enlace", q.x + 8, q.y + 15, C.darkRed, "bold 10px Consolas, monospace");
        }
      });
    }
    // punto de reunión (dispersión táctica)
    if (R.scatter && R.rendezvous) {
      const rq = project([R.rendezvous[0], R.rendezvous[1], cfg.z0]);
      ctx.strokeStyle = C.orange; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(rq.x, rq.y, 10, 0, 7); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(rq.x - 14, rq.y); ctx.lineTo(rq.x + 14, rq.y); ctx.moveTo(rq.x, rq.y - 14); ctx.lineTo(rq.x, rq.y + 14); ctx.stroke();
      halo("punto de reunión", rq.x, rq.y - 16, C.orange, "italic 10px Georgia, serif", "center");
    }

    const order = D.map((d, i) => ({ i, depth: project(P(d)).depth })).sort((a, b) => b.depth - a.depth);
    for (const { i } of order) {
      const d = D[i], p = P(d), g = G(d), qt = project(p);
      const isSel = selected === d.id, isAtt = attacked && attacked.id === d.id;
      const div = Math.hypot(g[0] - p[0], g[1] - p[1]);

      if (layers.trails) polyline3(d.true_trail, d.status === "captured" ? C.darkRed : d.down ? C.gray : C.green, isAtt ? 1.8 : 1.1, isAtt ? .9 : .35, [1, 3]);
      if (layers.ghost && div > 4 && d.status !== "captured") {
        polyline3(d.gps_trail, C.blue, 1.1, .55, [2, 3]);
        const qg = project(g);
        arrow(qt, qg, C.blue, 1.2, [4, 3]);
        ctx.strokeStyle = C.blue; ctx.lineWidth = 1.4; ctx.strokeRect(qg.x - 5, qg.y - 5, 10, 10);
        halo(`el GPS dice que está acá (+${div.toFixed(0)} m)`, qg.x + 9, qg.y + 4, C.blue, "10.5px Georgia, serif");
        hot.push({ x: qg.x, y: qg.y, r: 9, id: d.id, info: { title: `${d.name} · posición según GPS`, rows: [["Estado", "FALSA (spoofing)"], ["Desvío", `${div.toFixed(0)} m de la posición real`]] } });
      }
      if (d.est && d.status === "mitigated") {
        const qe = project(d.est);
        ctx.strokeStyle = C.green; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(qe.x, qe.y, 7, 0, 7); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(qe.x - 10, qe.y); ctx.lineTo(qe.x + 10, qe.y); ctx.moveTo(qe.x, qe.y - 10); ctx.lineTo(qe.x, qe.y + 10); ctx.stroke();
        halo("posición reconstruida por los vecinos", qe.x + 12, qe.y + 14, C.green, "italic 10px Georgia, serif");
      }

      let col = C.green, fill = C.green;
      if (d.down) { col = C.gray; fill = C.paper; }
      else if (d.status === "captured") { col = C.darkRed; fill = C.darkRed; }
      else if (d.status === "mitigated") { col = C.ink; fill = C.green; }
      else if (d.spoof) { col = C.ink; fill = C.blue; }
      const size = Math.max(6, 9 * qt.s);
      drawDrone(qt, size, col, fill);
      if (d.status === "mitigated") { ctx.strokeStyle = C.orange; ctx.lineWidth = 2; ctx.setLineDash([4, 3]); ctx.beginPath(); ctx.arc(qt.x, qt.y, size * 2.2 + 4 * pulse, 0, 7); ctx.stroke(); ctx.setLineDash([]); }
      if (isSel) { ctx.strokeStyle = C.ink; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(qt.x, qt.y, size * 2.8, 0, 7); ctx.stroke(); }

      if (layers.labels || isAtt || isSel || d.down || d.status === "captured" || d.id === 0) {
        let tagc = C.ink, tag = d.name;
        if (d.spoof && d.status === "ok") { tag += " · bajo ataque"; tagc = C.blue; }
        if (d.status === "mitigated") { tag += " · aislado, volviendo"; tagc = C.orange; }
        if (d.status === "captured") { tag += " · CAPTURADO"; tagc = C.darkRed; }
        if (d.down) { tag += " · sin enlace"; tagc = C.gray; }
        halo(tag, qt.x + size * 1.6, qt.y - size * 1.2, tagc, `${isAtt || isSel ? "bold " : ""}10.5px Consolas, monospace`);
        if (isAtt) halo(`${d.votes} de ${activeN - 1} vecinos en desacuerdo`, qt.x + size * 1.6, qt.y - size * 1.2 + 13, C.ink2, "10px Consolas, monospace");
      }
      hot.push({ x: qt.x, y: qt.y, r: size * 2, id: d.id, info: {
        title: `${d.name} — ${d.down ? "sin enlace (HPM)" : d.status === "captured" ? "capturado" : d.status === "mitigated" ? "aislado por consenso" : d.spoof ? "bajo ataque" : "nominal"}`,
        rows: [["Integridad GPS", d.status === "mitigated" ? "spoofeado, corregido" : d.status === "captured" ? "spoofeado, sin defensa" : d.down ? "—" : d.spoof ? "spoofeado, aún no confirmado" : "OK"],
               ["Vecinos en desacuerdo", `${d.votes} / ${activeN - 1}`], ["Residual", `${d.residual} m`], ["Posición real", `${d.true[0]}, ${d.true[1]} · ${d.true[2]} m`]],
      }});
    }
  }

  // ---------- narración ----------
  function narrate() {
    const D = state.drones, box = $("story");
    const need = Math.floor((state.stats.active - 1) * .5) + 1;
    const cap = D.find(d => d.status === "captured"), mit = D.find(d => d.status === "mitigated"),
          sp = D.find(d => d.spoof && d.status === "ok"), down = D.find(d => d.down);
    let step, title, text, cls = "";
    if (cap) { step = "6 · 6"; title = `${cap.name} fue capturado`; cls = "captured";
      text = `Sin consenso, nadie contradijo a su GPS y ${cap.name} siguió derecho a la zona trampa. <b>Esto es lo que pasa hoy con un dron aislado.</b>`; }
    else if (mit && !mit.spoof) { step = "5 · 6"; title = "El atacante desiste"; cls = "mitig";
      text = `${mit.name} vuelve a su puesto guiado por sus vecinos. Cuando la señal GPS vuelve a coincidir con las distancias medidas, recupera la confianza en su receptor.`; }
    else if (mit) { step = "4 · 6"; title = `Consenso: ${mit.name} aislado y corregido`; cls = "mitig";
      text = `${mit.votes} de ${state.stats.active - 1} vecinos lo contradicen. El enjambre <b>deja de creerle a su GPS</b>, reconstruye su posición real triangulando las distancias medidas (marca ⊕) y ${mit.name} vuelve solo a la formación.`; }
    else if (sp && !state.defense) { step = "3 · 6"; title = "Sin defensa: nadie lo contradice"; cls = "captured";
      text = `${sp.name} confía ciegamente en su GPS. Los vecinos miden distancias que no cuadran (✗), pero con la defensa desactivada <b>no votan ni corrigen</b>. Sigue derecho a la trampa.`; }
    else if (sp && sp.votes >= need) { step = "3 · 6"; title = "Los vecinos votan"; cls = "vote";
      text = `La distancia que los vecinos <b>miden por radio</b> hasta ${sp.name} no coincide con la que <b>implica su GPS</b> (marcas ✗). ${sp.votes} de ${state.stats.active - 1} ya lo contradicen; falta sostenerlo un instante para confirmar.`; }
    else if (sp) { step = "2 · 6"; title = `Ataque: señal GPS falsa a ${sp.name}`; cls = "attack";
      text = `${sp.name} <b>cree seguir en formación</b> (cuadrado azul), pero su GPS miente y físicamente es arrastrado hacia la trampa (flecha naranja). Él solo no tiene forma de notarlo.`; }
    else if (down) { step = "HPM"; title = `Pulso de energía dirigida sobre ${down.name}`; cls = "vote";
      text = `${down.name} perdió el enlace y cae. El enjambre <b>reconfigura la formación</b> con los que quedan; al reiniciarse, ${down.name} se reintegra solo.`; }
    else if (performance.now() - restoredAt < 5000) { step = "5 · 6"; title = "Ataque neutralizado"; cls = "mitig";
      text = `${restoredName} recuperó la integridad de su GPS y está de vuelta en su puesto. <b>El enjambre nunca dejó de volar la misión.</b>`; }
    else { step = "1 · 6"; title = "Formación nominal";
      text = `Siete drones vuelan la misión en cuña. Cada uno <b>mide por radio</b> la distancia a sus vecinos (líneas grises): esa distancia física no se puede falsificar desde lejos.`; }
    box.className = "story " + cls;
    $("story-step").textContent = step; $("story-title").textContent = title; $("story-text").innerHTML = text;
  }

  // ---------- demostración guiada ----------
  function demoNote(t) { const el = $("story-demo"); el.innerHTML = `<i></i>${t}`; el.classList.remove("hidden"); }
  function stopDemo(done) {
    if (demo) demo.timers.forEach(clearTimeout);
    demo = null; $("btn-demo").classList.remove("running");
    $("btn-demo").innerHTML = "▶ Demostración guiada <small>≈ 70 s, se explica sola</small>";
    if (done) demoNote("Fin de la demostración · Reiniciar ensayo para repetir"); else $("story-demo").classList.add("hidden");
  }
  function runDemo() {
    if (demo) { stopDemo(false); return; }
    demo = { timers: [] };
    const at = (ms, fn) => demo.timers.push(setTimeout(fn, ms));
    $("btn-demo").classList.add("running"); $("btn-demo").innerHTML = "■ Detener demostración <small>en curso</small>";
    api("/api/reset"); api("/api/defense?on=1"); api("/api/param?bias=5&vote=12"); selected = null;
    hist.t.length = 0; hist.res.length = 0;
    demoNote("Demo guiada · 1 de 3 · observá la formación");
    at(6000, () => { demoNote("Demo guiada · 2 de 3 · ataque con la defensa activa"); api("/api/spoof?node=2"); });
    at(30000, () => { demoNote("Demo guiada · 3 de 3 · el mismo ataque, sin defensa"); api("/api/defense?on=0"); api("/api/param?bias=14"); });
    at(33000, () => {
      // el atacante elige al dron más cercano a la trampa y aprieta el arrastre
      let best = null, bd = 1e9;
      (state ? state.drones : []).forEach(d => { if (d.status !== "ok" || d.down) return; const dd = Math.hypot(d.true[0] - cfg.trap.x, d.true[1] - cfg.trap.y); if (dd < bd) { bd = dd; best = d; } });
      api(`/api/spoof?node=${best ? best.id : 4}`);
    });
    at(68000, () => { api("/api/defense?on=1"); api("/api/param?bias=5"); stopDemo(true); });
  }

  // ---------- banner de modos de resiliencia ----------
  function updateResBanner(R) {
    let el = document.getElementById("res-banner");
    if (!el) {
      el = document.createElement("div"); el.id = "res-banner";
      el.style.cssText = "position:absolute;left:50%;transform:translateX(-50%);top:82px;z-index:6;display:flex;gap:8px;pointer-events:none;font:700 11px/1 Consolas,monospace;text-align:center;";
      (document.getElementById("scene-wrap") || document.body).appendChild(el);
    }
    const chips = [];
    if (R.gps_killed) chips.push(["GPS DE ENJAMBRE APAGADO · vuelo inercial/UWB", C.orange]);
    if (R.blackout) chips.push(["SIN ENLACE CON LA BASE · retorno coordinado", C.darkRed]);
    else if (R.jamming) chips.push(["JAMMING · relevo / línea de vista", C.orange]);
    if (R.shadow && R.shadow.length && !R.blackout) chips.push(["RELEVO DE COMMS activo", C.blue]);
    if (R.scatter) chips.push(["DISPERSIÓN TÁCTICA · reagrupando", C.orange]);
    el.innerHTML = chips.map(c => `<span style="background:${c[1]};color:#fff;padding:5px 10px;border-radius:3px;box-shadow:0 1px 5px rgba(0,0,0,.28)">${c[0]}</span>`).join("");
  }

  // ---------- escenarios guiados de resiliencia ----------
  function runScenario(name) {
    if (demo) stopDemo(false);
    demo = { timers: [] };
    const at = (ms, fn) => demo.timers.push(setTimeout(fn, ms));
    $("btn-demo").classList.add("running"); $("btn-demo").innerHTML = "■ Detener escenario <small>en curso</small>";
    api("/api/reset"); api("/api/defense?on=1"); api("/api/param?bias=5&vote=12"); selected = null;
    hist.t.length = 0; hist.res.length = 0;
    if (name === "masspoof") {
      demoNote("Escenario · Spoofing masivo → el enjambre apaga su GPS");
      for (let i = 0; i < 6; i++) at(1500 + i * 280, () => api(`/api/spoof?node=${i}`));
      at(11000, () => demoNote("≥80% reporta GPS inconsistente → VOTO DE ENJAMBRE: GPS apagado, vuelo inercial/UWB"));
      at(22000, () => stopDemo(true));
    } else if (name === "relay") {
      demoNote("Escenario · Relevo de comunicaciones (sombra de terreno)");
      at(3000, () => { api("/api/shadow?node=6"); demoNote("DELTA-2 entra en sombra de terreno: pierde enlace DIRECTO con la base"); });
      at(7000, () => demoNote("Un compañero se vuelve PUENTE repetidor → DELTA-2 sigue conectado (línea azul)"));
      at(18000, () => stopDemo(true));
    } else if (name === "jam") {
      demoNote("Escenario · Jamming total → retorno coordinado");
      at(3000, () => { api("/api/jamming"); demoNote("Inhibidor enemigo: se corta el enlace de radio con la base"); });
      at(7000, () => demoNote("Ascenso escalonado para recuperar línea de vista…"));
      at(11000, () => demoNote("…sin éxito → RETORNO COORDINADO por el último vector limpio"));
      at(22000, () => stopDemo(true));
    } else if (name === "energy") {
      demoNote("Escenario · Relevo de liderazgo por energía");
      at(2500, () => { api("/api/lowbatt"); demoNote("El líder (ALFA) se queda sin batería…"); });
      at(6500, () => demoNote("…el dron con más batería asume el mando (★); el agotado se repliega"));
      at(18000, () => stopDemo(true));
    } else if (name === "scatter") {
      demoNote("Escenario · Dispersión táctica y reagrupe");
      at(2500, () => { api("/api/scatter"); demoNote("Amenaza detectada (radar): el enjambre se DISPERSA con maniobras evasivas"); });
      at(9000, () => demoNote("Convergen al punto de reunión memorizado (rendezvous)…"));
      at(18000, () => demoNote("…formación restablecida"));
      at(22000, () => stopDemo(true));
    }
  }

  // ---------- strip chart ----------
  function drawStrip() {
    const W = strip.clientWidth, H = strip.clientHeight;
    sctx.clearRect(0, 0, W, H);
    if (!state || !hist.t.length) return;
    const thr = state.params.vote_thresh;
    let maxv = thr * 2.2; hist.res.forEach(r => r.forEach(v => { if (v > maxv) maxv = v; })); maxv *= 1.1;
    const padL = 30, padB = 14, padT = 6;
    const X = i => padL + (i / (HIST_MAX - 1)) * (W - padL - 6), Y = v => padT + (1 - v / maxv) * (H - padT - padB);
    sctx.strokeStyle = C.grid; sctx.lineWidth = 1;
    [0, .5, 1].forEach(f => { sctx.beginPath(); sctx.moveTo(padL, Y(maxv * f)); sctx.lineTo(W - 6, Y(maxv * f)); sctx.stroke(); });
    sctx.fillStyle = C.ink3; sctx.font = "9px Consolas, monospace"; sctx.textAlign = "right";
    [0, .5, 1].forEach(f => sctx.fillText(Math.round(maxv * f) + "", padL - 3, Y(maxv * f) + 3));
    sctx.textAlign = "left"; sctx.fillText("m", 2, padT + 8); sctx.fillText("−30 s", padL, H - 3); sctx.textAlign = "right"; sctx.fillText("ahora", W - 6, H - 3); sctx.textAlign = "left";
    sctx.strokeStyle = C.orange; sctx.setLineDash([4, 3]); sctx.beginPath(); sctx.moveTo(padL, Y(thr)); sctx.lineTo(W - 6, Y(thr)); sctx.stroke(); sctx.setLineDash([]);
    const n = hist.t.length, off = HIST_MAX - n, D = state.drones;
    const series = (k, color, width) => {
      sctx.strokeStyle = color; sctx.lineWidth = width; sctx.beginPath();
      for (let i = 0; i < n; i++) { const v = hist.res[i][k]; if (v == null) continue; const x = X(off + i), y = Y(v); i ? sctx.lineTo(x, y) : sctx.moveTo(x, y); }
      sctx.stroke();
    };
    D.forEach((d, k) => { if (d.id !== selected && d.status !== "mitigated" && !d.spoof) series(k, C.ink3, 1); });
    D.forEach((d, k) => { if (d.status === "mitigated" || d.spoof || d.status === "captured") series(k, C.orange, 1.6); });
    if (selected != null) { const k = D.findIndex(d => d.id === selected); if (k >= 0) series(k, C.ink, 2); }
  }

  // ---------- interacción ----------
  canvas.addEventListener("mousedown", e => { dragging = true; moved = false; lastX = e.clientX; lastY = e.clientY; });
  window.addEventListener("mouseup", e => {
    if (dragging && !moved) { const h = hitAt(e); selected = h ? h.id : null; renderPanel(); }
    dragging = false;
  });
  window.addEventListener("mousemove", e => {
    if (dragging) {
      const dx = e.clientX - lastX, dy = e.clientY - lastY;
      if (Math.abs(dx) + Math.abs(dy) > 2) { moved = true; autoRot = false; userRotated = true; }
      az += dx * .006; el = Math.max(.12, Math.min(1.5, el + dy * .005));
      lastX = e.clientX; lastY = e.clientY; return;
    }
    if (e.target !== canvas) { tooltip.classList.add("hidden"); return; }
    const rect = canvas.getBoundingClientRect(), mx = e.clientX - rect.left, my = e.clientY - rect.top, h = hitAt(e);
    if (!h) { tooltip.classList.add("hidden"); return; }
    tooltip.innerHTML = `<div class="tt-title">${h.info.title}</div>` + h.info.rows.map(r => `<div class="tt-row">${r[0]}: <b>${r[1]}</b></div>`).join("");
    tooltip.style.left = Math.min(mx + 16, rect.width - 250) + "px"; tooltip.style.top = (my + 16) + "px";
    tooltip.classList.remove("hidden");
  });
  canvas.addEventListener("mouseleave", () => tooltip.classList.add("hidden"));
  canvas.addEventListener("wheel", e => { e.preventDefault(); userZoom = Math.max(.5, Math.min(2.5, userZoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1))); }, { passive: false });
  canvas.addEventListener("dblclick", () => { userZoom = 1; userRotated = false; });
  function hitAt(e) {
    const rect = canvas.getBoundingClientRect(), mx = e.clientX - rect.left, my = e.clientY - rect.top;
    let f = null, bd = 1e9;
    for (const h of hot) { const d = Math.hypot(h.x - mx, h.y - my); if (d < h.r && d < bd) { bd = d; f = h; } }
    return f;
  }

  // ---------- panel ----------
  function setLamp(id, cls) { $(id).className = "lamp " + cls; }
  function renderPanel() {
    if (!state) return;
    const D = state.drones;
    $("clock").textContent = state.clock;
    const anyCap = D.some(d => d.status === "captured"), anyDown = D.some(d => d.down), anyAtk = D.some(d => d.spoof || d.status === "mitigated");
    const R = state.res || {};
    setLamp("lamp-swarm", anyCap ? "bad" : (anyDown || R.scatter) ? "warn" : "ok");
    setLamp("lamp-gps", R.gps_killed ? "warn" : anyCap ? "bad" : anyAtk ? "warn" : "ok");
    setLamp("lamp-link", R.blackout ? "bad" : (R.jamming || (R.shadow && R.shadow.length)) ? "warn" : anyDown ? "warn" : "ok");
    setLamp("lamp-def", state.defense ? "ok" : "off");
    updateResBanner(R);
    const tgt = D.find(d => d.id === selected);
    $("target-name").textContent = tgt ? tgt.name : "nodo aleatorio";
    $("btn-def").classList.toggle("on", state.defense);
    $("def-state").textContent = state.defense ? "ACTIVA" : "DESACTIVADA"; $("def-state").classList.toggle("off", !state.defense);
    if (document.activeElement !== $("bias")) { $("bias").value = state.params.bias_rate; $("bias-val").textContent = `${Math.round(state.params.bias_rate)} m/s`; }
    if (document.activeElement !== $("vote")) { $("vote").value = state.params.vote_thresh; $("vote-val").textContent = `${Math.round(state.params.vote_thresh)} m`; }
    document.querySelectorAll(".speed button[data-x]").forEach(b => b.classList.toggle("on", +b.dataset.x === state.speed));
    $("btn-pause").classList.toggle("on", state.paused); $("btn-pause").textContent = state.paused ? "Reanudar" : "Pausa";

    $("nodes").innerHTML = D.map(d => {
      const st = d.down ? "down" : d.status;
      const label = { ok: d.spoof ? "bajo ataque" : "nominal", mitigated: "aislado", down: "sin enlace", captured: "CAPTURADO" }[st];
      const w = Math.min(60, d.residual / Math.max(1, state.params.vote_thresh) * 20);
      const rol = d.role === "leader" ? ' <span title="líder">★</span>' : d.role === "rtb" ? ' <span title="regreso a base">⏎</span>' : "";
      const iner = d.nav_mode === "inertial" ? ' <span class="tagi">INER</span>' : (!d.gcs_link && !d.down ? ' <span class="tagi off">⚠</span>' : "");
      let bat = "";
      if (d.battery != null) {
        const bc = d.battery < 18 ? "crit" : d.battery < 35 ? "low" : "";
        bat = `<span class="batt ${bc}"><span style="width:${Math.max(0, Math.min(100, d.battery))}%"></span></span>${d.battery.toFixed(0)}%`;
      } else bat = "—";
      return `<tr data-id="${d.id}" class="${d.id === selected ? "sel" : ""}"><td><span class="dot ${st}"></span></td><td>${d.name}${rol}</td><td class="st-${st}">${label}${iner}</td><td class="r">${d.residual.toFixed(1)}<span class="bar ${d.residual > state.params.vote_thresh ? "hot" : ""}" style="width:${w}px"></span></td><td class="r">${d.votes}</td><td class="r nowrap">${bat}</td></tr>`;
    }).join("");
    document.querySelectorAll("#nodes tr").forEach(tr => tr.onclick = () => { selected = selected === +tr.dataset.id ? null : +tr.dataset.id; renderPanel(); });
    $("log").innerHTML = state.alerts.length ? state.alerts.slice(0, 40).map(a => `<div class="entry ${a.level}"><span class="e-t">${a.t}</span><span class="e-x">${a.text}</span></div>`).join("") : '<div class="empty">Sin novedades. El enjambre se vigila mutuamente.</div>';
    narrate();
  }

  function wire() {
    const node = () => selected != null ? `?node=${selected}` : "";
    $("btn-spoof").onclick = () => api("/api/spoof" + node());
    $("btn-hpm").onclick = () => api("/api/hpm" + node());
    $("btn-clear").onclick = () => { selected = null; renderPanel(); };
    $("btn-def").onclick = () => api(`/api/defense?on=${state && state.defense ? 0 : 1}`);
    $("btn-reset").onclick = () => { stopDemo(false); api("/api/reset"); hist.t.length = 0; hist.res.length = 0; };
    $("btn-pause").onclick = () => api(`/api/pause?on=${state && state.paused ? 0 : 1}`);
    $("btn-demo").onclick = runDemo;
    document.querySelectorAll(".speed button[data-x]").forEach(b => b.onclick = () => api(`/api/speed?x=${b.dataset.x}`));
    $("bias").oninput = () => { $("bias-val").textContent = `${$("bias").value} m/s`; api(`/api/param?bias=${$("bias").value}`); };
    $("vote").oninput = () => { $("vote-val").textContent = `${$("vote").value} m`; api(`/api/param?vote=${$("vote").value}`); };
    $("btn-jam").onclick = () => api("/api/jamming");
    $("btn-shadow").onclick = () => api("/api/shadow" + node());
    $("btn-scatter").onclick = () => api("/api/scatter");
    $("btn-kinetic").onclick = () => api("/api/kinetic?n=2");
    $("btn-lowbatt").onclick = () => api("/api/lowbatt" + node());
    document.querySelectorAll("[data-scen]").forEach(b => b.onclick = () => runScenario(b.dataset.scen));
    document.querySelectorAll(".viewbar button[data-view]").forEach(b => b.onclick = () => {
      viewMode = b.dataset.view; userRotated = false; userZoom = 1; autoRot = viewMode === "persp";
      if (viewMode === "persp" && Math.abs(az) < .01) az = .7;
      document.querySelectorAll(".viewbar button[data-view]").forEach(x => x.classList.toggle("on", x === b));
    });
    document.querySelectorAll(".viewbar input[data-layer]").forEach(i => i.onchange = () => { layers[i.dataset.layer] = i.checked; });
    window.addEventListener("keydown", e => {
      if (["INPUT", "BUTTON"].includes(e.target.tagName) && e.key !== " ") return;
      const k = e.key.toLowerCase();
      if (k >= "1" && k <= "7") { selected = +k - 1; renderPanel(); }
      else if (k === "a") $("btn-spoof").click();
      else if (k === "h") $("btn-hpm").click();
      else if (k === "g") runDemo();
      else if (k === " ") { e.preventDefault(); $("btn-pause").click(); }
      else if (k === "escape") { selected = null; renderPanel(); }
    });
  }

  async function poll() {
    try {
      const s = await (await fetch("/api/state")).json();
      prev = state; state = s;
      if (prev) prev.drones.forEach(pd => { const nd = state.drones.find(x => x.id === pd.id); if (nd && pd.status === "mitigated" && nd.status === "ok") { restoredAt = performance.now(); restoredName = nd.name; } });
      if (!state.paused) { hist.t.push(state.clock); hist.res.push(state.drones.map(d => d.residual)); if (hist.t.length > HIST_MAX) { hist.t.shift(); hist.res.shift(); } }
      renderPanel(); drawStrip();
    } catch (e) { /* servidor reiniciando */ }
  }
  async function init() {
    cfg = await (await fetch("/api/config")).json();
    terrain = buildTerrain(cfg.extent);
    resize(); wire(); await poll();
    setInterval(poll, 150);
    requestAnimationFrame(render);
  }
  init();
})();
