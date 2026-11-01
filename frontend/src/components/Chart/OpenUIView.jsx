import "../../openui/disableDevtools";
import { useMemo, useState } from "react";
import { Renderer } from "@openuidev/react-lang";
import "@openuidev/react-ui/defaults.css";
import "@openuidev/react-ui/components.css";
import { library, ROWS_TOOL } from "../../openui/library";
import DataTable from "./DataTable";
import styles from "./OpenUIView.module.css";

/**
 * Renders the interface the visualizer wrote, bound to the rows the answer already carries.
 *
 * The program never contains data. It says `Query("get_rows", ...)` and this component answers that
 * call from `rows` — the same rows the table view and "View full data" show — so the chart and the
 * table can't disagree, and a reopened conversation redraws from storage without any model call.
 *
 * A model-written program can be wrong in ways the backend's checks don't catch (a component used
 * with the wrong argument types, say). The renderer reports those through onError rather than
 * throwing; when it does, the plain table is shown instead of a half-drawn or blank chart.
 */
export default function OpenUIView({ program, columns, rows }) {
  const [errors, setErrors] = useState([]);

  // a fresh object per render would make the renderer treat the tools as changed and re-run queries
  const toolProvider = useMemo(() => ({ [ROWS_TOOL]: async () => ({ rows }) }), [rows]);

  const blocking = errors.filter((error) => error.severity !== "warning");

  return (
    <div className={styles.frame}>
      <Renderer
        library={library}
        response={program}
        isStreaming={false}
        toolProvider={toolProvider}
        onError={(found) => {
          if (found.length) console.warn("OpenUI program errors:", found);
          setErrors(found);
        }}
      />

      {blocking.length > 0 && (
        <div className={styles.fallback}>
          <p className={styles.fallbackNote}>This chart couldn't be drawn, so here is the data instead.</p>
          <DataTable columns={columns} rows={rows} />
        </div>
      )}
    </div>
  );
}
