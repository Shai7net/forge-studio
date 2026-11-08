/* Simulation 360 - solid objects: real 3D models (glTF binary files), their bones and their movements.
 * No page and no WebGL in it, so it can be tested with node.
 *
 * A model is read into plain arrays: corners (position, normal, where on its picture), triangles, pictures.
 * A model that brings bones and movements of its own (made in Blender, Mixamo...) keeps them. A model that
 * brings none (what Hunyuan3D makes) can be given bones here: where its joints are is guessed from its shape,
 * every corner is tied to the bones nearest to it along its surface, and the movements are written as
 * formulas (a walk is two legs half a stride apart), so they need no recorded motion.
 *
 * A model's own space: x to its left hand, y up, z out of its face (glTF's "front").
 */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.Sim360Solid = api;
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const RAD = Math.PI / 180;
    const clamp = (x, low, high) => Math.min(high, Math.max(low, x));
    const lerp = (a, b, t) => a + (b - a) * t;
    const smooth = t => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };

    /* ---- matrices (4 x 4, column after column, as WebGL takes them) --------------------------------------- */

    const identity = () => { const m = new Float32Array(16); m[0] = m[5] = m[10] = m[15] = 1; return m; };

    function multiply(a, b, out) {
        out = out || new Float32Array(16);
        const r = new Array(16);
        for (let c = 0; c < 4; c++) for (let row = 0; row < 4; row++) {
            r[c * 4 + row] = a[row] * b[c * 4] + a[4 + row] * b[c * 4 + 1] + a[8 + row] * b[c * 4 + 2] + a[12 + row] * b[c * 4 + 3];
        }
        for (let i = 0; i < 16; i++) out[i] = r[i];
        return out;
    }

    /* place, turn (a quaternion x, y, z, w) and size in one matrix */
    function fromTRS(t, q, s, out) {
        out = out || new Float32Array(16);
        const x = q[0], y = q[1], z = q[2], w = q[3];
        const xx = x * x, yy = y * y, zz = z * z, xy = x * y, xz = x * z, yz = y * z, wx = w * x, wy = w * y, wz = w * z;
        out[0] = (1 - 2 * (yy + zz)) * s[0]; out[1] = 2 * (xy + wz) * s[0]; out[2] = 2 * (xz - wy) * s[0]; out[3] = 0;
        out[4] = 2 * (xy - wz) * s[1]; out[5] = (1 - 2 * (xx + zz)) * s[1]; out[6] = 2 * (yz + wx) * s[1]; out[7] = 0;
        out[8] = 2 * (xz + wy) * s[2]; out[9] = 2 * (yz - wx) * s[2]; out[10] = (1 - 2 * (xx + yy)) * s[2]; out[11] = 0;
        out[12] = t[0]; out[13] = t[1]; out[14] = t[2]; out[15] = 1;
        return out;
    }

    /* a turn about a point: degrees about x (nod), y (turn) and z (lean), the turn about y done last */
    function about(at, rx, ry, rz, out) {
        out = out || new Float32Array(16);
        const cx = Math.cos(rx * RAD), sx = Math.sin(rx * RAD), cy = Math.cos(ry * RAD), sy = Math.sin(ry * RAD), cz = Math.cos(rz * RAD), sz = Math.sin(rz * RAD);
        // R = Ry * Rx * Rz
        const m00 = cy * cz + sy * sx * sz, m01 = -cy * sz + sy * sx * cz, m02 = sy * cx;
        const m10 = cx * sz, m11 = cx * cz, m12 = -sx;
        const m20 = -sy * cz + cy * sx * sz, m21 = sy * sz + cy * sx * cz, m22 = cy * cx;
        out[0] = m00; out[1] = m10; out[2] = m20; out[3] = 0;
        out[4] = m01; out[5] = m11; out[6] = m21; out[7] = 0;
        out[8] = m02; out[9] = m12; out[10] = m22; out[11] = 0;
        out[12] = at[0] - (m00 * at[0] + m01 * at[1] + m02 * at[2]);
        out[13] = at[1] - (m10 * at[0] + m11 * at[1] + m12 * at[2]);
        out[14] = at[2] - (m20 * at[0] + m21 * at[1] + m22 * at[2]);
        out[15] = 1;
        return out;
    }

    const moved = (m, p) => [m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12], m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13], m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14]];

    function slerp(a, b, t) {
        let dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3], sign = 1;
        if (dot < 0) { dot = -dot; sign = -1; }
        let ka = 1 - t, kb = t;
        if (dot < 0.9995) { const angle = Math.acos(dot), s = Math.sin(angle); ka = Math.sin((1 - t) * angle) / s; kb = Math.sin(t * angle) / s; }
        const q = [ka * a[0] + kb * sign * b[0], ka * a[1] + kb * sign * b[1], ka * a[2] + kb * sign * b[2], ka * a[3] + kb * sign * b[3]];
        const n = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
        return [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
    }

    /* ---- reading a .glb ---------------------------------------------------------------------------------- */

    const WIDTH = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 };
    const READ = {
        5120: [1, (v, o) => v.getInt8(o), 127], 5121: [1, (v, o) => v.getUint8(o), 255], 5122: [2, (v, o) => v.getInt16(o, true), 32767],
        5123: [2, (v, o) => v.getUint16(o, true), 65535], 5125: [4, (v, o) => v.getUint32(o, true), 1], 5126: [4, (v, o) => v.getFloat32(o, true), 1],
    };

    function numbers(gltf, bin, index, whole) {
        const a = gltf.accessors[index], n = WIDTH[a.type], count = a.count, how = READ[a.componentType];
        const out = whole ? new Uint32Array(count * n) : new Float32Array(count * n);
        if (a.bufferView == null || !how) return out;
        const view = gltf.bufferViews[a.bufferView], size = how[0], get = how[1];
        const stride = view.byteStride || n * size, start = bin.byteOffset + (view.byteOffset || 0) + (a.byteOffset || 0);
        const data = new DataView(bin.buffer, start, Math.min(bin.byteLength - (start - bin.byteOffset), (count - 1) * stride + n * size));
        const scale = a.normalized && !whole ? 1 / how[2] : 1;
        for (let i = 0; i < count; i++) for (let k = 0; k < n; k++) out[i * n + k] = get(data, i * stride + k * size) * scale;
        return out;
    }

    /* A model from the bytes of a .glb file. Throws an Error that says what is wrong with the file. */
    function read(buffer) {
        const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
        const head = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        if (bytes.length < 20 || head.getUint32(0, true) !== 0x46546C67 || head.getUint32(4, true) !== 2) throw new Error('This is not a .glb model');
        let gltf = null, bin = new Uint8Array(0), at = 12;
        while (at + 8 <= bytes.length) {
            const size = head.getUint32(at, true), kind = head.getUint32(at + 4, true), piece = bytes.subarray(at + 8, at + 8 + size);
            if (kind === 0x4E4F534A) gltf = JSON.parse(new TextDecoder().decode(piece));
            else if (kind === 0x004E4942) bin = piece;
            at += 8 + size + (size % 4 ? 4 - size % 4 : 0);
        }
        if (!gltf || !(gltf.meshes || []).length) throw new Error('This model has no shape in it');
        const packed = (gltf.extensionsRequired || []).filter(x => /draco|meshopt/i.test(x));
        if (packed.length) throw new Error('This model is packed: save it again without compression');

        const nodes = (gltf.nodes || []).map(n => ({
            parent: -1, mesh: n.mesh, skin: n.skin, children: n.children || [],
            t: n.translation || [0, 0, 0], r: n.rotation || [0, 0, 0, 1], s: n.scale || [1, 1, 1], matrix: n.matrix ? new Float32Array(n.matrix) : null,
        }));
        nodes.forEach((n, i) => { for (const c of n.children) if (nodes[c]) nodes[c].parent = i; });
        const scene = (gltf.scenes || [])[gltf.scene || 0], roots = scene && scene.nodes ? scene.nodes : nodes.map((n, i) => i).filter(i => nodes[i].parent < 0);
        const order = [];
        (function walk(list) { for (const i of list) if (nodes[i] && !order.includes(i)) { order.push(i); walk(nodes[i].children); } })(roots);
        const rest = worlds(nodes, order, null);
        const alive = (gltf.animations || []).length > 0 || (gltf.skins || []).length > 0;

        // every piece of every mesh becomes one run of corners; a model that moves keeps each corner's bones
        const position = [], normal = [], uv = [], index = [], bone = [], weight = [], parts = [], joints = [], inverse = [];
        const skinStart = (gltf.skins || []).map(skin => {
            const start = joints.length, given = skin.inverseBindMatrices != null ? numbers(gltf, bin, skin.inverseBindMatrices) : null;
            skin.joints.forEach((node, j) => { joints.push(node); inverse.push(given ? given.subarray(j * 16, j * 16 + 16) : identity()); });
            return start;
        });
        let corners = 0;
        for (const i of order) {
            const node = nodes[i], mesh = node.mesh != null ? gltf.meshes[node.mesh] : null;
            if (!mesh) continue;
            const skinned = alive && node.skin != null && skinStart[node.skin] != null;
            let own = -1;
            if (alive && !skinned) { own = joints.length; joints.push(i); inverse.push(identity()); }      // a stiff piece moves with its node
            for (const part of mesh.primitives || []) {
                if ((part.mode == null ? 4 : part.mode) !== 4 || part.attributes.POSITION == null) continue;
                const p = numbers(gltf, bin, part.attributes.POSITION), count = p.length / 3;
                const n = part.attributes.NORMAL != null ? numbers(gltf, bin, part.attributes.NORMAL) : null;
                const t = part.attributes.TEXCOORD_0 != null ? numbers(gltf, bin, part.attributes.TEXCOORD_0) : null;
                const tri = part.indices != null ? numbers(gltf, bin, part.indices, true) : Uint32Array.from({ length: count }, (_, k) => k);
                const m = alive ? null : rest[i];
                for (let k = 0; k < count; k++) {
                    const a = [p[k * 3], p[k * 3 + 1], p[k * 3 + 2]], b = m ? moved(m, a) : a;
                    position.push(b[0], b[1], b[2]);
                    if (n) {
                        const x = n[k * 3], y = n[k * 3 + 1], z = n[k * 3 + 2];
                        if (m) normal.push(m[0] * x + m[4] * y + m[8] * z, m[1] * x + m[5] * y + m[9] * z, m[2] * x + m[6] * y + m[10] * z); else normal.push(x, y, z);
                    } else normal.push(0, 0, 0);
                    uv.push(t ? t[k * 2] : 0, t ? t[k * 2 + 1] : 0);
                }
                if (alive) {
                    const j = skinned && part.attributes.JOINTS_0 != null ? numbers(gltf, bin, part.attributes.JOINTS_0, true) : null;
                    const w = skinned && part.attributes.WEIGHTS_0 != null ? numbers(gltf, bin, part.attributes.WEIGHTS_0) : null;
                    for (let k = 0; k < count; k++) {
                        if (j && w) {
                            const sum = w[k * 4] + w[k * 4 + 1] + w[k * 4 + 2] + w[k * 4 + 3] || 1;
                            for (let c = 0; c < 4; c++) { bone.push(skinStart[node.skin] + j[k * 4 + c]); weight.push(w[k * 4 + c] / sum); }
                        } else { bone.push(Math.max(0, own), 0, 0, 0); weight.push(1, 0, 0, 0); }
                    }
                }
                const start = index.length;
                for (let k = 0; k + 2 < tri.length; k += 3) index.push(tri[k] + corners, tri[k + 1] + corners, tri[k + 2] + corners);
                if (!n) flatNormals(position, normal, index, start);
                const material = part.material != null ? (gltf.materials || [])[part.material] : null, pbr = (material && material.pbrMetallicRoughness) || {};
                const old = material && material.extensions && material.extensions.KHR_materials_pbrSpecularGlossiness || {};
                const texture = pbr.baseColorTexture || old.diffuseTexture, source = texture && (gltf.textures || [])[texture.index];
                parts.push({
                    start, count: index.length - start, picture: source && source.source != null ? source.source : -1,
                    colour: pbr.baseColorFactor || old.diffuseFactor || [1, 1, 1, 1], cut: material && material.alphaMode === 'MASK' ? (material.alphaCutoff == null ? 0.5 : material.alphaCutoff) : 0,
                });
                corners += count;
            }
        }
        if (!index.length) throw new Error('This model has no triangles');
        const pictures = (gltf.images || []).map(image => {
            if (image.bufferView != null) { const v = gltf.bufferViews[image.bufferView]; return { type: image.mimeType || 'image/png', bytes: bin.subarray(v.byteOffset || 0, (v.byteOffset || 0) + v.byteLength) }; }
            const data = /^data:([^;,]+)[^,]*;base64,(.*)$/.exec(image.uri || '');
            if (!data) return null;
            const raw = typeof atob === 'function' ? atob(data[2]) : Buffer.from(data[2], 'base64').toString('binary'), out = new Uint8Array(raw.length);
            for (let k = 0; k < raw.length; k++) out[k] = raw.charCodeAt(k);
            return { type: data[1], bytes: out };
        });
        const model = {
            corners, position: new Float32Array(position), normal: new Float32Array(normal), uv: new Float32Array(uv), index: new Uint32Array(index),
            bone: alive ? new Uint16Array(bone) : null, weight: alive ? new Float32Array(weight) : null, parts, pictures,
            bones: alive ? { nodes, order, joints, inverse } : null,
            moves: (gltf.animations || []).map((a, n) => {
                const channels = [];
                let seconds = 0;
                for (const c of a.channels || []) {
                    const s = a.samplers[c.sampler], path = c.target && c.target.path;
                    if (!s || c.target.node == null || !['translation', 'rotation', 'scale'].includes(path)) continue;
                    const times = numbers(gltf, bin, s.input), values = numbers(gltf, bin, s.output);
                    seconds = Math.max(seconds, times[times.length - 1] || 0);
                    channels.push({ node: c.target.node, path, times, values, mode: s.interpolation || 'LINEAR', width: path === 'rotation' ? 4 : 3 });
                }
                return { name: a.name || `move ${n + 1}`, seconds, channels };
            }),
        };
        measure(model);
        return model;
    }

    function flatNormals(position, normal, index, start) {
        for (let k = start; k + 2 < index.length; k += 3) {
            const a = index[k] * 3, b = index[k + 1] * 3, c = index[k + 2] * 3;
            const ux = position[b] - position[a], uy = position[b + 1] - position[a + 1], uz = position[b + 2] - position[a + 2];
            const vx = position[c] - position[a], vy = position[c + 1] - position[a + 1], vz = position[c + 2] - position[a + 2];
            const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
            for (const i of [a, b, c]) { normal[i] += nx; normal[i + 1] += ny; normal[i + 2] += nz; }
        }
    }

    /* every node's place in the model, from its own and its parents'; `local` (node -> {t, r, s}) replaces the resting ones */
    function worlds(nodes, order, local) {
        const out = new Array(nodes.length);
        for (const i of order) {
            const n = nodes[i], now = local && local[i];
            const own = n.matrix && !now ? n.matrix : fromTRS((now && now.t) || n.t, (now && now.r) || n.r, (now && now.s) || n.s);
            out[i] = n.parent >= 0 && out[n.parent] ? multiply(out[n.parent], own) : own;
        }
        return out;
    }

    /* the model's size as it rests: low and high corners of its box */
    function measure(model) {
        const low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity], p = model.position;
        const m = model.bones ? poseOf(model, -1, 0) : null;
        for (let i = 0; i < model.corners; i++) {
            let q = [p[i * 3], p[i * 3 + 1], p[i * 3 + 2]];
            if (m) {
                const sum = [0, 0, 0];
                for (let c = 0; c < 4; c++) {
                    const w = model.weight[i * 4 + c];
                    if (w > 0) { const r = moved(m.subarray(model.bone[i * 4 + c] * 16, model.bone[i * 4 + c] * 16 + 16), q); sum[0] += r[0] * w; sum[1] += r[1] * w; sum[2] += r[2] * w; }
                }
                q = sum;
            }
            for (let k = 0; k < 3; k++) { if (q[k] < low[k]) low[k] = q[k]; if (q[k] > high[k]) high[k] = q[k]; }
        }
        model.low = low; model.high = high;
        return model;
    }

    /* A model's own movement at a moment: the matrices of its bones (16 numbers each). `move` -1 is how it rests. */
    function poseOf(model, move, seconds) {
        const b = model.bones, clip = move >= 0 ? model.moves[move] : null;
        let local = null;
        if (clip && clip.seconds > 0) {
            const t = ((seconds % clip.seconds) + clip.seconds) % clip.seconds;
            local = {};
            for (const c of clip.channels) {
                const times = c.times, n = times.length, w = c.width, cubic = c.mode === 'CUBICSPLINE', at = k => { const o = cubic ? k * w * 3 + w : k * w; return Array.from(c.values.subarray(o, o + w)); };
                let k = 0;
                while (k < n - 1 && t >= times[k + 1]) k++;
                const next = Math.min(n - 1, k + 1), span = times[next] - times[k];
                const x = c.mode === 'STEP' || span <= 0 ? 0 : clamp((t - times[k]) / span, 0, 1);
                const a = at(k), z = at(next);
                const value = c.path === 'rotation' ? slerp(a, z, x) : a.map((v, j) => lerp(v, z[j], x));
                const node = local[c.node] = local[c.node] || {};
                node[c.path === 'translation' ? 't' : c.path === 'rotation' ? 'r' : 's'] = value;
            }
        }
        const world = worlds(b.nodes, b.order, local), out = new Float32Array(b.joints.length * 16);
        b.joints.forEach((node, j) => { if (world[node]) multiply(world[node], b.inverse[j], out.subarray(j * 16, j * 16 + 16)); else out.set(identity(), j * 16); });
        return out;
    }

    /* ---- plain shapes ------------------------------------------------------------------------------------ */

    /* A plain shape as a model: one unit tall or less, standing on y = 0, round its own upright. Each has a
     * map for a picture to be wrapped on it (round the upright, and down its side). */
    const SHAPES = ['cube', 'ball', 'half', 'cone', 'pyramid', 'cylinder', 'tube', 'capsule', 'ring'];

    function shape(kind) {
        const position = [], normal = [], uv = [], index = [];
        // a line of points turned round the upright: [radius, height, the normal's part outwards, its part upwards]
        const turnedRound = (line, around, flat) => {
            const start = position.length / 3, rows = line.length;
            let along = 0;
            for (let i = 0; i < rows; i++) {
                if (i) along += Math.hypot(line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1]);
                for (let k = 0; k <= around; k++) {
                    const a = k / around * 2 * Math.PI, c = Math.cos(a), s = Math.sin(a), n = Math.hypot(line[i][2], line[i][3]) || 1;
                    position.push(line[i][0] * s, line[i][1], line[i][0] * c);
                    normal.push(line[i][2] / n * s, line[i][3] / n, line[i][2] / n * c);
                    uv.push(k / around * (flat ? 1 : 2), along);
                }
            }
            for (let i = 0; i < rows - 1; i++) for (let k = 0; k < around; k++) {
                const a = start + i * (around + 1) + k, b = a + 1, c = a + around + 1, d = c + 1;
                index.push(a, c, b, b, c, d);
            }
        };
        const arc = (cx, cy, r, from, to, steps) => Array.from({ length: steps + 1 }, (_, i) => { const a = (from + (to - from) * i / steps) * RAD; return [cx + r * Math.sin(a), cy + r * Math.cos(a), Math.sin(a), Math.cos(a)]; });
        if (kind === 'cube') {
            for (const [n, u, v] of [[[0, 0, 1], [1, 0, 0], [0, 1, 0]], [[0, 0, -1], [-1, 0, 0], [0, 1, 0]], [[1, 0, 0], [0, 0, -1], [0, 1, 0]], [[-1, 0, 0], [0, 0, 1], [0, 1, 0]], [[0, 1, 0], [1, 0, 0], [0, 0, -1]], [[0, -1, 0], [1, 0, 0], [0, 0, 1]]]) {
                const start = position.length / 3;
                for (const [a, b] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
                    position.push((n[0] + u[0] * a + v[0] * b) / 2, (n[1] + u[1] * a + v[1] * b) / 2 + 0.5, (n[2] + u[2] * a + v[2] * b) / 2);
                    normal.push(n[0], n[1], n[2]); uv.push((a + 1) / 2, (1 - b) / 2);
                }
                index.push(start, start + 1, start + 2, start, start + 2, start + 3);
            }
        } else if (kind === 'ball') turnedRound(arc(0, 0.5, 0.5, 0, 180, 18), 32);
        else if (kind === 'half') { turnedRound(arc(0, 0, 0.5, 0, 90, 10), 32); turnedRound([[0.5, 0, 0, -1], [0, 0, 0, -1]], 32, true); }
        else if (kind === 'cone' || kind === 'pyramid') {
            const around = kind === 'cone' ? 32 : 4;
            turnedRound([[0, 1, 1, 0.5], [0.5, 0, 1, 0.5]], around); turnedRound([[0.5, 0, 0, -1], [0, 0, 0, -1]], around, true);
        } else if (kind === 'cylinder') { turnedRound([[0, 1, 0, 1], [0.5, 1, 0, 1]], 32, true); turnedRound([[0.5, 1, 1, 0], [0.5, 0, 1, 0]], 32); turnedRound([[0.5, 0, 0, -1], [0, 0, 0, -1]], 32, true); }
        else if (kind === 'tube') {
            turnedRound([[0.32, 1, 0, 1], [0.5, 1, 0, 1]], 32, true); turnedRound([[0.5, 1, 1, 0], [0.5, 0, 1, 0]], 32);
            turnedRound([[0.5, 0, 0, -1], [0.32, 0, 0, -1]], 32, true); turnedRound([[0.32, 0, -1, 0], [0.32, 1, -1, 0]], 32);
        } else if (kind === 'capsule') turnedRound(arc(0, 0.75, 0.25, 0, 90, 8).concat(arc(0, 0.25, 0.25, 90, 180, 8)), 28);
        else if (kind === 'ring') turnedRound(Array.from({ length: 17 }, (_, i) => { const a = i / 16 * 2 * Math.PI; return [0.35 + 0.15 * Math.sin(a), 0.15 + 0.15 * Math.cos(a), Math.sin(a), Math.cos(a)]; }), 40);
        else throw new Error('There is no such shape');
        const model = {
            corners: position.length / 3, position: new Float32Array(position), normal: new Float32Array(normal), uv: new Float32Array(uv), index: new Uint32Array(index),
            bone: null, weight: null, bones: null, moves: [], pictures: [], parts: [{ start: 0, count: index.length, picture: -1, colour: [1, 1, 1, 1], cut: 0 }],
        };
        if (kind === 'pyramid' || kind === 'cone') {
            // (a cone's side is made with slanted normals; a pyramid's four sides are flat: each triangle gets its own)
            if (kind === 'pyramid') return measure(faceted(model));
        }
        return measure(model);
    }

    /* the same model with every triangle flat: its corners are its own */
    function faceted(model) {
        const position = [], normal = [], uv = [];
        for (let k = 0; k < model.index.length; k += 3) {
            const p = [0, 1, 2].map(c => model.index[k + c]), at = p.map(i => [model.position[i * 3], model.position[i * 3 + 1], model.position[i * 3 + 2]]);
            const u = at[1].map((v, j) => v - at[0][j]), w = at[2].map((v, j) => v - at[0][j]);
            const face = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]], length = Math.hypot(face[0], face[1], face[2]);
            if (length < 1e-10) continue;           // a triangle of no size (at a point where a round of corners meets)
            for (let c = 0; c < 3; c++) { position.push(...at[c]); normal.push(...face.map(v => v / length)); uv.push(model.uv[p[c] * 2], model.uv[p[c] * 2 + 1]); }
        }
        const n = position.length / 3;
        return Object.assign({}, model, { corners: n, position: new Float32Array(position), normal: new Float32Array(normal), uv: new Float32Array(uv), index: Uint32Array.from({ length: n }, (_, i) => i),
            parts: [Object.assign({}, model.parts[0], { start: 0, count: n })] });
    }

    /* ---- giving a model bones ---------------------------------------------------------------------------- */

    /* What kinds of body there are, and what each can do. */
    const KINDS = {
        biped: ['walk', 'run', 'idle', 'wave', 'talk', 'dance'],
        wings: ['fly', 'glide'],
        four: ['walk', 'run', 'idle'],
        fish: ['swim'],
    };

    /* The corners of a model that share a place are one "site": a model's skin is cut into pieces where its
     * picture is cut, and bones must not follow those cuts. Sites know their neighbours along the surface. */
    function surface(model) {
        if (model._surface) return model._surface;
        const p = model.position, n = model.corners, size = Math.max(model.high[0] - model.low[0], model.high[1] - model.low[1], model.high[2] - model.low[2]) || 1;
        const grid = 4096 / size, seen = new Map(), site = new Uint32Array(n), at = [];
        for (let i = 0; i < n; i++) {
            const key = `${Math.round((p[i * 3] - model.low[0]) * grid)},${Math.round((p[i * 3 + 1] - model.low[1]) * grid)},${Math.round((p[i * 3 + 2] - model.low[2]) * grid)}`;
            let s = seen.get(key);
            if (s === undefined) { s = at.length / 3; seen.set(key, s); at.push(p[i * 3], p[i * 3 + 1], p[i * 3 + 2]); }
            site[i] = s;
        }
        const count = at.length / 3, pairs = new Set(), index = model.index;
        for (let k = 0; k + 2 < index.length; k += 3) {
            const a = site[index[k]], b = site[index[k + 1]], c = site[index[k + 2]];
            for (const [u, v] of [[a, b], [b, c], [c, a]]) if (u !== v) pairs.add(u < v ? u * count + v : v * count + u);
        }
        const degree = new Uint32Array(count + 1);
        for (const pair of pairs) { degree[Math.floor(pair / count) + 1]++; degree[pair % count + 1]++; }
        for (let i = 0; i < count; i++) degree[i + 1] += degree[i];
        const next = new Uint32Array(degree[count]), fill = degree.slice(0, count);
        for (const pair of pairs) { const u = Math.floor(pair / count), v = pair % count; next[fill[u]++] = v; next[fill[v]++] = u; }
        model._surface = { count, at: new Float32Array(at), site, first: degree, next };
        return model._surface;
    }

    /* A model seen from its front as a sheet of squares: which are filled. x runs over the model's width,
     * y from its soles (0) to its top. */
    function outline(model, columns, rows) {
        const filled = new Uint8Array(columns * rows), p = model.position, index = model.index;
        const w = (model.high[0] - model.low[0]) || 1, h = (model.high[1] - model.low[1]) || 1;
        const cx = i => (p[i * 3] - model.low[0]) / w * columns, cy = i => (p[i * 3 + 1] - model.low[1]) / h * rows;
        // every triangle fills the squares it covers (its corners alone leave holes where the surface is flat)
        for (let k = 0; k + 2 < index.length; k += 3) {
            const ax = cx(index[k]), ay = cy(index[k]), bx = cx(index[k + 1]), by = cy(index[k + 1]), ex = cx(index[k + 2]), ey = cy(index[k + 2]);
            const n = Math.min(24, Math.ceil(Math.max(Math.abs(bx - ax), Math.abs(by - ay), Math.abs(ex - ax), Math.abs(ey - ay), Math.abs(ex - bx), Math.abs(ey - by)) * 1.5) + 1);
            for (let i = 0; i <= n; i++) for (let j = 0; i + j <= n; j++) {
                const u = i / n, v = j / n, x = Math.min(columns - 1, Math.floor(ax + (bx - ax) * u + (ex - ax) * v)), y = Math.min(rows - 1, Math.floor(ay + (by - ay) * u + (ey - ay) * v));
                filled[y * columns + x] = 1;
            }
        }
        // the stretches of filled squares in every row, small gaps closed
        const runs = [];
        for (let y = 0; y < rows; y++) {
            const row = [];
            let start = -1, gap = 0;
            for (let x = 0; x <= columns; x++) {
                const on = x < columns && filled[y * columns + x];
                if (on) { if (start < 0) start = x; gap = 0; } else if (start >= 0 && (++gap > 1 || x === columns)) { row.push([start, x - gap]); start = -1; gap = 0; }
            }
            runs.push(row);
        }
        return { columns, rows, filled, runs, x: c => model.low[0] + (c + 0.5) / columns * w, y: r => model.low[1] + (r + 0.5) / rows * h };
    }

    /* the middle of the model's surface near a point of its front view, in depth: a joint belongs inside the limb */
    function inside(model, x, y, reach) {
        const s = surface(model);
        let sum = 0, n = 0;
        for (let i = 0; i < s.count; i++) if (Math.abs(s.at[i * 3] - x) < reach && Math.abs(s.at[i * 3 + 1] - y) < reach) { sum += s.at[i * 3 + 2]; n++; }
        return n ? sum / n : (model.low[2] + model.high[2]) / 2;
    }

    /* Where the joints of a model are, guessed from its shape. `kind`: biped, wings, four, fish.
     * Returns {kind, front, joints: {name: [x, y, z]}} in the model's own space. */
    function guess(model, kind) {
        const low = model.low, high = model.high, H = (high[1] - low[1]) || 1, W = (high[0] - low[0]) || 1, D = (high[2] - low[2]) || 1;
        const Y = u => low[1] + u * H, joints = {};
        const put = (name, x, y) => { joints[name] = [x, y, inside(model, x, y, 0.04 * H)]; };
        if (kind === 'wings') {
            // the body is where the model is thick from top to bottom; the wings are what reaches out to the sides
            const o = outline(model, 64, 48), tall = new Array(64).fill(0);
            for (let y = 0; y < 48; y++) for (let x = 0; x < 64; x++) if (o.filled[y * 64 + x]) tall[x]++;
            const most = Math.max(...tall), mid = tall.indexOf(most);
            let left = mid, right = mid;
            while (left > 0 && tall[left - 1] > most * 0.55) left--;
            while (right < 63 && tall[right + 1] > most * 0.55) right++;
            const cx = o.x((left + right) / 2), half = Math.max(0.04 * W, (o.x(right) - o.x(left)) / 2), cy = (low[1] + high[1]) / 2;
            const centre = [cx, cy, (low[2] + high[2]) / 2];
            joints.body = centre;
            for (const [side, sign] of [['l', 1], ['r', -1]]) {
                const tip = sign > 0 ? high[0] : low[0], root = cx + sign * half;
                joints['wing_' + side] = [root, cy, centre[2]];
                joints['wing2_' + side] = [root + (tip - root) * 0.5, cy, centre[2]];
                joints['tip_' + side] = [tip, cy, centre[2]];
            }
            return { kind, front: 1, joints };
        }
        if (kind === 'fish') {
            // a spine along the model's longest level side, the head at its thicker end
            const along = W >= D ? 0 : 2, s = surface(model), length = along ? D : W, bins = new Array(12).fill(0);
            for (let i = 0; i < s.count; i++) bins[Math.min(11, Math.floor((s.at[i * 3 + along] - low[along]) / length * 12))]++;
            const head = bins.slice(0, 6).reduce((a, b) => a + b) >= bins.slice(6).reduce((a, b) => a + b) ? 0 : 1;
            for (let k = 0; k <= 5; k++) {
                const share = head ? 1 - k / 5 : k / 5, at = [(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, (low[2] + high[2]) / 2];
                at[along] = low[along] + share * length;
                joints['spine' + k] = at;
            }
            return { kind, front: along ? (head ? 1 : -1) : 1, along, joints };
        }
        if (kind === 'four') {
            // four legs: the lower part of the model falls into four groups, by which corner of its footprint each is nearest
            const s = surface(model), along = W >= D ? 0 : 2, across = along ? 0 : 2;
            const mid = [(low[0] + high[0]) / 2, 0, (low[2] + high[2]) / 2], groups = [[], [], [], []];
            for (let i = 0; i < s.count; i++) {
                if (s.at[i * 3 + 1] > Y(0.3)) continue;
                groups[(s.at[i * 3 + along] > mid[along] ? 1 : 0) + (s.at[i * 3 + across] > mid[across] ? 2 : 0)].push(i);
            }
            const belly = Y(0.45);
            joints.body = [mid[0], Y(0.62), mid[2]];
            groups.forEach((group, g) => {
                const c = [0, 0, 0];
                for (const i of group) { c[0] += s.at[i * 3]; c[2] += s.at[i * 3 + 2]; }
                const n = group.length || 1, name = (g & 1 ? 'a' : 'b') + (g & 2 ? '_l' : '_r');
                const x = group.length ? c[0] / n : mid[0] + ((along === 0 ? (g & 1) : (g & 2) / 2) - 0.5) * W * 0.6, z = group.length ? c[2] / n : mid[2] + ((along === 2 ? (g & 1) : (g & 2) / 2) - 0.5) * D * 0.6;
                joints['leg_' + name] = [x, belly, z];
                joints['knee_' + name] = [x, Y(0.22), z];
                joints['foot_' + name] = [x, Y(0.0), z];
            });
            return { kind, front: 1, along, joints };
        }
        // a figure on two legs, seen from its front
        const o = outline(model, 96, 128), row = u => clamp(Math.round(u * 128 - 0.5), 0, 127), wide = r => r[1] - r[0] + 1;
        const s = surface(model);
        // its middle line: the middle of the trunk, not of the box (an arm held out does not move it)
        const trunk = [];
        for (let i = 0; i < s.count; i++) { const u = (s.at[i * 3 + 1] - low[1]) / H; if (u > 0.5 && u < 0.72) trunk.push(s.at[i * 3]); }
        trunk.sort((a, b) => a - b);
        let x0 = trunk.length ? (trunk[Math.floor(trunk.length * 0.1)] + trunk[Math.floor(trunk.length * 0.9)]) / 2 : (low[0] + high[0]) / 2;
        const column = x => clamp((x - low[0]) / W * 96 - 0.5, 0, 95);
        const two = y => {
            // the two legs in a row: the two widest stretches, one on each side of the middle
            // (of a like width: a trunk with an arm hanging beside it is not a pair of legs)
            const big = o.runs[y].filter(r => wide(r) >= 3).sort((a, b) => wide(b) - wide(a)).slice(0, 2).sort((a, b) => a[0] - b[0]);
            return big.length === 2 && wide(big[0]) < wide(big[1]) * 2.4 && wide(big[1]) < wide(big[0]) * 2.4
                && (big[0][0] + big[0][1]) / 2 < column(x0) && (big[1][0] + big[1][1]) / 2 > column(x0) ? big : null;
        };
        // the legs: from the ground up, as long as there are two of them
        let top = -1, misses = 0, first = -1;
        for (let y = row(0.06); y <= row(0.62); y++) {
            if (two(y)) { top = y; misses = 0; if (first < 0) first = y; } else if (first >= 0 && ++misses > 3) break;
        }
        const legs = first >= 0 && (top - first) / 128 > 0.12;
        const crotch = legs ? (top + 1) / 128 : 0.47;
        const middle = (y, side) => { const pair = two(y); return pair ? o.x((pair[side][0] + pair[side][1]) / 2) : null; };
        if (legs) {             // between the legs is the surest middle there is (an arm on one side pulls the trunk's own)
            const y = Math.max(first, top - 4), between = (middle(y, 0) + middle(y, 1)) / 2;
            if (Math.abs(between - x0) < 0.08 * H) x0 = between;
        }
        // the neck: where the middle stretch is narrowest, under the head
        const central = y => o.runs[y].reduce((best, r) => { const far = Math.max(0, r[0] - column(x0), column(x0) - r[1]); return !best || far < best.far ? { far, r } : best; }, null);
        // The top of its head: the highest row its middle line is in. A hand held above the head makes the
        // model taller, not the body; every share of "its height" below is a share of the body (B of the model).
        let crown = 127;
        while (crown > 64) { const c = central(crown); if (c && c.far === 0) break; crown--; }
        const B = (crown + 1) / 128;
        let neck = 0.86 * B, narrow = Infinity;
        for (let y = row(0.7 * B); y <= row(0.93 * B); y++) { const c = central(y); if (c && c.far === 0 && wide(c.r) < narrow) { narrow = wide(c.r); neck = (y + 0.5) / 128; } }
        const headWide = Math.max(...[0.94, 0.96].map(u => { const c = central(row(u * B)); return c ? wide(c.r) : 0; }));
        if (!(narrow < headWide * 0.92) && !(narrow < 96 * 0.2 * H * B / W)) neck = 0.86 * B;          // no neck to be seen (a hood, a helmet)
        // the trunk's half width: at the waist, where arms are most likely to hang clear of it
        const widths = [];
        for (let y = row(crotch + 0.07); y <= row(crotch + 0.24); y++) { const c = central(y); if (c && c.far === 0) widths.push(wide(c.r) / 96 * W / 2); }
        widths.sort((a, b) => a - b);
        const half = clamp(widths.length ? widths[Math.floor(widths.length * 0.25)] : 0.12 * H * B, 0.05 * H * B, 0.17 * H * B);
        const shoulder = neck - 0.045 * B;
        put('hips', x0, Y(crotch + 0.04));
        put('chest', x0, Y(crotch + 0.04 + (shoulder - crotch - 0.04) * 0.5));
        put('neck', x0, Y(neck));
        joints.head = [x0, Y(B), joints.neck[2]];
        for (const [side, sign, i] of [['l', 1, 1], ['r', -1, 0]]) {
            const hipX = legs ? middle(Math.max(first, top - 4), i) : x0 + sign * half * 0.5, footX = legs ? middle(first, i) : x0 + sign * half * 0.5;
            const kneeU = crotch * 0.54, kneeX = legs && middle(row(kneeU), i) != null ? middle(row(kneeU), i) : (hipX + footX) / 2;
            put('hip_' + side, hipX, Y(crotch + 0.02));
            put('knee_' + side, kneeX, Y(kneeU));
            put('foot_' + side, footX, Y(0.03));
            // The arm: what stands out to this side of the trunk and hangs on it at the shoulder. Its hand is
            // the square farthest from the shoulder, going only through the arm's own squares (an arm that is
            // raised, held out or hanging is found the same way; the outside of a hip is not an arm).
            const from = [x0 + sign * half * 0.92, Y(shoulder)], reach = half * 1.1, C = 96, R = 128, dx = W / C, dy = H / R;
            const out = (x, y) => y >= row(crotch - 0.1) && o.filled[y * C + x] && (o.x(x) - x0) * sign > reach;
            const dist = new Float32Array(C * R).fill(Infinity), came = new Int32Array(C * R).fill(-1), queue = [];
            for (let y = row(neck - 0.22 * B); y <= row(neck + 0.03 * B); y++) for (let x = 0; x < C; x++) {
                if (!out(x, y)) continue;
                const inner = x - sign * 2, inner1 = x - sign;           // it touches the trunk here
                if ([inner, inner1].some(i => i >= 0 && i < C && o.filled[y * C + i] && (o.x(i) - x0) * sign <= reach)) { dist[y * C + x] = 0; queue.push(y * C + x); }
            }
            let hand = -1, far = 0;
            for (let q = 0; q < queue.length; q++) {
                // (the squares are not taken strictly nearest first; for a limb a few squares wide that is near enough)
                const cell = queue[q], cx = cell % C, cy = (cell - cx) / C;
                if (dist[cell] > far) { far = dist[cell]; hand = cell; }
                for (let oy = -1; oy <= 1; oy++) for (let ox = -1; ox <= 1; ox++) {
                    const nx = cx + ox, ny = cy + oy;
                    if ((!ox && !oy) || nx < 0 || nx >= C || ny < 0 || ny >= R || !out(nx, ny)) continue;
                    const d = dist[cell] + Math.hypot(ox * dx, oy * dy);
                    if (d < dist[ny * C + nx] - 1e-6) { dist[ny * C + nx] = d; came[ny * C + nx] = cell; queue.push(ny * C + nx); }
                }
            }
            if (hand >= 0 && far > 0.14 * H * B) {
                let cell = hand, start = hand;
                const way = [];
                while (cell >= 0) { way.push(cell); start = cell; cell = came[cell]; }
                const at = c => [o.x(c % C), o.y((c - c % C) / C)], middleOf = (c, r) => {
                    // the middle of the limb around a square of its way
                    const p = at(c);
                    let sx = 0, sy = 0, n = 0;
                    for (let y = 0; y < R; y++) for (let x = 0; x < C; x++) if (dist[y * C + x] < Infinity && Math.hypot(o.x(x) - p[0], o.y(y) - p[1]) < r) { sx += o.x(x); sy += o.y(y); n++; }
                    return n ? [sx / n, sy / n] : p;
                };
                // the shoulder: where the arm touches the trunk nearest to where a shoulder is (an arm that hangs
                // along the trunk touches it all the way down)
                let sh = at(start), nearest = Infinity;
                for (const cell of queue) {
                    if (dist[cell] !== 0) continue;
                    const q = at(cell), d = Math.hypot(q[0] - from[0], q[1] - from[1]);
                    if (d < nearest) { nearest = d; sh = q; }
                }
                const ha = middleOf(way[Math.floor(way.length * 0.12)], 0.04 * H), first = at(start);
                let half_way = way[way.length >> 1];
                if (Math.hypot(first[0] - sh[0], first[1] - sh[1]) > 0.06 * H) {
                    // the way did not begin at the shoulder: the elbow is the arm's square nearest to half way between shoulder and hand
                    const mid = [(sh[0] + ha[0]) / 2, (sh[1] + ha[1]) / 2];
                    let best = Infinity;
                    for (const cell of queue) { const q = at(cell), d = Math.hypot(q[0] - mid[0], q[1] - mid[1]); if (d < best) { best = d; half_way = cell; } }
                }
                const el = middleOf(half_way, 0.05 * H);
                put('arm_' + side, sh[0] - sign * half * 0.15, sh[1]);
                put('elbow_' + side, el[0], el[1]);
                put('hand_' + side, ha[0], ha[1]);
            } else {
                // no arm to be seen on this side: it lies along the trunk
                put('arm_' + side, from[0], from[1]);
                put('elbow_' + side, x0 + sign * half * 1.02, Y(shoulder - 0.19 * B));
                put('hand_' + side, x0 + sign * half * 1.05, Y(shoulder - 0.37 * B));
            }
        }
        // which way it faces: the feet reach forward of the ankles
        let toes = 0, ankles = 0, nt = 0, na = 0;
        for (let k = 0; k < s.count; k++) {
            const u = (s.at[k * 3 + 1] - low[1]) / H;
            if (u < 0.035) { toes += s.at[k * 3 + 2]; nt++; } else if (u > 0.08 && u < 0.16) { ankles += s.at[k * 3 + 2]; na++; }
        }
        const front = nt && na && Math.abs(toes / nt - ankles / na) > 0.01 * H ? Math.sign(toes / nt - ankles / na) : 1;
        return { kind: 'biped', front, legs, joints };
    }

    /* The bones of each kind of body: [joint it turns about, its parent, where the bone ends, how thick it is
     * (in the model's heights)]. */
    const BONES = {
        biped: [
            ['hips', null, 'chest', 0.13], ['chest', 'hips', 'neck', 0.13], ['neck', 'chest', 'head', 0.09],
            ['arm_l', 'chest', 'elbow_l', 0.045], ['elbow_l', 'arm_l', 'hand_l', 0.04], ['arm_r', 'chest', 'elbow_r', 0.045], ['elbow_r', 'arm_r', 'hand_r', 0.04],
            ['hip_l', 'hips', 'knee_l', 0.07], ['knee_l', 'hip_l', 'foot_l', 0.055], ['hip_r', 'hips', 'knee_r', 0.07], ['knee_r', 'hip_r', 'foot_r', 0.055],
        ],
        wings: [['body', null, null, 0.3], ['wing_l', 'body', 'wing2_l', 0.1], ['wing2_l', 'wing_l', 'tip_l', 0.1], ['wing_r', 'body', 'wing2_r', 0.1], ['wing2_r', 'wing_r', 'tip_r', 0.1]],
        four: [['body', null, null, 0.3]].concat(['a_l', 'a_r', 'b_l', 'b_r'].flatMap(n => [['leg_' + n, 'body', 'knee_' + n, 0.09], ['knee_' + n, 'leg_' + n, 'foot_' + n, 0.07]])),
        fish: [0, 1, 2, 3, 4].map(k => ['spine' + k, k ? 'spine' + (k - 1) : null, 'spine' + (k + 1), 0.3]),
    };

    /* Ties every corner of a model to bones. `shape` is what guess() returned (its joints may have been moved
     * by hand). The model gets `rig`, and each corner its four bones and how much each one pulls. */
    function bind(model, shape) {
        const s = surface(model), list = BONES[shape.kind], H = (model.high[1] - model.low[1]) || 1, J = shape.joints;
        const names = list.map(b => b[0]), parent = list.map(b => (b[1] == null ? -1 : names.indexOf(b[1])));
        const from = list.map(b => J[b[0]]), to = list.map((b, i) => (b[2] ? J[b[2]] : from[i])), thick = list.map(b => b[3] * H);
        const count = s.count, bones = list.length;
        // how far every site is from every bone, in that bone's own thicknesses
        const best = new Int16Array(count).fill(-1), claim = new Float32Array(count).fill(Infinity), second = new Float32Array(count).fill(Infinity), runner = new Int16Array(count).fill(-1);
        for (let b = 0; b < bones; b++) {
            const a = from[b], d = [to[b][0] - a[0], to[b][1] - a[1], to[b][2] - a[2]], length2 = d[0] * d[0] + d[1] * d[1] + d[2] * d[2];
            for (let i = 0; i < count; i++) {
                const px = s.at[i * 3] - a[0], py = s.at[i * 3 + 1] - a[1], pz = s.at[i * 3 + 2] - a[2];
                const t = length2 > 0 ? clamp((px * d[0] + py * d[1] + pz * d[2]) / length2, 0, 1) : 0;
                const far = Math.hypot(px - d[0] * t, py - d[1] * t, pz - d[2] * t) / thick[b];
                if (far < claim[i]) { second[i] = claim[i]; runner[i] = best[i]; claim[i] = far; best[i] = b; } else if (far < second[i]) { second[i] = far; runner[i] = b; }
            }
        }
        // A site is sure of its bone when no bone of another limb is nearly as near. From the sure ones the
        // bones spread along the surface: a hand that hangs beside a thigh is far from it along the skin.
        const related = (a, b) => a === b || parent[a] === b || parent[b] === a;
        const label = new Int16Array(count).fill(-1), cost = new Float32Array(count).fill(Infinity), heap = [];
        const push = (c, i) => { heap.push([c, i]); let k = heap.length - 1; while (k > 0) { const up = (k - 1) >> 1; if (heap[up][0] <= heap[k][0]) break; [heap[up], heap[k]] = [heap[k], heap[up]]; k = up; } };
        const pop = () => {
            const first = heap[0], last = heap.pop();
            if (heap.length) {
                heap[0] = last;
                let k = 0;
                for (;;) { const l = k * 2 + 1, r = l + 1; let m = k; if (l < heap.length && heap[l][0] < heap[m][0]) m = l; if (r < heap.length && heap[r][0] < heap[m][0]) m = r; if (m === k) break; [heap[m], heap[k]] = [heap[k], heap[m]]; k = m; }
            }
            return first;
        };
        for (let i = 0; i < count; i++) {
            if (claim[i] < 1.6 && (runner[i] < 0 || related(best[i], runner[i]) || second[i] > claim[i] * 1.7)) { label[i] = best[i]; cost[i] = 0; push(0, i); }
        }
        while (heap.length) {
            const [c, i] = pop();
            if (c > cost[i]) continue;
            for (let e = s.first[i]; e < s.first[i + 1]; e++) {
                const j = s.next[e], step = Math.hypot(s.at[i * 3] - s.at[j * 3], s.at[i * 3 + 1] - s.at[j * 3 + 1], s.at[i * 3 + 2] - s.at[j * 3 + 2]);
                if (c + step < cost[j]) { cost[j] = c + step; label[j] = label[i]; push(c + step, j); }
            }
        }
        for (let i = 0; i < count; i++) if (label[i] < 0) label[i] = best[i];               // a loose piece nothing reached
        // How free each arm is: an arm that hangs against the body is often one skin with it, and lifting it
        // would pull a web of skin after it. Counted: where arm and trunk touch away from the shoulder.
        const free = { l: 1, r: 1 }, up = { l: false, r: false };
        if (shape.kind === 'biped') {
            const trunk = new Set(['hips', 'chest', 'neck', 'hip_l', 'hip_r', 'knee_l', 'knee_r'].map(n => names.indexOf(n)));
            for (const side of ['l', 'r']) {
                const upper = names.indexOf('arm_' + side), lower = names.indexOf('elbow_' + side), sh = J['arm_' + side], hand = J['hand_' + side];
                const long = Math.hypot(hand[0] - sh[0], hand[1] - sh[1], hand[2] - sh[2]) + Math.hypot(J['elbow_' + side][0] - sh[0], J['elbow_' + side][1] - sh[1], J['elbow_' + side][2] - sh[2]);
                let touching = 0;
                for (let i = 0; i < count; i++) {
                    if (label[i] !== upper && label[i] !== lower) continue;
                    if (Math.hypot(s.at[i * 3] - sh[0], s.at[i * 3 + 1] - sh[1], s.at[i * 3 + 2] - sh[2]) < long * 0.3) continue;
                    for (let e = s.first[i]; e < s.first[i + 1]; e++) if (trunk.has(label[s.next[e]])) { touching++; break; }
                }
                free[side] = clamp(1 - touching / 30, 0, 1);
                up[side] = hand[1] > sh[1] + 0.04 * H;
            }
        }
        // the pull of each bone, softened along the surface so that joints bend and do not crack
        let pull = new Float32Array(count * bones), other = new Float32Array(count * bones);
        for (let i = 0; i < count; i++) pull[i * bones + label[i]] = 1;
        for (let pass = 0; pass < 36; pass++) {
            for (let i = 0; i < count; i++) {
                const n = s.first[i + 1] - s.first[i], o = i * bones;
                if (!n) { for (let b = 0; b < bones; b++) other[o + b] = pull[o + b]; continue; }
                for (let b = 0; b < bones; b++) other[o + b] = pull[o + b] * 0.5;
                for (let e = s.first[i]; e < s.first[i + 1]; e++) { const q = s.next[e] * bones; for (let b = 0; b < bones; b++) other[o + b] += pull[q + b] * 0.5 / n; }
            }
            [pull, other] = [other, pull];
        }
        // every corner keeps its four strongest bones
        const bone = new Uint16Array(model.corners * 4), weight = new Float32Array(model.corners * 4), order = new Array(bones);
        for (let v = 0; v < model.corners; v++) {
            const o = s.site[v] * bones;
            for (let b = 0; b < bones; b++) order[b] = b;
            order.sort((a, b) => pull[o + b] - pull[o + a]);
            let sum = 0;
            for (let c = 0; c < 4; c++) sum += c < bones ? pull[o + order[c]] : 0;
            for (let c = 0; c < 4; c++) { bone[v * 4 + c] = c < bones ? order[c] : 0; weight[v * 4 + c] = c < bones && sum > 0 ? pull[o + order[c]] / sum : (c === 0 ? 1 : 0); }
        }
        model.bone = bone; model.weight = weight;
        model.rig = { kind: shape.kind, front: shape.front || 1, legs: shape.legs !== false, along: shape.along || 0, names, parent, at: from, ends: to, height: H, joints: J, free, up };
        return model.rig;
    }

    /* ---- movements, as formulas -------------------------------------------------------------------------- */

    const wave = (t, every, shift) => Math.sin((t / every + (shift || 0)) * 2 * Math.PI);

    /* How a body is held at a moment of a movement: {joint: [nod, turn, lean] in degrees, rise: metres per
     * metre of height}. `phase` is where in the stride (radians; a stride is two steps), `t` seconds, `f`
     * which way the figure faces (1 or -1 along z). A leg that swings forward is a negative nod when it faces +z. */
    function hold(rig, move, phase, t, f) {
        const p = {}, s = Math.sin(phase), c = Math.cos(phase), fwd = a => -a * f;
        if (rig.kind === 'biped') {
            const breath = wave(t, 3.7), look = wave(t, 9.1) * 0.6 + wave(t, 5.3, 0.3) * 0.4;
            // an arm that is one skin with the body moves only a little; one that is held up keeps its own pose
            const loose = rig.free || { l: 1, r: 1 }, held = rig.up || {}, L = smooth(loose.l), R = smooth(loose.r);
            if (move === 'walk' || move === 'run') {
                const run = move === 'run', swing = run ? 40 : 25, knee = run ? 88 : 50, arm = run ? 42 : 20;
                // a leg bends as it comes forward and is straight while it carries the weight
                const bend = x => knee * Math.pow(Math.max(0, Math.cos(x + 0.35)), 1.4);
                p.hip_l = [fwd(swing * s + (run ? 8 : 0)), 0, 0]; p.hip_r = [fwd(-swing * s + (run ? 8 : 0)), 0, 0];
                p.knee_l = [-fwd(bend(phase)), 0, 0]; p.knee_r = [-fwd(bend(phase + Math.PI)), 0, 0];
                const al = (held.l ? 0.3 : 1) * (0.3 + 0.7 * L), ar = (held.r ? 0.3 : 1) * (0.3 + 0.7 * R);
                p.arm_l = [fwd(-arm * s * al), 0, 0]; p.arm_r = [fwd(arm * s * ar), 0, 0];
                p.elbow_l = [fwd(((run ? 70 : 10) + (run ? 12 : 8) * Math.max(0, -s)) * (held.l ? 0 : L)), 0, 0]; p.elbow_r = [fwd(((run ? 70 : 10) + (run ? 12 : 8) * Math.max(0, s)) * (held.r ? 0 : R)), 0, 0];
                p.hips = [fwd(run ? 7 : 1.5), 5 * s * f, 1.6 * c]; p.chest = [0, -9 * s * f, -1.2 * c]; p.neck = [fwd(run ? -5 : -1), 4 * s * f, 0];
                p.rise = (Math.abs(c) - 1) * (run ? 0.03 : 0.016) + (run ? 0.02 * Math.abs(s) : 0);
            } else if (move === 'wave') {
                // the arm that is already up waves as it is; otherwise the freer arm is raised
                const side = held.l !== held.r ? (held.l ? 'l' : 'r') : (loose.l > loose.r ? 'l' : 'r'), out = side === 'l' ? 1 : -1, k = side === 'l' ? L : R;
                if (held[side]) { p['arm_' + side] = [0, 0, out * 7 * wave(t, 1.1)]; p['elbow_' + side] = [0, 0, out * 22 * wave(t, 0.55)]; }
                else { p['arm_' + side] = [0, 0, out * (118 + 6 * wave(t, 1.1)) * k]; p['elbow_' + side] = [0, 0, out * (30 + 24 * wave(t, 0.55)) * k]; }
                p.chest = [0.8 * breath, 0, -out * 2]; p.neck = [0, 5 * look, out * 2];
                p.rise = 0.002 * breath;
            } else if (move === 'talk') {
                const kl = held.l ? 0.15 : L, kr = held.r ? 0.15 : R;
                p.arm_l = [fwd((8 + 6 * wave(t, 2.9)) * kl), 0, 0]; p.elbow_l = [fwd((38 + 22 * wave(t, 2.3, 0.2)) * kl), 0, 0];
                p.arm_r = [fwd((6 + 5 * wave(t, 3.4, 0.5)) * kr), 0, 0]; p.elbow_r = [fwd((30 + 20 * wave(t, 2.7, 0.6)) * kr), 0, 0];
                p.chest = [0.8 * breath, 2 * wave(t, 4.3), 0]; p.neck = [fwd(3 * wave(t, 1.7)), 7 * look, 2 * wave(t, 6.1)];
                p.rise = 0.002 * breath;
            } else if (move === 'dance') {
                const beat = wave(t, 1), half = wave(t, 2), dip = 0.5 - 0.5 * Math.cos(t * 4 * Math.PI);
                p.hips = [0, 9 * half * f, 5 * half]; p.chest = [fwd(3 * dip), -12 * half * f, -6 * half]; p.neck = [fwd(4 * dip), 6 * half * f, 3 * half];
                p.hip_l = [fwd(14 * dip), 0, 3 * half]; p.hip_r = [fwd(14 * dip), 0, 3 * half]; p.knee_l = [-fwd(28 * dip), 0, 0]; p.knee_r = [-fwd(28 * dip), 0, 0];
                const kl = held.l ? 0.25 : L, kr = held.r ? 0.25 : R;
                p.arm_l = [fwd(30 * beat * kl), 0, (22 + 18 * half) * kl]; p.arm_r = [fwd(-30 * beat * kr), 0, -(22 - 18 * half) * kr];
                p.elbow_l = [fwd((55 + 25 * beat) * kl), 0, 0]; p.elbow_r = [fwd((55 - 25 * beat) * kr), 0, 0];
                p.rise = -0.035 * dip;
            } else {            // idle: it breathes, shifts its weight and looks about
                const shift = wave(t, 7.9);
                p.hips = [0, 0, 1.4 * shift]; p.chest = [0.9 * breath, 1.5 * look, -1.4 * shift]; p.neck = [fwd(1.2 * wave(t, 6.7)), 9 * look, 0];
                p.arm_l = [fwd(1.5 * breath), 0, 0.8 * breath * L]; p.arm_r = [fwd(1.5 * breath), 0, -0.8 * breath * R];
                p.rise = 0.0025 * breath;
            }
            if (!rig.legs) { delete p.hip_l; delete p.hip_r; delete p.knee_l; delete p.knee_r; }       // a long coat or a dress: nothing to swing
        } else if (rig.kind === 'wings') {
            const beat = move === 'glide' ? 0.25 : 1, a = Math.sin(phase), b = Math.sin(phase - 0.9);
            p.wing_l = [0, 0, 34 * a * beat + 6]; p.wing2_l = [0, 0, 26 * b * beat];
            p.wing_r = [0, 0, -(34 * a * beat + 6)]; p.wing2_r = [0, 0, -26 * b * beat];
            p.body = [3 * Math.cos(phase) * beat, 0, 0];
            p.rise = -0.05 * a * beat + 0.01 * wave(t, 4.1);
        } else if (rig.kind === 'four') {
            if (move === 'idle' || move === 'sit' || move === 'lie') { p.body = [0.6 * wave(t, 3.9), 0, 0]; p.rise = 0.003 * wave(t, 3.9); }       // (sitting and lying are the real steps' to show)
            else {
                const run = move === 'run', swing = run ? 34 : 22, axis = rig.along ? 0 : 2;
                // a trot: the legs of a diagonal go together (a gallop: front pair, then hind pair)
                const leg = (name, x) => {
                    const turn = swing * Math.sin(x), bend = (run ? 45 : 28) * Math.pow(Math.max(0, Math.cos(x + 0.35)), 1.4);
                    p['leg_' + name] = axis ? [0, 0, turn] : [fwd(turn), 0, 0];
                    p['knee_' + name] = axis ? [0, 0, -bend] : [-fwd(bend), 0, 0];
                };
                leg('a_l', phase); leg('b_r', phase + (run ? 0.6 : 0)); leg('a_r', phase + Math.PI); leg('b_l', phase + Math.PI + (run ? 0.6 : 0));
                p.body = axis ? [0, 0, (run ? 4 : 1.5) * Math.sin(phase * 2)] : [(run ? 4 : 1.5) * Math.sin(phase * 2), 0, 0];
                p.rise = (Math.abs(c) - 1) * (run ? 0.03 : 0.012);
            }
        } else if (rig.kind === 'fish') {
            for (let k = 0; k < 5; k++) p['spine' + k] = [0, (4 + 5 * k) * Math.sin(phase - k * 0.9) * (k ? 1 : 0.4), 0];
            p.rise = 0.004 * wave(t, 3.3);
        }
        return p;
    }

    /* The matrices of a model's bones for a hold (what hold() or a clip gave), `strength` of the way from rest.
     * A joint is held as degrees [nod, turn, lean], or as a quaternion [x, y, z, w] (a clip's). */
    function pose(rig, held, strength) {
        const n = rig.names.length, out = new Float32Array(n * 16), k = strength == null ? 1 : strength;
        for (let j = 0; j < n; j++) {
            const a = held[rig.names[j]] || [0, 0, 0];
            const own = a.length === 4 ? aboutQ(rig.at[j], k >= 1 ? a : slerp([0, 0, 0, 1], a, k)) : about(rig.at[j], a[0] * k, a[1] * k, a[2] * k);
            if (rig.parent[j] < 0) { own[13] += (held.rise || 0) * k * rig.height; out.set(own, j * 16); }
            else multiply(out.subarray(rig.parent[j] * 16, rig.parent[j] * 16 + 16), own, out.subarray(j * 16, j * 16 + 16));
        }
        return out;
    }

    /* where the corners of a model are in a pose (for tests and for measuring): Float32Array of x, y, z */
    function posed(model, matrices) {
        const out = new Float32Array(model.corners * 3), p = model.position;
        for (let i = 0; i < model.corners; i++) {
            const q = [p[i * 3], p[i * 3 + 1], p[i * 3 + 2]];
            let x = 0, y = 0, z = 0;
            for (let c = 0; c < 4; c++) {
                const w = model.weight[i * 4 + c];
                if (w > 0) { const r = moved(matrices.subarray(model.bone[i * 4 + c] * 16, model.bone[i * 4 + c] * 16 + 16), q); x += r[0] * w; y += r[1] * w; z += r[2] * w; }
            }
            out[i * 3] = x; out[i * 3 + 1] = y; out[i * 3 + 2] = z;
        }
        return out;
    }

    /* ---- motion from elsewhere: clips of joint positions (AI4Animation's networks, motion capture) ------- */

    const q = {
        mul: (a, b) => [a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1], a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
            a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3], a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2]],
        inv: a => [-a[0], -a[1], -a[2], a[3]],
        rot: (a, v) => { const t = q.mul(q.mul(a, [v[0], v[1], v[2], 0]), q.inv(a)); return [t[0], t[1], t[2]]; },
        // the shortest turn that takes direction a to direction b
        arc(a, b) {
            const d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
            if (d < -0.999999) { const ax = Math.abs(a[0]) < 0.9 ? cross(a, [1, 0, 0]) : cross(a, [0, 1, 0]), n = norm(ax); return [n[0], n[1], n[2], 0]; }
            const c = cross(a, b), w = 1 + d, l = Math.hypot(c[0], c[1], c[2], w);
            return [c[0] / l, c[1] / l, c[2] / l, w / l];
        },
        // from a turning matrix given as its three columns
        fromColumns(x, y, z) {
            const m00 = x[0], m10 = x[1], m20 = x[2], m01 = y[0], m11 = y[1], m21 = y[2], m02 = z[0], m12 = z[1], m22 = z[2], tr = m00 + m11 + m22;
            let r;
            if (tr > 0) { const s = Math.sqrt(tr + 1) * 2; r = [(m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s]; }
            else if (m00 > m11 && m00 > m22) { const s = Math.sqrt(1 + m00 - m11 - m22) * 2; r = [0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s]; }
            else if (m11 > m22) { const s = Math.sqrt(1 + m11 - m00 - m22) * 2; r = [(m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s]; }
            else { const s = Math.sqrt(1 + m22 - m00 - m11) * 2; r = [(m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s]; }
            const l = Math.hypot(r[0], r[1], r[2], r[3]) || 1;
            return [r[0] / l, r[1] / l, r[2] / l, r[3] / l];
        },
    };
    const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const norm = a => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
    const minus = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
    // the turn that takes one pair of directions (along, across) to another
    function between(along1, across1, along2, across2) {
        const frame = (u, w) => { u = norm(u); const d = u[0] * w[0] + u[1] * w[1] + u[2] * w[2]; w = norm([w[0] - u[0] * d, w[1] - u[1] * d, w[2] - u[2] * d]); return [u, w, cross(u, w)]; };
        const A = frame(along1, across1), B = frame(along2, across2);
        // R = B * A^T, as columns
        const col = k => [0, 1, 2].map(r => B[0][r] * A[0][k] + B[1][r] * A[1][k] + B[2][r] * A[2][k]);
        return q.fromColumns(col(0), col(1), col(2));
    }

    function aboutQ(at, r, out) {
        out = out || new Float32Array(16);
        const [x, y, z, w] = r;
        const m00 = 1 - 2 * (y * y + z * z), m01 = 2 * (x * y - z * w), m02 = 2 * (x * z + y * w);
        const m10 = 2 * (x * y + z * w), m11 = 1 - 2 * (x * x + z * z), m12 = 2 * (y * z - x * w);
        const m20 = 2 * (x * z - y * w), m21 = 2 * (y * z + x * w), m22 = 1 - 2 * (x * x + y * y);
        out[0] = m00; out[1] = m10; out[2] = m20; out[3] = 0;
        out[4] = m01; out[5] = m11; out[6] = m21; out[7] = 0;
        out[8] = m02; out[9] = m12; out[10] = m22; out[11] = 0;
        out[12] = at[0] - (m00 * at[0] + m01 * at[1] + m02 * at[2]);
        out[13] = at[1] - (m10 * at[0] + m11 * at[1] + m12 * at[2]);
        out[14] = at[2] - (m20 * at[0] + m21 * at[1] + m22 * at[2]);
        out[15] = 1;
        return out;
    }

    /* Which joints of a source skeleton each bone of ours follows: [from, to]; '*' is the side (Left, Right).
     * Names as in Mixamo, Geno and most motion capture (a prefix such as "mixamorig:" is ignored). */
    const FOLLOWS = { hips: ['Hips', 'Spine2'], chest: ['Spine2', 'Neck'], neck: ['Neck', 'Head'],
        arm: ['*Arm', '*ForeArm'], elbow: ['*ForeArm', '*Hand'], hip: ['*UpLeg', '*Leg'], knee: ['*Leg', '*Foot'] };
    const ACROSS = { hips: ['*UpLeg', 'hip_'], chest: ['*Arm', 'arm_'] };      // the side-to-side line that says how hips and chest are turned

    /* A clip of joint positions laid onto a figure's own bones. Every bone of ours turns so that it points the
     * way the source's matching bone points (hips and chest also turn about themselves as the source's do);
     * lengths and proportions stay the figure's. clip: {fps, start, names, frames: [[[x, y, z] of each joint]]}
     * in the source body's frame (its left +x, up +y, facing +z). Returns {count, fps, start, at(seconds) ->
     * a hold for pose()}, or null for a body it does not fit. A body on four legs needs to know where its nose
     * is in the model (`nose`, from the object's `aim`). */
    function retarget(rig, clip, nose) {
        if (rig && rig.kind === 'four') return retargetFour(rig, clip, nose || [0, 0, 1]);
        if (!rig || rig.kind !== 'biped' || !clip || !clip.frames || !clip.frames.length) return null;
        const f = rig.front < 0 ? -1 : 1;           // a model made facing away: the source is turned round to it
        const index = new Map(clip.names.map((n, i) => [String(n).replace(/^.*[:|]/, '').toLowerCase(), i]));
        const find = name => { const i = index.get(name.toLowerCase()); if (i == null) throw new Error('The motion has no joint called ' + name); return i; };
        // our left (+x) is the source's left when the model faces +z, its right when it faces the other way
        const sided = (name, ours) => name.replace('*', (ours === 'l') === (f > 0) ? 'Left' : 'Right');
        const N = rig.names.length, count = clip.frames.length, named = n => rig.names.indexOf(n);
        const plan = rig.names.map((name, j) => {
            const base = name.replace(/_[lr]$/, ''), side = /_l$/.test(name) ? 'l' : /_r$/.test(name) ? 'r' : null;
            const [a, b] = FOLLOWS[base].map(n => find(side ? sided(n, side) : n));
            const across = ACROSS[base] ? { l: find(sided(ACROSS[base][0], 'l')), r: find(sided(ACROSS[base][0], 'r')),
                rest: minus(rig.at[named(ACROSS[base][1] + 'l')], rig.at[named(ACROSS[base][1] + 'r')]) } : null;
            return { a, b, across, rest: norm(minus(rig.ends[j], rig.at[j])) };
        });
        const hips = find('Hips'), first = clip.frames[0];
        const tall = Math.max(...first.map(p => p[1])) * 1.08 || 1.7, stand = first[hips][1];
        const turns = new Float32Array(count * N * 4), rise = new Float32Array(count);
        const world = new Array(N);
        for (let k = 0; k < count; k++) {
            const frame = clip.frames[k], at = i => [frame[i][0] * f, frame[i][1], frame[i][2] * f];
            for (let j = 0; j < N; j++) {
                const p = plan[j], parent = rig.parent[j] < 0 ? [0, 0, 0, 1] : world[rig.parent[j]], back = q.inv(parent);
                const along = q.rot(back, norm(minus(at(p.b), at(p.a))));
                const r = p.across ? between(p.rest, p.across.rest, along, q.rot(back, minus(at(p.across.l), at(p.across.r)))) : q.arc(p.rest, along);
                world[j] = q.mul(parent, r);
                turns.set(r, (k * N + j) * 4);
            }
            rise[k] = (frame[hips][1] - stand) / tall;
        }
        return {
            count, fps: clip.fps || 30, start: clip.start || 0,
            // the hold at a moment of the clip (seconds from its first frame), between its frames
            at(seconds) {
                const x = Math.max(0, Math.min(count - 1, seconds * this.fps)), k = Math.floor(x), u = x - k, k2 = Math.min(count - 1, k + 1), held = {};
                for (let j = 0; j < N; j++) {
                    const a = Array.from(turns.subarray((k * N + j) * 4, (k * N + j) * 4 + 4)), b = Array.from(turns.subarray((k2 * N + j) * 4, (k2 * N + j) * 4 + 4));
                    held[rig.names[j]] = u > 1e-4 ? slerp(a, b, u) : a;
                }
                held.rise = rise[k] * (1 - u) + rise[k2] * u;
                return held;
            },
        };
    }

    /* The same for a body on four legs (the dog of AI4Animation's quadruped controller). A dog's legs are not
     * straight when it stands and a model's legs are, so here every bone turns by as much as the source's bone
     * has turned since the source's first frame (it stands there): the body as hips-to-neck and its hips'
     * side-to-side line, each leg of ours as the source's leg on the same corner (front or hind by where the leg
     * is along the nose, left or right across it; upper leg to the knee, lower leg to the paw). The body is
     * raised or lowered so that its hind end is as high, for its legs, as the source's hips are: a dog that sits
     * puts its hind end down and keeps its chest up. */
    function retargetFour(rig, clip, nose) {
        if (!clip || !clip.frames || !clip.frames.length) return null;
        const index = new Map(clip.names.map((n, i) => [String(n).replace(/^.*[:|]/, '').toLowerCase(), i]));
        const find = name => { const i = index.get(name.toLowerCase()); if (i == null) throw new Error('The motion has no joint called ' + name); return i; };
        nose = norm([nose[0], 0, nose[2]]);
        const left = [nose[2], 0, -nose[0]];                // (up x nose: its own left)
        const toModel = p => [p[0] * left[0] + p[2] * nose[0], p[1], p[0] * left[2] + p[2] * nose[2]];
        const N = rig.names.length, count = clip.frames.length, body = rig.joints.body, first = clip.frames[0];
        const across = v => v[0] * left[0] + v[2] * left[2];
        const plan = rig.names.map((name, j) => {
            if (name === 'body') return { body: true };
            const corner = name.replace(/^(leg|knee)_/, ''), leg = rig.at[rig.names.indexOf('leg_' + corner)];
            const rel = minus(leg, body), front = rel[0] * nose[0] + rel[2] * nose[2] > 0;
            const chain = front ? ['Arm', 'ForeArm', 'HandSite'] : ['UpLeg', 'Leg', 'FootSite'], upper = /^leg_/.test(name);
            // which of the source's two legs is on this side: by where it is, not by its name (the dog's
            // "Left" legs are on its right)
            const side = ['Left', 'Right'].find(s => Math.sign(across(toModel(first[find(s + chain[0])]))) === Math.sign(across(rel))) || 'Left';
            return { a: find(side + (upper ? chain[0] : chain[1])), b: find(side + (upper ? chain[1] : chain[2])), hind: !front };
        });
        const hips = find('Hips'), neck = find('Neck'), hipL = find('LeftUpLeg'), hipR = find('RightUpLeg');
        const at = (frame, i) => toModel(frame[i]);
        const along0 = minus(at(first, neck), at(first, hips)), across0 = minus(at(first, hipL), at(first, hipR));
        // where the hind legs meet the body, and the ground: the hind end sits down as far as the source's hips do
        const hindLegs = rig.names.filter((name, j) => /^leg_/.test(name) && plan[j].hind).map(name => rig.at[rig.names.indexOf(name)]);
        const hind = [0, 1, 2].map(i => hindLegs.reduce((sum, p) => sum + p[i], 0) / Math.max(1, hindLegs.length));
        const ground = Math.min(...rig.ends.filter(Boolean).map(e => e[1])), pivot = rig.at[rig.names.indexOf('body')];
        const direction = (frame, p) => norm(minus(at(frame, p.b), at(frame, p.a)));
        plan.forEach(p => { if (!p.body) p.first = direction(first, p); });
        const turns = new Float32Array(count * N * 4), rise = new Float32Array(count), world = new Array(N);
        for (let k = 0; k < count; k++) {
            const frame = clip.frames[k];
            for (let j = 0; j < N; j++) {
                const p = plan[j], parent = rig.parent[j] < 0 ? [0, 0, 0, 1] : world[rig.parent[j]];
                let r;
                const turned = p.body ? between(along0, across0, minus(at(frame, neck), at(frame, hips)), minus(at(frame, hipL), at(frame, hipR))) : q.arc(p.first, direction(frame, p));
                r = q.mul(q.inv(parent), turned);
                world[j] = turned;
                turns.set(r, (k * N + j) * 4);
            }
            const turnedBody = world[rig.names.indexOf('body')], moved = q.rot(turnedBody, minus(hind, pivot));
            const wanted = ground + (frame[hips][1] / Math.max(1e-3, first[hips][1])) * (hind[1] - ground);
            rise[k] = (wanted - (pivot[1] + moved[1])) / rig.height;
        }
        return {
            count, fps: clip.fps || 30, start: clip.start || 0,
            at(seconds) {
                const x = Math.max(0, Math.min(count - 1, seconds * this.fps)), k = Math.floor(x), u = x - k, k2 = Math.min(count - 1, k + 1), held = {};
                for (let j = 0; j < N; j++) {
                    const a = Array.from(turns.subarray((k * N + j) * 4, (k * N + j) * 4 + 4)), b = Array.from(turns.subarray((k2 * N + j) * 4, (k2 * N + j) * 4 + 4));
                    held[rig.names[j]] = u > 1e-4 ? slerp(a, b, u) : a;
                }
                held.rise = rise[k] * (1 - u) + rise[k2] * u;
                return held;
            },
        };
    }

    /* how long a stride is for a body of this kind and height, in metres: its steps keep to the ground it covers */
    const stride = (rig, height, move) => height * (rig.kind === 'biped' ? (move === 'run' ? 1.25 : 0.82) : rig.kind === 'four' ? (move === 'run' ? 1.5 : 0.9) : 1);

    /* ---- feet that stay where they step ---------------------------------------------------------------------- */

    /* Which feet are on the ground in each frame of a clip: bit 1 the left, bit 2 the right. AI4Animation's
     * network says so itself (contacts: left ankle, left ball, right ankle, right ball); a capture's are read
     * from its feet: low, and hardly moving over the ground (where the hips go added back). A touch or a lift
     * shorter than three frames is taken for none. */
    function footContacts(clip) {
        const n = clip.frames.length, out = new Uint8Array(n), names = clip.names || [];
        const L = names.indexOf('LeftFoot'), R = names.indexOf('RightFoot');
        if (L < 0 || R < 0 || n < 3) return out;
        if (clip.contacts && clip.contacts.length === n) {
            for (let i = 0; i < n; i++) { const c = clip.contacts[i]; out[i] = (c[0] > 0.5 ? 1 : 0) | (c[2] > 0.5 ? 2 : 0); }
            return out;
        }
        const h = Math.max(0.5, clip.height || 1.7), fps = clip.fps || 30, root = clip.root || [];
        for (const [j, bit] of [[L, 1], [R, 2]]) {
            const ys = Float32Array.from(clip.frames, f => f[j][1]).sort(), low = ys[Math.floor(n * 0.05)];
            const over = i => { const f = clip.frames[i][j], r = root[i] || [0, 0]; return [f[0] + r[0], f[2] + r[1]]; };
            const down = new Uint8Array(n);
            for (let i = 0; i < n; i++) {
                const a = over(Math.max(0, i - 1)), b = over(Math.min(n - 1, i + 1)), speed = Math.hypot(b[0] - a[0], b[1] - a[1]) * fps / 2;
                down[i] = clip.frames[i][j][1] < low + 0.045 * h && speed < 0.3 * h / 1.7 ? 1 : 0;
            }
            for (const value of [1, 0]) {                         // short touches out, then short lifts filled
                for (let i = 0; i < n;) {
                    let k = i;
                    while (k < n && down[k] === down[i]) k++;
                    if (down[i] === value && k - i < 3 && i > 0 && k < n) for (let x = i; x < k; x++) down[x] = 1 - value;
                    i = k;
                }
            }
            for (let i = 0; i < n; i++) if (down[i]) out[i] |= bit;
        }
        return out;
    }

    const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    /* the shortest turn (a quaternion) that takes direction a to direction b */
    function arcQ(a, b) {
        const d = dot3(a, b);
        if (d < -0.999999) { const ax = norm(Math.abs(a[0]) < 0.9 ? [0, a[2], -a[1]] : [-a[2], 0, a[0]]); return [ax[0], ax[1], ax[2], 0]; }
        const c = cross(a, b), w = 1 + d, l = Math.hypot(c[0], c[1], c[2], w);
        return [c[0] / l, c[1] / l, c[2] / l, w / l];
    }
    /* the inverse of a matrix that turns, sizes (or mirrors) and places: column-major, like the model's */
    function affineInverse(m) {
        const a = m[0], b = m[4], c = m[8], d = m[1], e = m[5], f = m[9], g = m[2], h = m[6], i = m[10];
        const cof = [e * i - f * h, -(d * i - f * g), d * h - e * g, -(b * i - c * h), a * i - c * g, -(a * h - b * g), b * f - c * e, -(a * f - c * d), a * e - b * d];
        const det = a * cof[0] + b * cof[1] + c * cof[2] || 1e-12, out = new Float32Array(16);
        for (let col = 0; col < 3; col++) for (let row = 0; row < 3; row++) out[col * 4 + row] = cof[col * 3 + row] / det;
        for (let row = 0; row < 3; row++) out[12 + row] = -(out[row] * m[12] + out[4 + row] * m[13] + out[8 + row] * m[14]);
        out[15] = 1;
        return out;
    }

    /* Two bones (a thigh about its hip, a shin about its knee) bent so that the shin's end reaches `target`
     * (model space), the knee staying in the plane it was bending in. Changes `bones` in place. */
    function reach(rig, bones, hip, knee, target) {
        const at = i => bones.subarray(i * 16, i * 16 + 16);
        const H = moved(at(hip), rig.at[hip]), K = moved(at(knee), rig.at[knee]), F = moved(at(knee), rig.ends[knee]);
        const thigh = minus(K, H), a = Math.hypot(...thigh), b = Math.hypot(...minus(F, K));
        if (a < 1e-6 || b < 1e-6) return;
        // (soft at full stretch: past 95% of the leg's length it comes to straight slowly, so a knee near
        // straight does not snap about as the target moves a little; the foot then falls a little short)
        const full = a + b, soft = 0.95 * full, wanted = Math.hypot(...minus(target, H));
        const reachable = wanted > soft ? soft + (full - soft) * (1 - Math.exp(-(wanted - soft) / (full - soft))) : wanted;
        const toward = norm(minus(target, H)), d = clamp(reachable, Math.abs(a - b) + 1e-4, full * 0.9995);
        // the knee bends the way it bent: its side of the line from the hip to the foot, now beside the line
        // from the hip to the target
        // (a leg held straight has no side of its own: a knee bends toward the front, +z times rig.front)
        const line = norm(minus(F, H)), out = dot3(thigh, line), lean = 0.05 * a * (rig.front || 1);
        const bent = [thigh[0] - line[0] * out, thigh[1] - line[1] * out, thigh[2] - line[2] * out + lean];
        const along = dot3(bent, toward);
        let pole = [bent[0] - toward[0] * along, bent[1] - toward[1] * along, bent[2] - toward[2] * along];
        if (Math.hypot(...pole) < 1e-6 * a) pole = [-toward[2], 0, toward[0]];
        pole = norm(pole);
        const x = (a * a - b * b + d * d) / (2 * d), y = Math.sqrt(Math.max(0, a * a - x * x));
        const K2 = [0, 1, 2].map(j => H[j] + toward[j] * x + pole[j] * y), T2 = [0, 1, 2].map(j => H[j] + toward[j] * d);
        const first = aboutQ(H, arcQ(norm(thigh), norm(minus(K2, H))));
        const thighNow = multiply(first, at(hip)), shinNow = multiply(first, at(knee));
        const F1 = moved(shinNow, rig.ends[knee]);
        const shinThen = multiply(aboutQ(K2, arcQ(norm(minus(F1, K2)), norm(minus(T2, K2)))), shinNow);
        bones.set(thighNow, hip * 16);
        bones.set(shinThen, knee * 16);
    }

    /* ---- a head that looks somewhere ------------------------------------------------------------------------ */

    /* A bone turned about its own joint, and everything that hangs from it with it (`q`: a quaternion, in the
     * model's space). Changes `bones` in place. */
    function turnBranch(rig, bones, j, q) {
        const at = moved(bones.subarray(j * 16, j * 16 + 16), rig.at[j]), turn = aboutQ(at, q);
        const below = k => { for (let x = k; x >= 0; x = rig.parent[x]) if (x === j) return true; return false; };
        for (let k = 0; k < rig.names.length; k++) if (below(k)) bones.set(multiply(turn, bones.subarray(k * 16, k * 16 + 16)), k * 16);
    }

    /* A bone and everything that hangs from it drawn as nothing: pulled into a point at its joint (a head that is
     * not seen while the camera is in it). Changes `bones` in place. */
    function hideBranch(rig, bones, j) {
        if (j < 0) return;
        const at = moved(bones.subarray(j * 16, j * 16 + 16), rig.at[j]), k = 1e-3;
        const shrink = new Float32Array([k, 0, 0, 0, 0, k, 0, 0, 0, 0, k, 0, at[0] * (1 - k), at[1] * (1 - k), at[2] * (1 - k), 1]);
        const below = x0 => { for (let x = x0; x >= 0; x = rig.parent[x]) if (x === j) return true; return false; };
        for (let x = 0; x < rig.names.length; x++) if (below(x)) bones.set(multiply(shrink, bones.subarray(x * 16, x * 16 + 16)), x * 16);
    }

    const axisQ = (axis, degrees) => { const a = degrees * Math.PI / 360, s = Math.sin(a), n = norm(axis); return [n[0] * s, n[1] * s, n[2] * s, Math.cos(a)]; };
    const yawOf = v => Math.atan2(v[0], v[2]) * 180 / Math.PI;
    const wrap = a => ((a % 360) + 540) % 360 - 180;

    /* A figure on two legs looks the way `dir` points (in the model's space): the chest turns a third of the way
     * round, the neck the rest and nods; no further than a neck goes (75° to the side, 35° up or down, from where
     * the chest faces). `weight` 0..1. Changes `bones` in place. */
    function lookToward(rig, bones, dir, weight) {
        const chest = rig.names.indexOf('chest'), neck = rig.names.indexOf('neck'), k = weight == null ? 1 : clamp(weight, 0, 1);
        if (neck < 0 || !(k > 0) || !(Math.hypot(dir[0], dir[1], dir[2]) > 1e-9)) return;
        const front = rig.front || 1, faceOf = j => norm([bones[j * 16 + 8] * front, bones[j * 16 + 9] * front, bones[j * 16 + 10] * front]);
        const d = norm(dir), base = faceOf(chest >= 0 ? chest : neck), own = faceOf(neck), nodOf = v => Math.asin(clamp(v[1], -1, 1)) * 180 / Math.PI;
        const turn = clamp(wrap(yawOf(d) - yawOf(base)), -75, 75), nod = clamp(nodOf(d), -35, 35);
        // `weight` is how much of the way the head goes from where the movement holds it
        const yawTo = yawOf(own) + wrap(yawOf(base) + turn - yawOf(own)) * k, nodTo = nodOf(own) + (nod - nodOf(own)) * k;
        if (chest >= 0) turnBranch(rig, bones, chest, axisQ([0, 1, 0], turn / 3 * k));
        // the neck: the rest of the turn, then the nod (about the head's own side line)
        turnBranch(rig, bones, neck, axisQ([0, 1, 0], wrap(yawTo - yawOf(faceOf(neck)))));
        const now = faceOf(neck), side = cross(now, [0, 1, 0]);
        if (Math.hypot(side[0], side[1], side[2]) > 1e-6) turnBranch(rig, bones, neck, axisQ(side, nodTo - nodOf(now)));
    }

    return { identity, multiply, fromTRS, about, aboutQ, moved, slerp, read, measure, poseOf, SHAPES, shape, faceted, KINDS, BONES, surface, outline, guess, bind, hold, pose, posed, stride, retarget, FOLLOWS, footContacts, reach, affineInverse, arcQ, turnBranch, lookToward, hideBranch };
});
