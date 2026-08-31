import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { Html, Line } from "@react-three/drei";
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } from "d3-force-3d";
import * as THREE from "three";

/* ---------------------------------------------------------------------------
 * Why this component is written imperatively.
 *
 * The previous version drove the force simulation through React state:
 *
 *     sim.on("tick", () => setTick((t) => t + 1))     // ~60 renders/second
 *     useFrame(() => setPulseProgress(pulseRef.current))
 *
 * With the ten-nation roster there are 90 relation rows, so every physics step
 * and every animation frame re-rendered ~100 components, and drei's <Line>
 * rebuilt its geometry on each of those renders. That is the reason the scene
 * felt heavy — not the GPU.
 *
 * The rule now: React owns the *structure* (which nodes and edges exist), and
 * three.js objects are mutated directly for *motion*. React re-renders only when
 * the roster or a relation score actually changes. d3's internal timer is
 * stopped and the simulation is stepped from useFrame instead, so physics, pulse
 * and rendering all advance on one clock.
 *
 * Second change: the 90 relation rows are directed (A->B and B->A are separate
 * documents), and the old code drew both. That is 45 pairs of coincident lines
 * z-fighting with each other. They are now collapsed into one edge per pair.
 * ------------------------------------------------------------------------- */

/* Edge colours, matched to the CSS legend in styles.css (--verdigris / --brass /
   --oxide) but each pulled a little deeper. A line one pixel wide on a tan
   background needs more contrast than the same hue does as a block of fill, and
   these are the values that stay legible at MIN width against --sand-deep. */
const COLOR_ALLIED = "#2f7d68";
const COLOR_HOSTILE = "#9a3b24";
const COLOR_NEUTRAL = "#a8781f";

/* Node colours.
 *
 * This scene is lit and light, which inverts how selection has to be signalled.
 * In the old dark scene the selected sphere glowed: emissive intensity 1.3 over
 * a near-black background. On a tan background a glow just washes toward white
 * and the sphere loses its silhouette, so salience here comes from being the
 * DARKEST, richest object in frame rather than the brightest — plus the ring,
 * the scale and the slow rotation, none of which depend on luminance.
 *
 * Emissive is kept but turned right down; it is doing rim warmth now, not glow.
 *
 * Note metalness 0.45, not the 0.7 this had before. A near-metallic material
 * takes its colour from an environment map and there is none in this scene
 * (drei's <Environment> fetches an HDR, which the CSP would have to allow and
 * an offline build cannot fetch at all), so above ~0.6 the spheres render as
 * dark grey lumps. 0.45 keeps a diffuse term while the directional highlights
 * still read as polished brass. */
const NODE_BASE = "#b0812f";
const NODE_HOVER = "#c9993c";
const NODE_SELECTED = "#8a5a10";
const NODE_EMISSIVE_IDLE = "#4a3208";
const NODE_EMISSIVE_ACTIVE = "#7d5312";
const RING_SELECTED = "#2f7d68";

// Below this alpha d3 considers the layout settled. Raised from d3's default
// 0.001 so the graph comes to rest sooner — a layout that is still visibly
// creeping reads as "not finished loading".
const MIN_ALPHA = 0.008;

const PULSE_DECAY_PER_SECOND = 0.7;

function relationColor(score) {
  if (score >= 10) return COLOR_ALLIED;
  if (score <= -10) return COLOR_HOSTILE;
  return COLOR_NEUTRAL;
}

function edgeBaseWidth(score) {
  return Math.max(1.2, Math.min(6, Math.abs(score) / 15));
}

function edgeBaseOpacity(score) {
  return Math.max(0.4, Math.min(0.95, 0.4 + Math.abs(score) / 120));
}

/**
 * Collapse the directed relation rows into one undirected edge per pair.
 *
 * Both directions are kept on the edge (`scoreOut` / `scoreIn` relative to the
 * lexicographically first id) so nothing is thrown away, but the rendered
 * colour and width use the mean — that is the mutual standing, which is what a
 * single line between two spheres can honestly represent.
 */
function buildPairs(relations, resolveId) {
  const byKey = new Map();

  for (const row of relations) {
    const source = resolveId(row.source_agent_id);
    const target = resolveId(row.target_agent_id);

    if (!source || !target) {
      // Left as a warning on purpose: an unresolved id means the backend wrote a
      // display name where an agent_id belongs, which is a real data bug.
      console.warn("[graph] skipping relation with unresolved agent id:", row);
      continue;
    }
    if (source === target) continue;

    const forward = source < target;
    const [a, b] = forward ? [source, target] : [target, source];
    const key = `${a}__${b}`;

    let entry = byKey.get(key);
    if (!entry) {
      entry = { key, a, b, scoreOut: null, scoreIn: null };
      byKey.set(key, entry);
    }

    const score = Number(row.score || 0);
    if (forward) entry.scoreOut = score;
    else entry.scoreIn = score;
  }

  return [...byKey.values()].map((entry) => {
    const parts = [entry.scoreOut, entry.scoreIn].filter((v) => v !== null);
    const mean = parts.length ? parts.reduce((sum, v) => sum + v, 0) / parts.length : 0;
    return { ...entry, score: mean };
  });
}

/* ------------------------------------------------------------------------- */

function NodeSphere({ node, isSelected, pulseRef, quality, onSelect, onDragStart, onDragEnd }) {
  const [hovered, setHovered] = useState(false);
  const groupRef = useRef();
  const meshRef = useRef();
  const auraRef = useRef();
  const lightRef = useRef();
  const lastColorKey = useRef("");

  const segments = quality === "low" ? [16, 12] : [32, 24];

  // All motion happens here by mutation. Nothing in this callback calls setState,
  // so a settled scene costs zero React work per frame.
  useFrame((_, delta) => {
    const group = groupRef.current;
    const mesh = meshRef.current;
    if (!group || !mesh) return;

    group.position.set(node.x || 0, node.y || 0, node.z || 0);

    const pulse = pulseRef.current;
    const pulseScale = pulse > 0 ? 1 + Math.sin(pulse * Math.PI) * 0.28 : 1;
    const baseScale = isSelected ? 1.3 : hovered ? 1.15 : 1;
    mesh.scale.setScalar(baseScale * pulseScale);

    if (isSelected) mesh.rotation.y += delta * 1.5;

    const material = mesh.material;
    // Turned down across the board from the dark-scene values: emissive is rim
    // warmth here, not glow. See the note beside NODE_BASE.
    material.emissiveIntensity = isSelected
      ? 0.55
      : pulse > 0
      ? 0.2 + pulse * 0.6
      : hovered
      ? 0.35
      : 0.15;

    // Colour objects are comparatively expensive to parse, so only touch the
    // material when the target colours actually change.
    //
    // The cache key has to describe the whole visual state, not just the
    // emissive value. Idle, hovered and selected map onto only two emissive
    // colours, so keying on emissive alone would short-circuit the update when
    // you select a sphere you are already hovering — which is the normal way
    // anyone selects one.
    const active = isSelected || pulse > 0 || hovered;
    const key = isSelected ? "sel" : hovered ? "hov" : active ? "act" : "idle";
    if (key !== lastColorKey.current) {
      material.emissive.set(active ? NODE_EMISSIVE_ACTIVE : NODE_EMISSIVE_IDLE);
      material.color.set(
        isSelected ? NODE_SELECTED : hovered ? NODE_HOVER : NODE_BASE
      );
      lastColorKey.current = key;
    }

    // The aura and the point light stay mounted and are toggled with `visible`.
    // Mounting them per pulse would push a React commit into the animation.
    const aura = auraRef.current;
    if (aura) {
      const show = pulse > 0.02;
      aura.visible = show;
      if (show) {
        aura.rotation.z += delta * 2;
        aura.scale.setScalar(baseScale * pulseScale * (1.2 + (1 - pulse) * 0.8));
        aura.material.opacity = pulse * 0.6;
      }
    }

    const light = lightRef.current;
    if (light) {
      light.visible = isSelected || pulse > 0.5;
      if (light.visible) {
        light.color.set(isSelected ? NODE_EMISSIVE_ACTIVE : NODE_HOVER);
        // Halved from the dark-scene values. Against a lit background a 2.0
        // point light blows the sphere out to white instead of highlighting it.
        light.intensity = isSelected ? 1 : 0.75;
      }
    }
  });

  const stop = (event, fn) => {
    event.stopPropagation();
    fn(event);
  };

  return (
    <group ref={groupRef}>
      <mesh
        ref={meshRef}
        onClick={(event) => stop(event, () => onSelect(node.id))}
        onPointerOver={(event) => stop(event, () => setHovered(true))}
        onPointerOut={() => setHovered(false)}
        onPointerDown={(event) => stop(event, () => onDragStart(node, event))}
        onPointerUp={(event) => stop(event, () => onDragEnd(node))}
      >
        <sphereGeometry args={[0.55, segments[0], segments[1]]} />
        <meshStandardMaterial
          color={NODE_BASE}
          emissive={NODE_EMISSIVE_IDLE}
          emissiveIntensity={0.15}
          roughness={0.32}
          metalness={0.45}
        />
      </mesh>

      <mesh ref={auraRef} visible={false}>
        <sphereGeometry args={[0.6, 16, 12]} />
        <meshBasicMaterial
          color={NODE_EMISSIVE_ACTIVE}
          wireframe
          transparent
          opacity={0}
          depthWrite={false}
        />
      </mesh>

      {isSelected && (
        <mesh scale={1.6}>
          <ringGeometry args={[0.55, 0.65, 32]} />
          {/* Verdigris rather than brass: the selected sphere is itself the
              darkest brass in frame, so a brass ring around it would not
              separate from it. */}
          <meshBasicMaterial color={RING_SELECTED} side={THREE.DoubleSide} transparent opacity={0.85} />
        </mesh>
      )}

      <pointLight ref={lightRef} visible={false} distance={4} />

      <Html
        position={[0, 0.95, 0]}
        center
        distanceFactor={quality === "low" ? 18 : 15}
        zIndexRange={[8, 0]}
        style={{ pointerEvents: "none" }}
      >
        <div
          className={`node-label-pill ${isSelected ? "selected" : ""} ${hovered ? "hovered" : ""}`}
        >
          {node.label}
        </div>
      </Html>
    </group>
  );
}

/* ------------------------------------------------------------------------- */

/**
 * One relation edge. Rendered once by React and thereafter updated by the
 * parent through `register`, which hands the underlying Line2 object upwards.
 * The geometry is mutated in place rather than re-created from a `points` prop.
 *
 * `points` must be a stable module-level reference: drei memoises the
 * LineGeometry on it, so a fresh array literal here would rebuild the geometry
 * on every render and discard the endpoints written by the parent's frame loop.
 * Any two distinct points work, since the real endpoints land on the first
 * frame. Zero-length lines make LineGeometry produce NaN normals, hence 0->1.
 */
const EDGE_PLACEHOLDER_POINTS = [
  [0, 0, 0],
  [0, 0, 1],
];

function PairEdge({ pair, dimmed, highlighted, register }) {
  const color = relationColor(pair.score);
  const width = edgeBaseWidth(pair.score) * (highlighted ? 1.9 : 1);
  const opacity = dimmed ? 0.12 : edgeBaseOpacity(pair.score) * (highlighted ? 1.15 : 1);

  const attach = useCallback((object) => register(pair.key, object), [register, pair.key]);

  return (
    <Line
      ref={attach}
      points={EDGE_PLACEHOLDER_POINTS}
      color={color}
      lineWidth={width}
      transparent
      opacity={Math.min(1, opacity)}
      // Endpoints move every frame, so a cached bounding sphere would cull the
      // line incorrectly. Skipping culling is also cheaper than recomputing it.
      frustumCulled={false}
      depthWrite={false}
    />
  );
}

/* ------------------------------------------------------------------------- */

export default function RelationsGraph({
  agents,
  relations,
  selectedAgentId,
  onSelectAgent,
  setControlsEnabled,
  onUserInteract,
  quality = "high",
  reducedMotion = false,
}) {
  // Selector form, so this component re-renders only when these two slices
  // change. Destructuring the whole store subscribes to every field in it,
  // including `size`, which changes on every canvas resize.
  const camera = useThree((state) => state.camera);
  const invalidate = useThree((state) => state.invalidate);

  const pulseRef = useRef(0);
  const dirtyRef = useRef(true);
  const simRef = useRef(null);
  const draggingNodeRef = useRef(null);
  const dragPlaneRef = useRef(new THREE.Plane());
  const planeIntersectRef = useRef(new THREE.Vector3());
  const positionCacheRef = useRef(new Map());
  const lineRefs = useRef(new Map());
  const edgeScratch = useRef(new Float32Array(6));

  // Structural identity keys. `agents` and `relations` are fresh arrays on every
  // poll even when the contents are unchanged, so memoising on the arrays alone
  // would rebuild the nodes and restart the layout every few seconds. Deriving a
  // key means an identical payload is a no-op.
  const rosterKey = useMemo(
    () => agents.map((a) => `${a.agent_id}:${a.name}`).sort().join("|"),
    [agents]
  );
  const relationKey = useMemo(
    () =>
      relations
        .map((r) => `${r.source_agent_id}>${r.target_agent_id}=${Number(r.score || 0).toFixed(2)}`)
        .sort()
        .join("|"),
    [relations]
  );

  // Read the latest props from inside memos that are keyed on the derived keys
  // above. useMemo has no custom comparator, so this is the standard way to say
  // "recompute only when the content changed, not when the array identity did".
  const agentsRef = useRef(agents);
  const relationsRef = useRef(relations);
  agentsRef.current = agents;
  relationsRef.current = relations;

  const nodes = useMemo(() => {
    return agentsRef.current.map((agent) => {
      const cached = positionCacheRef.current.get(agent.agent_id);
      return {
        id: agent.agent_id,
        label: agent.name,
        x: cached ? cached.x : (Math.random() - 0.5) * 6,
        y: cached ? cached.y : (Math.random() - 0.5) * 6,
        z: cached ? cached.z : (Math.random() - 0.5) * 6,
      };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rosterKey]);

  const nodeById = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  const pairs = useMemo(() => {
    const validIds = new Set(nodes.map((n) => n.id));
    const nameToId = new Map(agentsRef.current.map((a) => [a.name, a.agent_id]));
    const resolveId = (value) => {
      if (validIds.has(value)) return value;
      if (nameToId.has(value)) return nameToId.get(value);
      return null;
    };
    return buildPairs(relationsRef.current, resolveId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [relationKey, nodes]);

  // d3-force mutates link objects in place (replacing the id strings with node
  // references), so it gets its own array rather than the render-facing `pairs`.
  const links = useMemo(
    () => pairs.map((pair) => ({ source: pair.a, target: pair.b, score: pair.score })),
    [pairs]
  );

  const registerEdge = useCallback((key, object) => {
    if (object) lineRefs.current.set(key, object);
    else lineRefs.current.delete(key);
  }, []);

  /* --- simulation lifecycle -------------------------------------------- */

  useEffect(() => {
    const sim = forceSimulation(nodes, 3)
      .force("link", forceLink([]).id((d) => d.id).distance(5.5).strength(0.35))
      .force("charge", forceManyBody().strength(-14))
      .force("center", forceCenter(0, 0, 0))
      .force("collide", forceCollide(1.8))
      .alphaMin(MIN_ALPHA);

    // Stop d3's own d3-timer loop. The simulation is advanced from useFrame so
    // that physics and rendering share a clock; leaving both running means the
    // layout can step twice between two painted frames, or not at all.
    sim.stop();
    simRef.current = sim;
    dirtyRef.current = true;

    return () => {
      sim.stop();
      simRef.current = null;
    };
  }, [nodes]);

  // Relation changes update the existing link force instead of rebuilding the
  // simulation, so scores can move without the graph visibly jumping.
  useEffect(() => {
    const sim = simRef.current;
    if (!sim) return;
    const linkForce = sim.force("link");
    if (linkForce) linkForce.links(links);
    sim.alpha(Math.max(sim.alpha(), 0.35));
    dirtyRef.current = true;
    invalidate();
  }, [links, invalidate]);

  // Pulse on a real relation change only. Keyed on `relationKey` rather than the
  // `relations` array, which is what previously made the whole graph flash every
  // time the six-second safety poll returned the same data.
  useEffect(() => {
    if (reducedMotion) return;
    pulseRef.current = 1;
    dirtyRef.current = true;
    invalidate();
  }, [relationKey, reducedMotion, invalidate]);

  /* --- the single animation loop --------------------------------------- */

  const syncEdges = useCallback(() => {
    const scratch = edgeScratch.current;

    for (const pair of pairs) {
      const line = lineRefs.current.get(pair.key);
      if (!line) continue;

      const from = nodeById.get(pair.a);
      const to = nodeById.get(pair.b);
      if (!from || !to) continue;

      scratch[0] = from.x || 0;
      scratch[1] = from.y || 0;
      scratch[2] = from.z || 0;
      scratch[3] = to.x || 0;
      scratch[4] = to.y || 0;
      scratch[5] = to.z || 0;

      const geometry = line?.geometry;
      if (!geometry) continue;
      const start = geometry.attributes?.instanceStart;
      if (start && start.data && start.data.array && start.data.array.length >= 6) {
        // Write straight into the interleaved buffer. geometry.setPositions()
        // allocates a new InstancedInterleavedBuffer on every call, which at 45
        // edges x 60fps is pure garbage-collector pressure.
        start.data.array.set(scratch, 0);
        start.data.needsUpdate = true;
      } else if (typeof geometry.setPositions === "function") {
        geometry.setPositions(scratch);
      }
    }
  }, [pairs, nodeById]);

  useFrame((_, delta) => {
    const sim = simRef.current;

    if (pulseRef.current > 0) {
      pulseRef.current = Math.max(0, pulseRef.current - delta * PULSE_DECAY_PER_SECOND);
      dirtyRef.current = true;
    }

    let stepped = false;
    if (sim && sim.alpha() > MIN_ALPHA) {
      sim.tick();
      stepped = true;
      for (const node of nodes) {
        positionCacheRef.current.set(node.id, { x: node.x, y: node.y, z: node.z });
      }
    }

    if (stepped || dirtyRef.current) {
      syncEdges();
      // Keep the flag up for one more frame after the last step so the final,
      // settled positions are written before the loop goes quiet.
      dirtyRef.current = stepped || pulseRef.current > 0;
    }
    // Negative priority only affects ordering (R3F disables its automatic render
    // for priorities > 0), so this still runs before the per-node callbacks
    // without taking over rendering.
  }, -1);

  /* --- dragging --------------------------------------------------------- */

  const handleDragStart = useCallback(
    (node) => {
      if (onUserInteract) onUserInteract();
      if (setControlsEnabled) setControlsEnabled(false);
      draggingNodeRef.current = node;

      node.fx = node.x;
      node.fy = node.y;
      node.fz = node.z;

      const normal = camera.getWorldDirection(new THREE.Vector3()).negate();
      dragPlaneRef.current.setFromNormalAndCoplanarPoint(
        normal,
        new THREE.Vector3(node.x, node.y, node.z)
      );

      const sim = simRef.current;
      if (sim) sim.alpha(0.3);
      dirtyRef.current = true;
      invalidate();
    },
    [camera, onUserInteract, setControlsEnabled, invalidate]
  );

  const handleDragEnd = useCallback(
    (node) => {
      // `node` is null when this arrives from the group-level pointerup, so fall
      // back to whatever is actually being dragged.
      const target = node || draggingNodeRef.current;
      if (!target || !draggingNodeRef.current) return;

      target.fx = null;
      target.fy = null;
      target.fz = null;
      draggingNodeRef.current = null;
      if (setControlsEnabled) setControlsEnabled(true);
      dirtyRef.current = true;
    },
    [setControlsEnabled]
  );

  // Safety net for touch: if the finger leaves the canvas mid-drag the R3F
  // pointerup never fires, and without this the node stays pinned and orbit
  // controls stay disabled — the scene looks frozen.
  useEffect(() => {
    const release = () => {
      if (draggingNodeRef.current) handleDragEnd(null);
    };
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    return () => {
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
    };
  }, [handleDragEnd]);

  const handlePointerMove = useCallback(
    (event) => {
      const node = draggingNodeRef.current;
      if (!node || !event.ray) return;
      if (onUserInteract) onUserInteract();

      if (event.ray.intersectPlane(dragPlaneRef.current, planeIntersectRef.current)) {
        const { x, y, z } = planeIntersectRef.current;
        node.fx = x;
        node.fy = y;
        node.fz = z;
        node.x = x;
        node.y = y;
        node.z = z;
        positionCacheRef.current.set(node.id, { x, y, z });
        dirtyRef.current = true;
        invalidate();
      }
    },
    [onUserInteract, invalidate]
  );

  const handleSelect = useCallback(
    (id) => {
      if (onUserInteract) onUserInteract();
      onSelectAgent(id);
    },
    [onSelectAgent, onUserInteract]
  );

  const hasSelection = Boolean(selectedAgentId) && nodeById.has(selectedAgentId);

  return (
    <group onPointerMove={handlePointerMove} onPointerUp={() => handleDragEnd(null)}>
      {pairs.map((pair) => {
        const incident = pair.a === selectedAgentId || pair.b === selectedAgentId;
        return (
          <PairEdge
            key={pair.key}
            pair={pair}
            highlighted={hasSelection && incident}
            dimmed={hasSelection && !incident}
            register={registerEdge}
          />
        );
      })}

      {nodes.map((node) => (
        <NodeSphere
          key={node.id}
          node={node}
          isSelected={node.id === selectedAgentId}
          pulseRef={pulseRef}
          quality={quality}
          onSelect={handleSelect}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        />
      ))}
    </group>
  );
}
