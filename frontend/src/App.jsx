import { useEffect } from "react";

import Foot from "./components/Foot";
import Rail from "./components/Rail";
import useWorldState from "./hooks/useWorldState";
import Landing from "./pages/Landing";
import Method from "./pages/Method";
import Nations from "./pages/Nations";
import Simulation from "./pages/Simulation";
import { Link, useRouter } from "./router";

/**
 * Shell and route table.
 *
 * The live data layer is mounted here, above the switch, so one socket and one
 * poll serve all four pages and nothing is refetched on navigation. See the
 * long comment at the top of hooks/useWorldState.js.
 */

const ROUTES = {
  "/": { title: "World in Motion — Multi-Agent Geopolitical Simulation", chrome: true },
  "/simulation": { title: "Live board — World in Motion", chrome: false },
  "/nations": { title: "Nations — World in Motion", chrome: true },
  "/method": { title: "Method — World in Motion", chrome: true }
};

function NotFound() {
  return (
    <div className="wrap page">
      <div className="notice">
        <h3>No such page</h3>
        <p>
          That path is not part of the app. The four pages are the landing page, the live
          board, the nations roster, and the method write-up.
        </p>
        <div className="btn-row">
          <Link to="/" className="btn">
            Back to the start
          </Link>
          <Link to="/simulation" className="btn btn-quiet">
            Open the live board
          </Link>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const { path } = useRouter();
  const world = useWorldState();

  const route = ROUTES[path];
  // The simulation page is a full-viewport instrument: it sizes itself to the
  // viewport and owns its own scrolling, so a footer under it would either be
  // unreachable or force the scene to shrink.
  const showFooter = route ? route.chrome : true;

  // A client-side route change does not update the title the way a real
  // navigation does, and the title is what ends up in the history menu and in a
  // bookmark.
  useEffect(() => {
    document.title = route ? route.title : "Not found — World in Motion";
  }, [route]);

  let page;
  if (path === "/") page = <Landing world={world} />;
  else if (path === "/simulation") page = <Simulation world={world} />;
  else if (path === "/nations") page = <Nations world={world} />;
  else if (path === "/method") page = <Method world={world} />;
  else page = <NotFound />;

  return (
    <div className="shell">
      {/* First tab stop on every page: the rail has four links before the
          content starts, and the live board has a canvas after that. */}
      <a href="#main" className="sr-only">
        Skip to content
      </a>

      <Rail />

      <main className="shell-main" id="main">
        {page}
      </main>

      {showFooter && <Foot disclaimer={world.meta.disclaimer} />}
    </div>
  );
}
