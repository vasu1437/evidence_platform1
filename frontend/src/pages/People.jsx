import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";

const KINDS = {
  person: { label: "Person", color: "#b69cff", mark: "P" },
  phone: { label: "Phone", color: "#65d6c3", mark: "#" },
  email: { label: "Email", color: "#69b9ff", mark: "@" },
  account: { label: "Account", color: "#ffbd69", mark: "A" },
  org: { label: "Organization", color: "#ff7d9c", mark: "O" },
  place: { label: "Place", color: "#c5d76a", mark: "L" },
};
const VIEW_KEY = "evidence-graph-saved-views";

const formatSize = (size = 0) => {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
};
const shorten = (text = "", max = 34) => text.length > max ? `${text.slice(0, max - 1)}…` : text;
const sourceLabel = (source) => source.ref || (source.page ? `Page ${source.page}` : source.start_sec != null ? `At ${Math.floor(source.start_sec)} sec` : "Source text");
const csvCell = (value) => {
  let text = String(value ?? "");
  if (/^[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
};

export default function People({ open, userId }) {
  const [graph, setGraph] = useState({ nodes: [], links: [], stats: {} });
  const [mergeHistory, setMergeHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [mergeReloadKey, setMergeReloadKey] = useState(0);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [focusConnections, setFocusConnections] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [hoveredId, setHoveredId] = useState("");
  const [pathSource, setPathSource] = useState("");
  const [pathTarget, setPathTarget] = useState("");
  const [pathResult, setPathResult] = useState(null);
  const [pathLoading, setPathLoading] = useState(false);
  const [mergeTarget, setMergeTarget] = useState("");
  const [mergeConfirmed, setMergeConfirmed] = useState(false);
  const [savedViews, setSavedViews] = useState([]);
  const [selectedView, setSelectedView] = useState("");
  const [viewName, setViewName] = useState("");
  const [saveOpen, setSaveOpen] = useState(false);
  const [viewError, setViewError] = useState("");
  const viewKey = `${VIEW_KEY}:${userId}`;

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    api.graph()
      .then((data) => { if (active) setGraph(data); })
      .catch((e) => { if (active) setError(e.message || "Could not load the evidence graph."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [reloadKey]);

  useEffect(() => {
    let active = true;
    api.entityMerges()
      .then((rows) => { if (active) setMergeHistory(rows); })
      .catch((e) => { if (active) setActionMessage(`Merge history could not be loaded: ${e.message}`); });
    return () => { active = false; };
  }, [mergeReloadKey]);

  useEffect(() => {
    try {
      const parsed = JSON.parse(localStorage.getItem(viewKey) || "[]");
      if (!Array.isArray(parsed)) throw new Error("Saved views data is not a list.");
      setSavedViews(parsed);
    } catch (e) {
      setViewError(`Saved views could not be loaded: ${e.message}`);
    }
  }, [viewKey]);

  const allNodes = graph.nodes || [];
  const allLinks = graph.links || [];
  const nodeById = useMemo(() => Object.fromEntries(allNodes.map((node) => [node.id, node])), [allNodes]);
  const allEntities = allNodes.filter((node) =>
    node.node_type === "entity" && node.verified && ["person", "place"].includes(node.kind)
  );
  const verifiedEntities = allEntities.filter((node) => node.verified);
  const networkLinks = useMemo(() => allLinks.filter((link) => {
    if (link.type !== "co_occurs") return false;
    const a = nodeById[link.a], b = nodeById[link.b];
    return a?.verified && b?.verified && a.kind !== b.kind &&
      ["person", "place"].includes(a.kind) && ["person", "place"].includes(b.kind);
  }), [allLinks, nodeById]);

  const visibleNodes = useMemo(() => {
    let entities = allEntities.filter((entity) => kind === "all" || entity.kind === kind);
    const text = query.trim().toLowerCase();
    if (text) {
      const matches = new Set(entities.filter((node) =>
        `${node.label} ${node.kind}`.toLowerCase().includes(text)
      ).map((node) => node.id));
      const related = new Set(matches);
      for (const link of networkLinks) {
        if (matches.has(link.a)) related.add(link.b);
        if (matches.has(link.b)) related.add(link.a);
      }
      entities = entities.filter((node) => related.has(node.id));
    }
    if (focusConnections && selectedId && entities.some((node) => node.id === selectedId)) {
      const related = new Set([selectedId]);
      for (const link of networkLinks) {
        if (link.a === selectedId) related.add(link.b);
        if (link.b === selectedId) related.add(link.a);
      }
      entities = entities.filter((node) => related.has(node.id));
    }
    return entities;
  }, [allEntities, networkLinks, kind, query, focusConnections, selectedId]);

  const visibleIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const visibleLinks = useMemo(() => networkLinks.filter((link) =>
    visibleIds.has(link.a) && visibleIds.has(link.b)
  ), [networkLinks, visibleIds]);
  const selected = nodeById[selectedId];
  const focusId = hoveredId || selectedId;
  const neighbors = useMemo(() => {
    const ids = new Set();
    if (focusId) {
      for (const link of visibleLinks) {
        if (link.a === focusId) ids.add(link.b);
        if (link.b === focusId) ids.add(link.a);
      }
    }
    return ids;
  }, [focusId, visibleLinks]);

  useEffect(() => {
    if (!visibleNodes.some((node) => node.id === selectedId)) {
      setSelectedId(visibleNodes[0]?.id || "");
    }
  }, [visibleNodes, selectedId]);
  useEffect(() => {
    if (!verifiedEntities.some((node) => node.id === pathSource)) setPathSource(verifiedEntities[0]?.id || "");
    if (!verifiedEntities.some((node) => node.id === pathTarget) || pathTarget === pathSource) {
      setPathTarget(verifiedEntities.find((node) => node.id !== pathSource)?.id || "");
    }
  }, [verifiedEntities, pathSource, pathTarget]);

  const entities = visibleNodes;
  const degree = new Map(entities.map((node) => [node.id, 0]));
  for (const link of visibleLinks) {
    degree.set(link.a, (degree.get(link.a) || 0) + 1);
    degree.set(link.b, (degree.get(link.b) || 0) + 1);
  }
  const byConnections = (a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0) || a.label.localeCompare(b.label);
  const people = entities.filter((node) => node.kind === "person").sort(byConnections);
  const places = entities.filter((node) => node.kind === "place").sort(byConnections);
  const width = 1160;
  const height = Math.max(580, Math.max(people.length, places.length, 1) * 72 + 150);
  const positions = {};
  const columnY = (index, count) => count === 1 ? height / 2 : 108 + index * (height - 216) / (count - 1);
  people.forEach((node, i) => { positions[node.id] = { x: 260, y: columnY(i, people.length) }; });
  places.forEach((node, i) => { positions[node.id] = { x: 850, y: columnY(i, places.length) }; });

  const selectedEntity = selected?.node_type === "entity" ? selected : null;
  const sourceFiles = selectedEntity
    ? (selectedEntity.files || []).map((id) => nodeById[`file:${id}`]).filter(Boolean)
    : [];
  const unmatchedFiles = selectedEntity
    ? (selectedEntity.unmatched_file_ids || []).map((id) => nodeById[`file:${id}`]).filter(Boolean)
    : [];
  const entitySourcesByFile = selectedEntity ? (selectedEntity.sources || []).reduce((result, source) => {
    (result[source.file_id] ||= []).push(source);
    return result;
  }, {}) : {};
  const relatedEntities = selectedEntity
    ? networkLinks.filter((link) => link.a === selectedEntity.id || link.b === selectedEntity.id)
      .map((link) => ({ node: nodeById[link.a === selectedEntity.id ? link.b : link.a], link }))
      .filter((item) => item.node)
    : [];
  const mergeOptions = selectedEntity
    ? verifiedEntities.filter((node) => node.kind === selectedEntity.kind && node.id !== selectedEntity.id)
    : [];

  const handleNodeKey = (event, id) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setSelectedId(id);
    }
  };
  const openFile = (file) => file && open?.({ fileId: file.file_id });
  const openSource = (source) => source && open?.({
    fileId: source.file_id, chunkId: source.chunk_id, start: source.start_sec,
    page: source.page, msg: source.msg_no,
  });

  const findPath = async () => {
    setPathLoading(true);
    setActionMessage("");
    setPathResult(null);
    try {
      setPathResult(await api.graphPath({ source_id: pathSource, target_id: pathTarget }));
    } catch (e) {
      setActionMessage(`Connection search failed: ${e.message}`);
    } finally {
      setPathLoading(false);
    }
  };

  const mergeSelected = async () => {
    if (!selectedEntity || !mergeTarget || !mergeConfirmed) return;
    setActionMessage("");
    try {
      await api.mergeEntities({ source_id: selectedEntity.id, target_id: mergeTarget });
      setSelectedId(mergeTarget);
      setMergeConfirmed(false);
      setMergeTarget("");
      setActionMessage("Entity merge saved and recorded in the audit log.");
      setReloadKey((n) => n + 1);
      setMergeReloadKey((n) => n + 1);
    } catch (e) {
      setActionMessage(`Merge was not applied: ${e.message}`);
    }
  };

  const undoMerge = async (id) => {
    setActionMessage("");
    try {
      await api.undoEntityMerge(id);
      setActionMessage("Merge undone and recorded in the audit log.");
      setReloadKey((n) => n + 1);
      setMergeReloadKey((n) => n + 1);
    } catch (e) {
      setActionMessage(`Could not undo merge: ${e.message}`);
    }
  };

  const currentView = () => ({ query, kind });
  const saveCurrentView = () => {
    const name = viewName.trim();
    if (!name) {
      setViewError("Enter a name for this saved view.");
      return;
    }
    const saved = { id: `${Date.now()}`, name, filters: currentView() };
    const updated = [saved, ...savedViews.filter((view) => view.name !== name)].slice(0, 20);
    try {
      localStorage.setItem(viewKey, JSON.stringify(updated));
      setSavedViews(updated);
      setSelectedView(saved.id);
      setViewName("");
      setSaveOpen(false);
      setViewError("");
    } catch (e) {
      setViewError(`Could not save this view: ${e.message}`);
    }
  };
  const loadSavedView = (id) => {
    setSelectedView(id);
    const saved = savedViews.find((view) => view.id === id);
    if (!saved) return;
    const filters = saved.filters || {};
    setQuery(filters.query || "");
    setKind(["all", "person", "place"].includes(filters.kind) ? filters.kind : "all");
    setFocusConnections(false);
    setPathResult(null);
  };

  const exportGraph = (format) => {
    const filteredNodes = visibleNodes;
    let content, mime, filename;
    if (format === "json") {
      content = JSON.stringify({ exported_at: new Date().toISOString(), filters: currentView(), nodes: filteredNodes, links: visibleLinks }, null, 2);
      mime = "application/json";
      filename = "evidence-graph.json";
    } else {
      const rows = [["record_type", "source_id", "source_label", "relationship", "target_id", "target_label", "source_documents", "source_references"]];
      for (const link of visibleLinks) {
        const from = nodeById[link.a], to = nodeById[link.b];
        const sources = link.sources || [];
        rows.push([
          "link", link.a, from?.label, link.type, link.b, to?.label,
          (link.file_names || [link.file_name].filter(Boolean)).join("; "),
          sources.map((source) => `${source.file_name}: ${sourceLabel(source)}`).join("; "),
        ]);
      }
      for (const node of filteredNodes.filter((item) => item.node_type === "entity" && !item.verified)) {
        rows.push(["unverified_entity", node.id, node.label, "not linked: no exact source text match", "", "", "", ""]);
      }
      content = rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
      mime = "text/csv;charset=utf-8";
      filename = "evidence-graph.csv";
    }
    const url = URL.createObjectURL(new Blob([content], { type: mime }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const pathSources = [...new Map((pathResult?.links || []).flatMap((link) => link.sources || [])
    .map((source) => [`${source.file_id}:${source.chunk_id}`, source])).values()];

  return (
    <section className="network-page">
      <header className="network-heading">
        <div>
          <div className="network-eyebrow"><span /> CASE INTELLIGENCE / EVIDENCE NETWORK</div>
          <h2>People &amp; places</h2>
          <p className="sub">Explore source-verified people and places. A line means they co-appeared in evidence, not that a real-world relationship is proven.</p>
        </div>
        <button className="btn network-refresh" onClick={() => setReloadKey((n) => n + 1)} disabled={loading}>
          <span aria-hidden="true">↻</span> Refresh graph
        </button>
      </header>

      <div className="network-stats">
        <div className="network-stat"><span className="network-stat-icon entity-icon">P</span><div><b>{allEntities.filter((node) => node.kind === "person").length}</b><span>People</span></div></div>
        <div className="network-stat"><span className="network-stat-icon place-icon">L</span><div><b>{allEntities.filter((node) => node.kind === "place").length}</b><span>Places</span></div></div>
        <div className="network-stat"><span className="network-stat-icon link-icon">⌁</span><div><b>{networkLinks.length}</b><span>People-place co-appearances</span></div></div>
      </div>

      <div className="network-toolbar">
        <label className="network-search">
          <span aria-hidden="true">⌕</span>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search a person or place…" aria-label="Search people and places" />
          {query && <button className="network-clear" onClick={() => setQuery("")} aria-label="Clear search">×</button>}
        </label>
        <label className="network-filter"><span>Entity type</span>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="all">People &amp; places</option>
            <option value="person">People</option>
            <option value="place">Places</option>
          </select>
        </label>
        <button className={`network-toggle${focusConnections ? " active" : ""}`} onClick={() => setFocusConnections((value) => !value)}
          aria-pressed={focusConnections} disabled={!selectedId} title="Show only the selected person/place and directly connected nodes">
          <span className="toggle-dot" /> Focus selected
        </button>
      </div>

      <div className="network-tools-row">
        <div className="network-view-tools">
          <select aria-label="Load saved graph view" value={selectedView} onChange={(e) => loadSavedView(e.target.value)}>
            <option value="">Saved views</option>
            {savedViews.map((view) => <option key={view.id} value={view.id}>{view.name}</option>)}
          </select>
          <button className="tool-button" onClick={() => setSaveOpen((v) => !v)}>Save view</button>
          <button className="tool-button" onClick={() => exportGraph("csv")}>Export CSV</button>
          <button className="tool-button" onClick={() => exportGraph("json")}>Export JSON</button>
        </div>
      </div>
      {saveOpen && <form className="save-view-form" onSubmit={(e) => { e.preventDefault(); saveCurrentView(); }}>
        <input value={viewName} onChange={(e) => setViewName(e.target.value)} placeholder="Name this view" aria-label="Saved view name" maxLength={60} />
        <button className="btn pri" type="submit">Save filters</button>
        <button className="btn" type="button" onClick={() => setSaveOpen(false)}>Cancel</button>
      </form>}
      {viewError && <p className="network-message error">{viewError}</p>}
      {actionMessage && <p className="network-message">{actionMessage}</p>}
      {error && <div className="flag">{error} <button className="link" onClick={() => setReloadKey((n) => n + 1)}>Try again</button></div>}

      <section className="network-path-tool">
        <div className="path-tool-copy"><span>INVESTIGATE A CONNECTION</span><b>Explain this path</b><small>Paths follow people-place co-appearance in source evidence; they do not prove a real-world relationship.</small></div>
        <label><span>From entity</span><select value={pathSource} onChange={(e) => { setPathSource(e.target.value); setPathResult(null); }}>
          {verifiedEntities.map((entity) => <option key={entity.id} value={entity.id}>{entity.label} · {KINDS[entity.kind]?.label || entity.kind}</option>)}
        </select></label>
        <span className="path-arrow">→</span>
        <label><span>To entity</span><select value={pathTarget} onChange={(e) => { setPathTarget(e.target.value); setPathResult(null); }}>
          {verifiedEntities.filter((entity) => entity.id !== pathSource).map((entity) => <option key={entity.id} value={entity.id}>{entity.label} · {KINDS[entity.kind]?.label || entity.kind}</option>)}
        </select></label>
        <button className="btn pri" disabled={!pathSource || !pathTarget || pathLoading} onClick={findPath}>{pathLoading ? "Searching…" : "Find path"}</button>
      </section>
      {verifiedEntities.length < 2 && <div className="network-message">Add at least two source-verified people or places to search for a path.</div>}
      {pathResult && <section className={`path-result${pathResult.connected ? " connected" : " disconnected"}`}>
        <div className="path-result-title"><b>{pathResult.connected ? "Co-appearance path found" : "No co-appearance path found"}</b><span>{pathResult.message}</span></div>
        {pathResult.connected && <>
          <div className="path-chain">{pathResult.nodes.map((node, index) => <span key={node.id} className={`path-chip ${node.node_type}`}>
            {index > 0 && <i>→</i>}{node.label}
          </span>)}</div>
          {pathSources.map((source) => <button key={`${source.file_id}:${source.chunk_id}`} className="path-source-link" onClick={() => openSource(source)}>
            Open exact support: {source.file_name} · {sourceLabel(source)} ↗
          </button>)}
          {pathResult.context_events?.length > 0 && <div className="path-context"><b>Timeline context</b>
            {pathResult.context_events.map((event) => <div key={event.id}><time>{event.date || "Undated"}</time> {event.label}</div>)}
          </div>}
        </>}
      </section>}

      <div className="network-layout">
        <section className="network-graph-card" aria-label="Evidence network graph">
          <div className="network-graph-head">
            <div><h3>People ↔ Places</h3><span>Select a node to focus its co-appearances</span></div>
            <span className="network-live"><i /> {loading ? "Updating" : "LIVE GRAPH"}</span>
          </div>
          {loading && !allNodes.length ? (
            <div className="network-empty"><div className="network-empty-mark">◌</div><b>Building the people and places map</b><span>Loading verified entities…</span></div>
          ) : !allEntities.length ? (
            <div className="network-empty"><div className="network-empty-mark">⌁</div><b>No verified people or places yet</b><span>Process evidence with exact source matches to build this network.</span></div>
          ) : !visibleNodes.length ? (
            <div className="network-empty"><div className="network-empty-mark">⌕</div><b>No matches in this view</b><span>Try another search or entity type.</span></div>
          ) : (
            <div className="network-canvas">
              <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Network of people and places that co-appear in evidence">
                <defs>
                  <pattern id="network-grid" width="28" height="28" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="#43516f" opacity=".42" /></pattern>
                  <linearGradient id="person-place-edge" x1="0" x2="1">
                    <stop offset="0" stopColor="#b69cff" stopOpacity=".48" /><stop offset="1" stopColor="#c5d76a" stopOpacity=".7" />
                  </linearGradient>
                </defs>
                <rect width={width} height={height} fill="url(#network-grid)" />
                <text x="104" y="46" className="network-column-title">PEOPLE <tspan>{people.length}</tspan></text>
                <text x="744" y="46" className="network-column-title">PLACES <tspan>{places.length}</tspan></text>
                <line x1="54" y1="66" x2={width - 56} y2="66" className="network-rule" />
                {visibleLinks.map((link, index) => {
                  const a = positions[link.a], b = positions[link.b], from = nodeById[link.a], to = nodeById[link.b];
                  if (!a || !b || !from || !to) return null;
                  const left = a.x < b.x ? a : b, right = a.x < b.x ? b : a;
                  const d = `M ${left.x + 22} ${left.y} C ${left.x + 170} ${left.y}, ${right.x - 170} ${right.y}, ${right.x - 22} ${right.y}`;
                  const emphasized = !focusId || link.a === focusId || link.b === focusId;
                  return <path key={`${link.a}-${link.b}-${index}`} d={d}
                    className={`network-edge co-edge${!emphasized ? " muted" : ""}`}
                    stroke="url(#person-place-edge)" strokeWidth={Math.min(2.6, 1.1 + (link.count || 1) * 0.3)}
                    strokeDasharray="4 5">
                    <title>{`${from.label} and ${to.label} co-appear in ${link.count} evidence item${link.count === 1 ? "" : "s"}`}</title>
                  </path>;
                })}
                {entities.map((node) => {
                  const p = positions[node.id];
                  const meta = KINDS[node.kind];
                  const active = selectedId === node.id, connected = neighbors.has(node.id);
                  return <g key={node.id} className={`network-node entity-node${active ? " selected" : ""}${focusId && !active && !connected ? " dimmed" : ""}`}
                    role="button" tabIndex="0" aria-label={`${meta.label}: ${node.label}, found in ${node.source_count} evidence items`} aria-pressed={active}
                    onClick={() => setSelectedId(node.id)} onKeyDown={(e) => handleNodeKey(e, node.id)}
                    onMouseEnter={() => setHoveredId(node.id)} onMouseLeave={() => setHoveredId("")}>
                    <circle className="entity-halo" cx={p.x} cy={p.y} r="25" />
                    <circle className="entity-core" cx={p.x} cy={p.y} r="18" fill={meta.color} />
                    <text className="entity-mark" x={p.x} y={p.y + 4} textAnchor="middle">{meta.mark}</text>
                    <text className="entity-name" x={p.x + 34} y={p.y - 2}>{shorten(node.label, 32)}</text>
                    <text className="entity-meta" x={p.x + 34} y={p.y + 16}>{meta.label.toUpperCase()} · {node.source_count} SOURCE{node.source_count === 1 ? "" : "S"}</text>
                  </g>;
                })}
              </svg>
            </div>
          )}
          <div className="network-legend">
            <span><i className="legend-entity" /> Person</span>
            <span><i className="legend-place" /> Place</span>
            <span><i className="legend-dashed" /> Co-appeared in a source</span>
          </div>
          <div className="network-caveat">Files are kept in the evidence details panel, not shown as graph nodes. Lines connect only a person and a place found in the same source; co-appearance is not proof of a real-world relationship.</div>
        </section>

        <aside className="network-inspector">
          <div className="inspector-head"><div><span>NETWORK NODE</span><h3>Evidence details</h3></div></div>
          {!selectedEntity ? <div className="inspector-empty">Select a person or place to inspect its evidence trail.</div> : <>
              <div className="inspector-type"><span className="inspector-entity-symbol" style={{ background: KINDS[selectedEntity.kind]?.color }}>{KINDS[selectedEntity.kind]?.mark}</span> {KINDS[selectedEntity.kind]?.label}</div>
              <h4 className="inspector-title">{selectedEntity.label}</h4>
              <div className="inspector-meta">SOURCE-VERIFIED · {sourceFiles.length} SOURCE{sourceFiles.length === 1 ? "" : "S"}</div>
              {selectedEntity.aliases?.length > 0 && <p className="alias-note">Reviewed aliases: {selectedEntity.aliases.join(", ")}</p>}
              {unmatchedFiles.length > 0 && <div className="unmatched-source-list"><b>Unlinked candidate documents</b><span>No exact source-text match was found, so these documents are deliberately not connected.</span>
                {unmatchedFiles.map((file) => <button key={file.id} onClick={() => openFile(file)}>{file.label} ↗</button>)}
              </div>}
              <div className="inspector-section"><div className="inspector-section-title">Evidence sources <b>{sourceFiles.length}</b></div>
                <div className="source-list">
                  {sourceFiles.map((file) => <div key={file.id} className="source-group">
                    <button className="source-row" onClick={() => openFile(file)}>
                      <span className="source-file-icon">{(file.file_type || "DOC").slice(0, 3).toUpperCase()}</span>
                      <span className="source-file-text"><b>{file.label}</b><small>{file.status} · {formatSize(file.size)}</small></span><span className="source-arrow">↗</span>
                    </button>
                    {(entitySourcesByFile[file.file_id] || []).slice(0, 5).map((source) => <button key={source.chunk_id} className="source-ref-link" onClick={() => openSource(source)}>Jump to {sourceLabel(source)} ↗</button>)}
                  </div>)}
                  {!sourceFiles.length && <p className="inspector-muted">No source documents are available.</p>}
                </div>
              </div>
              <div className="inspector-section"><div className="inspector-section-title">Co-appears with <b>{relatedEntities.length}</b></div>
                {relatedEntities.length ? <div className="inspector-chip-list">
                  {relatedEntities.map(({ node, link }) => <button key={node.id} className="entity-chip" onClick={() => setSelectedId(node.id)}>
                    <i style={{ background: KINDS[node.kind]?.color || "#b6c0d8" }} />{node.label}<span>{link.count} source{link.count === 1 ? "" : "s"}</span>
                  </button>)}
                </div> : <p className="inspector-muted">No other entities share a source document.</p>}
              </div>
              {mergeOptions.length > 0 && <div className="inspector-section">
                <div className="inspector-section-title">Identity review <span className="review-label">MANUAL ONLY</span></div>
                <p className="inspector-muted">Different labels stay separate unless you confirm they refer to the same entity. Type mismatches cannot be merged.</p>
                <select className="merge-select" value={mergeTarget} onChange={(e) => { setMergeTarget(e.target.value); setMergeConfirmed(false); }}>
                  <option value="">Choose same-type entity…</option>
                  {mergeOptions.map((node) => <option key={node.id} value={node.id}>{node.label}</option>)}
                </select>
                <label className="merge-confirm"><input type="checkbox" checked={mergeConfirmed} onChange={(e) => setMergeConfirmed(e.target.checked)} /> I verified these are the same entity</label>
                <button className="btn pri merge-button" disabled={!mergeTarget || !mergeConfirmed} onClick={mergeSelected}>Merge and log review</button>
              </div>}
            </>}
          {mergeHistory.length > 0 && <div className="inspector-section merge-history">
            <div className="inspector-section-title">Merge audit trail <b>{mergeHistory.filter((item) => item.active).length}</b></div>
            {mergeHistory.slice(0, 5).map((item) => <div key={item.id} className={`merge-record${item.active ? "" : " undone"}`}>
              <span>{item.source_label} → {item.target_label}<small>{item.active ? "Reviewed merge" : "Merge undone"} · {item.created_at?.slice(0, 10)}</small></span>
              {item.active && <button className="text-button" onClick={() => undoMerge(item.id)}>Undo</button>}
            </div>)}
          </div>}
          <div className="inspector-foot"><span className="foot-shield">◆</span><span>Source-linked<br /><b>Evidence trail preserved</b></span></div>
        </aside>
      </div>
    </section>
  );
}
