// Arc reactor (WebGL2 shader with plasma core, rings, voice spectrum, radar sweep) + animated HUD background.
// Public API: reactor.setState(s), reactor.setMic(v), reactor.setVoice(v), reactor.pulse(), reactor.setTheme(hex, hotHex)
(function () {
  const R = {
    state: "idle", mic: 0, voice: 0, energy: 0, boot: 0, spec: new Float32Array(64),
    col: [0.31, 0.85, 1.0], target: [0.31, 0.85, 1.0], base: [0.31, 0.85, 1.0], hot: [1.0, 0.7, 0.28],
    k: { think: 0, listen: 0, sleep: 0 }, ripples: [],
    setState(s) { if (s !== this.state && (s === "speaking" || s === "listening")) this.pulse(); this.state = s; },
    setMic(v) { this.mic = v; },
    setVoice(v) { this.voice = v; },
    pulse() { this.ripples.push({ t: performance.now() / 1000 }); if (this.ripples.length > 6) this.ripples.shift(); },
    setTheme(hex, hotHex) { this.base = hexRgb(hex); if (hotHex) this.hot = hexRgb(hotHex); if (typeof makeHexTile === "function") hexTile = makeHexTile(); },
  };
  window.reactor = R;
  function hexRgb(h) {
    h = (h || "#4fd8ff").trim().replace("#", "");
    if (h.length === 3) h = h.split("").map((c) => c + c).join("");
    const n = parseInt(h, 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }

  const cv = document.getElementById("reactor");
  const bg = document.getElementById("bg");
  const bctx = bg.getContext("2d");
  let W = 0, H = 0, BW = 0, BH = 0, dpr = 1, hexTile = null;

  // ------------------------------------------------------------------ WebGL reactor
  const VS = `#version 300 es
  in vec2 p; void main(){ gl_Position = vec4(p, 0., 1.); }`;
  const FS = `#version 300 es
  precision highp float;
  uniform vec2 uRes; uniform float uTime, uEnergy, uBoot, uThink, uListen, uSleep;
  uniform vec3 uCol, uHot; uniform float uSpec[64]; uniform vec4 uRip;
  out vec4 o;
  float hash(vec2 p){ p = fract(p*vec2(123.34, 456.21)); p += dot(p, p+45.32); return fract(p.x*p.y); }
  float noise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f*f*(3.-2.*f);
    return mix(mix(hash(i), hash(i+vec2(1,0)), f.x), mix(hash(i+vec2(0,1)), hash(i+vec2(1,1)), f.x), f.y); }
  float fbm(vec2 p){ float v = 0., a = .5; for (int i = 0; i < 4; i++) { v += a*noise(p); p = p*2.03 + vec2(1.7, 9.2); a *= .5; } return v; }
  float ring(float r, float rad, float w){ return 1. - smoothstep(0., w, abs(r - rad)); }
  float seg(float a01, float n, float duty, float rot){
    float f = fract((a01 + rot) * n);
    return smoothstep(0., .015*n, f) * (1. - smoothstep(duty - .015*n, duty, f));
  }
  float sdTri(vec2 p){ const float k = 1.7320508; p.x = abs(p.x) - 1.; p.y = p.y + 1./k;
    if (p.x + k*p.y > 0.) p = vec2(p.x - k*p.y, -k*p.x - p.y) / 2.;
    p.x -= clamp(p.x, -2., 0.); return -length(p) * sign(p.y); }
  void main(){
    vec2 uv = (gl_FragCoord.xy - .5*uRes) / min(uRes.x, uRes.y);
    float r = length(uv) * 2.8;
    float a01 = fract(atan(uv.y, uv.x) / 6.2831853 + .25);
    float t = uTime, e = uEnergy, b = uBoot;
    vec3 c = uCol, col = vec3(0.);
    float reveal = smoothstep(a01 - .02, a01, b * 1.04);
    float sp = 1. + uThink*2.6 + uListen*.7;

    // halo
    col += c * (.03 / (r*r + .025)) * (.35 + e*1.1) * b;
    // plasma core
    if (r < .5) {
      float p1 = fbm(uv*9. + vec2(t*.7, -t*.5));
      float p2 = fbm(uv*17. - vec2(t*1.2, t*.35) + p1);
      float core = 1. - smoothstep(0., .44, r);
      col += mix(c, vec3(1.), .3) * core * (p1*.9 + p2*.55) * (1.05 + e*2.) * b;
      col += vec3(1.) * (1. - smoothstep(0., .12 + e*.06, r)) * 1.25 * b;
    }
    // Mk II triangle
    { float an = t*.16*sp; mat2 m = mat2(cos(an), -sin(an), sin(an), cos(an));
      vec2 q = m * (uv * 2.8); float d = sdTri(q / .36) * .36;
      col += c * (1. - smoothstep(0., .02, abs(d))) * 1.35 * b;
      col += vec3(1.) * (1. - smoothstep(0., .006, abs(d))) * .7 * b; }
    // rings
    col += c * ring(r, .60, .014) * seg(a01, 24., .8, t*.05*sp) * .5 * reveal;
    col += c * ring(r, .68, .006) * seg(a01, 5., .62, -t*.12*sp) * 1.2 * reveal;
    col += c * ring(r, .77, .024) * seg(a01, 12., .86, t*.03*sp) * .32 * reveal;
    col += c * ring(r, .85, .004) * .9 * reveal;
    col += c * ring(r, .92, .011) * seg(a01, 3., .72, t*.085*sp) * .95 * reveal;
    // tick ring
    float fa = fract(a01*120.);
    float tick = smoothstep(0., .1, fa) * (1. - smoothstep(.25, .35, fa));
    float major = 1. - step(.017, fract(a01*12.));
    col += c * tick * step(.985, r) * (1. - step(1.02 + major*.035, r)) * (.45 + major*.5) * reveal;
    // voice spectrum
    float fi = a01 * 64.; int bi = int(floor(fi)); float bl = uSpec[bi];
    float bf = fract(fi); float bar = smoothstep(.12, .24, bf) * (1. - smoothstep(.76, .88, bf));
    float len = .015 + bl * .34;
    col += mix(c, uHot, bl*.6) * bar * step(1.07, r) * (1. - smoothstep(1.07 + len - .012, 1.07 + len, r)) * (.3 + bl*1.2) * reveal;
    // radar sweep
    float sw = fract(-t * (.22 + uThink*.7));
    float dA = fract(a01 - sw);
    col += mix(c, uHot, uThink) * exp(-dA*16.) * step(.42, r) * (1. - smoothstep(.95, 1.02, r)) * (.06 + uThink*.42 + uListen*.18);
    // ripples (speech start / wake)
    for (int i = 0; i < 4; i++) { float rp = uRip[i]; if (rp < 0. || rp > 1.) continue;
      col += c * ring(r, 1.05 + rp*.9, .012 + rp*.01) * (1. - rp) * .9; }
    // listening heartbeat ring
    float lb = fract(t*1.1); col += c * ring(r, 1.0 + lb*.35, .008) * (1. - lb) * uListen * .7;
    col *= mix(1., .32, uSleep);
    col = clamp(col, 0., 1.);
    o = vec4(col, max(col.r, max(col.g, col.b)));
  }`;

  let gl = null, prog = null, U = {};
  try {
    gl = cv.getContext("webgl2", { premultipliedAlpha: true, antialias: true, alpha: true });
    if (gl) {
      const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
      prog = gl.createProgram();
      gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS));
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
      gl.useProgram(prog);
      const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
      const loc = gl.getAttribLocation(prog, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      for (const n of ["uRes", "uTime", "uEnergy", "uBoot", "uThink", "uListen", "uSleep", "uCol", "uHot", "uSpec", "uRip"]) U[n] = gl.getUniformLocation(prog, n);
      gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    }
  } catch (e) { console.warn("WebGL reactor disabled:", e); gl = null; }
  const ctx2d = gl ? null : cv.getContext("2d");
  document.body.classList.toggle("webgl", !!gl);

  function resize() {
    dpr = Math.min(1.5, window.devicePixelRatio || 1);
    const r = cv.getBoundingClientRect();
    W = Math.max(1, r.width); H = Math.max(1, r.height);
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    BW = innerWidth; BH = innerHeight;
    bg.width = Math.round(BW * dpr); bg.height = Math.round(BH * dpr);
    hexTile = makeHexTile();
  }
  addEventListener("resize", resize);
  setTimeout(resize, 30);
  new ResizeObserver(resize).observe(cv);

  // ------------------------------------------------------------------ background
  function makeHexTile() {
    const s = 26, w = Math.sqrt(3) * s, h = 2 * s;
    const c = document.createElement("canvas"); c.width = Math.round(w * 2 * dpr); c.height = Math.round(h * 1.5 * dpr);
    const x = c.getContext("2d"); x.scale(dpr, dpr);
    const [hr, hg, hb] = R.base.map((v) => Math.round(v * 255));
    x.strokeStyle = `rgba(${hr},${hg},${hb},0.06)`; x.lineWidth = 1;
    const hex = (cx, cy) => { x.beginPath(); for (let i = 0; i < 6; i++) { const a = Math.PI / 3 * i + Math.PI / 6; x.lineTo(cx + s * Math.cos(a), cy + s * Math.sin(a)); } x.closePath(); x.stroke(); };
    for (let row = -1; row < 3; row++) for (let col = -1; col < 4; col++) hex(col * w + (row % 2 ? w / 2 : 0), row * h * 0.75);
    return c;
  }
  const dust = Array.from({ length: 110 }, () => ({ x: Math.random(), y: Math.random(), v: 0.00004 + Math.random() * 0.00016, s: Math.random() * 1.7 + 0.3, ph: Math.random() * 6 }));
  function drawBg(t) {
    const c = bctx; c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, BW, BH);
    const [r, g, b] = R.col.map((v) => Math.round(v * 255));
    const glow = c.createRadialGradient(BW * 0.5, BH * 0.46, 40, BW * 0.5, BH * 0.46, Math.max(BW, BH) * 0.75);
    glow.addColorStop(0, `rgba(${r},${g},${b},${0.13 + R.energy * 0.12})`); glow.addColorStop(1, "rgba(0,0,0,0)");
    c.fillStyle = glow; c.fillRect(0, 0, BW, BH);
    if (hexTile) {  // drifting hex grid
      const pat = c.createPattern(hexTile, "repeat");
      const m = new DOMMatrix().scaleSelf(1 / dpr, 1 / dpr).translateSelf((t * 6) % hexTile.width, (t * 3) % hexTile.height);
      pat.setTransform(m); c.fillStyle = pat; c.globalAlpha = 0.9; c.fillRect(0, 0, BW, BH); c.globalAlpha = 1;
    }
    // ripples from the reactor when speech starts
    const now = performance.now() / 1000;
    for (const rp of R.ripples) {
      const k = (now - rp.t) / 2.4; if (k > 1) continue;
      c.beginPath(); c.arc(BW * 0.5, BH * 0.45, 60 + k * Math.max(BW, BH) * 0.6, 0, 6.283);
      c.strokeStyle = `rgba(${r},${g},${b},${(1 - k) * 0.22})`; c.lineWidth = 2 + (1 - k) * 3; c.stroke();
    }
    for (const d of dust) {
      d.y -= d.v * 16 * (1 + R.energy * 3); if (d.y < 0) { d.y = 1; d.x = Math.random(); }
      c.fillStyle = `rgba(${r},${g},${b},${0.12 + 0.3 * Math.sin(t * 1.5 + d.ph) ** 2})`;
      c.fillRect(d.x * BW, d.y * BH, d.s, d.s);
    }
    const sy = (t * 70) % (BH + 300) - 150;  // slow scan beam
    const sg = c.createLinearGradient(0, sy - 90, 0, sy);
    sg.addColorStop(0, "rgba(0,0,0,0)"); sg.addColorStop(1, `rgba(${r},${g},${b},0.04)`);
    c.fillStyle = sg; c.fillRect(0, sy - 90, BW, 90);
  }

  // ------------------------------------------------------------------ 2D fallback reactor
  function draw2d(t, energy) {
    const c = ctx2d; c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, H);
    const cx = W / 2, cy = H / 2, r0 = Math.min(W, H) * 0.34 * (0.6 + 0.4 * R.boot);
    const rgb = R.col.map((v) => Math.round(v * 255)).join(",");
    c.globalCompositeOperation = "lighter";
    const halo = c.createRadialGradient(cx, cy, r0 * 0.1, cx, cy, r0 * 1.4);
    halo.addColorStop(0, `rgba(${rgb},${0.35 + energy * 0.4})`); halo.addColorStop(1, `rgba(${rgb},0)`);
    c.fillStyle = halo; c.beginPath(); c.arc(cx, cy, r0 * 1.4, 0, 6.283); c.fill();
    [[0.86, 3, 3, 0.35], [0.7, 2, 5, -0.6], [0.52, 2.5, 2, 0.9]].forEach(([rr, w, n, s]) => {
      for (let i = 0; i < n; i++) { const a = t * s + i * 6.283 / n; c.beginPath(); c.arc(cx, cy, r0 * rr, a, a + 6.283 / n * 0.7); c.lineWidth = w; c.strokeStyle = `rgba(${rgb},0.85)`; c.stroke(); }
    });
    const core = c.createRadialGradient(cx, cy, 0, cx, cy, r0 * 0.35);
    core.addColorStop(0, "rgba(255,255,255,.95)"); core.addColorStop(0.4, `rgba(${rgb},.85)`); core.addColorStop(1, `rgba(${rgb},0)`);
    c.fillStyle = core; c.beginPath(); c.arc(cx, cy, r0 * 0.35, 0, 6.283); c.fill();
    c.globalCompositeOperation = "source-over";
  }

  // ------------------------------------------------------------------ loop
  let last = performance.now();
  function frame(now) {
    requestAnimationFrame(frame);
    if (document.hidden) return;
    const t = now / 1000, dt = Math.min(0.05, (now - last) / 1000); last = now;
    R.boot = Math.min(1, R.boot + dt * 0.55);
    const st = document.body.classList.contains("sleeping") ? "sleeping" : R.state;
    const tgt = st === "listening" ? R.base.map((v) => Math.min(1, v * 0.6 + 0.45)) : st === "thinking" ? R.base.map((v, i) => v * 0.4 + R.hot[i] * 0.6) : R.base;
    for (let i = 0; i < 3; i++) R.col[i] += (tgt[i] - R.col[i]) * 0.07;
    const want = st === "speaking" ? R.voice * 1.2 : st === "listening" ? R.mic * 1.6 : st === "thinking" ? 0.35 + 0.12 * Math.sin(t * 6) : R.mic * 0.4;
    R.energy += (Math.min(1.2, want) - R.energy) * 0.18;
    const ease = (k, v) => k + (v - k) * 0.06;
    R.k.think = ease(R.k.think, st === "thinking" ? 1 : 0);
    R.k.listen = ease(R.k.listen, st === "listening" ? 1 : 0);
    R.k.sleep = ease(R.k.sleep, st === "sleeping" ? 1 : 0);
    for (let i = 0; i < 64; i++) {  // pseudo-spectrum shaped by the real level
      const n = 0.5 + 0.5 * Math.sin(i * 0.9 + t * 7.3) * Math.sin(i * 0.37 - t * 4.1);
      const env = 0.55 + 0.45 * Math.sin((i / 64) * 6.283 * 2 + t);
      R.spec[i] += (R.energy * (0.25 + n * 0.75) * env - R.spec[i]) * 0.3;
    }
    drawBg(t);
    if (gl) {
      gl.viewport(0, 0, cv.width, cv.height);
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform2f(U.uRes, cv.width, cv.height); gl.uniform1f(U.uTime, t);
      gl.uniform1f(U.uEnergy, R.energy); gl.uniform1f(U.uBoot, R.boot);
      gl.uniform1f(U.uThink, R.k.think); gl.uniform1f(U.uListen, R.k.listen); gl.uniform1f(U.uSleep, R.k.sleep);
      gl.uniform3fv(U.uCol, R.col); gl.uniform3fv(U.uHot, R.hot); gl.uniform1fv(U.uSpec, R.spec);
      const rp = [-1, -1, -1, -1]; R.ripples.slice(-4).forEach((x, i) => (rp[i] = (t - x.t) / 1.6));
      gl.uniform4fv(U.uRip, rp);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    } else {
      draw2d(t, R.energy);
    }
  }
  requestAnimationFrame(frame);
})();
