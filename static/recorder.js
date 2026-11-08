// Records a spoken question as a 16 kHz mono WAV (what the offline speech engines need).
// VoiceRecorder.record({ maxMs, silenceMs, onLevel }) -> Promise<Blob>  — stops by itself after a short silence.
(function () {
  function encodeWav(samples, rate) {
    const buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
    const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    str(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
    v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, 'data');
    v.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) { const s = Math.max(-1, Math.min(1, samples[i])); v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
    return new Blob([buf], { type: 'audio/wav' });
  }
  function downsample(chunks, from, to) {
    const len = chunks.reduce((a, c) => a + c.length, 0), all = new Float32Array(len);
    let o = 0; for (const c of chunks) { all.set(c, o); o += c.length; }
    if (from === to) return all;
    const ratio = from / to, out = new Float32Array(Math.floor(len / ratio));
    for (let i = 0; i < out.length; i++) {                        // average = simple low-pass
      const a = Math.floor(i * ratio), b = Math.min(len, Math.floor((i + 1) * ratio));
      let s = 0; for (let k = a; k < b; k++) s += all[k]; out[i] = s / Math.max(1, b - a);
    }
    return out;
  }
  let active = null;
  async function record({ maxMs = 15000, silenceMs = 1600, onLevel, onStart } = {}) {
    if (active) { active.stop(); return null; }                   // second click = stop now
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === 'suspended') { try { await ctx.resume(); } catch (e) {} }   // Edge/Chrome can start it paused
    const src = ctx.createMediaStreamSource(stream), proc = ctx.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    let spoke = false, quietSince = performance.now(), started = performance.now(), noise = 0, nFrames = 0, peak = 0;
    return new Promise(resolve => {
      let guard;
      const finish = () => {
        if (!active) return; active = null; clearTimeout(guard);
        proc.disconnect(); src.disconnect(); stream.getTracks().forEach(t => t.stop());
        const rate = ctx.sampleRate; ctx.close();
        // send it if speech was detected, or if there was any real sound at all (the server's voice detector decides)
        resolve(spoke || peak > 0.02 ? encodeWav(downsample(chunks, rate, 16000), 16000) : null);
      };
      active = { stop: finish };
      guard = setTimeout(finish, maxMs + 1500);                    // never hang, even if the audio stops flowing
      onStart && onStart();
      proc.onaudioprocess = e => {
        const d = e.inputBuffer.getChannelData(0); chunks.push(new Float32Array(d));
        let sum = 0; for (let i = 0; i < d.length; i++) sum += d[i] * d[i];
        const rms = Math.sqrt(sum / d.length), now = performance.now();
        peak = Math.max(peak, rms);
        if (now - started < 350) { noise = (noise * nFrames + rms) / (nFrames + 1); nFrames++; }   // average room noise
        const loud = rms > Math.max(0.012, Math.min(noise, 0.04) * 2.2);
        if (loud) { spoke = true; quietSince = now; }
        onLevel && onLevel(Math.min(1, rms * 8), loud);
        if ((spoke && now - quietSince > silenceMs) || now - started > maxMs) finish();
      };
      src.connect(proc); proc.connect(ctx.destination);
    });
  }
  window.VoiceRecorder = { record, get busy() { return !!active; } };
})();
