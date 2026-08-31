import AmbientField from "../components/AmbientField";
import { Link } from "../router";

/* ---------------------------------------------------------------------------
 * How it works, and what it does not do.
 *
 * The numbering here is legitimate: a tick genuinely is a sequence in which each
 * step consumes the previous step's output, and the order is information the
 * reader needs. Elsewhere in this app lists are not numbered, because ranking ten
 * nations 01–10 would imply a hierarchy that does not exist.
 *
 * The last two sections are the point of the page. A portfolio project that
 * simulates real countries with a language model has an obligation to say plainly
 * what the output is worth, and to be specific about which half of the stack has
 * actually been verified.
 * ------------------------------------------------------------------------- */

const STEPS = [
  {
    title: "Ingest",
    where: "ingestion/fetcher.py",
    body: (
      <>
        <p>
          GDELT and Google News are polled on an interval, and every returned article
          is entity-matched against the ten modelled actors. Matching is an alias
          index with word-boundary comparison plus a geopolitical-verb filter, so a
          story has to both name an actor and be about state behaviour to reach one.
        </p>
        <p>
          Two details do most of the work. Aliases of three or fewer characters are
          matched case-sensitively in upper case, which is what lets{" "}
          <strong>US</strong> catch “US and China agree to pause tariff escalation”
          without matching the pronoun “us” in ordinary prose. And events are
          deduplicated on a SHA-1 of the source URL, so the same story syndicated
          across four outlets moves the world once rather than four times.
        </p>
      </>
    ),
    code: "aliases: [\"united states\", \"us\", \"washington\", \"pentagon\", ...]\n# \"us\"  -> matched as US only, case-sensitive\n# \"usa\" -> matched as USA only\n# longer aliases fall through to case-insensitive matching"
  },
  {
    title: "Perceive",
    where: "agents/nation.py",
    body: (
      <p>
        Each agent writes the tick&rsquo;s relevant events into its own memory log
        before anything decides anything. Memory is per-agent rather than a shared
        transcript, so an actor reasons from what it has seen — which is what makes
        two nations able to respond differently to the same week.
      </p>
    )
  },
  {
    title: "Deliberate",
    where: "engine/deliberation.py",
    body: (
      <>
        <p>
          All ten actors decide inside a <strong>single</strong> structured Gemini
          call. Each is given its persona, its core interests, its current standings
          and the tick&rsquo;s events, and returns an action, a short piece of
          in-character reasoning, and a relation delta.
        </p>
        <p>
          One call per tick instead of ten is not a micro-optimisation. A free-tier
          key is limited to five requests per minute, so a ten-call tick cannot
          complete without waiting out the limiter twice. Batching turns a two-minute
          tick into a several-second one. When a batch response comes back malformed
          or short, the engine degrades to per-actor calls automatically rather than
          dropping the tick.
        </p>
      </>
    ),
    code: "RateLimiter(5, per=60)   # sliding window, thread-safe\n  -> 429  : exponential backoff, retry\n  -> 404  : model unavailable, walk the fallback chain\n  -> none : mock generator, output prefixed [DEMO MODE"
  },
  {
    title: "Commit",
    where: "engine/tick.py",
    body: (
      <p>
        Decisions are written to MongoDB and broadcast over Socket.IO. Relations are
        stored directed and keyed by canonical <code>agent_id</code>, the graph
        animates the delta without a reload, and the event feed gains the headline
        with every agent&rsquo;s reaction attached to it.
      </p>
    )
  }
];

export default function Method({ world }) {
  const { meta } = world;

  return (
    <>
      <AmbientField />

      <div className="wrap page above-field">
        <header className="page-head">
          <span className="plate-label eyebrow">Method</span>
          <h1>What happens in one tick</h1>
          <p className="lede">
            Four stages, in order, each consuming the last one&rsquo;s output. Nothing
            here is hidden behind a framework — the whole loop is a few hundred lines
            of Python.
          </p>
        </header>

        <p className="disclaimer-strip">
          <strong>Note</strong>
          <span>{meta.disclaimer}</span>
        </p>

        <section className="band">
          <div className="band-head">
            <h2>The loop</h2>
            <span className="plate-label">engine/</span>
          </div>
          <hr className="graduated-rule" />

          <div className="method-layout">
            {STEPS.map((step, index) => (
              <article className="method-step" key={step.title}>
                <div className="method-step-index" aria-hidden="true">
                  {String(index + 1).padStart(2, "0")}
                </div>
                <div>
                  <h3>{step.title}</h3>
                  <div className="prose">{step.body}</div>
                  {step.code && <code className="code-note">{step.code}</code>}
                  <span className="plate-label step-where">{step.where}</span>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="band">
          <div className="band-head">
            <h2>Reading the graph</h2>
          </div>
          <hr className="graduated-rule" />
          <div className="two-col">
            <div className="plate plate-pad">
              <span className="plate-label">Directed, not mutual</span>
              <div className="prose prose-tight">
                <p>
                  Ten actors each hold nine opinions, so the matrix has ninety rows.
                  How Washington reads Beijing is a separate number from how Beijing
                  reads Washington, and the two routinely disagree.
                </p>
                <p>
                  The graph draws forty-five edges rather than ninety, each coloured by
                  the <strong>mean</strong> of the pair. An edge is therefore a summary:
                  a warm-looking line can hide one side that is much cooler than the
                  other. The nations page shows the individual rows.
                </p>
              </div>
            </div>
            <div className="plate plate-pad">
              <span className="plate-label">Colour</span>
              <div className="prose prose-tight">
                <p>
                  Scores run from &minus;100 to +100. Verdigris is an allied pair, brass
                  is neutral, oxide red is hostile, and edge brightness tracks the
                  absolute strength of the relationship — a faint line is indifference,
                  not absence.
                </p>
                <p>
                  Sphere size tracks how connected an actor is. Selecting one brings its
                  own rows forward and opens a channel to speak to it in character.
                </p>
              </div>
            </div>
          </div>
        </section>

        <section className="band">
          <div className="band-head">
            <h2>What this is not</h2>
          </div>
          <hr className="graduated-rule" />
          <div className="plate plate-pad plate-screwed">
            <div className="prose">
              <p>
                <strong>It is not a forecast, and it is not analysis.</strong> The agents
                have no access to anything a government knows. They have a persona, a
                list of interests, a memory of headlines they were shown, and a language
                model&rsquo;s willingness to produce fluent reasoning about any of it.
              </p>
              <p>
                That willingness is the thing to be careful about. A model asked to play
                a country will always return something articulate and plausible-sounding,
                whether or not it corresponds to how that country behaves. Fluency here
                is not evidence. Read the output as a simulation of how a set of
                interests might interact, and nothing further.
              </p>
              <p>
                When Gemini is unavailable the app does not fail — it falls back to a
                mock generator so the interface stays explorable. Everything that
                generator produces is prefixed <code>[DEMO MODE</code>, precisely so
                fabricated reasoning can never be mistaken for live reasoning. If you see
                that prefix, no model was called.
              </p>
            </div>
          </div>
        </section>

        <section className="band">
          <div className="band-head">
            <h2>Verification comes in two halves</h2>
          </div>
          <hr className="graduated-rule" />
          <div className="two-col">
            <div className="plate plate-pad">
              <span className="plate-label">Offline — python -m tests.run_all</span>
              <div className="prose prose-tight">
                <p>
                  Five suites, 199 assertions, no credentials and no network. They stub
                  pymongo and <code>google.genai</code> to check the logic that would
                  otherwise need a live account: that every relation row is keyed by a
                  canonical id, that the entity matcher routes the right headlines to the
                  right actors, that the rate limiter and model fallback behave under
                  refusal, and that a malformed batch degrades to per-actor calls.
                </p>
                <p>
                  The security suite drives real HTTP through the app&rsquo;s actual
                  routing table, so a route that quietly loses its token decorator fails
                  there — which a unit test of the decorator would not catch.
                </p>
              </div>
            </div>
            <div className="plate plate-pad">
              <span className="plate-label">Live — python scripts/check_connections.py</span>
              <div className="prose prose-tight">
                <p>
                  The only thing that proves Atlas accepts the URI and that the Gemini
                  key can call the configured model. It also inspects the reply for the{" "}
                  <code>[DEMO MODE</code> prefix, because a call that looks successful can
                  still mean no real reasoning happened.
                </p>
                <p>
                  <strong>Passing one half proves nothing about the other.</strong> Both
                  are worth running before believing the stack works.
                </p>
              </div>
            </div>
          </div>
        </section>

        <section className="band">
          <div className="plate plate-pad closing-plate">
            <h2>Go and look</h2>
            <p className="prose prose-tight">
              The live board is the whole thing running. Inject a headline from the
              trigger panel and one tick resolves across all ten actors in front of you.
            </p>
            <div className="btn-row">
              <Link to="/simulation" className="btn">
                Live board
              </Link>
              <Link to="/nations" className="btn btn-quiet">
                The roster
              </Link>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
