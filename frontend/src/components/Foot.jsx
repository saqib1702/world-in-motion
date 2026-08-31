import { Link } from "../router";

const REPO = "https://github.com/saqib1702/world-in-motion";

export default function Foot({ disclaimer }) {
  return (
    <footer className="foot">
      <div className="wrap foot-inner">
        <p className="muted" style={{ maxWidth: "48ch" }}>
          {/* Served by the backend (GET /meta) so the wording cannot drift
              between the API and the UI. The hook supplies a fallback if /meta
              is unreachable — this line is never allowed to be empty. */}
          {disclaimer}
        </p>
        <nav className="foot-links" aria-label="Footer">
          <Link to="/simulation">Live board</Link>
          <Link to="/nations">Nations</Link>
          <Link to="/method">Method</Link>
          {/* data-native keeps the router's delegated click handler out of the
              way; without it an absolute URL is fine, but being explicit means
              this still works if the repo ever moves to a relative path. */}
          <a href={REPO} target="_blank" rel="noreferrer" data-native>
            Source
          </a>
        </nav>
      </div>
    </footer>
  );
}
