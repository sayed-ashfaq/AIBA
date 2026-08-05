import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import Modal from "../common/Modal";
import { EditIcon } from "../common/icons";
import ColumnCommentModal from "./ColumnCommentModal";
import * as api from "../../api/client";
import styles from "./SchemaGraphModal.module.css";

const DEFAULT_GROUP = "__default__";
const MIN_NODE_RADIUS = 3;
const MAX_NODE_RADIUS = 15;
const CLUSTER_STRENGTH = 0.12;

function readGraphTheme() {
  const cs = getComputedStyle(document.documentElement);
  return {
    node: cs.getPropertyValue("--graph-node").trim(),
    nodeSelected: cs.getPropertyValue("--graph-node-selected").trim(),
    link: cs.getPropertyValue("--graph-link").trim(),
    label: cs.getPropertyValue("--graph-label").trim(),
    schemaPalette: [1, 2, 3, 4].map((i) => cs.getPropertyValue(`--graph-schema-${i}`).trim()),
  };
}

// area (not radius) scales with the table's share of the largest table's row count — matches
// how humans perceive circle size, and a flat MIN/MAX keeps tiny/huge tables both legible
function nodeRadius(rowCount, maxRowCount) {
  if (!maxRowCount) return MIN_NODE_RADIUS;
  const ratio = Math.max(rowCount, 0) / maxRowCount;
  return MIN_NODE_RADIUS + Math.sqrt(ratio) * (MAX_NODE_RADIUS - MIN_NODE_RADIUS);
}

function formatRowCount(count) {
  if (!count) return "0 rows";
  if (count >= 1_000_000) return `~${(count / 1_000_000).toFixed(1)}M rows`;
  if (count >= 1_000) return `~${(count / 1_000).toFixed(1)}K rows`;
  return `~${count.toLocaleString()} rows`;
}

// annotations come back from the API keyed by separate schema_name/table_name/column_name
// fields — this is the single lookup key used everywhere on the frontend side, column_name=""
// reserved (per the backend) for a table-level comment, which this view doesn't edit yet
function annotationKey(tableId, columnName) {
  return `${tableId}::${columnName || ""}`;
}

function splitQualifiedName(node) {
  const schemaName = node.schemaGroup === DEFAULT_GROUP ? null : node.schemaGroup;
  const tableName = schemaName ? node.id.slice(schemaName.length + 1) : node.id;
  return { schemaName, tableName };
}

export default function SchemaGraphModal({ connectionId, onClose }) {
  const [graphData, setGraphData] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  const [selectedNode, setSelectedNode] = useState(null);
  const [hoveredNode, setHoveredNode] = useState(null);
  const [pointerPos, setPointerPos] = useState({ x: 0, y: 0 });
  const [graphTheme, setGraphTheme] = useState(readGraphTheme);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [annotations, setAnnotations] = useState(new Map());
  const [editingColumn, setEditingColumn] = useState(null); // { node, columnName } | null

  const fgRef = useRef(null);
  const containerRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getSchemaGraph()
      .then((data) => {
        if (cancelled) return;
        const maxRowCount = Math.max(0, ...data.nodes.map((n) => n.row_count || 0));
        setGraphData({
          nodes: data.nodes.map((n) => ({
            id: n.id,
            columns: n.columns,
            schemaGroup: n.table_schema || DEFAULT_GROUP,
            rowCount: n.row_count || 0,
            maxRowCount,
          })),
          links: data.edges.map((e) => ({
            source: e.from,
            target: e.to,
            fromColumn: e.from_column,
            toColumn: e.to_column,
          })),
        });
      })
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setIsLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!connectionId) return undefined;
    let cancelled = false;
    api
      .listAnnotations(connectionId)
      .then((rows) => {
        if (cancelled) return;
        const map = new Map();
        for (const row of rows) {
          const tableId = row.schema_name ? `${row.schema_name}.${row.table_name}` : row.table_name;
          map.set(annotationKey(tableId, row.column_name), row);
        }
        setAnnotations(map);
      })
      // comments are a supplementary layer — a failed fetch shouldn't block viewing the graph
      .catch((err) => console.warn("could not load column comments:", err.message));
    return () => {
      cancelled = true;
    };
  }, [connectionId]);

  // Canvas colors are drawn manually, so they don't follow CSS media queries automatically —
  // re-read the theme vars if the OS switches light/dark while the modal is open.
  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = () => setGraphTheme(readGraphTheme());
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      setDimensions({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const schemaGroups = useMemo(() => {
    if (!graphData) return [];
    return [...new Set(graphData.nodes.map((n) => n.schemaGroup))].sort((a, b) =>
      a === DEFAULT_GROUP ? 1 : b === DEFAULT_GROUP ? -1 : a.localeCompare(b),
    );
  }, [graphData]);

  // multiple schemas mixed together read as noise — cluster each schema's tables around its own
  // point on a ring so the layout visually separates them, on top of the normal FK-link forces
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || !graphData) return;
    if (schemaGroups.length < 2) {
      fg.d3Force("cluster", null);
      return;
    }
    const clusterRadius = 90 + schemaGroups.length * 35;
    const angleStep = (2 * Math.PI) / schemaGroups.length;
    const centroids = new Map(
      schemaGroups.map((group, i) => [
        group,
        { x: Math.cos(i * angleStep) * clusterRadius, y: Math.sin(i * angleStep) * clusterRadius },
      ]),
    );
    fg.d3Force("cluster", (alpha) => {
      for (const node of graphData.nodes) {
        const centroid = centroids.get(node.schemaGroup);
        node.vx -= (node.x - centroid.x) * CLUSTER_STRENGTH * alpha;
        node.vy -= (node.y - centroid.y) * CLUSTER_STRENGTH * alpha;
      }
    });
    fg.d3ReheatSimulation();
  }, [graphData, schemaGroups]);

  const schemaColor = useMemo(() => {
    const map = new Map();
    // a single schema (or no schema concept, e.g. mysql) isn't a "category" worth coloring —
    // only break out distinct hues once there's actually more than one group to tell apart
    if (schemaGroups.length < 2) {
      schemaGroups.forEach((group) => map.set(group, graphTheme.node));
      return map;
    }
    schemaGroups.forEach((group, i) => {
      map.set(group, i < graphTheme.schemaPalette.length ? graphTheme.schemaPalette[i] : graphTheme.node);
    });
    return map;
  }, [schemaGroups, graphTheme]);

  const matchedIds = useMemo(() => {
    if (!graphData || !search.trim()) return null;
    const q = search.trim().toLowerCase();
    return new Set(graphData.nodes.filter((n) => n.id.toLowerCase().includes(q)).map((n) => n.id));
  }, [graphData, search]);

  const handleNodeClick = (node) => {
    setSelectedNode(node);
    if (fgRef.current && Number.isFinite(node.x) && Number.isFinite(node.y)) {
      fgRef.current.centerAt(node.x, node.y, 400);
      fgRef.current.zoom(3, 400);
    }
  };

  const handlePointerMove = (e) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    setPointerPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
  };

  const handleSaveComment = async (comment) => {
    const { node, columnName } = editingColumn;
    const { schemaName, tableName } = splitQualifiedName(node);
    const saved = await api.upsertAnnotation(connectionId, {
      schema_name: schemaName,
      table_name: tableName,
      column_name: columnName,
      comment,
    });
    setAnnotations((prev) => new Map(prev).set(annotationKey(node.id, columnName), saved));
  };

  const handleRemoveComment = async () => {
    const { node, columnName } = editingColumn;
    const existing = annotations.get(annotationKey(node.id, columnName));
    if (!existing) return;
    await api.deleteAnnotation(connectionId, existing.id);
    setAnnotations((prev) => {
      const next = new Map(prev);
      next.delete(annotationKey(node.id, columnName));
      return next;
    });
  };

  const showLegend = schemaGroups.length >= 2;

  return (
    <Modal title="Schema graph" onClose={onClose} size="large">
      <div className={styles.toolbar}>
        <input
          type="text"
          className={styles.search}
          placeholder="Find a table…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {graphData && (
          <span className={styles.summary}>
            {graphData.nodes.length} tables · {graphData.links.length} relationships · node size ≈ row count
          </span>
        )}
      </div>

      <div className={styles.body}>
        <div
          className={styles.canvas}
          ref={containerRef}
          onMouseMove={handlePointerMove}
          onMouseLeave={() => setHoveredNode(null)}
        >
          {isLoading && <p className={styles.status}>Loading schema…</p>}
          {error && <p className={styles.statusError}>{error}</p>}
          {graphData && !error && (
            <ForceGraph2D
              ref={fgRef}
              width={dimensions.width}
              height={dimensions.height}
              graphData={graphData}
              nodeId="id"
              nodeRelSize={4}
              linkDirectionalArrowLength={4}
              linkDirectionalArrowRelPos={1}
              linkColor={() => graphTheme.link}
              linkWidth={1}
              onNodeClick={handleNodeClick}
              onNodeHover={setHoveredNode}
              onBackgroundClick={() => setSelectedNode(null)}
              nodePointerAreaPaint={(node, color, ctx) => {
                const r = nodeRadius(node.rowCount, node.maxRowCount);
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
                ctx.fill();
              }}
              nodeCanvasObject={(node, ctx, globalScale) => {
                const isMatch = !matchedIds || matchedIds.has(node.id);
                const isSelected = selectedNode?.id === node.id;
                const isHovered = hoveredNode?.id === node.id;
                const label = node.id.split(".").pop();
                const fontSize = 12 / globalScale;
                const r = nodeRadius(node.rowCount, node.maxRowCount);

                ctx.globalAlpha = isMatch ? 1 : 0.15;
                ctx.beginPath();
                ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
                ctx.fillStyle = isSelected ? graphTheme.nodeSelected : schemaColor.get(node.schemaGroup) ?? graphTheme.node;
                ctx.fill();
                if (isHovered || isSelected) {
                  ctx.lineWidth = 1.5 / globalScale;
                  ctx.strokeStyle = graphTheme.nodeSelected;
                  ctx.stroke();
                }

                ctx.font = `${fontSize}px -apple-system, sans-serif`;
                ctx.textAlign = "center";
                ctx.textBaseline = "top";
                ctx.fillStyle = graphTheme.label;
                ctx.fillText(label, node.x, node.y + r + 2);
                ctx.globalAlpha = 1;
              }}
            />
          )}

          {showLegend && (
            <div className={styles.legend}>
              {schemaGroups.map((group) => (
                <div key={group} className={styles.legendItem}>
                  <span className={styles.legendDot} style={{ background: schemaColor.get(group) }} />
                  {group === DEFAULT_GROUP ? "other" : group}
                </div>
              ))}
            </div>
          )}

          {hoveredNode && (
            <div
              className={styles.tooltip}
              style={{ left: pointerPos.x + 16, top: pointerPos.y + 16 }}
            >
              <div className={styles.tooltipTitle}>{hoveredNode.id}</div>
              <div className={styles.tooltipMeta}>{formatRowCount(hoveredNode.rowCount)}</div>
              <ul className={styles.tooltipColumns}>
                {hoveredNode.columns.slice(0, 10).map((c) => {
                  const comment = annotations.get(annotationKey(hoveredNode.id, c.name))?.comment;
                  return (
                    <li key={c.name}>
                      <div className={styles.tooltipColumnRow}>
                        <span>
                          {c.name}
                          {c.pk && <span className={styles.tooltipPk}>PK</span>}
                        </span>
                        <span className={styles.tooltipType}>{c.type}</span>
                      </div>
                      {comment && <div className={styles.tooltipComment}>{comment}</div>}
                    </li>
                  );
                })}
              </ul>
              {hoveredNode.columns.length > 10 && (
                <div className={styles.tooltipMore}>+{hoveredNode.columns.length - 10} more columns</div>
              )}
            </div>
          )}
        </div>

        {selectedNode && (
          <aside className={styles.details}>
            <div className={styles.detailsHeader}>
              <h3>{selectedNode.id}</h3>
              <button type="button" onClick={() => setSelectedNode(null)} aria-label="Close details">
                ×
              </button>
            </div>
            <p className={styles.detailsMeta}>{formatRowCount(selectedNode.rowCount)}</p>
            <table className={styles.columnTable}>
              <tbody>
                {selectedNode.columns.map((c) => {
                  const comment = annotations.get(annotationKey(selectedNode.id, c.name))?.comment;
                  return (
                    <tr key={c.name}>
                      <td>
                        <div className={styles.columnNameRow}>
                          <span>
                            {c.name}
                            {c.pk && <span className={styles.pkBadge}>PK</span>}
                          </span>
                          <button
                            type="button"
                            className={styles.editButton}
                            onClick={() => setEditingColumn({ node: selectedNode, columnName: c.name })}
                            aria-label={`Edit comment for ${c.name}`}
                            title="Edit comment"
                          >
                            <EditIcon />
                          </button>
                        </div>
                        {comment && <p className={styles.columnComment}>{comment}</p>}
                      </td>
                      <td className={styles.colType}>{c.type}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </aside>
        )}
      </div>

      {editingColumn && (
        <ColumnCommentModal
          tableId={editingColumn.node.id}
          columnName={editingColumn.columnName}
          initialComment={annotations.get(annotationKey(editingColumn.node.id, editingColumn.columnName))?.comment || ""}
          onSave={handleSaveComment}
          onRemove={handleRemoveComment}
          onClose={() => setEditingColumn(null)}
        />
      )}
    </Modal>
  );
}
