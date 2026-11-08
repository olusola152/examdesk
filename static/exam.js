// Exam answering: timer, palette, autosave, submit. Shared with proctor.js via window.ExamUI
(function () {
  const E = window.EXAM;
  const form = document.getElementById('examForm');
  let secondsLeft = E.secondsLeft, dirty = false, submitting = false;
  const flags = new Set();
  const timerEl = document.getElementById('timer');
  const saveState = document.getElementById('saveState');

  function fmt(s) { const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return (h ? h + ':' : '') + String(m).padStart(2, '0') + ':' + String(x).padStart(2, '0'); }
  function tick() {
    timerEl.textContent = fmt(Math.max(0, secondsLeft));
    timerEl.classList.toggle('warn', secondsLeft <= 300 && secondsLeft > 60);
    timerEl.classList.toggle('danger', secondsLeft <= 60);
    if (secondsLeft <= 0) { finalSubmit(true); return; }
    secondsLeft--;
  }
  tick(); setInterval(tick, 1000);

  function refresh() {
    let count = 0;
    document.querySelectorAll('.question').forEach(q => {
      const n = q.dataset.n, ta = q.querySelector('textarea');
      const done = !!q.querySelector('input[type=radio]:checked') || (ta && ta.value.trim().length > 0);
      if (ta) q.querySelector('.wcount').textContent = ta.value.trim() ? ta.value.trim().split(/\s+/).length : 0;
      document.getElementById('p' + n).classList.toggle('done', done);
      if (done) count++;
    });
    document.getElementById('answeredCount').textContent = count;
    return count;
  }
  window.toggleFlag = function (n, btn) {
    flags.has(n) ? flags.delete(n) : flags.add(n);
    btn.classList.toggle('on'); document.getElementById('p' + n).classList.toggle('flag');
  };
  form.addEventListener('input', () => { dirty = true; saveState.textContent = 'Unsaved changes…'; refresh(); });
  refresh();

  // block copy/paste/right-click during the exam
  ['copy', 'cut', 'paste', 'contextmenu', 'drop'].forEach(ev => document.addEventListener(ev, e => e.preventDefault()));

  async function autosave() {
    if (!dirty || submitting) return;
    dirty = false;
    try {
      const r = await fetch(E.urls.autosave, { method: 'POST', body: new FormData(form) });
      const j = await r.json();
      if (j.ok) saveState.textContent = 'Saved at ' + j.saved_at;
      else if (j.reason === 'banned') { submitting = true; window.location = E.urls.banned; }
      else finalSubmit(true);
    } catch (e) { dirty = true; saveState.textContent = 'Offline — retrying…'; }
  }
  setInterval(autosave, 8000);

  window.openSubmit = function () {
    const c = refresh(); document.getElementById('mAns').textContent = c;
    const w = document.getElementById('mWarn'), msgs = [], unans = E.totalQ - c;
    if (unans > 0) msgs.push(unans + ' question(s) unanswered.');
    if (flags.size) msgs.push(flags.size + ' question(s) flagged for review.');
    w.style.display = msgs.length ? 'block' : 'none'; w.textContent = msgs.join(' ');
    document.getElementById('submitModal').classList.add('show');
  };
  window.closeSubmit = () => document.getElementById('submitModal').classList.remove('show');

  async function finalSubmit(auto) {
    if (submitting) return; submitting = true;
    if (auto) saveState.textContent = 'Time up — submitting…';
    if (window.Proctor) { try { await window.Proctor.stop(); } catch (e) {} }
    form.submit();
  }
  window.finalSubmit = finalSubmit;
  window.addEventListener('beforeunload', e => { if (!submitting) { e.preventDefault(); e.returnValue = ''; } });

  window.ExamUI = {
    isSubmitting: () => submitting,
    leaveTo: async (url) => { submitting = true; if (window.Proctor) { try { await window.Proctor.stop(); } catch (e) {} } window.location = url; },
  };
})();
