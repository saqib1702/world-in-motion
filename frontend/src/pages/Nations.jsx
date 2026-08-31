import { useMemo } from "react";

import AmbientField from "../components/AmbientField";
import usePointerTilt from "../hooks/usePointerTilt";
import { Link } from "../router";

/* ---------------------------------------------------------------------------
 * The roster.
 *
 * Standings come from the live `relations` collection, which is keyed by
 * canonical agent_id. If the backend is unreachable this falls back to
 * `persona.relations`, which is keyed by DISPLAY NAME.
 *
 * Those two keyings are intentionally different and must not be "unified":
 * persona.relations is rendered into Gemini prompts, where "United States" is
 * what the model should see, while the relations collection is machine state and
 * has to survive a rename. Conflating them is exactly the bug that put a display
 * name into target_agent_id in the first place. The resolver below therefore
 * looks the live rows up by id and the fallback rows up by name, deliberately.
 * ------------------------------------------------------------------------- */

const ALLIED_AT = 30;
const HOSTILE_AT = -30;

function toneFor(score) {
  if (score >= ALLIED_AT) return "allied";
  if (score <= HOSTILE_AT) return "hostile";
  return "neutral";
}

/** Scores run -100..100; the bar is centred so its direction carries the sign. */
function barStyle(score) {
  const clamped = Math.max(-100, Math.min(100, score));
  const width = Math.abs(clamped) / 2;
  return {
    "--bar-width": `${width}%`,
    "--bar-left": clamped >= 0 ? "50%" : `${50 - width}%`
  };
}

function NationCard({ agent, standings, mean }) {
  const tilt = usePointerTilt({ max: 6, lift: 12 });
  const persona = agent.persona || {};
  const interests = (persona.core_interests || []).slice(0, 3);
  const allies = persona.allies || [];
  const rivals = persona.rivals || [];

  const tone = toneFor(mean);
  // -100..100 mapped onto a full turn, so the dial reads as a position on a
  // scale rather than as a percentage of something.
  const turn = `${(((mean + 100) / 200) * 360).toFixed(1)}deg`;

  return (
    <article className={`plate nation-card tilt ${tone}`} {...tilt}>
      <div className="nation-card-head tilt-layer">
        <div>
          <h3>{agent.name}</h3>
          <span className="gov">{persona.government_type || "—"}</span>
        </div>
        <div
          className="dial"
          style={{ "--dial-turn": turn }}
          data-value={Math.round(mean)}
          role="img"
          aria-label={`Mean standing toward the other nations: ${Math.round(mean)} out of 100`}
        />
      </div>

      {interests.length > 0 && (
        <div>
          <span className="plate-label">Core interests</span>
          <ul className="interest-list">
            {interests.map((interest) => (
              <li key={interest}>{interest}</li>
            ))}
          </ul>
        </div>
      )}

      {standings.length > 0 && (
        <div>
          <span className="plate-label">Standing</span>
          <dl className="stance-list">
            {standings.map((row) => (
              <div className="stance-row" key={row.name}>
                <dt>
                  {row.name}
                  <span className="stance-bar">
                    <i className={`tone-${toneFor(row.score)}`} style={barStyle(row.score)} />
                  </span>
                </dt>
                <dd className={`tone-${toneFor(row.score)}`}>
                  {row.score > 0 ? "+" : ""}
                  {Math.round(row.score)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {(allies.length > 0 || rivals.length > 0) && (
        <div className="tag-row">
          {allies.map((name) => (
            <span className="tag ally" key={`a-${name}`}>
              {name}
            </span>
          ))}
          {rivals.map((name) => (
            <span className="tag rival" key={`r-${name}`}>
              {name}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}

export default function Nations({ world }) {
  const { agents, relations, meta } = world;

  const nameById = useMemo(() => {
    const map = new Map();
    for (const agent of agents) map.set(agent.agent_id, agent.name);
    return map;
  }, [agents]);

  // agent_id -> [{ name, score }], sorted warmest first.
  const liveStandings = useMemo(() => {
    const map = new Map();
    for (const row of relations) {
      const source = row.source_agent_id;
      const targetName = nameById.get(row.target_agent_id);
      // A row whose target does not resolve is either mid-reseed or the
      // display-name regression coming back. Dropping it is correct here; the
      // thing that must fail loudly about it is check_connections.py.
      if (!source || !targetName) continue;
      if (!map.has(source)) map.set(source, []);
      map.get(source).push({ name: targetName, score: Number(row.score) || 0 });
    }
    for (const rows of map.values()) rows.sort((a, b) => b.score - a.score);
    return map;
  }, [relations, nameById]);

  const cards = useMemo(
    () =>
      agents.map((agent) => {
        const live = liveStandings.get(agent.agent_id);
        // Fallback keyed by display name — see the note at the top of this file.
        const fallback = Object.entries((agent.persona || {}).relations || {})
          .map(([name, score]) => ({ name, score: Number(score) || 0 }))
          .sort((a, b) => b.score - a.score);
        const rows = live && live.length > 0 ? live : fallback;
        const mean = rows.length
          ? rows.reduce((total, row) => total + row.score, 0) / rows.length
          : 0;
        // Warmest two and coldest two. The middle of a nine-row list is the least
        // informative part of it, and a card showing all nine is a spreadsheet.
        const trimmed =
          rows.length > 4 ? [...rows.slice(0, 2), ...rows.slice(-2)] : rows;
        return { agent, standings: trimmed, mean };
      }),
    [agents, liveStandings]
  );

  // Strongest tie and sharpest rift across the whole matrix — real figures, and
  // the fastest way to see what the simulation currently thinks.
  const extremes = useMemo(() => {
    let warmest = null;
    let coldest = null;
    for (const row of relations) {
      const source = nameById.get(row.source_agent_id);
      const target = nameById.get(row.target_agent_id);
      if (!source || !target) continue;
      const score = Number(row.score) || 0;
      if (!warmest || score > warmest.score) warmest = { source, target, score };
      if (!coldest || score < coldest.score) coldest = { source, target, score };
    }
    return { warmest, coldest };
  }, [relations, nameById]);

  return (
    <>
      <AmbientField />

      <div className="wrap page above-field">
        <header className="page-head">
          <span className="plate-label eyebrow">The roster</span>
          <h1>Ten actors, ninety opinions</h1>
          <p className="lede">
            Every nation holds a separate score toward each of the other nine, so the
            matrix is directed: how Washington reads Beijing is its own row. The dial on
            each plate is that actor&rsquo;s mean standing toward the rest of the world.
          </p>
        </header>

        <p className="disclaimer-strip">
          <strong>Note</strong>
          <span>{meta.disclaimer}</span>
        </p>

        {(extremes.warmest || extremes.coldest) && (
          <dl className="readout-strip">
            {extremes.warmest && (
              <div className="readout-cell">
                <dt>Strongest tie</dt>
                <dd>
                  {extremes.warmest.score > 0 ? "+" : ""}
                  {Math.round(extremes.warmest.score)}
                  <small>
                    {extremes.warmest.source} &rarr; {extremes.warmest.target}
                  </small>
                </dd>
              </div>
            )}
            {extremes.coldest && (
              <div className="readout-cell">
                <dt>Sharpest rift</dt>
                <dd>
                  {Math.round(extremes.coldest.score)}
                  <small>
                    {extremes.coldest.source} &rarr; {extremes.coldest.target}
                  </small>
                </dd>
              </div>
            )}
            <div className="readout-cell">
              <dt>Rows in matrix</dt>
              <dd>
                {relations.length}
                <small>directed, so {Math.round(relations.length / 2)} rendered edges</small>
              </dd>
            </div>
          </dl>
        )}

        <section className="band">
          <div className="band-head">
            <h2>Nations</h2>
            <span className="plate-label">db/seed.py</span>
          </div>
          <hr className="graduated-rule" />

          {agents.length === 0 ? (
            <div className="notice">
              <h3>No roster loaded</h3>
              <p>
                The backend has not answered <code>GET /agents</code> yet. If this persists,
                the usual causes are that Flask is not running, or that the database has
                not been seeded — <code>python -m db.seed</code> creates the ten actors and
                the ninety directed relations between them.
              </p>
              <div className="btn-row">
                <Link to="/method" className="btn btn-quiet">
                  Read the method instead
                </Link>
              </div>
            </div>
          ) : (
            <div className="nation-grid">
              {cards.map(({ agent, standings, mean }) => (
                <NationCard
                  key={agent.agent_id}
                  agent={agent}
                  standings={standings}
                  mean={mean}
                />
              ))}
            </div>
          )}
        </section>
      </div>
    </>
  );
}
