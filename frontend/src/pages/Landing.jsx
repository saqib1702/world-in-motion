import HeroOrrery from "../components/HeroOrrery";
import { Link } from "../router";

/* ---------------------------------------------------------------------------
 * The landing page.
 *
 * The numbers in the readout are live — they come from the same socket the live
 * board uses, not from a constant in this file. That matters more than it looks:
 * a portfolio landing page claiming "10 actors, 90 relations" is a marketing
 * line, whereas one reading it off the running simulation is a demonstration.
 * When the backend is unreachable they fall back to the modelled shape of the
 * world with an honest label rather than showing zeros.
 * ------------------------------------------------------------------------- */

const MOVEMENTS = [
  {
    title: "Ingest",
    body: "GDELT and Google News are polled, then every article is entity-matched against the ten modelled actors.",
    detail:
      "Alias index + word-boundary matching + a geopolitical-verb filter, so “Turkey” the country is not confused with the bird. Deduplicated on a SHA-1 of the source URL."
  },
  {
    title: "Perceive",
    body: "Each agent writes the tick's events into its own memory log, so later reasoning is conditioned on what that actor has already seen.",
    detail: "Per-agent memory, not a shared transcript."
  },
  {
    title: "Deliberate",
    body: "All ten actors decide inside a single structured Gemini call, each reasoning from its own persona, interests and current standings.",
    detail:
      "One request per tick rather than ten — decisive on a key limited to 5 requests per minute. A malformed batch degrades to per-actor calls."
  },
  {
    title: "Commit",
    body: "Every decision becomes an action, a reasoning string and a relation delta, written to MongoDB and pushed over Socket.IO.",
    detail: "The graph animates the change without a page reload."
  }
];

export default function Landing({ world }) {
  const { agents, relations, events, status, meta } = world;

  // Relations are directed: how Washington sees Beijing is a separate row from
  // how Beijing sees Washington. Ten actors therefore means 90 rows, drawn as 45
  // edges coloured by the mean of each pair.
  const actorCount = agents.length || meta.rosterSize || 10;
  const relationCount = relations.length || actorCount * (actorCount - 1);
  const edgeCount = Math.round(relationCount / 2);
  const live = agents.length > 0;

  return (
    <>
      <section className="hero">
        <HeroOrrery />

        <div className="wrap hero-body stagger">
          <span className="plate-label">Multi-agent geopolitical simulation</span>
          <h1>
            Ten nations, one <em>turning mechanism</em>.
          </h1>
          <p className="lede">
            Each actor is a language model with its own interests and memory. They read
            the day&rsquo;s real headlines, reason in character, and move their standing
            toward the other nine. The graph moves as the world moves.
          </p>
          <div className="btn-row">
            <Link to="/simulation" className="btn">
              Open the live board
            </Link>
            <Link to="/method" className="btn btn-quiet">
              How it works
            </Link>
          </div>
          <p className="disclaimer-strip">
            <strong>Note</strong>
            <span>
              The nations are real; the agents are not. Every statement and relation
              shift is a model&rsquo;s inference from a news headline and represents no
              government&rsquo;s actual position.
            </span>
          </p>
        </div>

        <div className="hero-scroll" aria-hidden="true">
          <span>Scroll</span>
          <i />
        </div>
      </section>

      <div className="wrap after-hero">
        <dl className="readout-strip">
          <div className="readout-cell">
            <dt>Actors</dt>
            <dd>
              {actorCount}
              <small>nations and blocs, each a separate agent</small>
            </dd>
          </div>
          <div className="readout-cell">
            <dt>Directed relations</dt>
            <dd>
              {relationCount}
              <small>{edgeCount} rendered edges, each a pair&rsquo;s mean</small>
            </dd>
          </div>
          <div className="readout-cell">
            <dt>Events in feed</dt>
            <dd>
              {events.length}
              <small>most recent first, with every agent&rsquo;s reaction</small>
            </dd>
          </div>
          <div className="readout-cell">
            <dt>Transport</dt>
            <dd className={`readout-status tone-${status.tone}`}>
              {live ? status.text : "Waiting for backend"}
              <small>
                {live
                  ? "figures above are read from the running simulation"
                  : "figures above show the modelled shape of the world, not live data"}
              </small>
            </dd>
          </div>
        </dl>

        <section className="band">
          <div className="band-head">
            <h2>One tick, in four movements</h2>
            <span className="plate-label">engine/tick.py</span>
          </div>
          <hr className="graduated-rule" />
          <div className="movements">
            {MOVEMENTS.map((movement) => (
              <article className="plate movement" key={movement.title}>
                <h3>{movement.title}</h3>
                <p>{movement.body}</p>
                <p className="movement-detail">{movement.detail}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="band">
          <div className="two-col">
            <div className="plate plate-pad plate-screwed">
              <span className="plate-label">What it is</span>
              <div className="prose prose-tight">
                <p>
                  A working model of how a small set of actors respond to shared events.
                  Ten agents, each with a persona, a set of core interests, a list of
                  allies and rivals, and a private memory of what it has seen. The
                  relation matrix is the output; the 3D graph is a view of it.
                </p>
              </div>
            </div>
            <div className="plate plate-pad plate-screwed">
              <span className="plate-label">What it is not</span>
              <div className="prose prose-tight">
                <p>
                  A forecast. The agents have no access to anything a government knows,
                  and a language model asked to play a country will produce fluent,
                  plausible reasoning whether or not it corresponds to anything.{" "}
                  <strong>Read it as a simulation, not as analysis.</strong>
                </p>
              </div>
            </div>
          </div>
        </section>

        <section className="band">
          <div className="plate plate-pad closing-plate">
            <h2>See it move</h2>
            <p className="prose prose-tight">
              The live board renders the current matrix as a force-directed graph. Select a
              nation to speak to it in character, or inject a headline and watch one tick
              resolve across all ten actors.
            </p>
            <div className="btn-row">
              <Link to="/simulation" className="btn">
                Live board
              </Link>
              <Link to="/nations" className="btn btn-quiet">
                Meet the roster
              </Link>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
