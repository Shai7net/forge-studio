/* Simulation 360 - drawing (WebGL 2, nothing borrowed).
 *
 * A world is one sphere of triangles around its centre. Every corner is pushed out as far as that direction
 * of the panorama is (the depth map), so when the camera leaves the centre near things pass far things.
 * Portals are painted on the same surface: where a portal's mask lies, the surface shows the world behind
 * it instead. That world is drawn first, as a picture of its own, from where the camera would be standing
 * in it (the portal's doorstep is its arrival point): it has its depth, its objects and its weather, things
 * in it pass each other as the camera moves, and walking in changes nothing in the picture. A portal that is
 * not looked at closely shows its world flat, as it looks from its arrival point.
 *
 * Three jobs:
 *   render   the view, on the page
 *   capture  the view (or a part of it) as pixels, at any size: for the recorder, for the automatic
 *            selection, and for drawing a character into a view
 *   bake     the reverse: a flat picture seen by the camera is laid back onto the panorama. This is how a
 *            shape drawn on the screen becomes a mask on the sphere, and how a character drawn in an
 *            ordinary view ends up in the world.
 */
(function () {
    'use strict';
    const Core = window.Sim360Core;
    const NX = 512, NY = 256;                 // the sphere: 512 x 256 squares
    const PORTALS = 8;                        // portals one world can show (two textures of four masks)
    const FAR_SHELL = 400;                    // where a world is drawn while it has no depth yet

    const VERTEX = `#version 300 es
layout(location=0) in vec2 aUv;
layout(location=1) in float aDist;
layout(location=2) in float aSteep;
uniform mat4 uVP; uniform vec3 uCam; uniform float uRelief, uFlip, uBake;
out vec2 vUv; out vec3 vWorld; out float vSteep;
const float PI = 3.141592653589793;
void main() {
    float lon = (aUv.x - 0.5) * 2.0 * PI, lat = (0.5 - aUv.y) * PI, c = cos(lat);
    vec3 d = vec3(c * sin(lon), sin(lat), -c * cos(lon));
    vec3 p = mix(uCam + d * ${FAR_SHELL.toFixed(1)}, d * aDist, uRelief);
    vUv = aUv; vWorld = p; vSteep = aSteep * uRelief;
    if (uBake > 0.5) { gl_Position = vec4(aUv * 2.0 - 1.0, 0.0, 1.0); return; }
    gl_Position = uVP * vec4(p, 1.0);
    gl_Position.y *= uFlip;
}`;

    const dests = Array.from({ length: PORTALS }, (_, i) => `uDest${i}`);
    const FRAGMENT = `#version 300 es
precision highp float;
in vec2 vUv; in vec3 vWorld; in float vSteep;
uniform sampler2D uPano, uMask0, uMask1, uSel, uMixTex, uInner0, uInner1, ${dests.join(', ')};
uniform float uSee[${PORTALS}], uTurn[${PORTALS}], uDeep[${PORTALS}], uDeepAmt[${PORTALS}];
uniform vec3 uTint[${PORTALS}];
uniform vec3 uCam, uMixDir;
uniform vec2 uViewport;
uniform float uPanoOn, uMaskOn, uGuide, uTime, uSelOn, uMixAmt, uMixTurn, uMixDeep, uOutput;
uniform vec4 uFade, uMist;
out vec4 o;
const float PI = 3.141592653589793;
vec2 turned(vec3 d, float a) {
    float c = cos(a), s = sin(a);
    vec3 r = vec3(c * d.x - s * d.z, d.y, s * d.x + c * d.z);
    return vec2(atan(r.x, -r.z) / (2.0 * PI) + 0.5, acos(clamp(r.y, -1.0, 1.0)) / PI);
}
// the world behind a portal, drawn beforehand as this camera sees it: the same point of the picture
vec3 behind(float which, vec2 at) { return which > 1.5 ? textureLod(uInner1, at, 0.0).rgb : textureLod(uInner0, at, 0.0).rgb; }
// what a portal shows: its world flat (as from its arrival point), in its depth, or on the way from one to the other
#define SHOWN(i, tex) (uDeep[i] < 0.5 ? textureLod(tex, turned(view, uTurn[i]), 0.0).rgb : uDeepAmt[i] > 0.999 ? behind(uDeep[i], screen) : mix(textureLod(tex, turned(view, uTurn[i]), 0.0).rgb, behind(uDeep[i], screen), uDeepAmt[i]))
#define PORTAL(i, m, tex) a = m; if (a > 0.004) { \\
    if (uSee[i] > 0.0) color = mix(color, SHOWN(i, tex), smoothstep(0.1, 0.9, a) * uSee[i]); \\
    float rim = smoothstep(0.03, 0.5, a) * (1.0 - smoothstep(0.5, 0.97, a)); \\
    float pulse = 0.6 + 0.4 * sin(uTime * 2.6 + (vUv.x + vUv.y) * 90.0 + float(i) * 1.7); \\
    color = mix(color, uTint[i], uGuide * clamp(rim * pulse + (1.0 - uSee[i]) * 0.2 * a, 0.0, 1.0)); }
void main() {
    vec3 view = normalize(vWorld - uCam);
    vec2 screen = gl_FragCoord.xy / uViewport;
    if (uOutput > 0.5) { float s = texture(uSel, vUv).r; o = vec4(s, s, s, 1.0); return; }
    vec3 color;
    if (uPanoOn > 0.5) {
        // Between a near thing and what is behind it the surface is a steep wall, and from the side the
        // picture on it is pulled into streaks. The farther the camera is from the centre, the softer that
        // wall is drawn, so it reads as a soft background and not as streaks.
        float soft = clamp(vSteep * length(uCam) / max(length(vWorld), 0.01) * 14.0 - 0.4, 0.0, 4.5);
        color = texture(uPano, vUv, soft).rgb;
    } else {  // a world with no picture yet: a quiet grid, so turning and moving can still be felt
        vec2 g = abs(fract(vUv * vec2(24.0, 12.0)) - 0.5);
        float line = 1.0 - smoothstep(0.0, 0.03, min(g.x, g.y));
        color = mix(vec3(0.07, 0.08, 0.11) + 0.05 * (1.0 - vUv.y), vec3(0.25, 0.3, 0.42), line * 0.6);
    }
    // mist: what is far fades into the air (the world behind a portal has its own air)
    if (uMist.a > 0.0) color = mix(color, uMist.rgb, uMist.a * (1.0 - exp(-length(vWorld - uCam) * 0.09)));
    if (uMaskOn > 0.5) {
        vec4 m0 = texture(uMask0, vUv), m1 = texture(uMask1, vUv);
        float a;
        PORTAL(0, m0.r, uDest0) PORTAL(1, m0.g, uDest1) PORTAL(2, m0.b, uDest2) PORTAL(3, m0.a, uDest3)
        PORTAL(4, m1.r, uDest4) PORTAL(5, m1.g, uDest5) PORTAL(6, m1.b, uDest6) PORTAL(7, m1.a, uDest7)
    }
    if (uSelOn > 0.5) {
        float s = texture(uSel, vUv).r;
        if (s > 0.004) {
            float stripes = step(0.5, fract((gl_FragCoord.x + gl_FragCoord.y) / 14.0 - uTime * 0.9));
            float edge = smoothstep(0.0, 0.5, s) * (1.0 - smoothstep(0.5, 1.0, s));
            color = mix(color, mix(vec3(0.1, 0.75, 1.0), vec3(1.0), stripes * 0.3), clamp(s * 0.36 + edge * 0.7, 0.0, 1.0));
        }
    }
    if (uMixAmt > 0.0) {
        if (uMixDeep > 0.5) {
            // going through a portal that shows its world in depth: that world opens around the camera, from
            // the portal outwards, until it is the whole picture. Then the camera is simply in it.
            float away = min(1.0, acos(clamp(dot(view, uMixDir), -1.0, 1.0)) / 1.3);
            color = mix(color, behind(uMixDeep, screen), smoothstep(0.0, 0.35, uMixAmt * 1.35 - away));
        } else color = mix(color, textureLod(uMixTex, turned(view, uMixTurn), 0.0).rgb, uMixAmt);
    }
    color = mix(color, uFade.rgb, uFade.a);
    o = vec4(color, 1.0);
}`;

    const BAKE_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vUv; in vec3 vWorld;
uniform mat4 uView; uniform sampler2D uSrc, uBase; uniform float uUseBase;
out vec4 o;
void main() {
    vec4 clip = uView * vec4(vWorld, 1.0);
    vec4 s = vec4(0.0);
    if (clip.w > 0.0001) {
        vec2 n = clip.xy / clip.w;
        if (abs(n.x) <= 1.0 && abs(n.y) <= 1.0) s = texture(uSrc, vec2(n.x * 0.5 + 0.5, 0.5 - n.y * 0.5));
    }
    if (uUseBase > 0.5) o = vec4(mix(texture(uBase, vUv).rgb, s.rgb, s.a), 1.0);
    else o = vec4(s.a, s.a, s.a, 1.0);
}`;

    /* An object standing in a world: a cut-out picture on an upright sheet of triangles, pushed towards the
     * viewer by the object's own shape map, so it has thickness. The world behind it is untouched. */
    const OBJECT_VERTEX = `#version 300 es
layout(location=0) in vec2 aUv;
uniform mat4 uVP; uniform vec3 uAnchor, uRight, uUp, uNormal, uEye; uniform vec2 uSize;
uniform float uThick, uFlip, uMirror;
uniform vec4 uGait; uniform vec3 uBody;
uniform sampler2D uShape;
out vec2 vUv; out float vFar;
void main() {
    vec2 uv = vec2(uMirror > 0.5 ? 1.0 - aUv.x : aUv.x, aUv.y);
    float near = textureLod(uShape, uv, 0.0).r;
    // on the sheet: across from its middle line (in widths) and up from its bottom edge (in heights)
    vec2 q = vec2(aUv.x - 0.5, 1.0 - aUv.y), step_ = vec2(0.0);
    if (uGait.y > 0.0) {
        // A walking figure, moved like a paper puppet (uGait: where in the stride, how strongly, how much of
        // the walk is across the picture; uBody: the figure's middle line, its soles and the top of its head
        // on the sheet). The two halves of the lower body are the two legs, half a stride apart: a foot
        // leaves the ground and goes from behind the body to ahead of it, while the other stands. The body
        // rises and falls with every step and sways from foot to foot; the arms swing against the legs.
        float tall = max(0.05, uBody.z - uBody.y);
        float up = clamp((q.y - uBody.y) / tall, 0.0, 1.0);                         // 0 at the soles, 1 at the top of the head
        float from = (aUv.x - uBody.x) * uSize.x / (tall * uSize.y);                // from the middle line, in the figure's heights
        float s = sin(uGait.x), c = cos(uGait.x), k = uGait.y * tall;
        float leg = 1.0 - smoothstep(0.04, 0.5, up), side = clamp(from * -14.0, -1.0, 1.0);
        step_.y += max(0.0, s * side) * leg * 0.055 * k;
        step_.x -= c * side * leg * 0.07 * k * uGait.z;
        step_.y += (abs(c) - 0.6) * 0.012 * k * smoothstep(0.0, 0.15, up);
        step_.x += s * 0.012 * k * smoothstep(0.3, 0.6, up);
        float arm = smoothstep(0.1, 0.19, abs(from)) * smoothstep(0.42, 0.55, up) * (1.0 - smoothstep(0.7, 0.8, up));
        step_.x += c * sign(from) * arm * 0.03 * k * uGait.z;
    }
    vec3 p = uAnchor + uRight * (q.x * uSize.x + step_.x * uSize.y) + uUp * (q.y + step_.y) * uSize.y + uNormal * (near - 0.5) * uThick;
    vUv = uv; vFar = length(p - uEye);
    gl_Position = uVP * vec4(p, 1.0);
    gl_Position.y *= uFlip;
}`;

    const OBJECT_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vUv; in float vFar;
uniform sampler2D uPicture;
uniform float uOpacity, uPacked, uChosen, uTime;
uniform vec4 uFade, uMist;
out vec4 o;
void main() {
    vec4 c;
    if (uPacked > 0.5) { c.rgb = texture(uPicture, vec2(vUv.x * 0.5, vUv.y)).rgb; c.a = texture(uPicture, vec2(0.5 + vUv.x * 0.5, vUv.y)).r; }
    else c = texture(uPicture, vUv);
    float a = c.a * uOpacity;
    if (a < 0.02) discard;
    vec3 color = c.rgb;
    if (uMist.a > 0.0) color = mix(color, uMist.rgb, uMist.a * (1.0 - exp(-vFar * 0.09)));
    if (uChosen > 0.5) {    // the chosen object: its outline glows
        float rim = smoothstep(0.05, 0.35, c.a) * (1.0 - smoothstep(0.55, 0.97, c.a));
        color = mix(color, vec3(1.0, 0.82, 0.35), rim * (0.65 + 0.35 * sin(uTime * 4.0)));
        a = max(a, rim * 0.9);
    }
    color = mix(color, uFade.rgb, uFade.a);
    o = vec4(color, a);
}`;
    const OX = 48, OY = 72;                   // an object's sheet: 48 x 72 squares

    /* A solid object: a real model, triangles with a picture wrapped around them. Its corners may hang on
     * bones (uBoneTex: one bone to a row, its matrix in four texels), so it can walk, flap or swim. The light
     * on it is the light of the world it stands in: the sky's colour from above, the ground's from below and
     * the brightest part of the panorama as its sun. */
    const SOLID_VERTEX = `#version 300 es
layout(location=0) in vec3 aPosition;
layout(location=1) in vec3 aNormal;
layout(location=2) in vec2 aUv;
layout(location=3) in vec4 aBone;
layout(location=4) in vec4 aWeight;
uniform mat4 uVP, uModel; uniform float uFlip, uBones;
uniform sampler2D uBoneTex;
out vec2 vUv; out vec3 vNormal, vWorld, vRest; out float vRestFront;
mat4 bone(float i) {
    int row = int(i + 0.5);
    return mat4(texelFetch(uBoneTex, ivec2(0, row), 0), texelFetch(uBoneTex, ivec2(1, row), 0), texelFetch(uBoneTex, ivec2(2, row), 0), texelFetch(uBoneTex, ivec2(3, row), 0));
}
void main() {
    vec4 p = vec4(aPosition, 1.0); vec3 n = aNormal;
    vRest = aPosition; vRestFront = aNormal.z;             // where the corner is while the model rests: a face painted on it stays on the skin
    if (uBones > 0.5) {
        mat4 m = bone(aBone.x) * aWeight.x + bone(aBone.y) * aWeight.y + bone(aBone.z) * aWeight.z + bone(aBone.w) * aWeight.w;
        p = vec4((m * p).xyz, 1.0); n = mat3(m) * n;
    }
    vec4 world = uModel * p;
    vWorld = world.xyz; vNormal = mat3(uModel) * n; vUv = aUv;
    gl_Position = uVP * world;
    gl_Position.y *= uFlip;
}`;

    const SOLID_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vUv; in vec3 vNormal, vWorld, vRest; in float vRestFront;
uniform sampler2D uPicture, uFace, uTalk;
uniform vec4 uColour, uFade, uMist, uFaceMap, uFaceOn, uTalkMap;
uniform vec3 uEye, uSky, uGround, uSun, uSunColour;
uniform vec3 uTone;
uniform float uOpacity, uCut, uChosen, uTime, uLit, uHasPicture, uWrap, uTalkOn;
uniform float uNear;            // seen through its own eyes: nothing of it nearer the camera than this is drawn
out vec4 o;
// a picture's colour at a place of it (a video carries its colours in its left half and its cut in its right)
vec3 laid(sampler2D picture, vec2 at, float packed) {
    return packed > 0.5 ? texture(picture, vec2(at.x * 0.5, at.y)).rgb : texture(picture, at).rgb;
}
float cutAt(sampler2D picture, vec2 at, float packed) {
    at = clamp(at, 0.004, 0.996);
    return packed > 0.5 ? texture(picture, vec2(0.5 + at.x * 0.5, at.y)).r : texture(picture, at).a;
}
// How well inside its figure a place of a picture is: 1 inside, falling to 0 before the figure's edge. The
// edge of a cut-out figure is its drawn outline and a trace of what was behind it; laid on a model that is
// seen from the side, that is a second outline drawn across the model.
float within(sampler2D picture, vec2 at, float packed) {
    float sum = cutAt(picture, at, packed);
    for (int i = 0; i < 8; i++) { float a = float(i) * 0.7853982; sum += cutAt(picture, at + vec2(cos(a), sin(a)) * 0.02, packed); }
    return smoothstep(0.5, 0.97, sum / 9.0);
}
void main() {
    if (uNear > 0.0 && distance(vWorld, uEye) < uNear) discard;
    vec4 c = uColour;
    if (uHasPicture > 0.5) c *= texture(uPicture, vUv * uWrap);
    if (uCut > 0.0 && c.a < uCut) discard;
    vec3 n = normalize(vNormal), toEye = normalize(uEye - vWorld);
    vec3 light = mix(uGround, uSky, n.y * 0.5 + 0.5) + uSunColour * max(dot(n, uSun), 0.0);
    vec3 base = c.rgb * uTone;
    if (uFaceOn.x != 0.0) {
        // The object's own picture (or the video of it speaking) lies on the front of the model, from a
        // height upwards: its face. uFaceMap: where a point of the resting model is in that picture.
        // uFaceOn: which way the model faces (+1, -1), from what height, how soft the lower edge, and what
        // the picture is (0: none, 1: a picture, 2: a video). Only what faces forward takes it: on a surface
        // that turns away the picture would be smeared along it.
        float w = smoothstep(0.3, 0.62, vRestFront * uFaceOn.x) * smoothstep(uFaceOn.y, uFaceOn.y + uFaceOn.z, vRest.y);
        if (w > 0.0 && uFaceOn.w > 0.5) {
            vec2 at = vec2(uFaceMap.x + uFaceMap.y * vRest.x, uFaceMap.z + uFaceMap.w * vRest.y);
            if (at.x > 0.002 && at.x < 0.998 && at.y > 0.002 && at.y < 0.998)
                base = mix(base, laid(uFace, at, uFaceOn.w - 1.0), w * within(uFace, at, uFaceOn.w - 1.0));
        }
        // the close-up of its head saying a line (uTalkMap: where a point of the resting model is in it)
        if (w > 0.0 && uTalkOn > 0.5) {
            vec2 at = vec2(uTalkMap.x + uTalkMap.y * vRest.x, uTalkMap.z + uTalkMap.w * vRest.y);
            vec2 margin = min(at, 1.0 - at);
            float inside = smoothstep(0.0, 0.09, min(margin.x, margin.y));
            if (inside > 0.0) base = mix(base, laid(uTalk, at, 1.0), w * inside * within(uTalk, at, 1.0));
        }
    }
    vec3 color = base * mix(vec3(1.0), light, uLit);
    if (uMist.a > 0.0) color = mix(color, uMist.rgb, uMist.a * (1.0 - exp(-length(uEye - vWorld) * 0.09)));
    if (uChosen > 0.5) {    // the chosen object: its outline glows
        float rim = pow(1.0 - abs(dot(n, toEye)), 2.2);
        color = mix(color, vec3(1.0, 0.82, 0.35), rim * (0.65 + 0.35 * sin(uTime * 4.0)));
    }
    color = mix(color, uFade.rgb, uFade.a);
    o = vec4(color, uOpacity);
}`;

    /* the soft shadow under a solid object: a dark patch lying on the ground */
    const SHADOW_VERTEX = `#version 300 es
uniform mat4 uVP; uniform vec3 uAt, uAcross, uAlong; uniform float uFlip;
out vec2 vAt;
void main() {
    vAt = vec2(float(gl_VertexID & 1), float((gl_VertexID >> 1) & 1)) * 2.0 - 1.0;
    gl_Position = uVP * vec4(uAt + uAcross * vAt.x + uAlong * vAt.y, 1.0);
    gl_Position.y *= uFlip;
}`;
    const SHADOW_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vAt;
uniform float uDark;
out vec4 o;
void main() {
    float a = 1.0 - smoothstep(0.25, 1.0, length(vAt));
    o = vec4(0.0, 0.0, 0.0, a * a * uDark);
}`;

    /* Weather: rain or snow that really is in the world. Every drop has its own place in a box of air around
     * the camera and falls through it; the box goes with the camera, the drops do not, so flying forward
     * the drops come towards you and pass. Where a drop is depends only on the time, so a recording drawn
     * again shows the same rain. */
    const DROPS = 14000;
    const WEATHER_VERTEX = `#version 300 es
layout(location=0) in vec3 aSeed;
layout(location=1) in vec2 aCorner;
uniform mat4 uVP; uniform vec3 uCam, uBox, uFall;
uniform float uTime, uLong, uWide, uFlip, uSnow;
out vec2 vCorner; out float vShow;
void main() {
    float own = 0.75 + 0.5 * fract(aSeed.x * 7.13 + aSeed.z * 3.71);          // each drop at its own speed
    vec3 p = aSeed * uBox + uFall * uTime * own;
    if (uSnow > 0.5) p.xz += vec2(sin(uTime * (0.5 + aSeed.y) + aSeed.x * 40.0), cos(uTime * (0.4 + aSeed.z) + aSeed.y * 31.0)) * 0.3;
    p = uCam + mod(p - uCam + uBox * 0.5, uBox) - uBox * 0.5;
    vec3 fall = normalize(uFall), toCam = normalize(uCam - p);
    vec3 side = normalize(cross(fall, toCam));
    vec3 along = uSnow > 0.5 ? cross(toCam, side) : fall;
    vec3 q = p + along * (aCorner.y - 0.5) * uLong + side * aCorner.x * uWide;
    float far = length(p - uCam);
    vShow = smoothstep(0.3, 1.4, far) * (1.0 - smoothstep(uBox.x * 0.36, uBox.x * 0.5, far));
    vCorner = aCorner;
    gl_Position = uVP * vec4(q, 1.0);
    gl_Position.y *= uFlip;
}`;
    const WEATHER_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vCorner; in float vShow;
uniform vec4 uTint; uniform float uSnow;
out vec4 o;
void main() {
    float across = 1.0 - abs(vCorner.x), along = 1.0 - abs(vCorner.y * 2.0 - 1.0);
    float a = uSnow > 0.5 ? smoothstep(0.0, 0.7, 1.0 - length(vec2(vCorner.x, vCorner.y * 2.0 - 1.0))) : across * smoothstep(0.0, 0.3, along);
    o = vec4(uTint.rgb, uTint.a * a * vShow);
}`;

    /* the eyelids: one triangle over the whole picture, dark where the lids are */
    const LIDS_VERTEX = `#version 300 es
out vec2 vAt;
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    vAt = p * 2.0 - 1.0;
    gl_Position = vec4(vAt, 0.0, 1.0);
}`;
    const LIDS_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vAt;
uniform float uLids;
out vec4 color;
void main() {
    // the opening between the lids is an eye's shape: tallest in the middle, and wide open it is larger than the picture
    float opening = (1.0 - uLids) * 1.6 * (1.0 - 0.28 * vAt.x * vAt.x) - 0.2 * uLids;
    float soft = 0.05 + 0.1 * uLids;
    float dark = smoothstep(-soft, soft * 0.4, abs(vAt.y) - opening);
    dark = max(dark, uLids * 0.4 * smoothstep(0.35, 1.45, length(vAt)));       // the edges dim first
    color = vec4(0.0, 0.0, 0.0, dark);
}`;

    /* a picture laid over the whole view at an amount: the other shot of a dissolve */
    const OVER_FRAGMENT = `#version 300 es
precision highp float;
uniform sampler2D uTex;
uniform vec2 uViewport;
uniform float uAmount;
out vec4 color;
void main() {
    color = vec4(texture(uTex, gl_FragCoord.xy / uViewport).rgb, uAmount);
}`;

    class Sim360GL {
        constructor(canvas) {
            this.canvas = canvas;
            const gl = canvas.getContext('webgl2', { antialias: true, alpha: false, depth: true, preserveDrawingBuffer: false });
            if (!gl) throw new Error('WebGL 2 is not available in this browser');
            this.gl = gl;
            this.worlds = new Map();
            this.maxSize = gl.getParameter(gl.MAX_TEXTURE_SIZE);
            this.aniso = gl.getExtension('EXT_texture_filter_anisotropic');
            this.program = this._program(VERTEX, FRAGMENT);
            this.bakeProgram = this._program(VERTEX, BAKE_FRAGMENT);
            this.objectProgram = this._program(OBJECT_VERTEX, OBJECT_FRAGMENT);
            this.solidProgram = this._program(SOLID_VERTEX, SOLID_FRAGMENT);
            this.shadowProgram = this._program(SHADOW_VERTEX, SHADOW_FRAGMENT);
            this.models = new Map();
            this.lights = new Map();                  // world id -> the light its solid objects stand in
            this.weatherProgram = this._program(WEATHER_VERTEX, WEATHER_FRAGMENT);
            this.lidsProgram = this._program(LIDS_VERTEX, LIDS_FRAGMENT);
            this.overProgram = this._program(LIDS_VERTEX, OVER_FRAGMENT);
            this.noVertices = gl.createVertexArray();
            this.objects = new Map();
            this._mesh();
            this._sheet();
            this._drops();
            this.blankTex = this._pixel([18, 20, 28, 255]);
            this.zeroTex = this._pixel([0, 0, 0, 0]);
            this.midTex = this._pixel([128, 128, 128, 255]);
            this.selTex = null; this.selOn = false;
            this.buffers = {};
            gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
            gl.pixelStorei(gl.PACK_ALIGNMENT, 1);
        }

        _shader(type, source) {
            const gl = this.gl, shader = gl.createShader(type);
            gl.shaderSource(shader, source); gl.compileShader(shader);
            if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error('shader: ' + gl.getShaderInfoLog(shader));
            return shader;
        }

        _program(vertex, fragment) {
            const gl = this.gl, program = gl.createProgram();
            gl.attachShader(program, this._shader(gl.VERTEX_SHADER, vertex));
            gl.attachShader(program, this._shader(gl.FRAGMENT_SHADER, fragment));
            gl.linkProgram(program);
            if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('program: ' + gl.getProgramInfoLog(program));
            const uniforms = {}, count = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS);
            for (let i = 0; i < count; i++) {
                const name = gl.getActiveUniform(program, i).name.replace(/\[0\]$/, '');
                uniforms[name] = gl.getUniformLocation(program, name);
            }
            return { program, u: uniforms };
        }

        _pixel(rgba) {
            const gl = this.gl, tex = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D, tex);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(rgba));
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
            return tex;
        }

        _mesh() {
            const gl = this.gl, columns = NX + 1, rows = NY + 1;
            const uv = new Float32Array(columns * rows * 2);
            for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
                uv[(y * columns + x) * 2] = x / NX; uv[(y * columns + x) * 2 + 1] = y / NY;
            }
            const index = new Uint32Array(NX * NY * 6);
            let n = 0;
            for (let y = 0; y < NY; y++) for (let x = 0; x < NX; x++) {
                const a = y * columns + x, b = a + 1, c = a + columns, d = c + 1;
                index[n++] = a; index[n++] = c; index[n++] = b; index[n++] = b; index[n++] = c; index[n++] = d;
            }
            this.vao = gl.createVertexArray();
            gl.bindVertexArray(this.vao);
            gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
            gl.bufferData(gl.ARRAY_BUFFER, uv, gl.STATIC_DRAW);
            gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
            gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, index, gl.STATIC_DRAW);
            this.indexCount = index.length;
            this.flatDist = gl.createBuffer();
            gl.bindBuffer(gl.ARRAY_BUFFER, this.flatDist);
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(columns * rows).fill(14), gl.STATIC_DRAW);
            gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 1, gl.FLOAT, false, 0, 0);
            this.flatSteep = gl.createBuffer();
            gl.bindBuffer(gl.ARRAY_BUFFER, this.flatSteep);
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(columns * rows), gl.STATIC_DRAW);
            gl.enableVertexAttribArray(2); gl.vertexAttribPointer(2, 1, gl.FLOAT, false, 0, 0);
            gl.bindVertexArray(null);
            this.vertexCount = columns * rows;
        }

        _sheet() {
            const gl = this.gl, columns = OX + 1, rows = OY + 1;
            const uv = new Float32Array(columns * rows * 2);
            for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) { uv[(y * columns + x) * 2] = x / OX; uv[(y * columns + x) * 2 + 1] = y / OY; }
            const index = new Uint16Array(OX * OY * 6);
            let n = 0;
            for (let y = 0; y < OY; y++) for (let x = 0; x < OX; x++) {
                const a = y * columns + x, b = a + 1, c = a + columns, d = c + 1;
                index[n++] = a; index[n++] = c; index[n++] = b; index[n++] = b; index[n++] = c; index[n++] = d;
            }
            this.sheet = gl.createVertexArray();
            gl.bindVertexArray(this.sheet);
            gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
            gl.bufferData(gl.ARRAY_BUFFER, uv, gl.STATIC_DRAW);
            gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
            gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, index, gl.STATIC_DRAW);
            gl.bindVertexArray(null);
            this.sheetCount = index.length;
        }

        _drops() {
            const gl = this.gl, data = new Float32Array(DROPS * 4 * 5), index = new Uint16Array(DROPS * 6);
            // the same drops every time: a small generator of its own, not Math.random
            let s = 12345;
            const next = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
            const corners = [[-1, 0], [1, 0], [-1, 1], [1, 1]];
            for (let i = 0; i < DROPS; i++) {
                const x = next(), y = next(), z = next();
                for (let c = 0; c < 4; c++) data.set([x, y, z, corners[c][0], corners[c][1]], (i * 4 + c) * 5);
                index.set([i * 4, i * 4 + 1, i * 4 + 2, i * 4 + 1, i * 4 + 3, i * 4 + 2], i * 6);
            }
            this.drops = gl.createVertexArray();
            gl.bindVertexArray(this.drops);
            gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
            gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
            gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 20, 0);
            gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 2, gl.FLOAT, false, 20, 12);
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
            gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, index, gl.STATIC_DRAW);
            gl.bindVertexArray(null);
        }

        /* state.weather: {kind: 'rain' | 'snow', amount 0..1, wind -1..1} */
        _drawWeather(state, flip, vp) {
            const w = state.weather;
            if (!w || !w.kind || !(w.amount > 0.003)) return;
            const gl = this.gl, P = this.weatherProgram, u = P.u, snow = w.kind === 'snow', wind = +w.wind || 0;
            gl.useProgram(P.program);
            gl.enable(gl.BLEND); gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
            gl.depthMask(false);
            gl.uniformMatrix4fv(u.uVP, false, vp);
            gl.uniform3fv(u.uCam, state.cam.pos);
            gl.uniform3f(u.uBox, 14, 10, 14);
            gl.uniform3f(u.uFall, wind * (snow ? 1.2 : 4.5), snow ? -1.1 : -9.5, wind * (snow ? 0.4 : 1.2));
            gl.uniform1f(u.uTime, (state.time || 0) % 600);            // small numbers: the graphics card counts in 32 bits
            gl.uniform1f(u.uLong, snow ? 0.05 : 0.5); gl.uniform1f(u.uWide, snow ? 0.025 : 0.011);
            gl.uniform1f(u.uFlip, flip ? -1 : 1);
            gl.uniform1f(u.uSnow, snow ? 1 : 0);
            gl.uniform4f(u.uTint, snow ? 0.97 : 0.8, snow ? 0.98 : 0.86, 1, snow ? 0.9 : 0.42);
            gl.bindVertexArray(this.drops);
            gl.drawElements(gl.TRIANGLES, Math.round(DROPS * Core.clamp(w.amount, 0, 1)) * 6, gl.UNSIGNED_SHORT, 0);
            gl.bindVertexArray(null);
            gl.depthMask(true); gl.disable(gl.BLEND);
        }

        /* ---- objects ------------------------------------------------------------------------------------ */

        _texture(tex, source, mip) {
            const gl = this.gl;
            gl.bindTexture(gl.TEXTURE_2D, tex);
            gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
            gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
            if (mip) { gl.generateMipmap(gl.TEXTURE_2D); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR); }
            else gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        }

        /* An object's picture (an image with its own transparency, or a playing video whose right half is
         * the transparency) and its shape map (or null: a flat sheet). */
        putObject(id, picture, shape, isVideo) {
            const gl = this.gl;
            let o = this.objects.get(id);
            if (!o) { o = { tex: gl.createTexture(), shape: null, video: null, videoTime: -1 }; this.objects.set(id, o); }
            this._texture(o.tex, picture, !isVideo);
            o.video = isVideo ? picture : null;
            o.videoTime = isVideo ? picture.currentTime : -1;
            if (shape) { if (!o.shape) o.shape = gl.createTexture(); this._texture(o.shape, shape, false); }
            else if (o.shape) { gl.deleteTexture(o.shape); o.shape = null; }
            return o;
        }

        /* the picture an object wears over its model (repeating), or null to take it off */
        putSkin(id, picture) {
            const gl = this.gl, o = this.objects.get(id);
            if (!o) return;
            if (o.skin) { gl.deleteTexture(o.skin); o.skin = null; }
            if (!picture) return;
            o.skin = gl.createTexture();
            this._texture(o.skin, picture, true);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
            if (this.aniso) gl.texParameterf(gl.TEXTURE_2D, this.aniso.TEXTURE_MAX_ANISOTROPY_EXT, 8);
        }

        dropObject(id) {
            this.dropModel(id);
            const gl = this.gl, o = this.objects.get(id);
            if (!o) return;
            if (o.skin) gl.deleteTexture(o.skin);
            gl.deleteTexture(o.tex);
            if (o.shape) gl.deleteTexture(o.shape);
            if (o.talk) gl.deleteTexture(o.talk);
            this.objects.delete(id);
        }

        /* A solid object's model (what Sim360Solid.read made of a .glb) and its pictures, already decoded
         * (ImageBitmaps, or nulls). */
        putModel(id, model, pictures) {
            const gl = this.gl;
            this.dropModel(id);
            const m = { vao: gl.createVertexArray(), buffers: [], textures: [], parts: model.parts, count: model.index.length, bones: false, boneRows: 0, boneTex: null, corners: model.corners };
            gl.bindVertexArray(m.vao);
            const feed = (location, data, width) => {
                const buffer = gl.createBuffer();
                gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
                gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
                gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, width, gl.FLOAT, false, 0, 0);
                m.buffers.push(buffer);
                return buffer;
            };
            feed(0, model.position, 3); feed(1, model.normal, 3); feed(2, model.uv, 2);
            m.boneBuffer = feed(3, new Float32Array(model.corners * 4), 4);
            m.weightBuffer = feed(4, new Float32Array(model.corners * 4), 4);
            const index = gl.createBuffer();
            gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, index);
            gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, model.index, gl.STATIC_DRAW);
            m.buffers.push(index);
            gl.bindVertexArray(null);
            for (const picture of pictures || []) {
                if (!picture) { m.textures.push(null); continue; }
                const tex = gl.createTexture();
                this._texture(tex, picture, true);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
                if (this.aniso) gl.texParameterf(gl.TEXTURE_2D, this.aniso.TEXTURE_MAX_ANISOTROPY_EXT, 4);
                m.textures.push(tex);
            }
            this.models.set(id, m);
            if (model.bone && model.weight) this.setBones(id, model.bone, model.weight);
            return m;
        }

        /* which bones each corner of a model hangs on (four to a corner) and how much each pulls; nulls: none */
        setBones(id, bone, weight) {
            const gl = this.gl, m = this.models.get(id);
            if (!m) return;
            m.bones = !!(bone && weight);
            if (!m.bones) return;
            gl.bindBuffer(gl.ARRAY_BUFFER, m.boneBuffer); gl.bufferData(gl.ARRAY_BUFFER, Float32Array.from(bone), gl.STATIC_DRAW);
            gl.bindBuffer(gl.ARRAY_BUFFER, m.weightBuffer); gl.bufferData(gl.ARRAY_BUFFER, weight, gl.STATIC_DRAW);
        }

        dropModel(id) {
            const gl = this.gl, m = this.models.get(id);
            if (!m) return;
            gl.deleteVertexArray(m.vao);
            for (const buffer of m.buffers) gl.deleteBuffer(buffer);
            for (const tex of m.textures) if (tex) gl.deleteTexture(tex);
            if (m.boneTex) gl.deleteTexture(m.boneTex);
            this.models.delete(id);
        }

        /* item.solid: {model (16 numbers: where the model stands, how large, which way), bones (16 numbers a
         * bone, or null), lit 0..1, shadow: {at, across, along, dark} or null}; state.light: {sky, ground, sun,
         * sunColour} */
        _drawSolid(item, state, flip, vp) {
            const gl = this.gl, solid = item.solid, m = this.models.get(solid.key || item.id);       // (copies of an object share one model)
            if (!m) return;
            const fade = state.fade || [0, 0, 0, 0], over = !!state.over;
            if (solid.shadow && solid.shadow.dark > 0.003) {
                const S = this.shadowProgram;
                gl.useProgram(S.program);
                gl.enable(gl.BLEND); gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
                gl.depthMask(false);
                if (over) gl.disable(gl.DEPTH_TEST);
                gl.uniformMatrix4fv(S.u.uVP, false, vp); gl.uniform1f(S.u.uFlip, flip ? -1 : 1);
                gl.uniform3fv(S.u.uAt, solid.shadow.at); gl.uniform3fv(S.u.uAcross, solid.shadow.across); gl.uniform3fv(S.u.uAlong, solid.shadow.along);
                gl.uniform1f(S.u.uDark, solid.shadow.dark * item.opacity * (1 - fade[3]));
                gl.bindVertexArray(this.noVertices);
                gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
                gl.depthMask(true);
            }
            const P = this.solidProgram, u = P.u, light = state.light || this.lights.get(state.world) || {};
            gl.useProgram(P.program);
            gl.enable(gl.DEPTH_TEST);
            // "never hidden by the world": the model is drawn in a thin slice of depth nearer than anything
            // of the world, in which its own front still hides its own back
            if (over) gl.depthRange(0, 0.02);
            gl.uniformMatrix4fv(u.uVP, false, vp); gl.uniformMatrix4fv(u.uModel, false, solid.model);
            gl.uniform1f(u.uFlip, flip ? -1 : 1); gl.uniform1f(u.uTime, state.time || 0);
            gl.uniform3fv(u.uEye, state.cam.pos);
            gl.uniform4f(u.uFade, fade[0], fade[1], fade[2], fade[3]);
            gl.uniform4f(u.uMist, 0.62, 0.66, 0.72, Core.clamp(state.weather && state.weather.mist > 0 ? state.weather.mist : 0, 0, 1) * 0.85);
            gl.uniform3fv(u.uSky, light.sky || [1, 1, 1]); gl.uniform3fv(u.uGround, light.ground || [0.62, 0.62, 0.62]);
            gl.uniform3fv(u.uSun, light.sun || [0.35, 0.8, 0.48]); gl.uniform3fv(u.uSunColour, light.sunColour || [0.15, 0.15, 0.15]);
            gl.uniform1f(u.uLit, solid.lit == null ? 0.8 : solid.lit);
            gl.uniform1f(u.uNear, solid.near || 0);
            gl.uniform3fv(u.uTone, solid.tone || [1, 1, 1]);
            // its face: its own picture, or the video of the line it is saying
            const sheet = this.objects.get(item.id), face = solid.face && sheet ? solid.face : null;
            let faceTex = null, kind = 0, talkTex = null;
            if (face) {
                const says = item.speech && item.speech.readyState >= 2 ? item.speech : null;
                if (says) {
                    if (!sheet.talk) { sheet.talk = gl.createTexture(); sheet.talkOf = null; }
                    if (sheet.talkOf !== says || says.currentTime !== sheet.talkTime) { this._texture(sheet.talk, says, false); sheet.talkOf = says; sheet.talkTime = says.currentTime; }
                }
                // a line's video is of the whole picture and takes its place, or of the head only (face.talk)
                // and lies over it
                if (says && !face.talk) { faceTex = sheet.talk; kind = 2; }
                else if (!face.speaking) { faceTex = sheet.tex; kind = sheet.video ? 2 : 1; }
                if (says && face.talk) talkTex = sheet.talk;
            }
            this._bind(2, faceTex || this.zeroTex); gl.uniform1i(u.uFace, 2);
            this._bind(3, talkTex || this.zeroTex); gl.uniform1i(u.uTalk, 3);
            if (faceTex || talkTex) { gl.uniform4fv(u.uFaceMap, face.map); gl.uniform4f(u.uFaceOn, face.front < 0 ? -1 : 1, face.from, face.soft, kind); }
            else gl.uniform4f(u.uFaceOn, 0, 0, 1, 0);
            gl.uniform1f(u.uTalkOn, talkTex ? 1 : 0);
            if (talkTex) gl.uniform4fv(u.uTalkMap, face.talk);
            gl.uniform1f(u.uOpacity, item.opacity); gl.uniform1f(u.uChosen, item.chosen ? 1 : 0);
            const boned = m.bones && solid.bones;
            if (boned) {
                const rows = solid.bones.length / 16;
                if (!m.boneTex) {
                    m.boneTex = gl.createTexture();
                    gl.bindTexture(gl.TEXTURE_2D, m.boneTex);
                    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
                    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
                }
                this._bind(1, m.boneTex);
                if (m.boneRows !== rows) { gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, 4, rows, 0, gl.RGBA, gl.FLOAT, solid.bones); m.boneRows = rows; }
                else gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 4, rows, gl.RGBA, gl.FLOAT, solid.bones);
            } else this._bind(1, this.zeroTex);
            gl.uniform1i(u.uBoneTex, 1); gl.uniform1f(u.uBones, boned ? 1 : 0);
            gl.uniform1i(u.uPicture, 0);
            gl.bindVertexArray(m.vao);
            // an object may wear a picture of its own over its model (a material), and have a colour of its own
            const skin = sheet && sheet.skin && solid.skin ? sheet.skin : null;
            gl.uniform1f(u.uWrap, skin ? solid.skin : 1);
            const draw = () => {
                for (const part of m.parts) {
                    const tex = skin || (part.picture >= 0 ? m.textures[part.picture] : null);
                    this._bind(0, tex || this.blankTex);
                    gl.uniform1f(u.uHasPicture, tex ? 1 : 0);
                    gl.uniform4fv(u.uColour, solid.colour && !skin ? solid.colour : part.colour);
                    gl.uniform1f(u.uCut, part.cut || 0);
                    gl.drawElements(gl.TRIANGLES, part.count, gl.UNSIGNED_INT, part.start * 4);
                }
            };
            if (item.opacity < 0.995) {
                // fading in or out: first only its nearest surface is found, then that is drawn, so its inside never shows through
                gl.colorMask(false, false, false, false); gl.disable(gl.BLEND);
                draw();
                gl.colorMask(true, true, true, true); gl.depthFunc(gl.LEQUAL);
                gl.enable(gl.BLEND); gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
                draw();
                gl.depthFunc(gl.LESS);
            } else { gl.disable(gl.BLEND); draw(); }
            if (over) gl.depthRange(0, 1);
        }

        /* A model by itself, seen from its front, as pixels with transparency around it (RGBA, top row
         * first): the picture an uploaded model is listed with. `low`, `high`: the corners of its box;
         * `turn`: degrees it is turned round; `lit`: how much light is on it (0: its own colours). */
        portrait(id, low, high, width, height, turn, lit, colour) {
            const gl = this.gl;
            this._buffer('portrait', width, height);
            const tall = high[1] - low[1] || 1, wide = Math.max(high[0] - low[0], 1e-3), aspect = width / height;
            const fov = 16, reach = Math.max(tall, wide / aspect) * 0.5 * 1.12 / Math.tan(fov * Core.RAD / 2);
            const cam = { pos: [0, 0, reach + (high[2] - low[2]) / 2], yaw: 0, pitch: 0, roll: 0, fov };
            const c = Math.cos((turn || 0) * Core.RAD), s = Math.sin((turn || 0) * Core.RAD), mx = (low[0] + high[0]) / 2, my = (low[1] + high[1]) / 2, mz = (low[2] + high[2]) / 2;
            const model = new Float32Array([c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, -(c * mx + s * mz), -my, s * mx - c * mz, 1]);
            gl.viewport(0, 0, width, height);
            gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
            gl.enable(gl.DEPTH_TEST); gl.disable(gl.CULL_FACE);
            this._drawSolid({ id, opacity: 1, solid: { model, bones: null, lit: lit == null ? 0.55 : lit, shadow: null, colour: colour || null } }, { cam, time: 0 }, true, Core.viewProjection(cam, aspect, null, reach * 0.05, reach * 4));
            gl.bindVertexArray(null);
            const pixels = new Uint8Array(width * height * 4);
            gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
            gl.bindFramebuffer(gl.FRAMEBUFFER, null);
            gl.clearColor(0, 0, 0, 1);
            return pixels;
        }

        /* the objects of the world being drawn, the farthest first: {id, anchor, right, up, normal, width,
         * height, thick, opacity, mirror, chosen}; one that has `solid` is drawn as its model */
        _drawObjects(state, flip, vp) {
            const list = state.objects;
            if (!list || !list.length) return;
            const gl = this.gl, P = this.objectProgram, u = P.u, fade = state.fade || [0, 0, 0, 0];
            gl.useProgram(P.program);
            gl.enable(gl.BLEND);
            gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
            gl.uniformMatrix4fv(u.uVP, false, vp);
            gl.uniform1f(u.uFlip, flip ? -1 : 1);
            gl.uniform1f(u.uTime, state.time || 0);
            gl.uniform4f(u.uFade, fade[0], fade[1], fade[2], fade[3]);
            gl.uniform3fv(u.uEye, state.cam.pos);
            gl.uniform4f(u.uMist, 0.62, 0.66, 0.72, Core.clamp(state.weather && state.weather.mist > 0 ? state.weather.mist : 0, 0, 1) * 0.85);
            // objects may be told to show even where the world's surface is nearer than they are (people far
            // down a street: the panorama's own depth is seldom as deep as the street looks)
            if (state.over) gl.disable(gl.DEPTH_TEST);
            const sheets = () => {
                // (again after every solid object, which is drawn by a program of its own)
                gl.useProgram(P.program);
                gl.enable(gl.BLEND);
                gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
                if (state.over) gl.disable(gl.DEPTH_TEST);
                gl.bindVertexArray(this.sheet);
            };
            gl.bindVertexArray(this.sheet);
            for (const item of list) {
                if (item.solid) {
                    if (item.opacity > 0.003 && this.models.has(item.solid.key || item.id)) { this._drawSolid(item, state, flip, vp); sheets(); }
                    continue;
                }
                const o = this.objects.get(item.id);
                if (!o || item.opacity <= 0.003) continue;
                // an object that is saying a line shows that line's video in place of its own picture
                const says = item.speech && item.speech.readyState >= 2 ? item.speech : null;
                if (says) {
                    if (!o.talk) { o.talk = gl.createTexture(); o.talkOf = null; }
                    if (o.talkOf !== says || says.currentTime !== o.talkTime) {
                        this._texture(o.talk, says, false);
                        o.talkOf = says; o.talkTime = says.currentTime;
                    }
                } else if (o.video && o.video.readyState >= 2 && o.video.currentTime !== o.videoTime) {
                    gl.bindTexture(gl.TEXTURE_2D, o.tex);
                    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
                    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, o.video);
                    o.videoTime = o.video.currentTime;
                }
                this._bind(0, says ? o.talk : o.tex); gl.uniform1i(u.uPicture, 0);
                this._bind(1, o.shape || this.midTex); gl.uniform1i(u.uShape, 1);
                gl.uniform3fv(u.uAnchor, item.anchor); gl.uniform3fv(u.uRight, item.right);
                gl.uniform3fv(u.uUp, item.up); gl.uniform3fv(u.uNormal, item.normal);
                gl.uniform2f(u.uSize, item.width, item.height);
                gl.uniform1f(u.uThick, item.thick || 0);
                const gait = item.gait;
                gl.uniform4f(u.uGait, gait ? gait.phase : 0, gait ? gait.strength : 0, gait ? gait.across : 0, 0);
                if (gait) gl.uniform3fv(u.uBody, gait.body);
                gl.uniform1f(u.uOpacity, item.opacity);
                gl.uniform1f(u.uMirror, item.mirror ? 1 : 0);
                gl.uniform1f(u.uPacked, says || o.video ? 1 : 0);
                gl.uniform1f(u.uChosen, item.chosen ? 1 : 0);
                gl.drawElements(gl.TRIANGLES, this.sheetCount, gl.UNSIGNED_SHORT, 0);
            }
            gl.bindVertexArray(null);
            gl.disable(gl.BLEND);
            if (state.over) gl.enable(gl.DEPTH_TEST);
        }

        /* the world's own distances (and how steep the surface is at each corner) for the next draw */
        _shape(w) {
            const gl = this.gl;
            gl.bindBuffer(gl.ARRAY_BUFFER, w && w.dist ? w.dist : this.flatDist);
            gl.vertexAttribPointer(1, 1, gl.FLOAT, false, 0, 0);
            gl.bindBuffer(gl.ARRAY_BUFFER, w && w.steep ? w.steep : this.flatSteep);
            gl.vertexAttribPointer(2, 1, gl.FLOAT, false, 0, 0);
        }

        /* ---- worlds ------------------------------------------------------------------------------------- */

        world(id) { return this.worlds.get(id); }

        _ensure(id) {
            let w = this.worlds.get(id);
            if (!w) { w = { id, tex: null, width: 0, height: 0, video: null, videoTime: -1, dist: null, masks: [null, null], maskOn: false }; this.worlds.set(id, w); }
            return w;
        }

        /* The picture of a world: an image, a canvas, an ImageBitmap or a playing video. */
        putPicture(id, source, isVideo) {
            const gl = this.gl, w = this._ensure(id);
            if (!w.tex) w.tex = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D, w.tex);
            gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
            if (isVideo) gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
            else {
                gl.generateMipmap(gl.TEXTURE_2D);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
                if (this.aniso) gl.texParameterf(gl.TEXTURE_2D, this.aniso.TEXTURE_MAX_ANISOTROPY_EXT, 8);
            }
            w.video = isVideo ? source : null;
            w.videoTime = isVideo ? source.currentTime : -1;
            w.width = source.videoWidth || source.naturalWidth || source.width;
            w.height = source.videoHeight || source.naturalHeight || source.height;
            return w;
        }

        /* Pixels straight into a world's picture (RGBA, top row first): a preview that is not saved yet. */
        putPixels(id, pixels, width, height) {
            const gl = this.gl, w = this._ensure(id);
            if (!w.tex) w.tex = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D, w.tex);
            gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
            gl.generateMipmap(gl.TEXTURE_2D);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
            w.video = null; w.width = width; w.height = height;
            return w;
        }

        /* How far every corner of the sphere is, from the world's depth (a Core.DepthField). */
        setDepth(id, field) {
            const gl = this.gl, w = this._ensure(id), columns = NX + 1, rows = NY + 1;
            const dist = new Float32Array(columns * rows);
            for (let y = 0; y < rows; y++) {
                const v = y / NY;
                for (let x = 0; x < columns; x++) dist[y * columns + x] = field.distAt(x / NX, v);
            }
            if (!w.dist) w.dist = gl.createBuffer();
            gl.bindBuffer(gl.ARRAY_BUFFER, w.dist);
            gl.bufferData(gl.ARRAY_BUFFER, dist, gl.DYNAMIC_DRAW);
            // how steep the surface is at each corner: the jump to its neighbours, as a share of its distance
            const steep = new Float32Array(columns * rows);
            for (let y = 0; y < rows; y++) {
                const up = Math.max(0, y - 1) * columns, down = Math.min(rows - 1, y + 1) * columns, here = y * columns;
                for (let x = 0; x < columns; x++) {
                    const left = here + (x === 0 ? NX - 1 : x - 1), right = here + (x === NX ? 1 : x + 1);
                    const a = dist[left], b = dist[right], c = dist[up + x], d = dist[down + x], e = dist[here + x];
                    const low = Math.min(a, b, c, d, e), high = Math.max(a, b, c, d, e);
                    steep[here + x] = (high - low) / low;
                }
            }
            if (!w.steep) w.steep = gl.createBuffer();
            gl.bindBuffer(gl.ARRAY_BUFFER, w.steep);
            gl.bufferData(gl.ARRAY_BUFFER, steep, gl.DYNAMIC_DRAW);
        }

        dropWorld(id) {
            const gl = this.gl, w = this.worlds.get(id);
            if (!w) return;
            if (w.tex) gl.deleteTexture(w.tex);
            if (w.dist) gl.deleteBuffer(w.dist);
            if (w.steep) gl.deleteBuffer(w.steep);
            for (const m of w.masks) if (m) gl.deleteTexture(m);
            this.worlds.delete(id);
        }

        /* The masks of a world's portals: up to eight arrays of width x height bytes (or nulls). */
        setPortalMasks(id, masks, width, height) {
            const gl = this.gl, w = this._ensure(id);
            w.maskOn = false;
            for (let t = 0; t < 2; t++) {
                const four = masks.slice(t * 4, t * 4 + 4);
                if (!four.some(Boolean)) { if (w.masks[t]) { gl.deleteTexture(w.masks[t]); w.masks[t] = null; } continue; }
                const packed = new Uint8Array(width * height * 4);
                four.forEach((mask, c) => { if (mask) for (let i = 0, n = width * height; i < n; i++) packed[i * 4 + c] = mask[i]; });
                if (!w.masks[t]) w.masks[t] = gl.createTexture();
                gl.bindTexture(gl.TEXTURE_2D, w.masks[t]);
                gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
                gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, packed);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
                w.maskOn = true;
            }
        }

        /* What is selected right now: width x height bytes, or null for nothing. */
        setSelection(data, width, height) {
            const gl = this.gl;
            this.selOn = !!data;
            if (!data) return;
            if (!this.selTex) this.selTex = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D, this.selTex);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, width, height, 0, gl.RED, gl.UNSIGNED_BYTE, data);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        }

        _freshVideo(w) {
            const video = w && w.video;
            if (!video || video.readyState < 2 || video.currentTime === w.videoTime) return;
            const gl = this.gl;
            gl.bindTexture(gl.TEXTURE_2D, w.tex);
            gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
            w.videoTime = video.currentTime;
        }

        /* ---- drawing ------------------------------------------------------------------------------------ */

        _bind(unit, tex) { const gl = this.gl; gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, tex); }

        /* state.through: [{index, state, weight}], the portals (by their place in state.portals) whose
         * worlds are drawn in their depth, and what to draw of each. Up to two of them, and behind each of
         * those one more, and one more behind that: a street seen through a street seen through a street.
         * `weight` (1 when it is not said) is how far a portal is on its way from flat to deep. `fbo` is
         * where this picture goes. */
        _draw(state, width, height, flip, fbo, level) {
            const gl = this.gl, P = this.program, u = P.u, w = this.worlds.get(state.world);
            const cam = state.cam, aspect = state.aspect || width / height;
            level = level || 0;
            // state.blend: {state, amount}, the other shot of a dissolve. It is drawn first, into a picture of its
            // own, and laid over this one at the end.
            let over = null;
            if (!level && state.blend && state.blend.state && state.blend.amount > 0.002 && this.worlds.get(state.blend.state.world)) {
                const buffer = this._buffer('blend', width, height);
                this._draw(Object.assign({}, state.blend.state, { aspect, crop: state.crop, blend: null }), width, height, flip, buffer.fbo, 0);
                over = { tex: buffer.color, amount: Core.clamp(state.blend.amount, 0, 1) };
            }
            const inner = [];
            if (level < 3 && state.through && state.output !== 'selection') {
                for (const through of state.through.slice(0, level ? 1 : 2)) {
                    if (!through || !through.state || !this.worlds.get(through.state.world)) continue;
                    const buffer = this._buffer('inner' + level + inner.length, width, height);
                    this._draw(Object.assign({}, through.state, { aspect, crop: state.crop }), width, height, flip, buffer.fbo, level + 1);
                    inner.push({ index: through.index, tex: buffer.color, weight: through.weight == null ? 1 : Core.clamp(through.weight, 0, 1) });
                }
            }
            gl.bindFramebuffer(gl.FRAMEBUFFER, fbo || null);
            gl.viewport(0, 0, width, height);
            gl.clearColor(0, 0, 0, 1);
            gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
            gl.enable(gl.DEPTH_TEST); gl.disable(gl.CULL_FACE); gl.disable(gl.BLEND);
            gl.useProgram(P.program);
            const vp = Core.viewProjection(cam, aspect, state.crop);
            gl.uniformMatrix4fv(u.uVP, false, vp);
            gl.uniform3fv(u.uCam, cam.pos);
            gl.uniform1f(u.uRelief, state.relief == null ? 1 : state.relief);
            gl.uniform1f(u.uFlip, flip ? -1 : 1);
            gl.uniform1f(u.uBake, 0);
            gl.uniform1f(u.uOutput, state.output === 'selection' ? 1 : 0);
            gl.uniform1f(u.uTime, state.time || 0);
            gl.uniform1f(u.uGuide, state.guide || 0);
            this._freshVideo(w);
            this._bind(0, w && w.tex ? w.tex : this.blankTex);
            gl.uniform1i(u.uPano, 0);
            gl.uniform1f(u.uPanoOn, w && w.tex ? 1 : 0);
            // portals
            const portals = state.portals || [], see = new Float32Array(PORTALS), turn = new Float32Array(PORTALS), tint = new Float32Array(PORTALS * 3), deep = new Float32Array(PORTALS), deepAmt = new Float32Array(PORTALS);
            inner.forEach((x, slot) => { if (x.index >= 0 && x.index < PORTALS) { deep[x.index] = slot + 1; deepAmt[x.index] = x.weight; } });
            this._bind(13, inner[0] ? inner[0].tex : this.blankTex); this._bind(14, inner[1] ? inner[1].tex : this.blankTex);
            gl.uniform1i(u.uInner0, 13); gl.uniform1i(u.uInner1, 14);
            gl.uniform2f(u.uViewport, width, height);
            const masked = !!(w && w.maskOn && portals.length);
            this._bind(1, masked && w.masks[0] ? w.masks[0] : this.zeroTex);
            this._bind(2, masked && w.masks[1] ? w.masks[1] : this.zeroTex);
            gl.uniform1i(u.uMask0, 1); gl.uniform1i(u.uMask1, 2);
            for (let i = 0; i < PORTALS; i++) {
                const portal = portals[i], dest = portal && this.worlds.get(portal.world);
                const open = portal && dest && dest.tex && portal.see > 0;
                if (open) this._freshVideo(dest);
                this._bind(5 + i, open ? dest.tex : this.blankTex);
                gl.uniform1i(u['uDest' + i], 5 + i);
                see[i] = open ? portal.see : 0;
                turn[i] = portal ? (portal.turn || 0) * Core.RAD : 0;
                const c = portal && portal.tint || [0.2, 0.8, 1];
                tint[i * 3] = c[0]; tint[i * 3 + 1] = c[1]; tint[i * 3 + 2] = c[2];
            }
            gl.uniform1fv(u.uSee, see); gl.uniform1fv(u.uTurn, turn); gl.uniform3fv(u.uTint, tint); gl.uniform1fv(u.uDeep, deep); gl.uniform1fv(u.uDeepAmt, deepAmt);
            gl.uniform1f(u.uMaskOn, masked ? 1 : 0);
            // selection
            const showSel = (state.selection || state.output === 'selection') && this.selOn;
            this._bind(3, showSel ? this.selTex : this.zeroTex);
            gl.uniform1i(u.uSel, 3);
            gl.uniform1f(u.uSelOn, showSel && state.output !== 'selection' ? 1 : 0);
            // the world that is taking over (going through a portal, or a fade)
            const mix = state.mix, other = mix && this.worlds.get(mix.world);
            if (other) this._freshVideo(other);
            this._bind(4, other && other.tex ? other.tex : this.blankTex);
            gl.uniform1i(u.uMixTex, 4);
            gl.uniform1f(u.uMixAmt, other && other.tex ? Core.clamp(mix.amount, 0, 1) : 0);
            gl.uniform1f(u.uMixTurn, mix ? (mix.turn || 0) * Core.RAD : 0);
            // the world it goes into may be one that is drawn in its depth behind its portal: then that picture takes over
            const slot = mix && mix.deep ? inner.findIndex(x => x.index === mix.index) + 1 : 0;
            gl.uniform1f(u.uMixDeep, slot);
            gl.uniform3fv(u.uMixDir, mix && mix.dir ? mix.dir : Core.dirFromAngles(cam.yaw, cam.pitch));
            const fade = state.fade || [0, 0, 0, 0];
            gl.uniform4f(u.uFade, fade[0], fade[1], fade[2], fade[3]);
            const mist = state.weather && state.weather.mist > 0 ? state.weather.mist : 0;
            gl.uniform4f(u.uMist, 0.62, 0.66, 0.72, Core.clamp(mist, 0, 1) * 0.85);

            gl.bindVertexArray(this.vao);
            this._shape(w);
            gl.drawElements(gl.TRIANGLES, this.indexCount, gl.UNSIGNED_INT, 0);
            gl.bindVertexArray(null);
            if (state.output !== 'selection') { this._drawObjects(state, flip, vp); this._drawWeather(state, flip, vp); if (!level) this._drawLids(state); }
            if (over) this._drawOver(over, width, height);
        }

        _drawOver(over, width, height) {
            const gl = this.gl, P = this.overProgram;
            gl.useProgram(P.program);
            gl.disable(gl.DEPTH_TEST); gl.depthMask(false);
            gl.enable(gl.BLEND); gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
            this._bind(0, over.tex); gl.uniform1i(P.u.uTex, 0);
            gl.uniform2f(P.u.uViewport, width, height); gl.uniform1f(P.u.uAmount, over.amount);
            gl.bindVertexArray(this.noVertices);
            gl.drawArrays(gl.TRIANGLES, 0, 3);
            gl.bindVertexArray(null);
            gl.disable(gl.BLEND); gl.depthMask(true); gl.enable(gl.DEPTH_TEST);
        }

        /* state.lids: 0 the eyes are open, 1 they are shut. Two lids come together over the picture. */
        _drawLids(state) {
            const lids = Core.clamp(+state.lids || 0, 0, 1);
            if (lids < 0.003) return;
            const gl = this.gl, P = this.lidsProgram;
            gl.useProgram(P.program);
            gl.disable(gl.DEPTH_TEST); gl.depthMask(false);
            gl.enable(gl.BLEND); gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
            gl.uniform1f(P.u.uLids, lids);
            gl.bindVertexArray(this.noVertices);
            gl.drawArrays(gl.TRIANGLES, 0, 3);
            gl.bindVertexArray(null);
            gl.disable(gl.BLEND); gl.depthMask(true); gl.enable(gl.DEPTH_TEST);
        }

        resize() {
            const canvas = this.canvas, ratio = Math.min(window.devicePixelRatio || 1, 2);
            const width = Math.max(2, Math.round(canvas.clientWidth * ratio)), height = Math.max(2, Math.round(canvas.clientHeight * ratio));
            if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
        }

        render(state) {
            const gl = this.gl;
            this.resize();
            this._draw(state, this.canvas.width, this.canvas.height, false, null);
        }

        /* a picture to draw into, kept by name: the one a capture is read from, and one for every world that
         * is drawn behind a portal */
        _buffer(name, width, height) {
            const gl = this.gl;
            this.buffers = this.buffers || {};
            let t = this.buffers[name];
            if (!t) {
                t = this.buffers[name] = { fbo: gl.createFramebuffer(), color: gl.createTexture(), depth: gl.createRenderbuffer(), width: 0, height: 0 };
            }
            if (t.width !== width || t.height !== height) {
                gl.bindTexture(gl.TEXTURE_2D, t.color);
                gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
                gl.bindRenderbuffer(gl.RENDERBUFFER, t.depth);
                gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT24, width, height);
                gl.bindFramebuffer(gl.FRAMEBUFFER, t.fbo);
                gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t.color, 0);
                gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, t.depth);
                if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new Error(`cannot draw at ${width}x${height}`);
                t.width = width; t.height = height;
            }
            gl.bindFramebuffer(gl.FRAMEBUFFER, t.fbo);
            return t;
        }

        _target(width, height) { return this._buffer('target', width, height); }

        /* The view as pixels: RGBA, top row first. `into` may be an array to reuse. */
        capture(state, width, height, into) {
            const gl = this.gl;
            if (Math.max(width, height) > this.maxSize) throw new Error(`this graphics card draws up to ${this.maxSize} pixels a side`);
            const target = this._target(width, height);
            this._draw(state, width, height, true, target.fbo);
            const pixels = into && into.length === width * height * 4 ? into : new Uint8Array(width * height * 4);
            gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
            gl.bindFramebuffer(gl.FRAMEBUFFER, null);
            return pixels;
        }

        /* Lays a flat picture, as the camera of `state` sees it, back onto the panorama.
         * With `ontoWorld` the answer is that world's whole panorama with the picture painted in where the
         * picture is opaque (RGBA, top row first). Without it the answer is only where the picture landed:
         * its opacity, in every colour. */
        bake(state, source, width, height, ontoWorld) {
            const gl = this.gl, P = this.bakeProgram, u = P.u, w = this.worlds.get(state.world);
            if (Math.max(width, height) > this.maxSize) throw new Error(`this graphics card draws up to ${this.maxSize} pixels a side`);
            const src = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D, src);
            gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
            gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            this._target(width, height);
            gl.viewport(0, 0, width, height);
            gl.disable(gl.DEPTH_TEST); gl.disable(gl.CULL_FACE); gl.disable(gl.BLEND);
            gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
            gl.useProgram(P.program);
            gl.uniform1f(u.uBake, 1); gl.uniform1f(u.uRelief, 1); gl.uniform1f(u.uFlip, 1);
            gl.uniform3fv(u.uCam, state.cam.pos);
            gl.uniformMatrix4fv(u.uView, false, Core.viewProjection(state.cam, state.aspect, state.crop));
            this._bind(0, src); gl.uniform1i(u.uSrc, 0);
            const base = ontoWorld && this.worlds.get(ontoWorld);
            this._bind(1, base && base.tex ? base.tex : this.blankTex); gl.uniform1i(u.uBase, 1);
            gl.uniform1f(u.uUseBase, base && base.tex ? 1 : 0);
            gl.bindVertexArray(this.vao);
            this._shape(w);
            gl.drawElements(gl.TRIANGLES, this.indexCount, gl.UNSIGNED_INT, 0);
            gl.bindVertexArray(null);
            const pixels = new Uint8Array(width * height * 4);
            gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
            gl.bindFramebuffer(gl.FRAMEBUFFER, null);
            gl.deleteTexture(src);
            return pixels;
        }
    }

    Sim360GL.PORTALS = PORTALS;
    window.Sim360GL = Sim360GL;
})();
