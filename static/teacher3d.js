// 3D AI Professor: loads static/teacher/teacher.glb (or .gltf / .fbx) and brings it to life.
// - Plays Mixamo-style animations if available: idle / talking / writing (in the model or as separate files)
// - Head nods and turns with the loudness of the voice; gentle breathing when quiet
// - Falls back to the cartoon teacher if no model is found or WebGL is unavailable
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';

const holder = document.getElementById('teacher3d');
const state = { talking: false, writing: false, level: 0, ready: false };
window.TeacherAvatar = {                       // called by the lecture page
  setTalking(on) { state.talking = on; pick(); },
  setWriting(on) { state.writing = on; pick(); },
  setLevel(v) { state.level = v; },
  get ready() { return state.ready; },
  get mouthOpen() { return jaw ? +jaw.open.toFixed(3) : 0; },
  get armLift() { return arm ? +arm.angle.toFixed(3) : 0; },
};

let mixer = null, actions = {}, current = null, head = null, neck = null, spine = null, model = null;
let armR = null, foreR = null, armL = null, foreL = null, handR = null;
let jaw = null, arm = null;   // arm: forearm bend for scans with no skeleton              // { meshes: [{attr, idx: Int32Array, w: Float32Array, dir: Vector3, base: Float32Array}], amount }

function loaderFor(url) { return /\.fbx$/i.test(url) ? new FBXLoader() : new GLTFLoader(); }
function load(url) {
  return new Promise((res, rej) => loaderFor(url).load(url, obj => res(obj.scene ? { scene: obj.scene, animations: obj.animations || [] } : { scene: obj, animations: obj.animations || [] }), undefined, rej));
}

function pick() {
  if (!mixer) return;
  const want = state.writing && actions.writing ? 'writing' : state.talking && actions.talking ? 'talking' : actions.idle ? 'idle' : null;
  if (want === current) return;
  const next = want && actions[want], prev = current && actions[current];
  if (next) { next.reset().setEffectiveWeight(1).fadeIn(0.4).play(); }
  if (prev) prev.fadeOut(0.4);
  current = want;
}

function standUpright(obj) {
  // some downloads come lying down (Z-up) or on their side: turn the tallest side upwards
  let size = new THREE.Box3().setFromObject(obj).getSize(new THREE.Vector3());
  if (size.z > size.y * 1.4) obj.rotation.x -= Math.PI / 2;
  else if (size.x > size.y * 1.4) obj.rotation.z += Math.PI / 2;
  obj.updateMatrixWorld(true);
}

function frameModel(camera, obj, opts) {
  const box = new THREE.Box3().setFromObject(obj), size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
  const h = size.y || 1;
  obj.position.sub(new THREE.Vector3(center.x, box.min.y, center.z));          // feet on the floor, centred
  const fov = camera.fov * Math.PI / 180, fill = 1.12 / (opts.zoom || 1);      // full body with a little headroom
  const dist = (h * fill) / (2 * Math.tan(fov / 2));
  camera.position.set(0, h * 0.56, dist);                                      // eye-level-ish, like a person in front of the class
  camera.lookAt(0, h * 0.5 - h * (fill - 1) * 0.08, 0);
  camera.near = dist / 100; camera.far = dist * 20; camera.updateProjectionMatrix();
  return h;
}

function floorShadow(scene, h) {
  const c = document.createElement('canvas'); c.width = c.height = 128;
  const g = c.getContext('2d'), grd = g.createRadialGradient(64, 64, 4, 64, 64, 64);
  grd.addColorStop(0, 'rgba(0,0,0,.45)'); grd.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
  const m = new THREE.Mesh(new THREE.PlaneGeometry(h * 0.42, h * 0.16), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false }));
  m.rotation.x = -Math.PI / 2; m.position.y = 0.002 * h; scene.add(m);
}

// Find the lower face (chin + lower lip) of any human model by reading the shape of its face profile.
function findJaw(root, h) {
  root.updateMatrixWorld(true);
  const v = new THREE.Vector3(), meshes = [];
  let top = -Infinity;
  root.traverse(o => { if (o.isMesh && o.geometry && o.geometry.attributes.position) meshes.push(o); });
  const box = new THREE.Box3().setFromObject(root); top = box.max.y;
  const band = h * 0.2, y0 = top - band;
  // centre line of the head
  let sx = 0, n = 0;
  for (const m of meshes) {
    const p = m.geometry.attributes.position;
    for (let i = 0; i < p.count; i += 7) { v.fromBufferAttribute(p, i).applyMatrix4(m.matrixWorld); if (v.y > y0) { sx += v.x; n++; } }
  }
  if (!n) return null;
  const cx = sx / n, strip = h * 0.035, NB = 90, maxZ = new Array(NB).fill(-Infinity);
  for (const m of meshes) {
    const p = m.geometry.attributes.position;
    for (let i = 0; i < p.count; i++) {
      v.fromBufferAttribute(p, i).applyMatrix4(m.matrixWorld);
      if (v.y <= y0 || Math.abs(v.x - cx) > strip) continue;
      const b = Math.min(NB - 1, Math.floor((v.y - y0) / band * NB));
      if (v.z > maxZ[b]) maxZ[b] = v.z;
    }
  }
  const yOf = b => y0 + (b + 0.5) / NB * band;
  // nose = most forward point in the lower 3/4 of the head band (hair is at the top)
  let nose = -1;
  for (let b = Math.floor(NB * 0.15); b < Math.floor(NB * 0.8); b++) if (maxZ[b] > -Infinity && (nose < 0 || maxZ[b] > maxZ[nose])) nose = b;
  if (nose < 0) return null;
  // chin = going down from the nose, the last point before the profile falls back to the neck
  let chin = -1;
  for (let b = nose - 1; b >= 0; b--) {
    if (maxZ[b] === -Infinity) continue;
    if (maxZ[nose] - maxZ[b] > h * 0.034) { chin = b + 1; break; }
  }
  if (chin < 0 || nose - chin < 3) return null;
  const noseY = yOf(nose), chinY = yOf(chin) - band / NB, span = noseY - chinY;
  const mouthY = noseY - span * 0.36, frontZ = maxZ[chin] - h * 0.03, half = h * 0.05;
  const out = { meshes: [], amount: span * 0.16, open: 0, count: 0, noseY, chinY };
  const inv = new THREE.Matrix4(), down = new THREE.Vector3();
  for (const m of meshes) {
    const p = m.geometry.attributes.position, idx = [], w = [];
    for (let i = 0; i < p.count; i++) {
      v.fromBufferAttribute(p, i).applyMatrix4(m.matrixWorld);
      if (v.y > mouthY || v.y < chinY - span * 0.12 || v.z < frontZ || Math.abs(v.x - cx) > half) continue;
      const t = Math.min(1, (mouthY - v.y) / (span * 0.22));              // 0 at the lip line -> 1 lower down
      const side = 1 - Math.min(1, Math.abs(v.x - cx) / half) ** 2;      // fade towards the cheeks
      const low = v.y < chinY ? Math.max(0, 1 - (chinY - v.y) / (span * 0.12)) : 1;   // fade under the chin
      const wt = t * t * (3 - 2 * t) * side * low;
      if (wt > 0.02) { idx.push(i); w.push(wt); }
    }
    if (!idx.length) continue;
    inv.copy(m.matrixWorld).invert();
    const scale = m.matrixWorld.getMaxScaleOnAxis() || 1;
    down.set(0, -1, -0.25).normalize().transformDirection(inv).multiplyScalar(1 / scale);
    const I = Int32Array.from(idx), base = new Float32Array(I.length * 3);
    I.forEach((i, k) => { base[k * 3] = p.getX(i); base[k * 3 + 1] = p.getY(i); base[k * 3 + 2] = p.getZ(i); });
    if (p.setUsage) p.setUsage(THREE.DynamicDrawUsage);
    out.meshes.push({ attr: p, idx: I, w: Float32Array.from(w), dir: down.clone(), base });
    out.count += I.length;
  }
  return out.count ? out : null;
}

// Find the forearm that hangs free (not in a pocket) in a scan, so it can be lifted at the elbow.
function findArm(root, h) {
  root.updateMatrixWorld(true);
  const v = new THREE.Vector3(), meshes = [];
  root.traverse(o => { if (o.isMesh && o.geometry && o.geometry.attributes.position && !o.isSkinnedMesh) meshes.push(o); });
  if (!meshes.length) return null;
  // body centre line at chest height
  let sx = 0, n = 0;
  for (const m of meshes) { const p = m.geometry.attributes.position;
    for (let i = 0; i < p.count; i += 5) { v.fromBufferAttribute(p, i).applyMatrix4(m.matrixWorld); if (v.y > h * 0.6 && v.y < h * 0.75) { sx += v.x; n++; } } }
  if (!n) return null;
  const cx = sx / n, lo = h * 0.43, hi = h * 0.58, NB = 160, span = h * 0.45, hist = new Array(NB).fill(0);
  const binOf = x => Math.floor((x - (cx - span / 2)) / span * NB);
  for (const m of meshes) { const p = m.geometry.attributes.position;
    for (let i = 0; i < p.count; i++) { v.fromBufferAttribute(p, i).applyMatrix4(m.matrixWorld);
      if (v.y > lo && v.y < hi) { const b = binOf(v.x); if (b >= 0 && b < NB) hist[b]++; } } }
  // walk out from the centre: torso, then a gap, then the arm
  function gap(dir) {
    let b = Math.floor(NB / 2), seenBody = false;
    for (; b > 0 && b < NB - 1; b += dir) {
      if (hist[b] > 0) seenBody = true;
      else if (seenBody && hist[b] === 0 && hist[b + dir] === 0) {
        // split right at the arm's inner edge (not the middle of the gap) so the body never bends with the arm
        for (let k = b; k > 0 && k < NB - 1; k += dir) if (hist[k] > 0) return cx - span / 2 + (k - dir * 0.5 + 0.5) / NB * span;
        return null;
      }
    }
    return null;
  }
  const sides = [];
  for (const dir of [1, -1]) {
    const gx = gap(dir);
    if (gx === null) continue;
    let minY = Infinity, cnt = 0, ex = 0, ez = 0, en = 0;
    const elbowY = h * 0.62;
    for (const m of meshes) { const p = m.geometry.attributes.position;
      for (let i = 0; i < p.count; i++) { v.fromBufferAttribute(p, i).applyMatrix4(m.matrixWorld);
        if ((v.x - gx) * dir <= 0 || v.y > elbowY + h * 0.03 || v.y < h * 0.25) continue;
        cnt++; if (v.y < minY) minY = v.y;
        if (Math.abs(v.y - elbowY) < h * 0.02) { ex += v.x; ez += v.z; en++; } } }
    if (cnt > 30 && en) sides.push({ dir, gx, minY, cnt, pivot: new THREE.Vector3(ex / en, elbowY, ez / en) });
  }
  if (!sides.length) return null;
  sides.sort((a, b) => a.minY - b.minY);            // the lower hand hangs free; the other is likely in a pocket
  const s = sides[0], fade = h * 0.035;
  const out = { meshes: [], pivot: s.pivot, side: s.dir > 0 ? 'screen-right' : 'screen-left', dir: s.dir, angle: 0, count: 0 };
  for (const m of meshes) {
    const p = m.geometry.attributes.position, nrm = m.geometry.attributes.normal, idx = [], w = [], wp = [], wn = [];
    const nmat = new THREE.Matrix3().getNormalMatrix(m.matrixWorld), nv = new THREE.Vector3();
    for (let i = 0; i < p.count; i++) {
      v.fromBufferAttribute(p, i).applyMatrix4(m.matrixWorld);
      if ((v.x - s.gx) * s.dir <= 0 || v.y > s.pivot.y + fade || v.y < s.minY - h * 0.01) continue;
      const t = Math.min(1, Math.max(0, (s.pivot.y + fade - v.y) / (fade * 2)));
      idx.push(i); w.push(t * t * (3 - 2 * t)); wp.push(v.x, v.y, v.z);
      if (nrm) { nv.fromBufferAttribute(nrm, i).applyMatrix3(nmat).normalize(); wn.push(nv.x, nv.y, nv.z); }
    }
    if (!idx.length) continue;
    if (p.setUsage) p.setUsage(THREE.DynamicDrawUsage);
    if (nrm && nrm.setUsage) nrm.setUsage(THREE.DynamicDrawUsage);
    out.meshes.push({ mesh: m, attr: p, nattr: nrm || null, wnorm: nrm ? Float32Array.from(wn) : null,
                      idx: Int32Array.from(idx), w: Float32Array.from(w), world: Float32Array.from(wp),
                      inv: new THREE.Matrix4().copy(m.matrixWorld).invert() });
    out.count += idx.length;
  }
  return out.count ? out : null;
}

const _n = new THREE.Vector3(), _q = new THREE.Quaternion(), _qs = new THREE.Quaternion(), _v = new THREE.Vector3(), _ax = new THREE.Vector3(1, 0, 0), _az = new THREE.Vector3(0, 0, 1), _id = new THREE.Quaternion();
function bendArm(a, lift, sway) {
  // rotate the forearm about the elbow: forward/up (towards the class) plus a small side-to-side wave
  _q.setFromAxisAngle(_ax, -lift).multiply(_qs.setFromAxisAngle(_az, sway * a.dir));
  for (const m of a.meshes) {
    m.mesh.updateMatrixWorld(true);
    const inv = m.inv.copy(m.mesh.matrixWorld).invert();
    for (let k = 0; k < m.idx.length; k++) {
      const w = m.w[k];
      _v.set(m.world[k * 3] - a.pivot.x, m.world[k * 3 + 1] - a.pivot.y, m.world[k * 3 + 2] - a.pivot.z);
      _qs.copy(_id).slerp(_q, w);
      _v.applyQuaternion(_qs).add(a.pivot);
      // world (as detected, before the model was turned) -> the mesh's own space
      _v.applyMatrix4(a.unturn).applyMatrix4(inv);
      m.attr.setXYZ(m.idx[k], _v.x, _v.y, _v.z);
      if (m.nattr) {                                   // turn the surface normals too, so the sleeve stays lit
        _n.set(m.wnorm[k * 3], m.wnorm[k * 3 + 1], m.wnorm[k * 3 + 2]).applyQuaternion(_qs).transformDirection(a.unturn).transformDirection(inv);
        m.nattr.setXYZ(m.idx[k], _n.x, _n.y, _n.z);
      }
    }
    (m.attr.isInterleavedBufferAttribute ? m.attr.data : m.attr).needsUpdate = true;
    if (m.nattr) (m.nattr.isInterleavedBufferAttribute ? m.nattr.data : m.nattr).needsUpdate = true;
  }
}

async function start() {
  let info;
  try { info = await (await fetch('/teacher-avatar.json')).json(); } catch (e) { return; }
  if (!info.model) return;                                  // no 3D model -> keep the cartoon
  const canvasTest = document.createElement('canvas');
  if (!(canvasTest.getContext('webgl2') || canvasTest.getContext('webgl'))) return;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(28, 1, 0.01, 1000);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8d7b60, 1.6));
  const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(2, 4, 3); scene.add(key);
  const rim = new THREE.DirectionalLight(0xffe2b0, 1.0); rim.position.set(-3, 3, -2); scene.add(rim);

  holder.classList.add('loading');
  let gltf;
  try { gltf = await load(info.model); }
  catch (e) { console.warn('3D teacher failed to load, using the cartoon teacher', e); holder.classList.remove('loading'); return; }
  model = gltf.scene;
  model.traverse(o => {
    if (o.isMesh) { o.frustumCulled = false; if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { m.side = THREE.FrontSide; }); }
    if (o.isBone) {
      const n = o.name.toLowerCase();
      if (!head && /head(?!top|_end)/.test(n)) head = o;
      if (!neck && /neck/.test(n)) neck = o;
      if (!spine && /spine2|spine1|chest|spine$/.test(n)) spine = o;
      if (/right|(^|[_.\s:])r($|[_.\s])/.test(n)) {
        if (!foreR && /forearm|lowerarm/.test(n)) foreR = o;
        else if (!armR && /(^|[^e])arm$|upperarm/.test(n.replace(/^mixamorig[:_]?/, ''))) armR = o;
        if (!handR && /hand$/.test(n)) handR = o;
      }
      if (/left|(^|[_.\s:])l($|[_.\s])/.test(n)) {
        if (!foreL && /forearm|lowerarm/.test(n)) foreL = o;
        else if (!armL && /(^|[^e])arm$|upperarm/.test(n.replace(/^mixamorig[:_]?/, ''))) armL = o;
      }
    }
  });
  standUpright(model);
  const rig = new THREE.Group();          // turning the rig (not the model) keeps every calculation simple
  rig.add(model); scene.add(rig);
  const opts = { zoom: +info.zoom || 1, turn: (info.turn ?? 12) * Math.PI / 180 };
  const height = frameModel(camera, model, opts);
  floorShadow(scene, height);
  try { jaw = findJaw(model, height); } catch (e) { console.warn('mouth detection failed', e); }
  window.TeacherAvatar.mouth = jaw ? { vertices: jaw.count, noseY: jaw.noseY, chinY: jaw.chinY } : null;
  if (!armR && !foreR && info.gesture !== false) {
    try { arm = findArm(model, height); } catch (e) { console.warn('arm detection failed', e); }
  }
  window.TeacherAvatar.arm = arm ? { vertices: arm.count, side: arm.side, elbowY: +arm.pivot.y.toFixed(3) } : null;
  rig.rotation.y = opts.turn;                             // stand slightly turned towards the board, like a real lecturer
  rig.updateMatrixWorld(true);
  if (arm) arm.unturn = new THREE.Matrix4().makeRotationY(opts.turn);

  // animations: inside the model, or separate files (idle / talking / writing) from Mixamo
  mixer = new THREE.AnimationMixer(model);
  const clips = [...gltf.animations];
  for (const [kind, url] of Object.entries(info.animations || {})) {
    try { const a = await load(url); if (a.animations[0]) { a.animations[0].name = kind; clips.push(a.animations[0]); } } catch (e) { console.warn('animation failed', url, e); }
  }
  const byName = re => clips.find(c => re.test(c.name));
  const assign = { idle: byName(/idle|breath|stand/i), talking: byName(/talk|speak|explain|gesture|lectur/i), writing: byName(/writ|board|point/i) };
  if (!assign.talking && clips.length) assign.talking = clips[0];
  for (const [k, clip] of Object.entries(assign)) if (clip) actions[k] = mixer.clipAction(clip);
  pick();

  holder.appendChild(renderer.domElement);
  holder.classList.remove('loading');
  holder.classList.add('on');
  document.getElementById('prof').style.display = 'none';
  if (info.credit) { const c = document.createElement('div'); c.className = 'model-credit'; c.textContent = info.credit; holder.parentElement.appendChild(c); }
  state.ready = true;

  function resize() {
    const w = holder.clientWidth, h = holder.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(holder); resize();

  const clock = new THREE.Clock();
  let smooth = 0;
  const off = { hx: 0, hy: 0, sx: 0, ar: 0, fx: 0, fz: 0, al: 0, gw: 0 };        // procedural offsets layered on top of animations (never drift)
  renderer.setAnimationLoop(() => {
    const dt = Math.min(0.05, clock.getDelta()), t = clock.elapsedTime;
    if (head) { head.rotation.x -= off.hx; head.rotation.y -= off.hy; }
    if (spine) spine.rotation.x -= off.sx;
    if (mixer) mixer.update(dt);
    smooth += ((state.talking ? state.level : 0) - smooth) * 0.25;
    off.hx = smooth * 0.12 * Math.sin(t * 9) + (state.talking ? 0.02 * Math.sin(t * 1.7) : 0);
    off.hy = state.talking ? 0.10 * Math.sin(t * 0.8) : 0.04 * Math.sin(t * 0.4);
    off.sx = 0.012 * Math.sin(t * 1.6);                                       // breathing
    if (head) { head.rotation.x += off.hx; head.rotation.y += off.hy; }
    if (spine) spine.rotation.x += off.sx;
    // hands: if the model is rigged but has no talking animation, gesture with the right forearm while explaining
    const gesturing = state.talking && !actions.talking;   // (also used by the forearm bend below)
    if (armR) armR.rotation.z -= off.ar;
    if (foreR) { foreR.rotation.x -= off.fx; foreR.rotation.z -= off.fz; }
    if (armL) armL.rotation.z -= off.al;
    const gw = (off.gw += ((gesturing || state.writing ? 1 : 0) - off.gw) * 0.05);
    off.ar = gw * (0.35 + 0.12 * Math.sin(t * 1.3));
    off.fx = gw * (-0.9 + 0.35 * Math.sin(t * 2.1) + smooth * 0.2);
    off.fz = gw * 0.25 * Math.sin(t * 1.7);
    off.al = gw * -0.06 * Math.sin(t * 0.9);
    if (armR) armR.rotation.z += off.ar;
    if (foreR) { foreR.rotation.x += off.fx; foreR.rotation.z += off.fz; }
    if (armL) armL.rotation.z += off.al;
    // hand: bend the free forearm up and gesture (scans without a skeleton)
    if (arm) {
      const target = gw * (0.95 + 0.28 * Math.sin(t * 2.1) + smooth * 0.15), sway = gw * 0.22 * Math.sin(t * 1.4);
      arm.angle += (target - arm.angle) * 0.18;
      bendArm(arm, arm.angle, sway);
    }
    // mouth: open the jaw with the loudness of the voice
    if (jaw) {
      const open = state.talking ? Math.min(1, smooth * 1.4) : 0;
      jaw.open += (open - jaw.open) * 0.45;
      for (const m of jaw.meshes) {
        const d = jaw.amount * jaw.open;
        for (let k = 0; k < m.idx.length; k++) {
          const w = m.w[k] * d;
          m.attr.setXYZ(m.idx[k], m.base[k * 3] + m.dir.x * w, m.base[k * 3 + 1] + m.dir.y * w, m.base[k * 3 + 2] + m.dir.z * w);
        }
        (m.attr.isInterleavedBufferAttribute ? m.attr.data : m.attr).needsUpdate = true;
      }
    }
    if (!head && model) {                     // static scan with no bones: gentle sway + nod of the whole body
      rig.rotation.y = opts.turn + (state.talking ? 0.06 : 0.025) * Math.sin(t * (state.talking ? 0.9 : 0.4));
      if (arm) arm.unturn.makeRotationY(rig.rotation.y);
    }
    renderer.render(scene, camera);
  });
}
start();
