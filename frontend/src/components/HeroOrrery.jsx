import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";

import RenderGovernor from "./RenderGovernor";
import useMediaQuery, { COMPACT_QUERY, REDUCED_MOTION_QUERY } from "../hooks/useMediaQuery";

/* ---------------------------------------------------------------------------
 * The landing page's orrery.
 *
 * "World in Motion" is, near enough, the definition of an orrery: a brass model
 * of bodies held in relation to each other, turning. A force-directed graph is
 * the same idea with the arithmetic done live, so the hero is the product's own
 * metaphor rather than an abstract shape.
 *
 * Two things about this scene are unusual enough to be worth stating, because
 * both are easy to "fix" back into being wrong:
 *
 *   1. It is LIT AND LIGHT. Almost every WebGL hero is white-on-black because
 *      that is the forgiving case — emissive material on a dark background needs
 *      no lighting design at all. Here the background is the page's own tan and
 *      the mechanism has to be lit like a real object, which is the entire visual
 *      idea and the reason the ambient term is unusually high.
 *
 *   2. metalness is 0.35–0.55, not 1.0. A fully metallic PBR material derives
 *      essentially all of its colour from the environment map, and this project
 *      has no environment map — drei's <Environment> downloads an HDR, which does
 *      not work offline and would need a CDN in the CSP. At metalness 1 with no
 *      envMap, brass renders black. Partial metalness keeps a diffuse term from
 *      the lights while the directional highlights still read as polished metal.
 *
 * Everything moves imperatively. One useFrame advances all ten bodies and then
 * rewrites the relation lines from their world positions, in that order, in the
 * same callback — so the lines cannot lag a frame behind the spheres they are
 * supposed to be joining.
 * ------------------------------------------------------------------------- */

const BRASS = "#b0812f";
const BRASS_DEEP = "#7e5a16";
const WALNUT = "#6a4b2a";
const VERDIGRIS = "#3f7a6a";
const OXIDE = "#9a3b24";

// Four orbital planes, each tilted differently so the assembly reads as a
// mechanism rather than a set of concentric circles. Signs on `speed` alternate:
// counter-rotating rings are what make an orrery look driven by gears.
const RINGS = [
  { radius: 2.05, tilt: [0.34, 0, 0.12], speed: 0.30 },
  { radius: 3.1, tilt: [-0.22, 0, -0.3], speed: -0.22 },
  { radius: 4.15, tilt: [0.52, 0, 0.08], speed: 0.16 },
  { radius: 5.2, tilt: [-0.12, 0, 0.42], speed: -0.11 }
];

// Ten bodies for the ten modelled actors. Sizes and colours are deliberately
// uneven — a ring of identical spheres looks like a loading spinner.
const BODIES = [
  { ring: 0, phase: 0.0, size: 0.2, color: BRASS },
  { ring: 0, phase: 2.4, size: 0.15, color: VERDIGRIS },
  { ring: 1, phase: 0.9, size: 0.24, color: BRASS },
  { ring: 1, phase: 3.6, size: 0.16, color: WALNUT },
  { ring: 1, phase: 5.2, size: 0.13, color: VERDIGRIS },
  { ring: 2, phase: 0.4, size: 0.22, color: OXIDE },
  { ring: 2, phase: 2.9, size: 0.17, color: BRASS },
  { ring: 2, phase: 4.8, size: 0.14, color: WALNUT },
  { ring: 3, phase: 1.6, size: 0.19, color: VERDIGRIS },
  { ring: 3, phase: 4.2, size: 0.15, color: BRASS }
];

// Which bodies are joined, and in what tone. This previews the live board's
// legend — verdigris allied, brass neutral, oxide hostile — so the landing page
// teaches the colour language before the graph uses it.
const PAIRS = [
  [0, 5, VERDIGRIS],
  [1, 8, VERDIGRIS],
  [2, 6, BRASS],
  [3, 9, BRASS],
  [4, 7, OXIDE],
  [5, 8, BRASS],
  [6, 1, VERDIGRIS],
  [7, 0, OXIDE]
];

/** Radial graduation marks in the XZ plane, like the scale on an armillary. */
function useTickGeometry(radius, count) {
  const geometry = useMemo(() => {
    const positions = new Float32Array(count * 6);
    const inner = radius - 0.07;
    const outer = radius + 0.07;
    for (let i = 0; i < count; i += 1) {
      const angle = (i / count) * Math.PI * 2;
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      const o = i * 6;
      positions[o] = cos * inner;
      positions[o + 1] = 0;
      positions[o + 2] = sin * inner;
      positions[o + 3] = cos * outer;
      positions[o + 4] = 0;
      positions[o + 5] = sin * outer;
    }
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    return geom;
  }, [radius, count]);

  // Geometries created outside the reconciler are not disposed by it either.
  useEffect(() => () => geometry.dispose(), [geometry]);

  return geometry;
}

function GraduatedRing({ radius, tilt, ticks, segments }) {
  const tickGeometry = useTickGeometry(radius, ticks);

  return (
    <group rotation={tilt}>
      {/* torusGeometry is built in the XY plane; this lays it flat into XZ so the
          ring tilt above is the only rotation that carries meaning. */}
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[radius, 0.014, 8, segments]} />
        <meshStandardMaterial color={BRASS} metalness={0.55} roughness={0.3} />
      </mesh>
      <lineSegments geometry={tickGeometry}>
        <lineBasicMaterial color={BRASS_DEEP} transparent opacity={0.45} />
      </lineSegments>
    </group>
  );
}

function Hub({ segments }) {
  return (
    <group>
      <mesh>
        <sphereGeometry args={[0.62, segments, segments]} />
        <meshStandardMaterial
          color="#8a6526"
          metalness={0.5}
          roughness={0.26}
          // A faint internal warmth so the hub reads as the driven centre of the
          // mechanism instead of just the largest sphere.
          emissive="#3a2a10"
          emissiveIntensity={0.35}
        />
      </mesh>
      {/* Three crossed meridians: the armillary cage around the hub. */}
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.95, 0.022, 8, segments * 2]} />
        <meshStandardMaterial color={BRASS} metalness={0.6} roughness={0.28} />
      </mesh>
      <mesh rotation={[0, 0, Math.PI / 2]}>
        <torusGeometry args={[1.06, 0.016, 8, segments * 2]} />
        <meshStandardMaterial color={BRASS_DEEP} metalness={0.6} roughness={0.3} />
      </mesh>
      <mesh rotation={[0, Math.PI / 2, Math.PI / 2]}>
        <torusGeometry args={[1.14, 0.012, 8, segments * 2]} />
        <meshStandardMaterial color={VERDIGRIS} metalness={0.4} roughness={0.4} />
      </mesh>
    </group>
  );
}

/** Suspended brass dust. The warm-room equivalent of the live board's starfield. */
function Motes({ count }) {
  const pointsRef = useRef();
  const geometry = useMemo(() => {
    const positions = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      const r = 4 + Math.random() * 7;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.cos(phi) * 0.55;
      positions[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
    }
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    return geom;
  }, [count]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  useFrame((state) => {
    if (pointsRef.current) {
      pointsRef.current.rotation.y = state.clock.elapsedTime * 0.02;
    }
  });

  return (
    <points ref={pointsRef} geometry={geometry}>
      <pointsMaterial
        size={0.04}
        color={BRASS_DEEP}
        transparent
        opacity={0.45}
        sizeAttenuation
        depthWrite={false}
      />
    </points>
  );
}

const tmpA = new THREE.Vector3();
const tmpB = new THREE.Vector3();

function Orrery({ quality, animate }) {
  const assemblyRef = useRef(null);
  const bodyRefs = useRef([]);

  const segments = quality === "high" ? 24 : 14;
  const ringSegments = quality === "high" ? 128 : 64;
  const ticks = quality === "high" ? 48 : 24;

  const initialPositions = useMemo(
    () =>
      BODIES.map((body) => {
        const ring = RINGS[body.ring];
        return [
          Math.cos(body.phase) * ring.radius,
          0,
          Math.sin(body.phase) * ring.radius
        ];
      }),
    []
  );

  const lineGeometry = useMemo(() => {
    const positions = new Float32Array(PAIRS.length * 6);
    const colors = new Float32Array(PAIRS.length * 6);
    const color = new THREE.Color();
    PAIRS.forEach((pair, index) => {
      color.set(pair[2]);
      const o = index * 6;
      colors[o] = color.r;
      colors[o + 1] = color.g;
      colors[o + 2] = color.b;
      colors[o + 3] = color.r;
      colors[o + 4] = color.g;
      colors[o + 5] = color.b;
    });
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geom.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    return geom;
  }, []);

  useEffect(() => () => lineGeometry.dispose(), [lineGeometry]);

  useFrame((state) => {
    const t = state.clock.elapsedTime;

    if (animate) {
      if (assemblyRef.current) assemblyRef.current.rotation.y = t * 0.05;

      for (let i = 0; i < BODIES.length; i += 1) {
        const body = BODIES[i];
        const ring = RINGS[body.ring];
        const mesh = bodyRefs.current[i];
        if (!mesh) continue;
        const angle = body.phase + t * ring.speed;
        const bob = Math.sin(t * 1.6 + body.phase) * 0.16;
        mesh.position.set(
          Math.cos(angle) * ring.radius,
          bob,
          Math.sin(angle) * ring.radius
        );
        mesh.rotation.y = t * 0.6;
      }
    }

    // Rewritten in the same callback, after the bodies have moved. getWorldPosition
    // calls updateWorldMatrix itself, so these are this frame's positions and not
    // last frame's — but only because the body writes above already happened.
    const attribute = lineGeometry.attributes.position;
    const array = attribute.array;
    for (let i = 0; i < PAIRS.length; i += 1) {
      const a = bodyRefs.current[PAIRS[i][0]];
      const b = bodyRefs.current[PAIRS[i][1]];
      if (!a || !b) continue;
      a.getWorldPosition(tmpA);
      b.getWorldPosition(tmpB);
      const o = i * 6;
      array[o] = tmpA.x;
      array[o + 1] = tmpA.y;
      array[o + 2] = tmpA.z;
      array[o + 3] = tmpB.x;
      array[o + 4] = tmpB.y;
      array[o + 5] = tmpB.z;
    }
    attribute.needsUpdate = true;
  });

  return (
    <>
      <group ref={assemblyRef}>
        <Hub segments={segments} />

        {RINGS.map((ring, index) => (
          <GraduatedRing
            key={`ring-${index}`}
            radius={ring.radius}
            tilt={ring.tilt}
            ticks={ticks}
            segments={ringSegments}
          />
        ))}

        {BODIES.map((body, index) => (
          <group key={`body-${index}`} rotation={RINGS[body.ring].tilt}>
            <mesh
              ref={(element) => {
                bodyRefs.current[index] = element;
              }}
              position={initialPositions[index]}
            >
              <sphereGeometry args={[body.size, segments, segments]} />
              <meshStandardMaterial
                color={body.color}
                metalness={0.4}
                roughness={0.32}
              />
            </mesh>
          </group>
        ))}
      </group>

      {/* Deliberately OUTSIDE the rotating assembly. The vertices written above
          are world positions; inside the assembly they would be transformed a
          second time and the lines would swing away from the spheres. */}
      <lineSegments geometry={lineGeometry}>
        <lineBasicMaterial vertexColors transparent opacity={0.4} depthWrite={false} />
      </lineSegments>
    </>
  );
}

/**
 * Pointer parallax, applied to the camera rather than to a group.
 *
 * Tilting a group would move the mechanism relative to the relation lines, which
 * live in world space — and it would look like the object was being nudged
 * rather than walked around. Moving the camera is both correct and the better
 * effect: the whole scene gains real depth as the pointer moves.
 */
function CameraRig({ base }) {
  const camera = useThree((state) => state.camera);

  useFrame((state, delta) => {
    const pointer = state.pointer || state.mouse;
    const px = pointer ? pointer.x : 0;
    const py = pointer ? pointer.y : 0;
    // Framerate-independent damping, clamped so a long frame cannot overshoot.
    const k = Math.min(1, delta * 2.4);
    camera.position.x += (base[0] + px * 1.7 - camera.position.x) * k;
    camera.position.y += (base[1] + py * 1.0 - camera.position.y) * k;
    camera.lookAt(0, 0, 0);
  });

  return null;
}

export default function HeroOrrery() {
  const isCompact = useMediaQuery(COMPACT_QUERY);
  const reducedMotion = useMediaQuery(REDUCED_MOTION_QUERY);

  const base = useMemo(() => (isCompact ? [0, 0.5, 14.5] : [0, 0.7, 11.5]), [isCompact]);

  return (
    <div className="hero-canvas" aria-hidden="true">
      <Canvas
        camera={{ position: base, fov: isCompact ? 58 : 48 }}
        dpr={isCompact ? [1, 1.5] : [1, 2]}
        // `flat` selects NoToneMapping. ACES (r3f's default) is built for
        // dark-scene HDR and quietly desaturates a light warm palette — the
        // brass goes chalky and the tan goes grey.
        flat
        performance={{ min: 0.5 }}
        gl={{
          // Transparent, so the page's own background gradient shows through and
          // the canvas cannot drift out of step with --sand.
          alpha: true,
          antialias: !isCompact,
          powerPreference: "high-performance",
          preserveDrawingBuffer: false
        }}
        style={{ width: "100%", height: "100%" }}
      >
        <RenderGovernor mode={reducedMotion ? "demand" : "always"} />

        {/* High ambient because the background is light: on a dark scene the
            unlit side of a sphere reads as shadow, on a light one it reads as a
            hole. */}
        <ambientLight intensity={0.9} />
        <directionalLight position={[6, 9, 6]} intensity={1.15} color="#fff4dc" />
        <directionalLight position={[-7, -3, -5]} intensity={0.4} color="#d9c49a" />
        {/* Near the camera, so there is always a specular highlight to sell the
            metal regardless of where the parallax has moved the viewpoint. */}
        <pointLight position={[2.5, 1.5, 8]} intensity={0.55} color="#ffe9c2" />

        <Motes count={isCompact ? 70 : 150} />
        <Orrery quality={isCompact ? "low" : "high"} animate={!reducedMotion} />

        {!reducedMotion && <CameraRig base={base} />}
      </Canvas>
    </div>
  );
}
