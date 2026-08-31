import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";

import RenderGovernor from "./RenderGovernor";
import useMediaQuery, { COMPACT_QUERY, REDUCED_MOTION_QUERY } from "../hooks/useMediaQuery";

/* ---------------------------------------------------------------------------
 * Background depth for the two reading pages.
 *
 * The brief asks for 3D movement on every page, and the roster and method pages
 * are text. The wrong answer is a second orrery behind the prose — depth that
 * competes with the thing you are trying to read is just noise.
 *
 * So this is the same vocabulary at a whisper: three graduated rings, drawn as
 * lines rather than lit meshes, turning slowly behind the content at low opacity.
 * Lines need no lighting, no materials worth the name and no normals, so the
 * whole scene is a few hundred vertices and one draw call per ring. The cards on
 * top carry the pointer-driven depth (see hooks/usePointerTilt.js); this only has
 * to make the page feel like it has a floor.
 * ------------------------------------------------------------------------- */

const BRASS_DEEP = "#7e5a16";
const VERDIGRIS = "#3f7a6a";

const RINGS = [
  { radius: 5.4, tilt: [1.15, 0, 0.2], speed: 0.028, color: BRASS_DEEP, ticks: 60 },
  { radius: 7.8, tilt: [1.32, 0.4, -0.15], speed: -0.019, color: VERDIGRIS, ticks: 40 },
  { radius: 10.6, tilt: [0.98, -0.3, 0.35], speed: 0.012, color: BRASS_DEEP, ticks: 80 }
];

/**
 * A circle plus its graduation marks, as one lineSegments geometry.
 *
 * Built as explicit vertex pairs rather than a LineLoop so there is exactly one
 * geometry and one draw call for both the circle and its ticks.
 */
function useRingGeometry(radius, ticks, segments) {
  const geometry = useMemo(() => {
    const vertexCount = (segments + ticks) * 2;
    const positions = new Float32Array(vertexCount * 3);
    let o = 0;

    for (let i = 0; i < segments; i += 1) {
      const a0 = (i / segments) * Math.PI * 2;
      const a1 = ((i + 1) / segments) * Math.PI * 2;
      positions[o] = Math.cos(a0) * radius;
      positions[o + 1] = 0;
      positions[o + 2] = Math.sin(a0) * radius;
      positions[o + 3] = Math.cos(a1) * radius;
      positions[o + 4] = 0;
      positions[o + 5] = Math.sin(a1) * radius;
      o += 6;
    }

    for (let i = 0; i < ticks; i += 1) {
      const angle = (i / ticks) * Math.PI * 2;
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      // Every fifth mark is longer, the way a real scale is subdivided.
      const length = i % 5 === 0 ? 0.34 : 0.15;
      positions[o] = cos * (radius - length);
      positions[o + 1] = 0;
      positions[o + 2] = sin * (radius - length);
      positions[o + 3] = cos * radius;
      positions[o + 4] = 0;
      positions[o + 5] = sin * radius;
      o += 6;
    }

    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    return geom;
  }, [radius, ticks, segments]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  return geometry;
}

function Ring({ radius, tilt, speed, color, ticks, segments, animate }) {
  const groupRef = useRef(null);
  const geometry = useRingGeometry(radius, ticks, segments);

  useFrame((state) => {
    if (!animate || !groupRef.current) return;
    const t = state.clock.elapsedTime;
    groupRef.current.rotation.y = t * speed;
    groupRef.current.rotation.x = Math.sin(t * 0.2 + radius) * 0.08;
  });

  return (
    <group rotation={tilt}>
      <group ref={groupRef}>
        <lineSegments geometry={geometry}>
          <lineBasicMaterial color={color} transparent opacity={0.5} depthWrite={false} />
        </lineSegments>
      </group>
    </group>
  );
}

export default function AmbientField() {
  const isCompact = useMediaQuery(COMPACT_QUERY);
  const reducedMotion = useMediaQuery(REDUCED_MOTION_QUERY);
  const segments = isCompact ? 72 : 144;

  return (
    <div className="ambient-field" aria-hidden="true">
      <Canvas
        camera={{ position: [0, 2.2, 15], fov: isCompact ? 62 : 52 }}
        // Capped harder than the hero: this is background texture at 20% opacity,
        // so rendering it at 2x device pixel ratio buys literally nothing.
        dpr={[1, 1.25]}
        flat
        performance={{ min: 0.5 }}
        gl={{ alpha: true, antialias: true, powerPreference: "low-power" }}
        style={{ width: "100%", height: "100%" }}
      >
        <RenderGovernor mode={reducedMotion ? "demand" : "always"} />
        {RINGS.map((ring, index) => (
          <Ring
            key={index}
            {...ring}
            segments={segments}
            animate={!reducedMotion}
          />
        ))}
      </Canvas>
    </div>
  );
}
