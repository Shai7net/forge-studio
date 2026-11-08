// node --test extensions/sd-forge-simulation-360/tests/solid.test.js
// Solid objects: reading a .glb, a model's own bones and movements, bones given to a model that has none.
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const Solid = require(path.join(__dirname, '..', 'javascript', 'sim360-1-solid.js'));

/* a .glb file made here: `gltf` with its numbers taken from `arrays` ({name: typed array}) */
function glb(build) {
    const pieces = [], views = [], accessors = [];
    let length = 0;
    const add = (data, type, extra) => {
        const bytes = Buffer.from(data.buffer, data.byteOffset, data.byteLength);
        views.push({ buffer: 0, byteOffset: length, byteLength: bytes.length });
        pieces.push(bytes); length += bytes.length;
        if (length % 4) { const pad = Buffer.alloc(4 - length % 4); pieces.push(pad); length += pad.length; }
        if (!type) return views.length - 1;
        const width = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 }[type];
        const kind = data instanceof Float32Array ? 5126 : data instanceof Uint32Array ? 5125 : data instanceof Uint16Array ? 5123 : 5121;
        accessors.push(Object.assign({ bufferView: views.length - 1, componentType: kind, type, count: data.length / width }, extra || {}));
        return accessors.length - 1;
    };
    const gltf = build(add);
    gltf.asset = { version: '2.0' }; gltf.buffers = [{ byteLength: length }]; gltf.bufferViews = views; gltf.accessors = accessors;
    let json = Buffer.from(JSON.stringify(gltf), 'utf8');
    if (json.length % 4) json = Buffer.concat([json, Buffer.alloc(4 - json.length % 4, 0x20)]);
    const bin = Buffer.concat(pieces), head = Buffer.alloc(12), a = Buffer.alloc(8), b = Buffer.alloc(8);
    head.writeUInt32LE(0x46546C67, 0); head.writeUInt32LE(2, 4); head.writeUInt32LE(12 + 8 + json.length + 8 + bin.length, 8);
    a.writeUInt32LE(json.length, 0); a.writeUInt32LE(0x4E4F534A, 4); b.writeUInt32LE(bin.length, 0); b.writeUInt32LE(0x004E4942, 4);
    return new Uint8Array(Buffer.concat([head, a, json, b, bin]));
}

/* a closed tube of squares around a line, as corners and triangles */
function tube(from, to, radius, rings, around, out) {
    const start = out.position.length / 3, axis = [to[0] - from[0], to[1] - from[1], to[2] - from[2]];
    // two directions across the tube
    const u = Math.abs(axis[1]) > Math.abs(axis[0]) ? [1, 0, 0] : [0, 1, 0], v = [axis[1] * u[2] - axis[2] * u[1], axis[2] * u[0] - axis[0] * u[2], axis[0] * u[1] - axis[1] * u[0]];
    const vl = Math.hypot(...v), w = v.map(x => x / vl), al = Math.hypot(...axis), ax = axis.map(x => x / al);
    const uu = [w[1] * ax[2] - w[2] * ax[1], w[2] * ax[0] - w[0] * ax[2], w[0] * ax[1] - w[1] * ax[0]];
    for (let r = 0; r <= rings; r++) for (let k = 0; k < around; k++) {
        const a = k / around * 2 * Math.PI, c = Math.cos(a) * radius, s = Math.sin(a) * radius, t = r / rings;
        out.position.push(from[0] + axis[0] * t + uu[0] * c + w[0] * s, from[1] + axis[1] * t + uu[1] * c + w[1] * s, from[2] + axis[2] * t + uu[2] * c + w[2] * s);
    }
    for (let r = 0; r < rings; r++) for (let k = 0; k < around; k++) {
        const a = start + r * around + k, b = start + r * around + (k + 1) % around, c = a + around, d = b + around;
        out.index.push(a, c, b, b, c, d);
    }
}

/* a figure of tubes: a trunk with a head, two legs that stand apart, one arm hanging clear and one held up */
function figure() {
    const out = { position: [], index: [] };
    tube([0, 0.9, 0], [0, 1.45, 0], 0.16, 14, 16, out);                    // trunk
    tube([0, 1.5, 0], [0, 1.75, 0], 0.1, 6, 12, out);                      // head
    tube([-0.1, 0, 0], [-0.1, 0.92, 0], 0.07, 20, 10, out);                // legs (x < 0 is its right)
    tube([0.1, 0, 0], [0.1, 0.92, 0], 0.07, 20, 10, out);
    tube([-0.1, 0.03, 0], [-0.1, 0.03, 0.2], 0.04, 4, 8, out);             // feet, pointing where it faces (+z)
    tube([0.1, 0.03, 0], [0.1, 0.03, 0.2], 0.04, 4, 8, out);
    tube([-0.3, 1.4, 0], [-0.34, 0.85, 0], 0.045, 14, 8, out);             // right arm, hanging clear of the trunk
    tube([-0.14, 1.4, 0], [-0.31, 1.4, 0], 0.045, 4, 8, out);
    tube([0.14, 1.4, 0], [0.31, 1.42, 0], 0.045, 4, 8, out);               // left arm, held up
    tube([0.3, 1.42, 0], [0.42, 1.95, 0], 0.045, 14, 8, out);
    return glb(add => ({
        scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
        meshes: [{ primitives: [{ attributes: { POSITION: add(new Float32Array(out.position), 'VEC3') }, indices: add(new Uint32Array(out.index), 'SCALAR') }] }],
    }));
}

test('a .glb is read: its corners, its triangles, its size; a node that moves its mesh is taken into account', () => {
    const file = glb(add => ({
        scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0, translation: [10, 0, 0], scale: [2, 2, 2] }],
        materials: [{ pbrMetallicRoughness: { baseColorFactor: [1, 0.5, 0.25, 1] } }],
        meshes: [{ primitives: [{ material: 0, attributes: { POSITION: add(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 'VEC3'), TEXCOORD_0: add(new Float32Array([0, 0, 1, 0, 0, 1]), 'VEC2') }, indices: add(new Uint16Array([0, 1, 2]), 'SCALAR') }] }],
    }));
    const model = Solid.read(file);
    assert.strictEqual(model.corners, 3);
    assert.deepStrictEqual(Array.from(model.index), [0, 1, 2]);
    assert.deepStrictEqual(model.low, [10, 0, 0]);
    assert.deepStrictEqual(model.high, [12, 2, 0]);
    assert.deepStrictEqual(model.parts.map(p => [p.start, p.count, p.picture, p.colour]), [[0, 3, -1, [1, 0.5, 0.25, 1]]]);
    assert.ok(Math.abs(Math.hypot(model.normal[0], model.normal[1], model.normal[2]) - 0) > 0, 'a mesh without normals gets them');
    assert.strictEqual(model.bones, null);
    assert.throws(() => Solid.read(new Uint8Array(40)), /not a \.glb/);
});

test('a model that brings a bone and a movement plays it: the corner tied to the bone turns with it', () => {
    const quarter = Math.SQRT1_2;
    const file = glb(add => ({
        scene: 0, scenes: [{ nodes: [0, 1] }], nodes: [{ mesh: 0, skin: 0 }, { translation: [0, 1, 0] }],
        skins: [{ joints: [1], inverseBindMatrices: add(new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -1, 0, 1]), 'MAT4') }],
        meshes: [{ primitives: [{ attributes: {
            POSITION: add(new Float32Array([0, 1, 0, 1, 1, 0, 0, 2, 0]), 'VEC3'), JOINTS_0: add(new Uint16Array([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]), 'VEC4'),
            WEIGHTS_0: add(new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]), 'VEC4'),
        }, indices: add(new Uint16Array([0, 1, 2]), 'SCALAR') }] }],
        // in one second the bone turns a quarter of the way round, about z
        animations: [{ name: 'turn', channels: [{ sampler: 0, target: { node: 1, path: 'rotation' } }], samplers: [{ input: add(new Float32Array([0, 1]), 'SCALAR'), output: add(new Float32Array([0, 0, 0, 1, 0, 0, quarter, quarter]), 'VEC4') }] }],
    }));
    const model = Solid.read(file);
    assert.deepStrictEqual(model.moves.map(m => [m.name, m.seconds]), [['turn', 1]]);
    const at = (move, t) => Array.from(Solid.posed(model, Solid.poseOf(model, move, t)).subarray(3, 6)).map(v => +v.toFixed(4));
    assert.deepStrictEqual(at(-1, 0), [1, 1, 0]);                  // as it rests
    assert.deepStrictEqual(at(0, 0), [1, 1, 0]);
    assert.deepStrictEqual(at(0, 0.999999), [0, 2, 0]);            // the corner one to the right of the bone is now one above it
    const half = at(0, 0.5);
    assert.ok(Math.abs(half[0] - quarter) < 1e-3 && Math.abs(half[1] - 1 - quarter) < 1e-3, 'half way it is an eighth of the way round');
});

test('a figure is given bones where its joints are: legs, the arm that hangs and the arm that is held up', () => {
    const model = Solid.read(figure()), shape = Solid.guess(model, 'biped'), J = shape.joints;
    assert.strictEqual(shape.legs, true);
    assert.strictEqual(shape.front, 1, 'its feet point where it faces');
    assert.ok(Math.abs(J.hips[0]) < 0.03 && J.hips[1] > 0.85 && J.hips[1] < 1.05, `hips at ${J.hips}`);
    assert.ok(J.hip_l[0] > 0.05 && J.hip_r[0] < -0.05 && J.knee_l[1] > 0.35 && J.knee_l[1] < 0.65, 'a leg on each side, a knee half way down');
    assert.ok(J.hand_r[1] < 1.05 && J.hand_r[0] < -0.25, `the hanging hand is low: ${J.hand_r}`);
    assert.ok(J.hand_l[1] > 1.75 && J.hand_l[0] > 0.3, `the raised hand is high: ${J.hand_l}`);
    const rig = Solid.bind(model, shape);
    assert.deepStrictEqual([rig.up.l, rig.up.r], [true, false]);
    assert.ok(rig.free.l > 0.8 && rig.free.r > 0.8, 'both arms are clear of the trunk');
    for (let i = 0; i < model.corners; i++) {
        const sum = model.weight[i * 4] + model.weight[i * 4 + 1] + model.weight[i * 4 + 2] + model.weight[i * 4 + 3];
        assert.ok(Math.abs(sum - 1) < 1e-4, 'the pulls on a corner add up to one');
    }
    // at rest nothing moves; walking, the feet swing in turn and the head stays above the trunk
    const rest = Solid.posed(model, Solid.pose(rig, {}, 0));
    for (let i = 0; i < rest.length; i++) assert.ok(Math.abs(rest[i] - model.position[i]) < 1e-5);
    const foot = (pose, right) => { let z = 0, n = 0; for (let i = 0; i < model.corners; i++) if (model.position[i * 3 + 1] < 0.12 && (model.position[i * 3] < 0) === right) { z += pose[i * 3 + 2]; n++; } return z / n; };
    const a = Solid.posed(model, Solid.pose(rig, Solid.hold(rig, 'walk', Math.PI / 2, 0, 1), 1)), b = Solid.posed(model, Solid.pose(rig, Solid.hold(rig, 'walk', 3 * Math.PI / 2, 0, 1), 1));
    assert.ok(foot(a, false) - foot(a, true) > 0.25, 'half a stride: the left foot is ahead');
    assert.ok(foot(b, true) - foot(b, false) > 0.25, 'the other half: the right foot is ahead');
    let top = -Infinity;
    for (let i = 0; i < model.corners; i++) top = Math.max(top, a[i * 3 + 1]);
    assert.ok(top > 1.9 && top < 2.05, `it neither grows nor collapses: ${top}`);
    // a figure that faces the other way walks the other way
    const back = Solid.posed(model, Solid.pose(rig, Solid.hold(rig, 'walk', Math.PI / 2, 0, -1), 1));
    assert.ok(foot(back, false) - foot(back, true) < -0.25);
});

test('every kind of body has its bones and every movement it is said to know gives a pose', () => {
    const model = Solid.read(figure());
    for (const kind of Object.keys(Solid.KINDS)) {
        const rig = Solid.bind(model, Solid.guess(model, kind));
        assert.strictEqual(rig.names.length, Solid.BONES[kind].length);
        for (const move of Solid.KINDS[kind]) {
            const pose = Solid.pose(rig, Solid.hold(rig, move, 1.3, 0.7, 1), 1);
            assert.strictEqual(pose.length, rig.names.length * 16);
            assert.ok(pose.every(Number.isFinite), `${kind} ${move}`);
        }
        assert.ok(Solid.stride(rig, 1.7, Solid.KINDS[kind][0]) > 0);
    }
});

test('every plain shape is a whole model: it stands on the ground, its normals are of length one, a picture can be wrapped on it', () => {
    assert.deepStrictEqual(Solid.SHAPES, ['cube', 'ball', 'half', 'cone', 'pyramid', 'cylinder', 'tube', 'capsule', 'ring']);
    for (const kind of Solid.SHAPES) {
        const model = Solid.shape(kind);
        assert.ok(model.corners > 3 && model.index.length % 3 === 0, kind);
        assert.ok(Math.abs(model.low[1]) < 1e-6 && model.high[1] <= 1 + 1e-6 && model.high[1] > 0.25, `${kind} stands on y = 0 and is no taller than one`);
        assert.ok(model.high[0] - model.low[0] <= 1 + 1e-6 && model.high[2] - model.low[2] <= 1 + 1e-6, `${kind} fits a box of one`);
        assert.strictEqual(Math.max(...model.index) < model.corners, true);
        assert.strictEqual(model.uv.length, model.corners * 2);
        for (let i = 0; i < model.corners; i++) {
            const n = Math.hypot(model.normal[i * 3], model.normal[i * 3 + 1], model.normal[i * 3 + 2]);
            assert.ok(Math.abs(n - 1) < 1e-4, `${kind}: normal ${i} is ${n}`);
        }
        assert.deepStrictEqual(model.parts.map(p => [p.start, p.count]), [[0, model.index.length]]);
    }
    assert.strictEqual(Solid.shape('pyramid').index.length / 3, 8, 'four sides and a floor of four triangles');
    assert.throws(() => Solid.shape('teapot'), /no such shape/);
});

test('a clip of joint positions (a neural walk, motion capture) is laid onto a figure\'s own bones', () => {
    const model = Solid.read(figure()), rig = Solid.bind(model, Solid.guess(model, 'biped')), J = rig.joints;
    // a source skeleton standing as our figure stands (its left is +x, it faces +z): every bone stays as it is
    const names = ['Hips', 'Spine2', 'Neck', 'Head', 'LeftArm', 'LeftForeArm', 'LeftHand', 'RightArm', 'RightForeArm', 'RightHand',
        'LeftUpLeg', 'LeftLeg', 'LeftFoot', 'RightUpLeg', 'RightLeg', 'RightFoot'];
    const ours = ['hips', 'chest', 'neck', 'head', 'arm_l', 'elbow_l', 'hand_l', 'arm_r', 'elbow_r', 'hand_r', 'hip_l', 'knee_l', 'foot_l', 'hip_r', 'knee_r', 'foot_r'];
    const rest = ours.map(n => J[n].slice());
    const still = Solid.retarget(rig, { fps: 30, names: names.map(n => 'mixamorig:' + n), frames: [rest, rest] });
    const held = still.at(0);
    for (const n of rig.names) assert.ok(Math.abs(held[n][3]) > 0.9999, n + ' stays as it is');
    assert.ok(Math.abs(held.rise) < 1e-6);
    // the left thigh and shin swing forward by 30 degrees about the hip: our left knee comes forward, the right one stays
    const a = 30 * Math.PI / 180, swing = p => { const h = J.hip_l, y = p[1] - h[1], z = p[2] - h[2]; return [p[0], h[1] + y * Math.cos(a) + z * Math.sin(a), h[2] - y * Math.sin(a) + z * Math.cos(a)]; };
    const step = rest.map((p, i) => (['knee_l', 'foot_l'].includes(ours[i]) ? swing(p) : p));
    const walked = Solid.retarget(rig, { fps: 30, names, frames: [rest, step] }).at(1 / 30);
    const bones = Solid.pose(rig, walked, 1), knee = rig.names.indexOf('knee_l'), foot = Solid.moved(bones.subarray(knee * 16, knee * 16 + 16), J.foot_l);
    assert.ok(Math.abs(foot[2] - swing(J.foot_l)[2]) < 0.01 * rig.height, 'the foot is where the source\'s foot went');
    const other = Solid.moved(bones.subarray(rig.names.indexOf('knee_r') * 16, rig.names.indexOf('knee_r') * 16 + 16), J.foot_r);
    assert.ok(Math.hypot(other[0] - J.foot_r[0], other[1] - J.foot_r[1], other[2] - J.foot_r[2]) < 1e-4, 'the other foot stays');
    // halfway between two frames, halfway there
    const half = Solid.pose(rig, Solid.retarget(rig, { fps: 30, names, frames: [rest, step] }).at(0.5 / 30), 1);
    const mid = Solid.moved(half.subarray(knee * 16, knee * 16 + 16), J.foot_l);
    assert.ok(mid[2] > J.foot_l[2] + 0.001 && mid[2] < foot[2] - 0.001);
    // a model made facing away takes the source turned round: its +x side follows the source's right
    assert.throws(() => Solid.retarget(rig, { fps: 30, names: ['Hips'], frames: [[[0, 1, 0]]] }), /no joint/);
});

test('a leg is bent so that its foot reaches a point: thigh and shin keep their lengths, the knee bends the way it bent', () => {
    // a leg: hip, knee a little forward (+z), ankle under the hip; two bones, the shin a child of the thigh
    const rig = { names: ['hip_l', 'knee_l'], at: [[0.1, 1, 0], [0.1, 0.55, 0.03]], ends: [[0.1, 0.55, 0.03], [0.1, 0.1, 0]] };
    const bones = new Float32Array(32); bones.set(Solid.identity(), 0); bones.set(Solid.identity(), 16);
    const length = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
    const thigh = length(rig.at[0], rig.ends[0]), shin = length(rig.at[1], rig.ends[1]);
    const target = [0.12, 0.2, 0.25];
    Solid.reach(rig, bones, 0, 1, target);
    const H = Solid.moved(bones.subarray(0, 16), rig.at[0]), K = Solid.moved(bones.subarray(16, 32), rig.at[1]), F = Solid.moved(bones.subarray(16, 32), rig.ends[1]);
    assert.ok(length(F, target) < 1e-4, 'the foot is at the target');
    assert.ok(Math.abs(length(H, K) - thigh) < 1e-4 && Math.abs(length(K, F) - shin) < 1e-4, 'the bones keep their lengths');
    assert.ok(length(H, rig.at[0]) < 1e-6, 'the hip stays');
    assert.ok(Solid.moved(bones.subarray(0, 16), rig.ends[0])[0] - K[0] < 1e-4, 'the thigh\'s end is the knee');
    assert.ok(K[2] > 0.03, 'the knee still bends forward');
    // a point out of reach: the leg points at it, straight, and no bone is stretched
    Solid.reach(rig, bones, 0, 1, [0.1, -2, 0]);
    const F2 = Solid.moved(bones.subarray(16, 32), rig.ends[1]), K2 = Solid.moved(bones.subarray(16, 32), rig.at[1]);
    assert.ok(Math.abs(length(rig.at[0], K2) - thigh) < 1e-4 && Math.abs(length(K2, F2) - shin) < 1e-4);
    assert.ok(F2[1] < 0.11 && Math.abs(F2[0] - 0.1) < 1e-3, 'it points straight at it');
});

test('a placing matrix is undone by its inverse, mirrored or not', () => {
    for (const mirror of [1, -1]) {
        const m = Solid.multiply(Solid.fromTRS([3, -1, 2], [0, Math.sin(0.4), 0, Math.cos(0.4)], [0.7 * mirror, 0.7, 0.7]), Solid.identity());
        const inv = Solid.affineInverse(m), p = [0.3, 1.2, -0.8], back = Solid.moved(inv, Solid.moved(m, p));
        assert.ok(Math.hypot(back[0] - p[0], back[1] - p[1], back[2] - p[2]) < 1e-5, 'mirror ' + mirror);
    }
});

test('which feet are down: the network\'s own contacts, or a capture\'s feet that are low and still', () => {
    const names = ['Hips', 'LeftFoot', 'RightFoot'];
    // a walk in place, the hips going forward 1 m/s: a foot is down while it goes back at the hips' speed
    const frames = [], root = [];
    for (let i = 0; i < 60; i++) {
        const t = i / 30, phase = (t % 1);
        const foot = (shift) => { const p = (phase + shift) % 1; return p < 0.6 ? [0, 0.08, 0.3 - p / 0.6 * 0.6] : [0, 0.08 + 0.15 * Math.sin((p - 0.6) / 0.4 * Math.PI), -0.3 + (p - 0.6) / 0.4 * 0.6]; };
        const l = foot(0), r = foot(0.5);
        frames.push([[0, 0.9, 0], [0.1, l[1], l[2]], [-0.1, r[1], r[2]]]);
        root.push([0, t * 1.0 - 0.0, 0]);
    }
    // (the feet go back 1 m/s while down, the hips forward 1 m/s: over the ground, still)
    const clip = { fps: 30, names, frames, root, height: 1.7 };
    const c = Solid.footContacts(clip);
    let left = 0, right = 0, both = 0;
    for (let i = 0; i < 60; i++) { left += c[i] & 1 ? 1 : 0; right += c[i] & 2 ? 1 : 0; both += (c[i] & 3) === 3 ? 1 : 0; }
    assert.ok(left > 25 && left < 45 && right > 25 && right < 45, 'each foot is down about 60% of the time: ' + left + ', ' + right);
    assert.ok(both > 0 && both < 30, 'and both are down for a while between steps');
    // the network's contacts are taken as they are: left ankle, left ball, right ankle, right ball
    const told = { fps: 30, names, frames: frames.slice(0, 3), contacts: [[1, 1, 0, 0], [0, 1, 1, 0], [0.2, 0, 0.9, 1]] };
    assert.deepStrictEqual(Array.from(Solid.footContacts(told)), [1, 2, 2]);
});

test('a straight leg bends its knee toward the front of the figure', () => {
    for (const front of [1, -1]) {
        const rig = { front, names: ['hip_l', 'knee_l'], at: [[0, 1, 0], [0, 0.55, 0]], ends: [[0, 0.55, 0], [0, 0.1, 0]] };
        const bones = new Float32Array(32); bones.set(Solid.identity(), 0); bones.set(Solid.identity(), 16);
        Solid.reach(rig, bones, 0, 1, [0, 0.3, 0]);
        const K = Solid.moved(bones.subarray(16, 32), rig.at[1]);
        assert.ok(K[2] * front > 0.1, 'front ' + front + ': knee at z ' + K[2].toFixed(3));
        assert.ok(Math.abs(K[0]) < 1e-3, 'not to the side');
    }
});

test('a head looks where it is told: the chest a third of the way, the neck the rest, no further than a neck goes', () => {
    // a body facing +z: chest at the hips' top, neck on it, an arm hanging from the chest
    const rig = { front: 1, names: ['hips', 'chest', 'neck', 'arm_l'], parent: [-1, 0, 1, 1], at: [[0, 1, 0], [0, 1.2, 0], [0, 1.5, 0], [0.2, 1.45, 0]], ends: [[0, 1.2, 0], [0, 1.5, 0], [0, 1.7, 0], [0.2, 1.1, 0]] };
    const fresh = () => { const b = new Float32Array(64); for (let i = 0; i < 4; i++) b.set(Solid.identity(), i * 16); return b; };
    const faceOf = (b, j) => { const f = [b[j * 16 + 8], b[j * 16 + 9], b[j * 16 + 10]], l = Math.hypot(...f); return f.map(v => v / l); };
    const yaw = v => Math.atan2(v[0], v[2]) * 180 / Math.PI, nod = v => Math.asin(v[1]) * 180 / Math.PI;
    let b = fresh();
    Solid.lookToward(rig, b, [Math.sin(60 * Math.PI / 180), 0, Math.cos(60 * Math.PI / 180)], 1);
    assert.ok(Math.abs(yaw(faceOf(b, 2)) - 60) < 0.01, 'the head looks there: ' + yaw(faceOf(b, 2)));
    assert.ok(Math.abs(yaw(faceOf(b, 1)) - 20) < 0.01, 'the chest a third of the way');
    assert.ok(Math.abs(yaw(faceOf(b, 3)) - 20) < 0.01, 'the arm goes with the chest');
    assert.ok(Math.abs(yaw(faceOf(b, 0))) < 1e-6, 'the hips stay');
    const neckAt = Solid.moved(b.subarray(32, 48), rig.at[2]);
    assert.ok(Math.hypot(neckAt[0], neckAt[1] - 1.5, neckAt[2]) < 1e-5, 'the neck stays on the chest (it turns about its own joint)');
    // straight behind: no further than 75°
    b = fresh(); Solid.lookToward(rig, b, [0, 0, -1], 1);
    assert.ok(Math.abs(Math.abs(yaw(faceOf(b, 2))) - 75) < 0.01, 'a neck goes so far');
    // up: a nod of no more than 35°
    b = fresh(); Solid.lookToward(rig, b, [0, 1, 0.2], 1);
    assert.ok(Math.abs(nod(faceOf(b, 2)) - 35) < 0.05, 'nods up 35°: ' + nod(faceOf(b, 2)));
    // half the weight: half the way
    b = fresh(); Solid.lookToward(rig, b, [Math.sin(60 * Math.PI / 180), 0, Math.cos(60 * Math.PI / 180)], 0.5);
    assert.ok(Math.abs(yaw(faceOf(b, 2)) - 30) < 0.01, 'half: ' + yaw(faceOf(b, 2)));
});

test('a head that is not seen is pulled into its neck; the rest of the body stays as it was', () => {
    const rig = { front: 1, names: ['hips', 'chest', 'neck', 'arm_l'], parent: [-1, 0, 1, 1], at: [[0, 1, 0], [0, 1.2, 0], [0, 1.5, 0], [0.2, 1.45, 0]], ends: [[0, 1.2, 0], [0, 1.5, 0], [0, 1.7, 0], [0.2, 1.1, 0]] };
    const b = new Float32Array(64); for (let i = 0; i < 4; i++) b.set(Solid.identity(), i * 16);
    Solid.hideBranch(rig, b, 2);
    const top = Solid.moved(b.subarray(32, 48), [0, 1.75, 0.1]), base = Solid.moved(b.subarray(32, 48), [0, 1.5, 0]);
    assert.ok(Math.hypot(top[0], top[1] - 1.5, top[2]) < 0.001, 'the top of the head is at the neck');
    assert.ok(Math.hypot(base[0], base[1] - 1.5, base[2]) < 1e-6, 'the neck stays where it is');
    const hand = Solid.moved(b.subarray(48, 64), [0.2, 1.1, 0]);
    assert.ok(Math.hypot(hand[0] - 0.2, hand[1] - 1.1, hand[2]) < 1e-6, 'an arm (not under the neck) is as it was');
});
