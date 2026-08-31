import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls, Html } from "@react-three/drei";
import * as THREE from "three";

import RelationsGraph from "./RelationsGraph";
import RenderGovernor from "./RenderGovernor";
import useMediaQuery, { COMPACT_QUERY, REDUCED_MOTION_QUERY, TOUCH_QUERY } from "../hooks/useMediaQuery";

/* ---------------------------------------------------------------------------
 * The board's scene.
 *
 * This was a dark-space scene: black background, blue-white starfield, cool
 * rim light. The redesign inverts it into the same warm, lit room the rest of
 * the app lives in. Three things make that work, and all three look like
 * mistakes if you do not know why they are there:
 *
 *   - The canvas is TRANSPARENT and there is no <color attach="background">.
 *     The page's own gradient shows through, so the scene can never drift out
 *     of step with --sand the way a hard-coded hex would.
 *   - `flat` selects NoToneMapping. r3f's default ACES curve is built for dark
 *     HDR scenes and desaturates a light warm palette — brass goes chalky.
 *   - The ambient term is unusually high (1.0). In a dark scene ambient light
 *     is what kills the mood; in a lit one it is the room itself.
 *
 * The starfield is replaced by warm motes. A starfield in a light scene reads
 * as dust on the lens, which is a different and worse effect.
 * ------------------------------------------------------------------------- */

const MOTE_COLOR = new THREE.Color("#8a6520");

function SceneLoader() {
  return (
    <Html center>
      <div className="scene-loader-badge">
        <div className="spinner-ring" />
        <span>Winding the mechanism…</span>
      </div>
    </Html>
  );
}

/**
 * Suspended dust, as one Points draw call.
 *
 * Positions are generated once into a Float32Array and never touched again; the
 * drift is a slow rotation of the parent, which is one matrix update per frame
 * rather than several thousand vertex writes.
 */
function Motes({ count, animate }) {
  const groupRef = useRef(null);

  const geometry = useMemo(() => {
    const positions = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      // Rejection-free spherical shell: a uniform direction scaled by a radius
      // biased outward, so the middle of the scene stays clear for the graph.
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const radius = 26 + Math.random() * 44;
      positions[i * 3] = Math.sin(phi) * Math.cos(theta) * radius;
      positions[i * 3 + 1] = Math.cos(phi) * radius * 0.7;
      positions[i * 3 + 2] = Math.sin(phi) * Math.sin(theta) * radius;
    }
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    return geom;
  }, [count]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  useFrame((state) => {
    if (!animate || !groupRef.current) return;
    groupRef.current.rotation.y = state.clock.elapsedTime * 0.012;
  });

  return (
    <group ref={groupRef}>
      <points geometry={geometry}>
        <pointsMaterial
          color={MOTE_COLOR}
          size={0.26}
          sizeAttenuation
          transparent
          opacity={0.5}
          depthWrite={false}
        />
      </points>
    </group>
  );
}

export default function Scene3D({ agents, relations, selectedAgentId, onSelectAgent }) {
  const [controlsEnabled, setControlsEnabled] = useState(true);
  const [isIdle, setIsIdle] = useState(true);
  const idleTimerRef = useRef(null);

  const isCompact = useMediaQuery(COMPACT_QUERY);
  const isTouch = useMediaQuery(TOUCH_QUERY);
  const reducedMotion = useMediaQuery(REDUCED_MOTION_QUERY);

  const handleUserInteract = useCallback(() => {
    setIsIdle(false);
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
    idleTimerRef.current = setTimeout(() => setIsIdle(true), 4000);
  }, []);

  useEffect(() => () => {
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current);
  }, []);

  // A phone reporting devicePixelRatio 3 asks the GPU for nine times the
  // fragment work of a 1x display for no perceptible gain on a scene made of
  // shaded spheres. Capping the ratio is the single largest mobile win here.
  const dpr = useMemo(() => (isCompact ? [1, 1.5] : [1, 2]), [isCompact]);

  const moteCount = isCompact ? 260 : 900;

  // The default camera sits too close for a portrait viewport: with ten nations
  // the outer spheres fall outside a narrow frustum. Pull back and widen.
  const cameraPosition = isCompact ? [0, 0, 19] : [0, 0, 14];
  const fov = isCompact ? 62 : 55;

  const touches = useMemo(
    () => ({ ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN }),
    []
  );

  return (
    <div className="scene-container" onPointerDown={handleUserInteract} onWheel={handleUserInteract}>
      <Canvas
        camera={{ position: cameraPosition, fov }}
        dpr={dpr}
        // NoToneMapping — see the note at the top of this file.
        flat
        // Lets R3F drop resolution under sustained load rather than dropping
        // frames, which reads as a soft image instead of a stutter.
        performance={{ min: 0.5 }}
        gl={{
          antialias: !isCompact,
          // Transparent, so the page's gradient is the background.
          alpha: true,
          powerPreference: "high-performance",
          // Nothing reads the buffer back, so the driver may discard it.
          preserveDrawingBuffer: false,
        }}
        style={{ width: "100%", height: "100%" }}
      >
        <RenderGovernor mode="always" />

        {/* A lit room, not a void. Key light warm and high, a cooler verdigris
            fill from below so the shadowed side of a sphere is not dead grey,
            and a point light near the camera so there is always a specular
            highlight to read the metal by. */}
        <ambientLight intensity={1.0} />
        <directionalLight position={[12, 14, 9]} intensity={1.25} color="#fff2d8" />
        <directionalLight position={[-11, -8, -7]} intensity={0.42} color="#cfe3da" />
        <pointLight position={[0, 2, 16]} intensity={0.55} color="#ffe8bd" distance={60} decay={2} />

        <Motes count={moteCount} animate={!reducedMotion} />

        <Suspense fallback={<SceneLoader />}>
          <RelationsGraph
            agents={agents}
            relations={relations}
            selectedAgentId={selectedAgentId}
            onSelectAgent={onSelectAgent}
            setControlsEnabled={setControlsEnabled}
            onUserInteract={handleUserInteract}
            quality={isCompact ? "low" : "high"}
            reducedMotion={reducedMotion}
          />
        </Suspense>

        <OrbitControls
          enabled={controlsEnabled}
          autoRotate={isIdle && controlsEnabled && !reducedMotion}
          autoRotateSpeed={0.5}
          enableZoom
          // One finger rotates and two fingers zoom/pan. Single-finger panning
          // on a touch screen fights the rotate gesture, so it is disabled there
          // rather than left to whichever handler wins.
          enablePan={!isTouch}
          touches={touches}
          rotateSpeed={isTouch ? 0.85 : 0.6}
          zoomSpeed={isTouch ? 1.1 : 0.9}
          enableDamping
          dampingFactor={0.08}
          // Clamps so a stray pinch cannot leave the user staring into empty
          // space with no way back to the graph.
          minDistance={6}
          maxDistance={40}
          onStart={handleUserInteract}
        />
      </Canvas>
    </div>
  );
}
