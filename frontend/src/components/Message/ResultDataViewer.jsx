import { useState } from "react";
import Modal from "../common/Modal";
import { TableIcon } from "../common/icons";
import DataTable from "../Chart/DataTable";
import styles from "./ResultDataViewer.module.css";

// Anything at or below this many rows is already readable inline (or in ChartCard's own table
// toggle) — the floating window earns its place once a result is long enough that scanning it in
// the chat column stops being practical.
const ROW_THRESHOLD = 10;

/**
 * "You can view the complete list in the result file /results/xxxx.json" is true on the agent's
 * side, but that path is a page in the agent's own virtual filesystem — nothing a browser can open.
 * This is the user-facing version of that promise: every row the answer is talking about, in a
 * floating window they can scroll instead of a dead file path.
 */
export default function ResultDataViewer({ data }) {
  const [isOpen, setIsOpen] = useState(false);

  if (!data || data.row_count <= ROW_THRESHOLD) return null;

  // reopened conversations keep only an evenly-spaced sample of a large result (see
  // services/results.py) — said plainly, so "the complete list" doesn't quietly mean 200 of 4,000
  const isStoredSample = data.rows.length < data.row_count;

  return (
    <>
      <button type="button" className={styles.trigger} onClick={() => setIsOpen(true)}>
        <TableIcon />
        View full data ({data.row_count.toLocaleString()} rows)
      </button>

      {isOpen && (
        <Modal
          title={`Query result — ${data.row_count.toLocaleString()} row${data.row_count === 1 ? "" : "s"}`}
          size="large"
          onClose={() => setIsOpen(false)}
        >
          <div className={styles.body}>
            <DataTable columns={data.columns} rows={data.rows} limit={data.rows.length} maxHeight="65vh" />

            {(isStoredSample || data.truncated) && (
              <p className={styles.note}>
                {isStoredSample &&
                  `${data.rows.length.toLocaleString()} of the ${data.row_count.toLocaleString()} rows this answer found were kept, spread evenly across the result. `}
                {data.truncated && `The query itself stopped at a ${data.row_count.toLocaleString()}-row cap — there may be more behind it.`}
              </p>
            )}
          </div>
        </Modal>
      )}
    </>
  );
}
