/* Time Strata - the picture, with WebGL2 and no library.
 *
 * The world is a dome: a 360 panorama (or a sky and a ground when there is none) put on a sphere whose lower
 * half is pressed flat into a ground at y = 0, as if the panorama had been taken `eye` metres above it. So the
 * camera can walk and fly inside it and the ground moves under it as a ground does.
 *
 * Every frame of every chain is a card: one quad, its picture a layer of one texture array (premultiplied, with
 * mipmaps and anisotropic filtering, because a stack is mostly seen at a slant). The cards are drawn every
 * frame from the farthest to the nearest, blended, testing but not writing depth, so the ground hides what is
 * below it and the cards show through one another as the stack does. A card dissolves as it reaches the lens
 * (instead of being sliced by the near plane), the knife throws away whatever is on its near side, and the
 * look of each chain (fade and tint towards its old end, glow at the edges, darker when seen edge-on) is
 * carried per card.
 */
(function () {
    'use strict';
    const Core = window.StrataCore;

    const WORLD_VS = `#version 300 es
in vec3 a_pos;
uniform mat4 u_mvp;
out vec3 v_world;
void main() { v_world = a_pos; gl_Position = u_mvp * vec4(a_pos, 1.0); }`;

    const WORLD_FS = `#version 300 es
precision highp float;
in vec3 v_world;
uniform sampler2D u_pano;
uniform float u_hasPano, u_turn, u_eye, u_radius, u_fog;
uniform vec3 u_top, u_horizon, u_ground, u_eyePos;
out vec4 o;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y);
}
void main() {
    vec3 d = normalize(v_world - vec3(0.0, u_eye, 0.0));
    vec3 c;
    if (u_hasPano > 0.5) {
        float u = 0.5 + atan(d.x, -d.z) / 6.2831853 + u_turn / 360.0;
        float v = acos(clamp(d.y, -1.0, 1.0)) / 3.1415927;
        vec2 uv = vec2(fract(u), v), uv2 = vec2(fract(u + 0.5) - 0.5, v);
        vec2 dx = dFdx(uv), dy = dFdy(uv), dx2 = dFdx(uv2), dy2 = dFdy(uv2);
        if (dot(dx2, dx2) + dot(dy2, dy2) < dot(dx, dx) + dot(dy, dy)) { dx = dx2; dy = dy2; }
        c = textureGrad(u_pano, uv, dx, dy).rgb;
    } else if (d.y >= 0.0) {
        c = mix(u_horizon, u_top, pow(d.y, 0.55));
    } else {
        float far = length(v_world.xz);
        float grain = noise(v_world.xz * 3.0) * 0.6 + noise(v_world.xz * 0.7) * 0.4;
        c = u_ground * (0.75 + 0.5 * grain);
        c = mix(c, u_horizon * 0.8, smoothstep(u_radius * 0.15, u_radius * 0.9, far));
    }
    float dist = length(v_world - u_eyePos);
    c = mix(c, u_horizon, (1.0 - exp(-u_fog * dist * 0.012)) * step(d.y, 0.02));
    o = vec4(c, 1.0);
}`;

    const CARD_VS = `#version 300 es
in vec3 a_bottom, a_right, a_up, a_tint, a_glowColor;
in float a_layer, a_alpha, a_tintAmount, a_glow, a_rib, a_sharp;
uniform mat4 u_mvp;
uniform vec2 u_uvScale;
out vec2 v_uv;
out vec3 v_world, v_normal, v_tint, v_glowColor;
flat out float v_layer, v_sharp;
out float v_alpha, v_tintAmount, v_glow, v_rib;
void main() {
    vec2 corner = vec2(float(gl_VertexID & 1), float(gl_VertexID >> 1));
    vec3 p = a_bottom + a_right * (corner.x * 2.0 - 1.0) + a_up * corner.y;
    v_uv = vec2(corner.x, 1.0 - corner.y) * u_uvScale;
    v_world = p;
    v_normal = normalize(cross(a_up, a_right));
    v_layer = a_layer; v_sharp = a_sharp; v_alpha = a_alpha; v_tint = a_tint; v_tintAmount = a_tintAmount;
    v_glow = a_glow; v_glowColor = a_glowColor; v_rib = a_rib;
    gl_Position = u_mvp * vec4(p, 1.0);
}`;

    const CARD_FS = `#version 300 es
precision highp float;
precision highp sampler2DArray;
in vec2 v_uv;
in vec3 v_world, v_normal, v_tint, v_glowColor;
flat in float v_layer, v_sharp;
in float v_alpha, v_tintAmount, v_glow, v_rib;
uniform sampler2DArray u_cards, u_sharp;
uniform vec3 u_eye, u_fogColor;
uniform vec4 u_knife;
uniform float u_knifeOn, u_fog;
uniform vec2 u_near;
out vec4 o;
vec4 frame(float lod) {
    // a frame near the camera is read from the full-size copy, when it has one
    if (v_sharp >= 0.0) return lod < 0.0 ? texture(u_sharp, vec3(v_uv, v_sharp)) : textureLod(u_sharp, vec3(v_uv, v_sharp), lod + 1.0);
    return lod < 0.0 ? texture(u_cards, vec3(v_uv, v_layer)) : textureLod(u_cards, vec3(v_uv, v_layer), lod);
}
void main() {
    if (u_knifeOn > 0.5 && dot(u_knife.xyz, v_world) + u_knife.w < 0.0) discard;
    vec4 c = frame(-1.0);
    if (c.a < 0.003) discard;
    vec3 toCard = v_world - u_eye;
    float dist = length(toCard);
    vec3 rgb = c.rgb;
    if (v_rib > 0.0) {
        float facing = abs(dot(toCard / max(dist, 1e-4), v_normal));
        rgb *= mix(1.0, 0.5 + 0.5 * facing, v_rib);
    }
    rgb = mix(rgb, v_tint * c.a, v_tintAmount);
    if (v_glow > 0.0) {
        float soft = frame(3.0).a;
        float rim = clamp((c.a - soft) * 2.5, 0.0, 1.0);
        rgb += v_glowColor * rim * v_glow * c.a;
    }
    float f = 1.0 - exp(-u_fog * dist * 0.012);
    rgb = mix(rgb, u_fogColor * c.a, f);
    float keep = v_alpha * smoothstep(u_near.x, u_near.y, dist);
    o = vec4(rgb, c.a) * keep;
}`;

    const SHADOW_VS = `#version 300 es
in vec3 a_pos;
in float a_shade;
uniform mat4 u_mvp;
out float v_shade;
void main() { v_shade = a_shade; gl_Position = u_mvp * vec4(a_pos, 1.0); }`;

    const SHADOW_FS = `#version 300 es
precision highp float;
in float v_shade;
out vec4 o;
void main() { o = vec4(0.0, 0.0, 0.0, v_shade); }`;

    function compile(gl, vs, fs) {
        const program = gl.createProgram();
        for (const [type, text] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
            const shader = gl.createShader(type);
            gl.shaderSource(shader, text);
            gl.compileShader(shader);
            if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error('shader: ' + gl.getShaderInfoLog(shader));
            gl.attachShader(program, shader);
        }
        gl.linkProgram(program);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('program: ' + gl.getProgramInfoLog(program));
        const info = { program, u: {}, a: {} };
        const uniforms = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS);
        for (let i = 0; i < uniforms; i++) { const n = gl.getActiveUniform(program, i).name; info.u[n] = gl.getUniformLocation(program, n); }
        const attributes = gl.getProgramParameter(program, gl.ACTIVE_ATTRIBUTES);
        for (let i = 0; i < attributes; i++) { const n = gl.getActiveAttrib(program, i).name; info.a[n] = gl.getAttribLocation(program, n); }
        return info;
    }

    const hex = (text, fallback) => {
        const m = /^#?([0-9a-f]{6})$/i.exec(String(text || ''));
        if (!m) return fallback || [0.5, 0.5, 0.5];
        const n = parseInt(m[1], 16);
        return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255];
    };

    // per card: bottom 3, right 3, up 3, layer 1, alpha 1, tint 3, tintAmount 1, glow 1, glowColor 3, rib 1,
    // sharp 1 (its slot among the full-size copies, or -1)
    const CARD_FLOATS = 21;
    const CARD_LAYOUT = [['a_bottom', 3, 0], ['a_right', 3, 3], ['a_up', 3, 6], ['a_layer', 1, 9], ['a_alpha', 1, 10],
                         ['a_tint', 3, 11], ['a_tintAmount', 1, 14], ['a_glow', 1, 15], ['a_glowColor', 3, 16], ['a_rib', 1, 19],
                         ['a_sharp', 1, 20]];

    function create(canvas) {
        const gl = canvas.getContext('webgl2', { antialias: true, alpha: false, premultipliedAlpha: true, preserveDrawingBuffer: true });
        if (!gl) throw new Error('This browser has no WebGL2');
        const aniso = gl.getExtension('EXT_texture_filter_anisotropic');
        const world = compile(gl, WORLD_VS, WORLD_FS), cards = compile(gl, CARD_VS, CARD_FS), shadow = compile(gl, SHADOW_VS, SHADOW_FS);
        const R = {
            gl, canvas, maxLayers: gl.getParameter(gl.MAX_ARRAY_TEXTURE_LAYERS), maxSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
            texture: null, texW: 0, texH: 0, layers: 0, uvScale: [1, 1], pano: null, panoUrl: null, worldKey: '',
            drawn: 0,
        };

        /* ---- the dome ---- */
        const worldVao = gl.createVertexArray(), worldBuffer = gl.createBuffer(), worldIndex = gl.createBuffer();
        let worldCount = 0;
        function buildWorld(eye, radius) {
            const key = eye + '/' + radius;
            if (key === R.worldKey) return;
            R.worldKey = key;
            const W = 160, H = 96, pos = [], idx = [];
            for (let j = 0; j <= H; j++) {
                // more rows near the horizon, where the ground runs out to the wall
                const v = j / H, theta = Math.PI * (v < 0.5 ? v : 0.5 + Math.pow((v - 0.5) * 2, 0.6) * 0.5);
                for (let i = 0; i <= W; i++) {
                    const phi = 2 * Math.PI * i / W;
                    const d = [Math.sin(theta) * Math.sin(phi), Math.cos(theta), -Math.sin(theta) * Math.cos(phi)];
                    const t = d[1] < -1e-4 ? Math.min(radius, eye / -d[1]) : radius;
                    pos.push(d[0] * t, eye + d[1] * t, d[2] * t);
                }
            }
            for (let j = 0; j < H; j++) for (let i = 0; i < W; i++) {
                const a = j * (W + 1) + i, b = a + W + 1;
                idx.push(a, b, a + 1, a + 1, b, b + 1);
            }
            gl.bindVertexArray(worldVao);
            gl.bindBuffer(gl.ARRAY_BUFFER, worldBuffer);
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(pos), gl.STATIC_DRAW);
            gl.enableVertexAttribArray(world.a.a_pos);
            gl.vertexAttribPointer(world.a.a_pos, 3, gl.FLOAT, false, 0, 0);
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, worldIndex);
            gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint32Array(idx), gl.STATIC_DRAW);
            gl.bindVertexArray(null);
            worldCount = idx.length;
        }

        function panoramaPicture(t, source) {
            gl.bindTexture(gl.TEXTURE_2D, t);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, source);
            gl.generateMipmap(gl.TEXTURE_2D);
        }

        /* The world's panorama: a picture, or (`video`) a video whose frame is chosen by panoramaFrame. A video is
         * read whole into memory first, so it can be sent to any moment at once. */
        R.setPanorama = async function (url, video) {
            if (url === R.panoUrl) return;
            R.panoUrl = url;
            if (R.pano) { gl.deleteTexture(R.pano); R.pano = null; }
            if (R.panoVideo) { URL.revokeObjectURL(R.panoVideo.el.src); R.panoVideo.el.removeAttribute('src'); R.panoVideo = null; }
            if (!url) return;
            const blob = await (await fetch(url)).blob();
            let source, el = null;
            if (video) {
                el = document.createElement('video');
                el.muted = true; el.playsInline = true; el.preload = 'auto';
                const typed = new Blob([blob], { type: /\.webm$/i.test(url) ? 'video/webm' : 'video/mp4' });
                await new Promise((resolve, reject) => {
                    el.addEventListener('loadeddata', resolve, { once: true });
                    el.addEventListener('error', () => reject(new Error('This video cannot be played here')), { once: true });
                    el.src = URL.createObjectURL(typed);
                });
                source = el;
            } else source = await createImageBitmap(blob, { colorSpaceConversion: 'none' });
            if (R.panoUrl !== url) { if (el) URL.revokeObjectURL(el.src); return; }
            const t = gl.createTexture();
            panoramaPicture(t, source);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            if (aniso) gl.texParameterf(gl.TEXTURE_2D, aniso.TEXTURE_MAX_ANISOTROPY_EXT, 8);
            if (!video) source.close && source.close();
            R.pano = t;
            R.panoVideo = el ? { el, shown: 0, seeking: null } : null;
        };

        R.panoramaDuration = () => (R.panoVideo ? R.panoVideo.el.duration || 0 : 0);

        /* The world video's frame at second `time` of the video put on the panorama. With `wait`, a promise that
         * ends when it is there (a recording draws only then); without, the seek goes on and `ready` is called
         * when the frame is in place (the preview draws again). A frame already shown costs nothing. */
        R.panoramaFrame = function (time, wait, ready) {
            const V = R.panoVideo;
            if (!V || !R.pano) return Promise.resolve();
            const t = Math.max(0, Math.min(time, (V.el.duration || 0) - 0.001));
            if (Math.abs(V.shown - t) < 0.004) return Promise.resolve();
            if (V.seeking) return wait ? V.seeking.then(() => R.panoramaFrame(time, wait, ready)) : Promise.resolve();
            V.seeking = new Promise(resolve => {
                let timer = null;
                const done = () => {
                    V.el.removeEventListener('seeked', done);
                    clearTimeout(timer);
                    if (R.panoVideo === V && R.pano) { panoramaPicture(R.pano, V.el); V.shown = t; }
                    V.seeking = null;
                    resolve();
                    if (ready) ready();
                };
                V.el.addEventListener('seeked', done);
                timer = setTimeout(done, 3000);          // (a seek that never ends does not stop a recording)
                V.el.currentTime = t;
            });
            return wait ? V.seeking : Promise.resolve();
        };

        /* ---- the frames ---- */

        /* The size a frame gets on the graphics card: as sharp as the source, but all the frames of every chain
         * must fit in `budget` megabytes (with their mipmaps). */
        R.textureSize = function (layers, aspect, sourceHeight, budget) {
            const bytes = (budget || 640) * 1048576;
            let h = Math.floor(Math.sqrt(bytes / (layers * 4 * 1.34 * aspect)));
            h = Math.max(64, Math.min(sourceHeight || 1024, h, R.maxSize));
            h -= h % 4;
            const w = Math.max(4, Math.min(R.maxSize, Math.round(h * aspect / 4) * 4));
            return [w, h];
        };

        R.allocate = function (layers, w, h) {
            if (R.texture) gl.deleteTexture(R.texture);
            layers = Math.max(1, Math.min(layers, R.maxLayers));
            const levels = 1 + Math.floor(Math.log2(Math.max(w, h)));
            R.texture = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D_ARRAY, R.texture);
            gl.texStorage3D(gl.TEXTURE_2D_ARRAY, levels, gl.RGBA8, w, h, layers);
            gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
            gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            if (aniso) gl.texParameterf(gl.TEXTURE_2D_ARRAY, aniso.TEXTURE_MAX_ANISOTROPY_EXT, Math.min(16, gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT)));
            // (WebGL hands out a new texture cleared, so a layer not loaded yet shows nothing)
            R.texW = w; R.texH = h; R.layers = layers;
        };

        /* a frame's picture into a layer; `source`: a Blob or an ImageBitmap-able thing */
        R.upload = async function (layer, source) {
            const bitmap = await createImageBitmap(source, { premultiplyAlpha: 'premultiply', colorSpaceConversion: 'none',
                                                             resizeWidth: R.texW, resizeHeight: R.texH, resizeQuality: 'high' });
            gl.bindTexture(gl.TEXTURE_2D_ARRAY, R.texture);
            gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, layer, R.texW, R.texH, 1, gl.RGBA, gl.UNSIGNED_BYTE, bitmap);
            bitmap.close && bitmap.close();
        };

        R.finishUploads = function () {
            if (!R.texture) return;
            gl.bindTexture(gl.TEXTURE_2D_ARRAY, R.texture);
            gl.generateMipmap(gl.TEXTURE_2D_ARRAY);
        };

        /* ---- full-size copies of the frames nearest the camera ----
         * All the frames of a long chain must share the card's memory, so each is kept small (a 600-frame chain:
         * about 440 pixels high). The few that come close to the lens are also kept at full size, in slots of a
         * second array; a card that has a slot is drawn from it. */
        function arrayTexture(w, h, layers, levels) {
            const t = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D_ARRAY, t);
            gl.texStorage3D(gl.TEXTURE_2D_ARRAY, levels, gl.RGBA8, w, h, layers);
            gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MIN_FILTER, levels > 1 ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            if (aniso && levels > 1) gl.texParameterf(gl.TEXTURE_2D_ARRAY, aniso.TEXTURE_MAX_ANISOTROPY_EXT, Math.min(16, gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT)));
            return t;
        }
        const noSharp = arrayTexture(1, 1, 1, 1);         // what the sampler reads when there are no copies
        R.sharp = null; R.sharpW = 0; R.sharpH = 0; R.sharpSlots = 0;

        R.allocateSharp = function (slots, w, h) {
            if (R.sharp) gl.deleteTexture(R.sharp);
            R.sharp = null; R.sharpSlots = 0;
            if (!slots) return;
            w = Math.min(w, R.maxSize); h = Math.min(h, R.maxSize);
            R.sharp = arrayTexture(w, h, slots, 1 + Math.floor(Math.log2(Math.max(w, h))));
            R.sharpW = w; R.sharpH = h; R.sharpSlots = slots;
        };

        R.uploadSharp = async function (slot, source) {
            const bitmap = await createImageBitmap(source, { premultiplyAlpha: 'premultiply', colorSpaceConversion: 'none',
                                                             resizeWidth: R.sharpW, resizeHeight: R.sharpH, resizeQuality: 'high' });
            if (!R.sharp) return;
            gl.bindTexture(gl.TEXTURE_2D_ARRAY, R.sharp);
            gl.texSubImage3D(gl.TEXTURE_2D_ARRAY, 0, 0, 0, slot, R.sharpW, R.sharpH, 1, gl.RGBA, gl.UNSIGNED_BYTE, bitmap);
            R.sharpDirty = true;            // the smaller levels are made once, before the next picture
            bitmap.close && bitmap.close();
        };

        /* ---- cards ---- */
        const cardVao = gl.createVertexArray(), cardBuffer = gl.createBuffer();
        let cardData = new Float32Array(CARD_FLOATS * 256);
        gl.bindVertexArray(cardVao);
        gl.bindBuffer(gl.ARRAY_BUFFER, cardBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, cardData.byteLength, gl.DYNAMIC_DRAW);
        for (const [name, size, offset] of CARD_LAYOUT) {
            const loc = cards.a[name];
            if (loc == null || loc < 0) continue;
            gl.enableVertexAttribArray(loc);
            gl.vertexAttribPointer(loc, size, gl.FLOAT, false, CARD_FLOATS * 4, offset * 4);
            gl.vertexAttribDivisor(loc, 1);
        }
        gl.bindVertexArray(null);

        const shadowVao = gl.createVertexArray(), shadowBuffer = gl.createBuffer();
        gl.bindVertexArray(shadowVao);
        gl.bindBuffer(gl.ARRAY_BUFFER, shadowBuffer);
        gl.enableVertexAttribArray(shadow.a.a_pos);
        gl.vertexAttribPointer(shadow.a.a_pos, 3, gl.FLOAT, false, 16, 0);
        gl.enableVertexAttribArray(shadow.a.a_shade);
        gl.vertexAttribPointer(shadow.a.a_shade, 1, gl.FLOAT, false, 16, 12);
        gl.bindVertexArray(null);

        R.resize = function (w, h) {
            if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
        };

        /* Draws one picture.
         * scene: {world, camera: {eye, target, up, fov}, cards: [{bottom, right, up, layer, alpha, tint, tintAmount,
         *         glow, glowColor, rib}], shadows: [[x, z, halfWidth, strength] ...] per chain, knife, near: [a, b]} */
        R.draw = function (scene) {
            const w = canvas.width, h = canvas.height;
            gl.viewport(0, 0, w, h);
            const W = scene.world || {};
            const eyeHeight = +W.eye || 1.6, radius = +W.radius || 90;
            buildWorld(eyeHeight, radius);
            const cam = scene.camera;
            const view = Core.lookAt(cam.eye, cam.target, cam.up);
            const proj = Core.perspective(cam.fov, w / Math.max(1, h), 0.03, radius * 3);
            const mvp = new Float32Array(Core.multiply(proj, view));
            const horizon = hex(W.sky_horizon, [0.9, 0.75, 0.84]);

            gl.clearColor(0, 0, 0, 1);
            gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
            gl.enable(gl.DEPTH_TEST);
            gl.depthFunc(gl.LEQUAL);
            gl.disable(gl.CULL_FACE);

            // the world
            gl.disable(gl.BLEND);
            gl.depthMask(true);
            gl.useProgram(world.program);
            gl.uniformMatrix4fv(world.u.u_mvp, false, mvp);
            gl.uniform1f(world.u.u_hasPano, R.pano ? 1 : 0);
            gl.uniform1f(world.u.u_turn, +W.turn || 0);
            gl.uniform1f(world.u.u_eye, eyeHeight);
            gl.uniform1f(world.u.u_radius, radius);
            gl.uniform1f(world.u.u_fog, +W.fog || 0);
            gl.uniform3fv(world.u.u_top, hex(W.sky_top, [0.36, 0.37, 0.66]));
            gl.uniform3fv(world.u.u_horizon, horizon);
            gl.uniform3fv(world.u.u_ground, hex(W.ground, [0.2, 0.31, 0.12]));
            gl.uniform3fv(world.u.u_eyePos, cam.eye);
            gl.activeTexture(gl.TEXTURE0);
            gl.bindTexture(gl.TEXTURE_2D, R.pano);
            gl.uniform1i(world.u.u_pano, 0);
            gl.bindVertexArray(worldVao);
            gl.drawElements(gl.TRIANGLES, worldCount, gl.UNSIGNED_INT, 0);

            gl.enable(gl.BLEND);
            gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
            gl.depthMask(false);

            // the soft shadow the chain lays on the ground under its feet
            if (scene.shadows && scene.shadows.length) {
                const data = [];
                for (const strip of scene.shadows) {
                    for (let i = 0; i < strip.length - 1; i++) {
                        const [x0, z0, w0, a0] = strip[i], [x1, z1, w1, a1] = strip[i + 1];
                        let dx = x1 - x0, dz = z1 - z0; const l = Math.hypot(dx, dz) || 1; dx /= l; dz /= l;
                        const n0 = [-dz * w0, dx * w0], n1 = [-dz * w1, dx * w1];
                        const y = 0.004;
                        // a strip of quads: dark in the middle, clear at the sides
                        const rows = [[-1, 0], [-0.45, 1], [0.45, 1], [1, 0]];
                        for (let r = 0; r < 3; r++) {
                            const [sa, fa] = rows[r], [sb, fb] = rows[r + 1];
                            const q = [[x0 + n0[0] * sa, y, z0 + n0[1] * sa, a0 * fa], [x0 + n0[0] * sb, y, z0 + n0[1] * sb, a0 * fb],
                                       [x1 + n1[0] * sa, y, z1 + n1[1] * sa, a1 * fa], [x1 + n1[0] * sb, y, z1 + n1[1] * sb, a1 * fb]];
                            data.push(...q[0], ...q[1], ...q[2], ...q[2], ...q[1], ...q[3]);
                        }
                    }
                }
                if (data.length) {
                    gl.useProgram(shadow.program);
                    gl.uniformMatrix4fv(shadow.u.u_mvp, false, mvp);
                    gl.bindVertexArray(shadowVao);
                    gl.bindBuffer(gl.ARRAY_BUFFER, shadowBuffer);
                    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STREAM_DRAW);
                    gl.drawArrays(gl.TRIANGLES, 0, data.length / 4);
                }
            }

            // the cards, farthest first
            const list = scene.cards || [];
            R.drawn = 0;
            if (R.texture && list.length) {
                const eye = cam.eye, fwd = cam.forward || Core.norm(Core.sub(cam.target, cam.eye));
                const knife = scene.knife;
                const kept = [];
                for (const c of list) {
                    const centre = [c.bottom[0] + c.up[0] * 0.5, c.bottom[1] + c.up[1] * 0.5, c.bottom[2] + c.up[2] * 0.5];
                    const rel = [centre[0] - eye[0], centre[1] - eye[1], centre[2] - eye[2]];
                    const reach = Math.hypot(c.up[0], c.up[1], c.up[2]) * 0.6 + Math.hypot(c.right[0], c.right[1], c.right[2]);
                    if (rel[0] * fwd[0] + rel[1] * fwd[1] + rel[2] * fwd[2] < -reach) continue;     // behind the camera
                    if (knife) {
                        const side = Core.dot(knife.normal, Core.sub(centre, knife.point));
                        if (side < -reach) continue;                                                  // cut away whole
                    }
                    c._d = rel[0] * rel[0] + rel[1] * rel[1] + rel[2] * rel[2];
                    kept.push(c);
                }
                kept.sort((a, b) => b._d - a._d);
                if (cardData.length < kept.length * CARD_FLOATS) {
                    cardData = new Float32Array(Math.ceil(kept.length * 1.5) * CARD_FLOATS);
                    gl.bindBuffer(gl.ARRAY_BUFFER, cardBuffer);
                    gl.bufferData(gl.ARRAY_BUFFER, cardData.byteLength, gl.DYNAMIC_DRAW);
                }
                let o = 0;
                for (const c of kept) {
                    cardData.set(c.bottom, o); cardData.set(c.right, o + 3); cardData.set(c.up, o + 6);
                    cardData[o + 9] = c.layer; cardData[o + 10] = c.alpha == null ? 1 : c.alpha;
                    cardData.set(c.tint || [0, 0, 0], o + 11); cardData[o + 14] = c.tintAmount || 0;
                    cardData[o + 15] = c.glow || 0; cardData.set(c.glowColor || [0, 0, 0], o + 16); cardData[o + 19] = c.rib || 0;
                    cardData[o + 20] = R.sharp && c.sharp != null ? c.sharp : -1;
                    o += CARD_FLOATS;
                }
                gl.useProgram(cards.program);
                gl.uniformMatrix4fv(cards.u.u_mvp, false, mvp);
                gl.uniform2fv(cards.u.u_uvScale, R.uvScale);
                gl.uniform3fv(cards.u.u_eye, eye);
                gl.uniform3fv(cards.u.u_fogColor, horizon);
                gl.uniform1f(cards.u.u_fog, +W.fog || 0);
                gl.uniform2fv(cards.u.u_near, scene.near || [0.12, 0.42]);
                if (knife) {
                    gl.uniform1f(cards.u.u_knifeOn, 1);
                    gl.uniform4f(cards.u.u_knife, knife.normal[0], knife.normal[1], knife.normal[2], -Core.dot(knife.normal, knife.point));
                } else gl.uniform1f(cards.u.u_knifeOn, 0);
                gl.activeTexture(gl.TEXTURE1);
                gl.bindTexture(gl.TEXTURE_2D_ARRAY, R.texture);
                gl.uniform1i(cards.u.u_cards, 1);
                gl.activeTexture(gl.TEXTURE2);
                gl.bindTexture(gl.TEXTURE_2D_ARRAY, R.sharp || noSharp);
                if (R.sharp && R.sharpDirty) { gl.generateMipmap(gl.TEXTURE_2D_ARRAY); R.sharpDirty = false; }
                gl.uniform1i(cards.u.u_sharp, 2);
                gl.bindVertexArray(cardVao);
                gl.bindBuffer(gl.ARRAY_BUFFER, cardBuffer);
                gl.bufferSubData(gl.ARRAY_BUFFER, 0, cardData, 0, kept.length * CARD_FLOATS);
                gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, kept.length);
                R.drawn = kept.length;
            }
            gl.bindVertexArray(null);
            gl.depthMask(true);
        };

        /* the picture as it is now: RGBA bytes, the bottom row first */
        R.pixels = function () {
            const out = new Uint8Array(canvas.width * canvas.height * 4);
            gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, out);
            return out;
        };

        return R;
    }

    window.StrataGL = { create, hex };
})();
