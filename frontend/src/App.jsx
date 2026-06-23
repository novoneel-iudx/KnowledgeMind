import { useEffect, useState } from "react";
import { getJSON, postJSON, getKey, validateKey, setUnauthorizedHandler, clearKey } from "./api";
import Login from "./Login.jsx";
import { Dashboard, Graph, Connectors, Assistant, Documents, Settings } from "./views.jsx";

const ic = (d) => <svg viewBox="0 0 24 24" className="icon">{d}</svg>;
const ICON = {
  dashboard:  ic(<><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>),
  graph:      ic(<><circle cx="6" cy="6" r="2.4" /><circle cx="18" cy="7" r="2.4" /><circle cx="12" cy="18" r="2.4" /><path d="M8 7l8 0M7.5 8L11 16M16.5 9L13 16" /></>),
  connectors: ic(<path d="M22 12h-4l-3 8-6-16-3 8H2" />),
  assistant:  ic(<path d="M21 11.5a8.38 8.38 0 0 1-8.5 8.5 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.7a8.5 8.5 0 0 1-.9-3.8A8.38 8.38 0 0 1 12.5 3 8.38 8.38 0 0 1 21 11.5z" />),
  documents:  ic(<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></>),
  settings:   ic(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></>),
  shield:     ic(<><path d="M12 2l8 4v6c0 5-3.4 8.5-8 10-4.6-1.5-8-5-8-10V6l8-4z" /><path d="M9 12l2 2 4-4" /></>),
  refresh:    ic(<><path d="M21 12a9 9 0 1 1-2.6-6.4" /><path d="M21 4v5h-5" /></>),
  tests:      ic(<><path d="M9 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9z" /><path d="M9 3v6h6" /><path d="M8 13h8M8 17h4" /></>),
  beaker:     ic(<><path d="M6 2v6l-3 5a4 4 0 0 0 3.4 6h7.2A4 4 0 0 0 17 13L14 8V2" /><path d="M6 2h8" /><circle cx="10" cy="14" r="1.2" /></>),
  extlink:    ic(<><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><polyline points="15 3 21 3 21 9" /><line x1="10" y1="14" x2="21" y2="3" /></>),
  projadvisor: ic(<><circle cx="12" cy="4" r="2"/><circle cx="5" cy="20" r="2"/><circle cx="19" cy="20" r="2"/><path d="M12 6v5M12 11l-5.5 7.5M12 11l5.5 7.5"/></>),
};

// projmgmt is mounted inside KM at /projmgmt (same origin, same port).
// Override at build time with VITE_PROJMGMT_PREFIX if the mount path changes.
const PROJMGMT_PREFIX = import.meta.env.VITE_PROJMGMT_PREFIX || "/projmgmt";

// Sub-entries shown under the "Tests" accordion
const TEST_SUITES = [
  {
    id:    "project-mgmt",
    label: "Project Management",
    icon:  "beaker",
    href:  `${PROJMGMT_PREFIX}/scenarios.html`,
  },
];

const NAV = [
  ["dashboard",    "Dashboard"],
  ["graph",        "Knowledge Graph"],
  ["connectors",   "Connectors"],
  ["assistant",    "Assistant"],
  ["documents",    "Documents"],
  ["projadvisor",  "Project Advisor"],
  ["settings",     "Settings"],
];
const TITLES = { ...Object.fromEntries(NAV), tests: "Tests" };

const ProjectAdvisor = () => (
  <iframe
    src={`${PROJMGMT_PREFIX}/`}
    style={{ width: "100%", height: "100%", border: "none", display: "block" }}
    title="Project Advisor"
    allow="same-origin"
  />
);

const VIEW = {
  dashboard:   Dashboard,
  graph:       Graph,
  connectors:  Connectors,
  assistant:   Assistant,
  documents:   Documents,
  projadvisor: ProjectAdvisor,
  settings:    Settings,
};

// Chevron SVG (inline — not in ICON so it can rotate via CSS)
const Chevron = () => (
  <svg viewBox="0 0 24 24" className="nav-chevron">
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

export default function App() {
  const [authed,    setAuthed]    = useState(false);
  const [booting,   setBooting]   = useState(true);
  const [view,      setView]      = useState("dashboard");
  const [refresh,   setRefresh]   = useState(0);
  const [mode,      setMode]      = useState("");
  const [toast,     setToast]     = useState("");
  const [scanning,  setScanning]  = useState(false);
  const [testsOpen, setTestsOpen] = useState(false);

  const notify = (m) => {
    setToast(m);
    window.clearTimeout(notify._t);
    notify._t = window.setTimeout(() => setToast(""), 2400);
  };

  useEffect(() => {
    setUnauthorizedHandler(() => setAuthed(false));
    (async () => {
      const k = getKey();
      if (k && (await validateKey(k))) setAuthed(true);
      setBooting(false);
    })();
  }, []);

  useEffect(() => {
    if (!authed) return;
    getJSON("/api/status").then((s) => setMode(s.assistant_mode)).catch(() => {});
  }, [authed, refresh]);

  async function runScan() {
    setScanning(true);
    try {
      const r = await postJSON("/api/scan");
      notify(`Scan complete · ${r.commitments} commitments · ${r.conflicts} conflict(s)`);
      setRefresh((x) => x + 1);
    } catch {
      notify("Scan failed");
    }
    setScanning(false);
  }

  if (booting) return <div className="boot"><span className="brand-mark">🧠</span> Loading…</div>;
  if (!authed) return <Login onAuthed={() => setAuthed(true)} />;

  const ViewComp = VIEW[view];
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">🧠</div>
          <div className="brand-name">Knowledge<span>Mind</span></div>
        </div>

        <nav className="nav">
          {/* Regular nav items */}
          {NAV.map(([id, label]) => (
            <button
              key={id}
              className={"nav-item" + (view === id ? " active" : "")}
              onClick={() => setView(id)}
            >
              {ICON[id]}<span>{label}</span>
            </button>
          ))}

          {/* Tests accordion */}
          <div className="nav-group">
            <button
              className={"nav-item nav-group-toggle" + (testsOpen ? " open" : "")}
              onClick={() => setTestsOpen((o) => !o)}
            >
              {ICON.tests}<span>Tests</span><Chevron />
            </button>

            {testsOpen && (
              <div className="nav-sub">
                {TEST_SUITES.map((suite) => (
                  <a
                    key={suite.id}
                    href={suite.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="nav-sub-item"
                    title={`Open ${suite.label} scenario runner`}
                  >
                    {ICON[suite.icon]}<span>{suite.label}</span>{ICON.extlink}
                  </a>
                ))}
              </div>
            )}
          </div>
        </nav>

        <div className="sidebar-foot">
          <div className="privacy-pill">
            {ICON.shield}
            <div><strong>On-device</strong><span>Personal data stays private</span></div>
          </div>
          <button className="lock-btn" onClick={() => { clearKey(); setAuthed(false); }}>
            Lock workspace
          </button>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <h1>{TITLES[view] || "KnowledgeMind"}</h1>
          <div className="topbar-actions">
            <span className={"chip " + (mode === "live" ? "chip-live" : "chip-demo")}>
              {mode === "live" ? "● Live" : mode ? "Demo mode" : "…"}
            </span>
            <button className="btn btn-primary" onClick={runScan} disabled={scanning}>
              {ICON.refresh}<span>{scanning ? "Scanning…" : "Run scan"}</span>
            </button>
          </div>
        </header>
        <div className={"content" + (view === "projadvisor" ? " content--full" : "")}>
          <ViewComp refresh={refresh} notify={notify} />
        </div>
      </main>

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
