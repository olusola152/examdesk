// Plays a diagram drawn by Python (diagrams.py): each box appears when the teacher's voice reaches its cue.
// DiagramPlayer.mount(hostElement, svgString, cues) -> { setProgress(0..1), finish() }
(function () {
  function mount(host, svg, cues) {
    host.innerHTML = svg || '';
    const root = host.querySelector('svg');
    if (!root) return { setProgress() {}, finish() {} };
    cues = cues || [];
    const steps = [...root.querySelectorAll('.dg-step')];
    const cam = root.querySelector('.dg-cam');
    root.querySelectorAll('.dg-arrow').forEach(p => {                 // arrows "draw" themselves
      const L = p.getTotalLength ? p.getTotalLength() : 300;
      p.style.strokeDasharray = L; p.style.strokeDashoffset = L;
    });
    let shown = -2;
    function setProgress(p) {
      p = Math.max(0, Math.min(1, p));
      let upto = -1;
      cues.forEach((c, i) => { if (c <= p + 0.002) upto = i; });
      if (!cues.length) upto = Math.floor(p * steps.length);
      if (cam) cam.setAttribute('transform', `translate(480,250) scale(${1 + p * 0.05}) translate(-480,-250)`);
      if (upto === shown) return;
      shown = upto;
      const lastNode = Math.max(-1, ...steps.filter(n => n.classList.contains('dg-node') && +n.dataset.step <= upto).map(n => +n.dataset.step));
      steps.forEach(n => {
        const s = +n.dataset.step, on = s <= upto;
        n.classList.toggle('on', on);
        n.classList.toggle('active', s === lastNode);
        if (n.classList.contains('dg-arrow')) n.style.strokeDashoffset = on ? 0 : n.style.strokeDasharray;
      });
    }
    setProgress(0);
    return { setProgress, finish: () => setProgress(1) };
  }
  window.DiagramPlayer = { mount };
})();
