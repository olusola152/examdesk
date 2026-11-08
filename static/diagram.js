// Animated lecture diagrams ("video" on the projector screen), revealed in step with the teacher's voice.
// Diagram.render(svg, visual) -> { setProgress(0..1), finish() }
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const W = 960, H = 500;
  const el = (tag, attrs = {}, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };
  function wrap(text, max) {
    const words = String(text || '').split(/\s+/).filter(Boolean), lines = [];
    let line = '';
    for (const w of words) {
      if ((line + ' ' + w).trim().length > max && line) { lines.push(line); line = w; } else line = (line + ' ' + w).trim();
    }
    if (line) lines.push(line);
    return lines.slice(0, 4);
  }
  function text(parent, x, y, str, { size = 16, weight = 400, fill = '#14243a', max = 22, anchor = 'middle', lh = 1.25, maxLines = 4 } = {}) {
    let lines = wrap(str, max);
    if (lines.length > maxLines) { lines = lines.slice(0, maxLines); lines[maxLines - 1] = lines[maxLines - 1].replace(/\s*\S*$/, '') + '…'; }
    const t = el('text', { x, y: y - (lines.length - 1) * size * lh / 2, 'text-anchor': anchor,
      'font-size': size, 'font-weight': weight, fill, 'dominant-baseline': 'middle' }, parent);
    lines.forEach((ln, i) => { const s = el('tspan', { x, dy: i ? size * lh : 0 }, t); s.textContent = ln; });
    return t;
  }
  function card(parent, cx, cy, w, h, it, step, opts = {}) {
    const g = el('g', { class: 'dg-step dg-node', 'data-step': step, transform: `translate(${cx},${cy})` }, parent);
    el('rect', { x: -w / 2, y: -h / 2, width: w, height: h, rx: 14, class: 'dg-card' }, g);
    if (opts.badge !== undefined) {
      el('circle', { cx: -w / 2 + 4, cy: -h / 2 + 4, r: 15, class: 'dg-badge' }, g);
      text(g, -w / 2 + 4, -h / 2 + 5, String(opts.badge), { size: 14, weight: 700, fill: '#14243a' });
    }
    const size = opts.size || 17, lmax = Math.floor(w / 10), lh = size * 1.25;
    const hasDetail = it.detail && h >= 80;
    if (!hasDetail) { text(g, 0, 0, it.label, { size, weight: 700, max: lmax, maxLines: 3 }); return g; }
    // label on top (1-2 lines), detail fills the space underneath without overlapping
    const nLab = Math.min(2, wrap(it.label, lmax).length), top = -h / 2 + 12;
    text(g, 0, top + nLab * lh / 2, it.label, { size, weight: 700, max: lmax, maxLines: 2 });
    const dTop = top + nLab * lh + 4, room = h / 2 - 8 - dTop, dSize = 12.5, dLh = dSize * 1.25;
    const dLines = Math.max(1, Math.floor(room / dLh));
    const shown = Math.min(dLines, wrap(it.detail, Math.floor(w / 7.2)).length);
    text(g, 0, dTop + shown * dLh / 2, it.detail, { size: dSize, fill: '#4a5a72', max: Math.floor(w / 7.2), maxLines: dLines });
    return g;
  }
  function arrow(parent, d, step) {
    const p = el('path', { d, class: 'dg-step dg-arrow', 'data-step': step, 'marker-end': 'url(#dgArrow)' }, parent);
    return p;
  }

  const layouts = {
    flow(g, v) {
      const n = v.items.length, perRow = n <= 4 ? n : Math.ceil(n / 2), rows = Math.ceil(n / perRow);
      const w = Math.min(200, (W - 80) / perRow - 40), h = 120, gapX = (W - 60) / perRow;
      let step = 0; const pos = [];
      v.items.forEach((it, i) => {
        const r = Math.floor(i / perRow), c = r % 2 ? perRow - 1 - (i % perRow) : i % perRow;   // snake layout
        pos.push([30 + gapX * (c + 0.5), rows === 1 ? 240 : 140 + r * 200]);
      });
      v.items.forEach((it, i) => {
        if (i) {
          const [x1, y1] = pos[i - 1], [x2, y2] = pos[i];
          const d = y1 === y2 ? `M${x1 + (x2 > x1 ? w / 2 : -w / 2) + 4},${y1} L${x2 + (x2 > x1 ? -w / 2 : w / 2) - (x2 > x1 ? 8 : -8)},${y2}`
                              : `M${x1},${y1 + h / 2 + 4} L${x2},${y2 - h / 2 - 10}`;
          arrow(g, d, step++);
        }
        card(g, pos[i][0], pos[i][1], w, h, it, step++, { badge: i + 1 });
      });
      return step;
    },
    cycle(g, v) {
      const n = v.items.length, cx = W / 2, cy = 245, rx = 330, ry = 165, w = 175, h = 92;
      const pos = v.items.map((_, i) => { const a = -Math.PI / 2 + i * 2 * Math.PI / n; return [cx + rx * Math.cos(a), cy + ry * Math.sin(a)]; });
      text(g, cx, cy, v.title || '', { size: 20, weight: 700, fill: '#ffd166', max: 18 }).setAttribute('class', 'dg-center');
      let step = 0;
      v.items.forEach((it, i) => {
        card(g, pos[i][0], pos[i][1], w, h, it, step++, { badge: i + 1 });
        const [x1, y1] = pos[i], [x2, y2] = pos[(i + 1) % n];
        const mx = (x1 + x2) / 2 + (cy - (y1 + y2) / 2) * 0.25 * (x2 > x1 ? -1 : 1), my = (y1 + y2) / 2 + ((x1 + x2) / 2 - cx) * 0.25;
        const sx = x1 + (x2 - x1) * 0.28, sy = y1 + (y2 - y1) * 0.28, ex = x1 + (x2 - x1) * 0.72, ey = y1 + (y2 - y1) * 0.72;
        arrow(g, `M${sx},${sy} Q${mx},${my} ${ex},${ey}`, step++);
      });
      return step;
    },
    timeline(g, v) {
      const n = v.items.length, x0 = 110, x1 = W - 110, y = 250;
      el('line', { x1: x0, y1: y, x2: x1, y2: y, class: 'dg-step dg-axis', 'data-step': 0 }, g);
      let step = 1;
      v.items.forEach((it, i) => {
        const x = x0 + (x1 - x0) * (n === 1 ? 0.5 : i / (n - 1)), up = i % 2 === 0;
        const gg = el('g', { class: 'dg-step dg-node', 'data-step': step++, transform: `translate(${x},${y})` }, g);
        el('circle', { r: 11, class: 'dg-dot' }, gg);
        el('line', { x1: 0, y1: up ? -14 : 14, x2: 0, y2: up ? -60 : 60, class: 'dg-tick' }, gg);
        el('rect', { x: -85, y: up ? -165 : 60, width: 170, height: 105, rx: 12, class: 'dg-card' }, gg);
        text(gg, 0, up ? -140 : 85, it.label, { size: 17, weight: 700, max: 16 });
        text(gg, 0, up ? -95 : 130, it.detail, { size: 12, fill: '#4a5a72', max: 24 });
      });
      return step;
    },
    concept(g, v) {
      const n = v.items.length, cx = W / 2, cy = 245, rx = 340, ry = 170;
      const c = el('g', { class: 'dg-step dg-node', 'data-step': 0, transform: `translate(${cx},${cy})` }, g);
      el('circle', { r: 78, class: 'dg-hub' }, c);
      text(c, 0, 0, v.title || '', { size: 18, weight: 800, fill: '#14243a', max: 13 });
      let step = 1;
      v.items.forEach((it, i) => {
        const a = -Math.PI / 2 + i * 2 * Math.PI / n, x = cx + rx * Math.cos(a), y = cy + ry * Math.sin(a);
        arrow(g, `M${cx + 82 * Math.cos(a)},${cy + 82 * Math.sin(a) * 0.9} L${x - 95 * Math.cos(a)},${y - 52 * Math.sin(a)}`, step++);
        card(g, x, y, 200, 112, it, step++);
      });
      return step;
    },
    hierarchy(g, v) {
      const n = v.items.length, top = 70, rowY = 300, w = Math.min(190, (W - 60) / n - 18);
      const root = el('g', { class: 'dg-step dg-node', 'data-step': 0, transform: `translate(${W / 2},${top})` }, g);
      el('rect', { x: -150, y: -34, width: 300, height: 68, rx: 14, class: 'dg-card dg-root' }, root);
      text(root, 0, 0, v.title || '', { size: 19, weight: 800, max: 26 });
      let step = 1;
      v.items.forEach((it, i) => {
        const x = 30 + (W - 60) / n * (i + 0.5);
        arrow(g, `M${W / 2},${top + 36} L${W / 2},${top + 90} L${x},${top + 90} L${x},${rowY - 92}`, step++);
        card(g, x, rowY, w, 170, it, step++);
      });
      return step;
    },
    compare(g, v) {
      const cols = v.items.slice(0, 2), w = 380, h = 330, y = 255;
      let step = 0;
      cols.forEach((it, i) => {
        const x = i ? W - 40 - w / 2 : 40 + w / 2;
        const gg = el('g', { class: 'dg-step dg-node', 'data-step': step++, transform: `translate(${x},${y})` }, g);
        el('rect', { x: -w / 2, y: -h / 2, width: w, height: h, rx: 16, class: 'dg-card' }, gg);
        el('rect', { x: -w / 2, y: -h / 2, width: w, height: 58, rx: 16, class: i ? 'dg-head2' : 'dg-head1' }, gg);
        text(gg, 0, -h / 2 + 29, it.label, { size: 20, weight: 800, fill: '#fff', max: 30 });
        String(it.detail || '').split(/;|•/).map(x => x.trim()).filter(Boolean).slice(0, 5).forEach((pt, k) => {
          text(gg, -w / 2 + 26, -h / 2 + 95 + k * 50, '• ' + pt, { size: 14.5, anchor: 'start', max: 40 });
        });
      });
      const vs = el('g', { class: 'dg-step dg-node', 'data-step': step++, transform: `translate(${W / 2},${y})` }, g);
      el('circle', { r: 34, class: 'dg-hub' }, vs);
      text(vs, 0, 1, 'VS', { size: 22, weight: 900 });
      return step;
    },
  };

  function render(svg, visual) {
    svg.innerHTML = '';
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    const defs = el('defs', {}, svg);
    const m = el('marker', { id: 'dgArrow', viewBox: '0 0 10 10', refX: 8, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, defs);
    el('path', { d: 'M0,0 L10,5 L0,10 z', fill: '#ffd166' }, m);
    const cam = el('g', { class: 'dg-cam' }, svg);           // slow "camera" zoom like a video
    const g = el('g', {}, cam);
    const v = visual && layouts[visual.type] ? visual : { type: 'concept', title: visual && visual.title || '', items: (visual && visual.items) || [] };
    const steps = Math.max(1, layouts[v.type](g, v));
    const cap = el('g', { class: 'dg-caption' }, svg);
    el('rect', { x: 0, y: H - 40, width: W, height: 40, fill: 'rgba(0,0,0,.35)' }, cap);
    const ct = el('text', { x: 20, y: H - 15, fill: '#ffd166', 'font-size': 16, 'font-weight': 700 }, cap);
    ct.textContent = '🎬  ' + (v.title || '');
    // prepare arrows for "drawing"
    svg.querySelectorAll('.dg-arrow').forEach(p => { const L = p.getTotalLength ? p.getTotalLength() : 300; p.style.strokeDasharray = L; p.style.strokeDashoffset = L; });
    const nodes = [...svg.querySelectorAll('.dg-step')];
    let shown = -1;
    function setProgress(p) {
      p = Math.max(0, Math.min(1, p));
      const upto = Math.min(steps - 1, Math.floor(p * steps * 1.08));
      if (upto === shown) { cam.setAttribute('transform', `translate(${W / 2},${H / 2}) scale(${1 + p * 0.05}) translate(${-W / 2},${-H / 2})`); return; }
      shown = upto;
      nodes.forEach(n => {
        const s = +n.dataset.step, on = s <= upto;
        n.classList.toggle('on', on);
        n.classList.toggle('active', s === upto || (n.classList.contains('dg-node') && s === upto - 1 && !nodes.some(o => +o.dataset.step === upto && o.classList.contains('dg-node'))));
        if (n.classList.contains('dg-arrow')) n.style.strokeDashoffset = on ? 0 : n.style.strokeDasharray;
      });
      cam.setAttribute('transform', `translate(${W / 2},${H / 2}) scale(${1 + p * 0.05}) translate(${-W / 2},${-H / 2})`);
    }
    setProgress(0);
    return { setProgress, finish: () => setProgress(1), steps };
  }
  window.Diagram = { render };
})();
