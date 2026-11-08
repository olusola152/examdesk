// AI proctor: fullscreen camera, recording, live frames, face + hand rules, strikes.
const E = window.EXAM;
const $ = id => document.getElementById(id);
const video = $('cam');

const CFG = {
  fps: 8,
  noFaceMs: 3000,        // face missing this long -> away
  sidewaysMs: 2500,      // head turned this long -> away
  multiFaceMs: 2000,     // >1 person this long -> away
  yawLimit: 0.55,        // nose offset vs eye distance
  edgeLimit: 0.12,       // face centre too close to frame edge
  minFaceWidth: 0.10,    // face too small = moved far back
  handWindowMs: 3000,    // hand motion measured over this window
  handMotion: 0.35,      // summed normalized movement in window
  idleTypingMs: 2500,    // no key/mouse input for this long
  cooldownMs: 10000,     // between warnings of the same kind (client side)
  tabAwayMs: 1500,       // left the window this long -> away
  frameEveryMs: 5000,    // live snapshot to lecturer
  chunkMs: 10000,        // recording upload interval
};
const MODELS = {
  face: ['/static/models/blaze_face_short_range.tflite',
    'https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite'],
  hand: ['/static/models/hand_landmarker.task',
    'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task'],
};
const VISION_SRC = [E.urls.mediapipe, 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs'];

let stream, recorder, uploadChain = Promise.resolve(), stopped = false, banned = false;
let faceDet = null, handDet = null;
const state = { noFaceSince: 0, sideSince: 0, multiSince: 0, lastWarn: { away: 0, hand: 0 },
  lastInput: performance.now(), handTrail: [], tabHiddenAt: 0 };

// ---------------------------------------------------------------- camera
async function startCamera() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' }, audio: true });
  } catch (e) {
    try { stream = await navigator.mediaDevices.getUserMedia({ video: true }); }
    catch (e2) {
      $('gateMsg').innerHTML = '<b style="color:var(--red)">Camera access was blocked.</b> Allow the camera in your browser (padlock icon in the address bar) and reload this page. You cannot write this exam without a camera.';
      $('recLabel').textContent = 'Camera blocked';
      return false;
    }
  }
  video.srcObject = stream;
  await video.play().catch(() => {});
  stream.getVideoTracks()[0].addEventListener('ended', () => warn('away', 'Camera was turned off'));
  $('gateMsg').textContent = 'Camera is on. Click below to begin.';
  $('gateBtn').disabled = false;
  return true;
}

// ---------------------------------------------------------------- recording
function startRecording() {
  const types = ['video/webm;codecs=vp8,opus', 'video/webm;codecs=vp8', 'video/webm'];
  const mimeType = types.find(t => window.MediaRecorder && MediaRecorder.isTypeSupported(t));
  if (!mimeType) { $('recLabel').textContent = 'Live (recording unsupported in this browser)'; return; }
  recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 350000, audioBitsPerSecond: 32000 });
  recorder.ondataavailable = ev => {
    if (!ev.data || !ev.data.size) return;
    const blob = ev.data;
    uploadChain = uploadChain.then(() => fetch(`${E.urls.chunk}?attempt=${E.attempt}`, { method: 'POST', body: blob })
      .catch(() => {}));
  };
  recorder.start(CFG.chunkMs);
  $('recDot').classList.add('on');
  $('recLabel').textContent = 'REC · Live to lecturer';
}

function stopAll() {
  return new Promise(resolve => {
    if (stopped) return resolve();
    stopped = true;
    const finish = () => uploadChain.then(() => { stream && stream.getTracks().forEach(t => t.stop()); resolve(); });
    if (recorder && recorder.state !== 'inactive') {
      recorder.addEventListener('stop', () => setTimeout(finish, 50), { once: true });
      recorder.stop();
      setTimeout(resolve, 6000); // never hang the submission
    } else finish();
  });
}
window.Proctor = { stop: stopAll };

// ---------------------------------------------------------------- snapshots
const canvas = document.createElement('canvas');
function snapshot(w = 480) {
  if (!video.videoWidth) return null;
  canvas.width = w; canvas.height = Math.round(w * video.videoHeight / video.videoWidth);
  const ctx = canvas.getContext('2d');
  ctx.translate(canvas.width, 0); ctx.scale(-1, 1);
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  return canvas.toDataURL('image/jpeg', 0.6);
}
setInterval(() => {
  if (stopped || !video.videoWidth) return;
  fetch(E.urls.frame, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image: snapshot(320) }) })
    .then(r => r.status === 409 ? r.json() : null)
    .then(j => { if (j && j.reason === 'banned') goBanned(); })
    .catch(() => {});
}, CFG.frameEveryMs);

// ---------------------------------------------------------------- warnings
let warnTimer;
function showWarn(title, text) {
  $('wbTitle').textContent = title; $('wbText').textContent = text;
  $('warnbox').classList.add('show');
  clearTimeout(warnTimer); warnTimer = setTimeout(() => $('warnbox').classList.remove('show'), 4500);
}
function setCount(id, n) { const el = $(id); el.textContent = n; el.classList.toggle('hot', n >= E.maxStrikes - 1); }

async function warn(type, detail) {
  if (stopped || banned || window.ExamUI.isSubmitting()) return;
  const t = performance.now();
  if (t - state.lastWarn[type] < CFG.cooldownMs) return;
  state.lastWarn[type] = t;
  try {
    const r = await fetch(E.urls.violation, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, detail, image: snapshot(480) }) });
    const j = await r.json();
    if (j.banned) return goBanned();
    if (j.ignored) return;
    setCount('awayN', j.away); setCount('handN', j.hand);
    const left = E.maxStrikes - (type === 'away' ? j.away : j.hand);
    showWarn(`Warning ${j.strike} of ${E.maxStrikes}`,
      `${detail}. ${type === 'away' ? 'Stay in front of the camera and face your screen.' : 'Keep your hands on the keyboard — no papers or phones.'} ` +
      `${left} more and you will be banned from this exam for today.`);
  } catch (e) { /* offline: try again next time */ state.lastWarn[type] = 0; }
}

async function goBanned() {
  if (banned) return; banned = true;
  showWarn('Banned for today', 'You reached the maximum number of warnings. Your exam has been locked and your lecturer has been notified.');
  setTimeout(() => window.ExamUI.leaveTo(E.urls.banned), 2500);
}

// ---------------------------------------------------------------- window / fullscreen rules
document.addEventListener('visibilitychange', () => {
  if (document.hidden) state.tabHiddenAt = performance.now();
  else if (state.tabHiddenAt && performance.now() - state.tabHiddenAt > CFG.tabAwayMs) warn('away', 'You left the exam window');
});
window.addEventListener('blur', () => { state.tabHiddenAt = state.tabHiddenAt || performance.now(); });
window.addEventListener('focus', () => {
  if (state.tabHiddenAt && performance.now() - state.tabHiddenAt > CFG.tabAwayMs) warn('away', 'You switched away from the exam');
  state.tabHiddenAt = 0;
});
document.addEventListener('fullscreenchange', () => {
  if (!document.fullscreenElement && !stopped && !window.ExamUI.isSubmitting()) {
    warn('away', 'You exited full screen');
    $('gateMsg').textContent = 'You left full screen. Click to return to the exam.';
    $('gate').style.display = 'flex';
  }
});
['keydown', 'mousedown', 'wheel', 'touchstart', 'input'].forEach(ev =>
  document.addEventListener(ev, () => { state.lastInput = performance.now(); }, { passive: true }));

window.enterExam = async function () {
  try { await document.documentElement.requestFullscreen({ navigationUI: 'hide' }); } catch (e) {}
  $('gate').style.display = 'none';
  if (!recorder) startRecording();
};

// ---------------------------------------------------------------- AI detection
async function loadVision() {
  for (const src of VISION_SRC) {
    try {
      const mod = await import(src);
      const wasmBase = src.startsWith('http') ? src.replace(/vision_bundle\.mjs$/, 'wasm') : src.replace(/vision_bundle\.mjs$/, 'wasm');
      return { mod, files: await mod.FilesetResolver.forVisionTasks(wasmBase) };
    } catch (e) { console.warn('vision load failed', src, e); }
  }
  return null;
}
async function createWith(urls, factory) {
  for (const u of urls) {
    for (const delegate of ['GPU', 'CPU']) {
      try { return await factory(u, delegate); } catch (e) { /* try next */ }
    }
  }
  return null;
}

async function initDetectors() {
  const v = await loadVision();
  if (!v) { $('proctorState').textContent = '⚠ AI proctor unavailable (no internet?) — recording only'; return; }
  const { FaceDetector, HandLandmarker } = v.mod;
  faceDet = await createWith(MODELS.face, (u, d) => FaceDetector.createFromOptions(v.files,
    { baseOptions: { modelAssetPath: u, delegate: d }, runningMode: 'VIDEO', minDetectionConfidence: 0.5 }));
  handDet = await createWith(MODELS.hand, (u, d) => HandLandmarker.createFromOptions(v.files,
    { baseOptions: { modelAssetPath: u, delegate: d }, runningMode: 'VIDEO', numHands: 2, minHandDetectionConfidence: 0.5 }));
  $('proctorState').textContent = (faceDet ? '✔ Face tracking' : '⚠ Face tracking off') + ' · ' + (handDet ? '✔ Hand tracking' : '⚠ Hand tracking off');
  if (faceDet || handDet) loop();
}

const box = $('facebox');
let lastTs = -1;
function loop() {
  if (stopped) return;
  setTimeout(loop, 1000 / CFG.fps);
  if (!video.videoWidth || video.readyState < 2) return;
  const ts = performance.now();
  if (ts <= lastTs) return; lastTs = ts;
  if (faceDet) checkFace(faceDet.detectForVideo(video, ts).detections || [], ts);
  if (handDet) checkHands(handDet.detectForVideo(video, ts).landmarks || [], ts);
}

function checkFace(dets, t) {
  const W = video.videoWidth, H = video.videoHeight;
  let bad = '';
  if (dets.length === 0) {
    state.sideSince = state.multiSince = 0;
    state.noFaceSince = state.noFaceSince || t;
    if (t - state.noFaceSince > CFG.noFaceMs) { warn('away', 'Your face is not visible — you moved away from the camera'); state.noFaceSince = t; }
    box.style.display = 'none';
    $('proctorState').dataset.face = 'none';
    return;
  }
  state.noFaceSince = 0;
  if (dets.length > 1) {
    state.multiSince = state.multiSince || t;
    if (t - state.multiSince > CFG.multiFaceMs) { warn('away', 'Another person was detected in view'); state.multiSince = t; }
    bad = 'multi';
  } else state.multiSince = 0;

  const d = dets[0], bb = d.boundingBox, kp = d.keypoints || [];
  const cx = (bb.originX + bb.width / 2) / W, fw = bb.width / W;
  let sideways = cx < CFG.edgeLimit || cx > 1 - CFG.edgeLimit || fw < CFG.minFaceWidth;
  if (kp.length >= 3) {
    const eyeMid = (kp[0].x + kp[1].x) / 2, eyeDist = Math.abs(kp[1].x - kp[0].x) || 0.001;
    if (Math.abs((kp[2].x - eyeMid) / eyeDist) > CFG.yawLimit) sideways = true;
  }
  if (sideways) {
    state.sideSince = state.sideSince || t;
    if (t - state.sideSince > CFG.sidewaysMs) {
      warn('away', fw < CFG.minFaceWidth ? 'You moved too far from the camera' : 'You turned or moved sideways away from the screen');
      state.sideSince = t;
    }
    bad = bad || 'side';
  } else state.sideSince = 0;

  // draw face box over the mirrored, cover-fitted video
  const scale = Math.max(innerWidth / W, innerHeight / H), offX = (innerWidth - W * scale) / 2, offY = (innerHeight - H * scale) / 2;
  box.style.display = 'block';
  box.style.left = (innerWidth - (offX + (bb.originX + bb.width) * scale)) + 'px';
  box.style.top = (offY + bb.originY * scale) + 'px';
  box.style.width = bb.width * scale + 'px'; box.style.height = bb.height * scale + 'px';
  box.classList.toggle('bad', !!bad);
}

function checkHands(hands, t) {
  if (!hands.length) { state.handTrail = []; return; }
  const c = hands.map(h => h.reduce((a, p) => ({ x: a.x + p.x / h.length, y: a.y + p.y / h.length }), { x: 0, y: 0 }));
  state.handTrail.push({ t, c });
  state.handTrail = state.handTrail.filter(p => t - p.t <= CFG.handWindowMs);
  let motion = 0;
  for (let i = 1; i < state.handTrail.length; i++) {
    const a = state.handTrail[i - 1].c, b = state.handTrail[i].c, n = Math.min(a.length, b.length);
    for (let k = 0; k < n; k++) motion += Math.hypot(b[k].x - a[k].x, b[k].y - a[k].y);
  }
  const span = state.handTrail.length ? t - state.handTrail[0].t : 0;
  if (span > CFG.handWindowMs * 0.8 && motion > CFG.handMotion && t - state.lastInput > CFG.idleTypingMs) {
    warn('hand', 'Your hands were moving in view without typing');
    state.handTrail = [];
  }
}

// ---------------------------------------------------------------- boot
(async () => {
  if (await startCamera()) initDetectors();
})();
