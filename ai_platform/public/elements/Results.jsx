import { useEffect, useMemo, useRef, useState } from "react";

const PAGE_SIZE = 12;
const SELECTION_CAP = 25;
const PAGE_WINDOW = 2;
const BLANK = "(blank)";
const EXPORT_URL = "/api/export/xlsx";
const CHANGE_EVENT = "oi-results-change";

// Chainlit reopens the sidebar with every side element of the thread at once, so
// several panels can be mounted together. They find each other through window
// because each one is evaluated in its own sandbox and shares no module scope.
function mountedPanels() {
  if (!window.__oiResults) window.__oiResults = { panels: new Map(), mounts: 0 };
  return window.__oiResults;
}

function isNewestPanel(id) {
  const { panels } = mountedPanels();
  const mine = panels.get(id);
  if (!mine) return false;
  return [...panels.values()].every(
    (other) =>
      other === mine ||
      other.seq < mine.seq ||
      (other.seq === mine.seq && other.order < mine.order),
  );
}

function isBlank(value) {
  return value === null || value === undefined || value === "";
}

function label(value) {
  return isBlank(value) ? BLANK : String(value);
}

function compare(a, b) {
  if (isBlank(a) && isBlank(b)) return 0;
  if (isBlank(a)) return 1;
  if (isBlank(b)) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

function pageNumbers(current, total) {
  const wanted = new Set([0, total - 1]);
  for (let i = current - PAGE_WINDOW; i <= current + PAGE_WINDOW; i += 1) {
    if (i >= 0 && i < total) wanted.add(i);
  }
  const out = [];
  let previous = null;
  for (const page of [...wanted].sort((x, y) => x - y)) {
    if (previous !== null && page - previous > 1) out.push("gap");
    out.push(page);
    previous = page;
  }
  return out;
}

function toMarkdown(columns, rows) {
  const keep = columns
    .map((name, index) => ({ name, index }))
    .filter(({ index }) => rows.some((row) => !isBlank(row[index])));
  const cell = (value) =>
    isBlank(value) ? "" : String(value).replace(/\|/g, "\\|").replace(/\n/g, " ");
  return [
    `| ${keep.map((c) => c.name).join(" | ")} |`,
    `| ${keep.map(() => "---").join(" | ")} |`,
    ...rows.map((row) => `| ${keep.map((c) => cell(row[c.index])).join(" | ")} |`),
  ].join("\n");
}

// React ignores a direct .value assignment because its own tracker sees no change.
// Writing through the prototype setter and dispatching a bubbling input event is
// what makes a controlled textarea notice.
function writeToComposer(text) {
  const input = document.querySelector("#chat-input");
  if (!input || input.tagName !== "TEXTAREA") return false;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    "value",
  )?.set;
  if (!setter) return false;
  setter.call(input, (input.value ? `${input.value}\n\n` : "") + text);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.focus();
  return true;
}

function Table({ columns, rows, noun }) {

  const [filters, setFilters] = useState({});
  const [sort, setSort] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [page, setPage] = useState(0);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [sendMenuOpen, setSendMenuOpen] = useState(false);
  const sendMenuRef = useRef(null);
  const [openColumn, setOpenColumn] = useState(null);
  const [menuAt, setMenuAt] = useState(null);
  const [search, setSearch] = useState("");
  const menuRef = useRef(null);
  const rootRef = useRef(null);
  const searchRef = useRef(null);

  const distinct = useMemo(() => {
    const found = new Map();
    columns.forEach((column, at) => {
      const values = new Map();
      rows.forEach((row) => values.set(label(row[at]), row[at]));
      found.set(
        column,
        [...values.keys()].sort((a, b) =>
          a === BLANK ? 1 : b === BLANK ? -1 : compare(a, b),
        ),
      );
    });
    return found;
  }, [columns, rows]);

  const filtered = useMemo(() => {
    const active = Object.entries(filters).filter(([, set]) => set.size);
    const all = rows.map((_, index) => index);
    if (!active.length) return all;
    return all.filter((index) =>
      active.every(([column, set]) =>
        set.has(label(rows[index][columns.indexOf(column)])),
      ),
    );
  }, [filters, rows, columns]);

  const ordered = useMemo(() => {
    if (!sort) return filtered;
    const at = columns.indexOf(sort.column);
    const direction = sort.direction === "asc" ? 1 : -1;
    return [...filtered].sort((x, y) => compare(rows[x][at], rows[y][at]) * direction);
  }, [filtered, sort, rows, columns]);

  const pageCount = Math.max(1, Math.ceil(ordered.length / PAGE_SIZE));
  const current = Math.min(page, pageCount - 1);
  const visible = ordered.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE);

  useEffect(() => setPage(0), [filters, sort]);
  useEffect(() => {
    if (!notice) return undefined;
    const timer = setTimeout(() => setNotice(""), 4000);
    return () => clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!sendMenuOpen) return undefined;
    const close = (event) => {
      if (sendMenuRef.current && !sendMenuRef.current.contains(event.target)) {
        setSendMenuOpen(false);
      }
    };
    const onKey = (event) => {
      if (event.key === "Escape") setSendMenuOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [sendMenuOpen]);

  useEffect(() => {
    if (!openColumn) return undefined;
    // preventScroll matters: a plain focus makes the browser scroll every
    // ancestor to reveal the input, which drags the panel off the table.
    searchRef.current?.focus({ preventScroll: true });
    const close = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setOpenColumn(null);
      }
    };
    const escape = (event) => event.key === "Escape" && setOpenColumn(null);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [openColumn]);

  // Placed against the component's own root rather than the viewport. `position:
  // fixed` silently degrades to `absolute` under any transformed ancestor, and
  // Chainlit's panel is not ours to rely on, so viewport coordinates are not safe.
  const openMenu = (column, event) => {
    if (openColumn === column) return setOpenColumn(null);
    const root = rootRef.current?.getBoundingClientRect();
    const button = event.currentTarget.getBoundingClientRect();
    if (!root) return undefined;
    const MENU_WIDTH = 240;
    const left = Math.max(
      0,
      Math.min(button.left - root.left, root.width - MENU_WIDTH),
    );
    setMenuAt({ left, top: button.bottom - root.top + 4 });
    setSearch("");
    setOpenColumn(column);
    return undefined;
  };

  const setColumnFilter = (column, values) => {
    const next = { ...filters };
    if (values.size) next[column] = values;
    else delete next[column];
    setFilters(next);
  };

  const allFilteredSelected =
    ordered.length > 0 && ordered.every((index) => selected.has(index));

  const toggleRow = (index) => {
    const next = new Set(selected);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    setSelected(next);
  };

  const toggleAllFiltered = () => {
    const next = new Set(selected);
    if (allFilteredSelected) ordered.forEach((index) => next.delete(index));
    else ordered.forEach((index) => next.add(index));
    setSelected(next);
  };

  const cycleSort = (column) => {
    if (!sort || sort.column !== column) setSort({ column, direction: "asc" });
    else if (sort.direction === "asc") setSort({ column, direction: "desc" });
    else setSort(null);
  };

  const sendSelected = async () => {
    const chosen = [...selected].sort((x, y) => x - y).map((index) => rows[index]);
    if (!chosen.length) return;
    const markdown = toMarkdown(columns, chosen);
    if (writeToComposer(markdown)) {
      setNotice(`${chosen.length} ${noun} added below — press enter to send`);
      return;
    }
    try {
      await navigator.clipboard.writeText(markdown);
      setNotice("Could not reach the message box — copied, paste with Ctrl+V");
    } catch {
      setNotice("Could not reach the message box or the clipboard");
    }
  };

  const findVessels = () => {
    const chosen = [...selected].sort((x, y) => x - y).map((index) => rows[index]);
    if (!chosen.length || chosen.length > SELECTION_CAP) return;
    const ask = chosen.length === 1
      ? "Find all open vessels that can carry this order"
      : "Find all open vessels that can carry these orders";
    sendUserMessage(`${ask}

${toMarkdown(columns, chosen)}`);
    setSelected(new Set());
    setNotice(`Asked for vessels for ${chosen.length} ${chosen.length === 1 ? "order" : "orders"}`);
  };

  const downloadExcel = async () => {
    if (busy) return;
    const indexes = selected.size ? [...selected].sort((x, y) => x - y) : ordered;
    if (!indexes.length) return;
    setBusy(true);
    setNotice("");
    try {
      const response = await fetch(EXPORT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          columns,
          rows: indexes.map((index) => rows[index]),
          noun,
        }),
      });
      if (!response.ok) throw new Error(`export failed with ${response.status}`);
      const named = /filename="([^"]+)"/.exec(
        response.headers.get("Content-Disposition") ?? "",
      );
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = named ? named[1] : `${noun}.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setNotice(`${indexes.length.toLocaleString()} ${noun} downloaded`);
    } catch {
      setNotice("Could not build the Excel file — try again");
    } finally {
      setBusy(false);
    }
  };

  const overCap = selected.size > SELECTION_CAP;
  const menuValues = openColumn
    ? (distinct.get(openColumn) ?? []).filter((value) =>
        value.toLowerCase().includes(search.trim().toLowerCase()),
      )
    : [];
  const menuChosen = openColumn ? (filters[openColumn] ?? new Set()) : new Set();

  return (
    <div className="oi-t" ref={rootRef}>
      <style>{`
        .oi-t {
          position: relative;
          display: flex;
          flex-direction: column;
          gap: 0.6rem;
          font-size: 0.78rem;
          color: hsl(var(--foreground));
        }
        .oi-t-scroll {
          overflow: auto;
          border: 1px solid hsl(var(--border));
          border-radius: 0.625rem;
          background: hsl(var(--background));
        }
        .oi-t table { border-collapse: separate; border-spacing: 0; width: 100%; }
        .oi-t th, .oi-t td {
          padding: 0.45rem 0.6rem;
          text-align: left;
          white-space: nowrap;
          border-bottom: 1px solid hsl(var(--border) / 0.6);
        }
        .oi-t thead th {
          position: sticky;
          top: 0;
          z-index: 2;
          background: hsl(var(--muted));
          font-weight: 600;
          font-size: 0.72rem;
          letter-spacing: 0.02em;
          text-transform: uppercase;
          color: hsl(var(--muted-foreground));
          border-bottom: 1px solid hsl(var(--border));
        }
        .oi-t tbody tr:last-child td { border-bottom: none; }
        .oi-t tbody tr:nth-child(even) { background: hsl(var(--muted) / 0.35); }
        .oi-t tbody tr:hover { background: hsl(var(--accent) / 0.5); }
        .oi-t tbody tr[data-on="true"] { background: hsl(var(--primary) / 0.12); }
        .oi-t td {
          max-width: 15rem;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .oi-t td[data-num="true"] { text-align: right; font-variant-numeric: tabular-nums; }

        .oi-t-head { display: flex; align-items: center; gap: 0.3rem; }
        .oi-t-sort {
          background: none;
          border: none;
          padding: 0;
          font: inherit;
          color: inherit;
          text-transform: inherit;
          letter-spacing: inherit;
          cursor: pointer;
          display: inline-flex;
          align-items: center;
          gap: 0.2rem;
        }
        .oi-t-sort:hover { color: hsl(var(--foreground)); }
        .oi-t-arrow { font-size: 0.6rem; opacity: 0.8; }
        .oi-t-funnel {
          border: none;
          background: none;
          padding: 0.1rem 0.2rem;
          border-radius: 0.25rem;
          cursor: pointer;
          color: inherit;
          opacity: 0.45;
          line-height: 1;
          font-size: 0.7rem;
        }
        .oi-t-funnel:hover { opacity: 1; background: hsl(var(--background)); }
        .oi-t-funnel[data-on="true"] {
          opacity: 1;
          color: hsl(var(--primary));
          background: hsl(var(--primary) / 0.14);
        }

        .oi-t-sendwrap { position: relative; }
        .oi-t-sendmenu {
          position: absolute;
          right: 0;
          top: calc(100% + 0.35rem);
          z-index: 60;
          width: 16rem;
          display: flex;
          flex-direction: column;
          padding: 0.25rem;
          background: hsl(var(--background));
          border: 1px solid hsl(var(--border));
          border-radius: 0.5rem;
          box-shadow: 0 8px 24px rgb(0 0 0 / 0.18);
        }
        .oi-t-sendopt {
          display: flex;
          flex-direction: column;
          align-items: flex-start;
          gap: 0.1rem;
          padding: 0.45rem 0.55rem;
          font: inherit;
          text-align: left;
          color: hsl(var(--foreground));
          background: transparent;
          border: none;
          border-radius: 0.375rem;
          cursor: pointer;
        }
        .oi-t-sendopt:hover:not(:disabled) { background: hsl(var(--muted)); }
        .oi-t-sendopt:disabled { opacity: 0.5; cursor: not-allowed; }
        .oi-t-sendopt-label { font-size: 0.78rem; font-weight: 500; }
        .oi-t-sendopt-desc { font-size: 0.7rem; color: hsl(var(--muted-foreground)); }
        .oi-t-menu {
          position: absolute;
          z-index: 60;
          width: 15rem;
          max-height: 20rem;
          display: flex;
          flex-direction: column;
          background: hsl(var(--background));
          border: 1px solid hsl(var(--border));
          border-radius: 0.5rem;
          box-shadow: 0 8px 24px rgb(0 0 0 / 0.18);
          overflow: hidden;
        }
        .oi-t-menu-search {
          margin: 0.5rem;
          padding: 0.3rem 0.5rem;
          font: inherit;
          font-size: 0.75rem;
          color: inherit;
          background: hsl(var(--muted));
          border: 1px solid transparent;
          border-radius: 0.375rem;
        }
        .oi-t-menu-search:focus {
          outline: none;
          border-color: hsl(var(--ring));
          background: hsl(var(--background));
        }
        .oi-t-menu-list { overflow-y: auto; padding: 0 0.25rem 0.25rem; }
        .oi-t-opt {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          padding: 0.25rem 0.4rem;
          border-radius: 0.3rem;
          cursor: pointer;
        }
        .oi-t-opt:hover { background: hsl(var(--muted)); }
        .oi-t-opt span {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .oi-t-menu-foot {
          display: flex;
          justify-content: space-between;
          gap: 0.5rem;
          padding: 0.4rem 0.5rem;
          border-top: 1px solid hsl(var(--border));
        }
        .oi-t-link {
          background: none;
          border: none;
          padding: 0;
          font: inherit;
          font-size: 0.72rem;
          color: hsl(var(--primary));
          cursor: pointer;
        }
        .oi-t-link:disabled { color: hsl(var(--muted-foreground)); cursor: default; }

        .oi-t input[type="checkbox"] {
          accent-color: hsl(var(--primary));
          width: 0.85rem;
          height: 0.85rem;
          margin: 0;
          cursor: pointer;
          flex: none;
        }

        .oi-t-bar {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          justify-content: space-between;
          gap: 0.5rem;
        }
        .oi-t-count { color: hsl(var(--muted-foreground)); }
        .oi-t-pages { display: flex; flex-wrap: wrap; gap: 0.2rem; align-items: center; }
        .oi-t-page {
          min-width: 1.7rem;
          padding: 0.2rem 0.4rem;
          font: inherit;
          font-size: 0.72rem;
          color: inherit;
          background: transparent;
          border: 1px solid hsl(var(--border));
          border-radius: 0.375rem;
          cursor: pointer;
        }
        .oi-t-page:hover { background: hsl(var(--muted)); }
        .oi-t-page[data-on="true"] {
          background: hsl(var(--primary));
          color: hsl(var(--primary-foreground));
          border-color: hsl(var(--primary));
        }
        .oi-t-gap { padding: 0 0.15rem; color: hsl(var(--muted-foreground)); }
        .oi-t-send {
          padding: 0.35rem 0.75rem;
          font: inherit;
          font-size: 0.75rem;
          font-weight: 500;
          border-radius: 0.4rem;
          border: 1px solid hsl(var(--primary));
          background: hsl(var(--primary));
          color: hsl(var(--primary-foreground));
          cursor: pointer;
        }
        .oi-t-download {
          margin-left: auto;
          padding: 0.35rem 0.75rem;
          font: inherit;
          font-size: 0.75rem;
          font-weight: 500;
          border-radius: 0.4rem;
          border: 1px solid hsl(var(--border));
          background: transparent;
          color: hsl(var(--foreground));
          cursor: pointer;
        }
        .oi-t-download:hover:not(:disabled) { background: hsl(var(--muted)); }
        .oi-t-download:disabled { opacity: 0.5; cursor: not-allowed; }
        .oi-t-send:hover:not(:disabled) { filter: brightness(1.08); }
        .oi-t-send:disabled {
          opacity: 0.5;
          cursor: not-allowed;
          background: transparent;
          color: hsl(var(--muted-foreground));
          border-color: hsl(var(--border));
        }
        .oi-t-notice { color: hsl(var(--muted-foreground)); font-size: 0.72rem; }
        .oi-t-empty { padding: 1.5rem; text-align: center; color: hsl(var(--muted-foreground)); }
        .oi-t-tabwrap { display: flex; flex-direction: column; gap: 0.6rem; }
        .oi-t-tabs {
          display: flex;
          flex-wrap: wrap;
          gap: 0.2rem;
          border-bottom: 1px solid hsl(var(--border));
        }
        .oi-t-tab {
          padding: 0.35rem 0.7rem;
          font-size: 0.76rem;
          text-transform: capitalize;
          color: hsl(var(--muted-foreground));
          background: transparent;
          border: none;
          border-bottom: 2px solid transparent;
          cursor: pointer;
        }
        .oi-t-tab:hover { color: hsl(var(--foreground)); }
        .oi-t-tab-on {
          color: hsl(var(--foreground));
          border-bottom-color: hsl(var(--primary));
        }
      `}</style>

      <div className="oi-t-scroll">
        <table>
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  checked={allFilteredSelected}
                  onChange={toggleAllFiltered}
                  title="Select everything the filters leave"
                />
              </th>
              {columns.map((column) => (
                <th key={column}>
                  <div className="oi-t-head">
                    <button className="oi-t-sort" onClick={() => cycleSort(column)}>
                      {column.replace(/_/g, " ")}
                      <span className="oi-t-arrow">
                        {sort?.column === column
                          ? sort.direction === "asc"
                            ? "▲"
                            : "▼"
                          : ""}
                      </span>
                    </button>
                    <button
                      className="oi-t-funnel"
                      data-on={Boolean(filters[column]?.size)}
                      onClick={(event) => openMenu(column, event)}
                      title="Filter this column"
                    >
                      ▼
                    </button>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((index) => (
              <tr key={index} data-on={selected.has(index)}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.has(index)}
                    onChange={() => toggleRow(index)}
                  />
                </td>
                {columns.map((column, at) => (
                  <td
                    key={column}
                    data-num={typeof rows[index][at] === "number"}
                    title={label(rows[index][at])}
                  >
                    {isBlank(rows[index][at]) ? "" : String(rows[index][at])}
                  </td>
                ))}
              </tr>
            ))}
            {!visible.length && (
              <tr>
                <td className="oi-t-empty" colSpan={columns.length + 1}>
                  Nothing matches those filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {openColumn && menuAt && (
        <div
          className="oi-t-menu"
          ref={menuRef}
          style={{ left: menuAt.left, top: menuAt.top }}
        >
          <input
            className="oi-t-menu-search"
            ref={searchRef}
            value={search}
            placeholder={`Search ${openColumn.replace(/_/g, " ")}`}
            onChange={(event) => setSearch(event.target.value)}
          />
          <div className="oi-t-menu-list">
            {menuValues.map((value) => (
              <label className="oi-t-opt" key={value}>
                <input
                  type="checkbox"
                  checked={menuChosen.has(value)}
                  onChange={() => {
                    const next = new Set(menuChosen);
                    if (next.has(value)) next.delete(value);
                    else next.add(value);
                    setColumnFilter(openColumn, next);
                  }}
                />
                <span title={value}>{value}</span>
              </label>
            ))}
            {!menuValues.length && (
              <div className="oi-t-notice" style={{ padding: "0.5rem" }}>
                No matching values.
              </div>
            )}
          </div>
          <div className="oi-t-menu-foot">
            <button
              className="oi-t-link"
              onClick={() => setColumnFilter(openColumn, new Set(menuValues))}
            >
              Select shown
            </button>
            <button
              className="oi-t-link"
              disabled={!menuChosen.size}
              onClick={() => setColumnFilter(openColumn, new Set())}
            >
              Clear
            </button>
          </div>
        </div>
      )}

      <div className="oi-t-bar">
        <span className="oi-t-count">
          {ordered.length.toLocaleString()} of {rows.length.toLocaleString()} {noun}
          {selected.size ? ` · ${selected.size} selected` : ""}
        </span>
        <button
          className="oi-t-download"
          disabled={busy || !ordered.length}
          onClick={downloadExcel}
          title={
            selected.size
              ? "Download the selected rows as an Excel file"
              : "Download every row matching the current filters as an Excel file"
          }
        >
          {busy ? "Preparing…" : "Download Excel"}
        </button>
        {noun === "cargoes" ? (
          <div className="oi-t-sendwrap" ref={sendMenuRef}>
            <button
              className="oi-t-send"
              disabled={!selected.size}
              aria-haspopup="menu"
              aria-expanded={sendMenuOpen}
              onClick={() => setSendMenuOpen((open) => !open)}
            >
              {`Send ${selected.size || ""} to chat`} &#9662;
            </button>
            {sendMenuOpen && (
              <div className="oi-t-sendmenu" role="menu">
                <button
                  role="menuitem"
                  className="oi-t-sendopt"
                  disabled={overCap}
                  onClick={() => { setSendMenuOpen(false); sendSelected(); }}
                >
                  <span className="oi-t-sendopt-label">Add to message box</span>
                  <span className="oi-t-sendopt-desc">
                    {overCap ? `Select ${SELECTION_CAP} or fewer` : "Paste the rows, edit, then press enter"}
                  </span>
                </button>
                <button
                  role="menuitem"
                  className="oi-t-sendopt"
                  disabled={overCap}
                  onClick={() => { setSendMenuOpen(false); findVessels(); }}
                >
                  <span className="oi-t-sendopt-label">Find open vessels</span>
                  <span className="oi-t-sendopt-desc">
                    {overCap
                      ? `Select ${SELECTION_CAP} or fewer`
                      : "Sends now: open vessels that can carry these orders"}
                  </span>
                </button>
              </div>
            )}
          </div>
        ) : (
          <button
            className="oi-t-send"
            disabled={!selected.size || overCap}
            onClick={sendSelected}
            title={
              overCap
                ? `Too many to paste into one message. Select ${SELECTION_CAP} or fewer.`
                : "Add the selected rows to the message box"
            }
          >
            {overCap ? `Select ${SELECTION_CAP} or fewer` : `Send ${selected.size || ""} to chat`}
          </button>
        )}
      </div>

      {pageCount > 1 && (
        <div className="oi-t-pages">
          {pageNumbers(current, pageCount).map((entry, at) =>
            entry === "gap" ? (
              <span className="oi-t-gap" key={`gap-${at}`}>
                …
              </span>
            ) : (
              <button
                className="oi-t-page"
                key={entry}
                data-on={entry === current}
                onClick={() => setPage(entry)}
              >
                {entry + 1}
              </button>
            ),
          )}
        </div>
      )}

      {notice && <div className="oi-t-notice">{notice}</div>}
    </div>
  );
}


const PORT_SUGGESTIONS = 50;

const SOURCE_TAGS = {
  supabase: "Supabase",
  computed: "Computed",
  default: "Default",
  saved: "Your default",
  trader: "Edited",
};

// The port list is the same for every panel, so it is fetched once per page and
// shared through window, as each element runs in its own sandbox.
function usePortNames() {
  const [names, setNames] = useState(() => window.__oiPortNames ?? []);
  useEffect(() => {
    if (window.__oiPortNames) return;
    callAction({ name: "port_names", payload: {} })
      .then((reply) => {
        const found = (reply?.response ?? reply)?.names ?? [];
        window.__oiPortNames = found;
        setNames(found);
      })
      .catch(() => {});
  }, []);
  return names;
}

function money(value) {
  return Math.round(value).toLocaleString();
}

function toNumber(text) {
  if (isBlank(text)) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

// One section per group name, in the order groups first appear; a port box leads
// its section so the distance worked out from it sits directly below.
function groupFields(fields, level) {
  const groups = new Map();
  for (const field of fields.filter((each) => each.level === level)) {
    if (!groups.has(field.group)) groups.set(field.group, []);
    groups.get(field.group).push(field);
  }
  return [...groups].map(([name, members]) => ({
    name,
    fields: [...members.filter((each) => each.kind === "port"), ...members.filter((each) => each.kind !== "port")],
  }));
}

function Voyages({ set }) {
  const fields = set.fields ?? [];
  const [drafts, setDrafts] = useState(set.voyages ?? []);
  const [vesselId, setVesselId] = useState(drafts[0]?.vessel_id ?? null);
  const [orderEdits, setOrderEdits] = useState({ ports: {}, values: {} });
  const [vesselEdits, setVesselEdits] = useState({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const portNames = usePortNames();

  const draft = drafts.find((each) => each.vessel_id === vesselId) ?? drafts[0];
  const first = drafts[0];
  if (!draft) return null;
  const baseOf = (field) => (field.level === "order" ? first : draft);

  const editsFor = (id) => vesselEdits[id] ?? { ports: {}, values: {} };
  const editBucket = (field) =>
    field.level === "order" ? orderEdits : editsFor(draft.vessel_id);

  const typedValue = (field) => {
    const bucket = field.kind === "port" ? editBucket(field).ports : editBucket(field).values;
    return bucket[field.name];
  };

  const shown = (field) => {
    const typed = typedValue(field);
    if (typed !== undefined) return typed;
    const base = baseOf(field);
    const stored = field.kind === "port" ? base.ports[field.name] : base.values[field.name];
    return isBlank(stored) ? "" : String(stored);
  };

  const isNeeded = (field) => {
    if (!isBlank(typedValue(field))) return false;
    const owners = field.level === "order" ? drafts : [draft];
    return owners.some((each) => each.needed.includes(field.name));
  };

  const sourceTag = (field) => {
    if (typedValue(field) !== undefined) return "Edited";
    return SOURCE_TAGS[baseOf(field).sources[field.name]] ?? "";
  };

  const fieldByName = Object.fromEntries(fields.map((field) => [field.name, field]));

  // A needed number counts as filled once typed; a needed port only once the
  // backend has accepted it, since only then is its distance known.
  const stillNeeded = (each) =>
    each.needed.filter((name) => {
      const field = fieldByName[name];
      if (!field || field.kind === "port") return true;
      const typed =
        field.level === "order" ? orderEdits.values[name] : editsFor(each.vessel_id).values[name];
      return isBlank(typed);
    });
  const waitingCount = drafts.filter((each) => stillNeeded(each).length).length;

  const clearPortEdits = (vesselIds, key) => {
    if (fieldByName[key]?.level === "order") {
      setOrderEdits((edits) => {
        const ports = { ...edits.ports };
        delete ports[key];
        return { ...edits, ports };
      });
    }
    setVesselEdits((all) => {
      const next = { ...all };
      vesselIds.forEach((id) => {
        if (!next[id]) return;
        const ports = { ...next[id].ports };
        delete ports[key];
        next[id] = { ...next[id], ports };
      });
      return next;
    });
  };

  const refreshPorts = async (key, name, vesselIds) => {
    const targets = drafts.filter((each) => vesselIds.includes(each.vessel_id));
    if (!targets.length) return;
    try {
      const reply = await callAction({
        name: "refresh_ports",
        payload: { vessels: targets.map((each) => ({ draft: each, ports: { [key]: name } })) },
      });
      const updated = (reply?.response ?? reply)?.voyages;
      if (!Array.isArray(updated)) throw new Error("no voyages in reply");
      const byId = Object.fromEntries(updated.map((each) => [each.vessel_id, each]));
      setDrafts((current) => current.map((each) => byId[each.vessel_id] ?? each));
      clearPortEdits(vesselIds, key);
      const problem = updated.flatMap((each) => each.problems)[0];
      setNotice(problem ?? `${fieldByName[key]?.label ?? "Port"} set — distances updated`);
    } catch {
      setNotice("Could not update the distances — try again");
    }
  };

  const portVessels = (field) =>
    field.level === "order" ? drafts.map((each) => each.vessel_id) : [draft.vessel_id];

  // Browsers show only the start of a long suggestion list, so the full port
  // list is narrowed to what the trader has typed instead of sent whole.
  const matchingPorts = (typed) => {
    const wanted = typed.trim().toLowerCase();
    if (!wanted) return [];
    const starts = portNames.filter((name) => name.toLowerCase().startsWith(wanted));
    const contains = portNames.filter(
      (name) => !name.toLowerCase().startsWith(wanted) && name.toLowerCase().includes(wanted),
    );
    return [...starts, ...contains].slice(0, PORT_SUGGESTIONS);
  };

  const isKnownPort = (name, options) => {
    const wanted = name.trim().toLowerCase();
    return Boolean(wanted) && [...options, ...portNames].some((each) => each.toLowerCase() === wanted);
  };

  const commitPort = (field) => {
    const typed = typedValue(field);
    if (typed === undefined) return;
    const stored = baseOf(field).ports[field.name] ?? "";
    if (typed.trim() === stored) {
      clearPortEdits(portVessels(field), field.name);
      return;
    }
    refreshPorts(field.name, typed, portVessels(field));
  };

  const write = (field, text) => {
    const key = field.kind === "port" ? "ports" : "values";
    if (field.level === "order") {
      setOrderEdits((edits) => ({ ...edits, [key]: { ...edits[key], [field.name]: text } }));
      return;
    }
    setVesselEdits((all) => {
      const mine = all[draft.vessel_id] ?? { ports: {}, values: {} };
      return { ...all, [draft.vessel_id]: { ...mine, [key]: { ...mine[key], [field.name]: text } } };
    });
  };

  const applyToAllVessels = (field) => {
    const value = shown(field);
    if (field.kind === "port") {
      refreshPorts(field.name, value, drafts.map((each) => each.vessel_id));
      return;
    }
    const key = "values";
    setVesselEdits((all) =>
      Object.fromEntries(
        drafts.map((each) => {
          const mine = all[each.vessel_id] ?? { ports: {}, values: {} };
          return [each.vessel_id, { ...mine, [key]: { ...mine[key], [field.name]: value } }];
        }),
      ),
    );
    setNotice(`${field.label} set to ${value} for all ${drafts.length} vessels — press Calculate`);
  };

  const numbers = (values) =>
    Object.fromEntries(Object.entries(values).map(([name, text]) => [name, toNumber(text)]));

  const calculate = async () => {
    if (busy) return;
    setBusy(true);
    setNotice("");
    const vessels = drafts.map((each) => {
      const mine = editsFor(each.vessel_id);
      return {
        draft: each,
        ports: { ...orderEdits.ports, ...mine.ports },
        values: { ...numbers(orderEdits.values), ...numbers(mine.values) },
      };
    });
    try {
      const reply = await callAction({ name: "calculate_voyages", payload: { vessels } });
      const ranked = (reply?.response ?? reply)?.voyages;
      if (!Array.isArray(ranked)) throw new Error("no voyages in reply");
      setDrafts(ranked);
      setOrderEdits({ ports: {}, values: {} });
      setVesselEdits({});
      const ready = ranked.filter((each) => each.result).length;
      setNotice(`${ready} of ${ranked.length} vessels ranked`);
      updateElement({
        ...props,
        sets: props.sets.map((each) =>
          each.label === set.label ? { ...each, voyages: ranked } : each,
        ),
      });
    } catch {
      setNotice("Could not calculate — try again");
    } finally {
      setBusy(false);
    }
  };

  const applyLink = (field) =>
    field.level === "vessel" && drafts.length > 1 ? (
      <button
        type="button"
        className="oi-v-apply"
        disabled={isBlank(shown(field))}
        onClick={() => applyToAllVessels(field)}
      >
        Apply to all vessels
      </button>
    ) : null;

  const renderField = (field) => {
    const needed = isNeeded(field);
    const tag = sourceTag(field);
    if (field.kind === "port") {
      const base = baseOf(field);
      const options = base.port_options[field.name] ?? [];
      const listId = `oi-v-ports-${field.name}-${options.length ? base.vessel_id : "all"}`;
      const place = base.places[field.name];
      return (
        <div className="oi-v-row" key={field.name}>
          <label className="oi-v-label">
            {field.label}
            <span className="oi-v-place">In data: {isBlank(place) ? "(blank)" : place}</span>
            {applyLink(field)}
          </label>
          <input
            className="oi-v-input oi-v-port"
            data-needed={needed}
            list={listId}
            value={shown(field)}
            placeholder={options.length ? "pick a port…" : "start typing a port…"}
            onChange={(event) => {
              const value = event.target.value;
              write(field, value);
              const stored = baseOf(field).ports[field.name] ?? "";
              if (isKnownPort(value, options) && value.trim() !== stored) {
                refreshPorts(field.name, value, portVessels(field));
              }
            }}
            onBlur={() => commitPort(field)}
            onKeyDown={(event) => event.key === "Enter" && commitPort(field)}
          />
          <datalist id={listId}>
            {(options.length ? options : matchingPorts(shown(field))).map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
          <span className="oi-v-tag" data-needed={needed}>{needed ? "Needed" : tag}</span>
        </div>
      );
    }
    return (
      <div className="oi-v-row" key={field.name}>
        <label className="oi-v-label">
          {field.label}
          {applyLink(field)}
        </label>
        <span className="oi-v-numwrap">
          <input
            className="oi-v-input"
            data-needed={needed}
            type="number"
            step="any"
            value={shown(field)}
            onChange={(event) => write(field, event.target.value)}
          />
          <span className="oi-v-unit">{field.unit}</span>
        </span>
        <span className="oi-v-tag" data-needed={needed}>{needed ? "Needed" : tag}</span>
      </div>
    );
  };

  const renderGroups = (level) =>
    groupFields(fields, level).map((group) => (
      <div className="oi-v-group" key={group.name}>
        <div className="oi-v-group-name">{group.name}</div>
        {group.fields.map(renderField)}
      </div>
    ));

  const statusText = (each) => {
    const count = stillNeeded(each).length;
    return count ? `Needs input (${count} ${count === 1 ? "field" : "fields"})` : "Ready — press Calculate";
  };

  const ranked = drafts.filter((each) => each.result);
  const waiting = drafts.filter((each) => !each.result);

  return (
    <div className="oi-v">
      <style>{`
        .oi-v { display: flex; flex-direction: column; gap: 0.8rem; font-size: 0.78rem; color: hsl(var(--foreground)); }
        .oi-v-card { border: 1px solid hsl(var(--border)); border-radius: 0.625rem; background: hsl(var(--background)); padding: 0.6rem 0.75rem; }
        .oi-v-title { font-size: 0.72rem; font-weight: 600; letter-spacing: 0.02em; text-transform: uppercase; color: hsl(var(--muted-foreground)); margin-bottom: 0.4rem; }
        .oi-v-rank { width: 100%; border-collapse: collapse; }
        .oi-v-rank td { padding: 0.3rem 0.4rem; border-bottom: 1px solid hsl(var(--border) / 0.6); white-space: nowrap; }
        .oi-v-rank tr:last-child td { border-bottom: none; }
        .oi-v-rank tr { cursor: pointer; }
        .oi-v-rank tr:hover { background: hsl(var(--accent) / 0.5); }
        .oi-v-rank tr[data-on="true"] { background: hsl(var(--primary) / 0.12); }
        .oi-v-rank td[data-num="true"] { text-align: right; font-variant-numeric: tabular-nums; }
        .oi-v-muted { color: hsl(var(--muted-foreground)); }
        .oi-v-group { display: flex; flex-direction: column; gap: 0.3rem; padding: 0.35rem 0; }
        .oi-v-group + .oi-v-group { border-top: 1px solid hsl(var(--border) / 0.6); }
        .oi-v-group-name { font-weight: 600; font-size: 0.74rem; }
        .oi-v-row { display: grid; grid-template-columns: minmax(0, 1fr) 11rem 4.6rem; align-items: center; gap: 0.5rem; }
        .oi-v-label { display: flex; flex-direction: column; min-width: 0; }
        .oi-v-apply {
          align-self: flex-start; margin-top: 0.15rem; padding: 0; font: inherit; font-size: 0.7rem;
          color: hsl(var(--primary)); background: none; border: none; cursor: pointer;
        }
        .oi-v-apply:disabled { color: hsl(var(--muted-foreground)); cursor: default; }
        .oi-v-place { font-size: 0.7rem; color: hsl(var(--muted-foreground)); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .oi-v-numwrap { display: flex; align-items: center; gap: 0.3rem; }
        .oi-v-input {
          width: 100%; min-width: 0; padding: 0.25rem 0.4rem; font: inherit; color: inherit;
          background: hsl(var(--background)); border: 1px solid hsl(var(--border)); border-radius: 0.375rem;
        }
        .oi-v-numwrap .oi-v-input { text-align: right; font-variant-numeric: tabular-nums; }
        .oi-v-input:focus { outline: none; border-color: hsl(var(--ring)); }
        .oi-v-input[data-needed="true"] { border-color: hsl(var(--destructive)); box-shadow: 0 0 0 1px hsl(var(--destructive)); }
        .oi-v-unit { flex: none; width: 3.2rem; font-size: 0.7rem; color: hsl(var(--muted-foreground)); }
        .oi-v-tag { font-size: 0.68rem; color: hsl(var(--muted-foreground)); text-align: right; }
        .oi-v-tag[data-needed="true"] { color: hsl(var(--destructive)); font-weight: 600; }
        .oi-v-dot { display: inline-block; width: 0.4rem; height: 0.4rem; margin-left: 0.3rem; border-radius: 50%; background: hsl(var(--destructive)); vertical-align: middle; }
        .oi-v-problem { color: hsl(var(--destructive)); font-size: 0.72rem; }
        .oi-v-bar { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
        .oi-v-calc {
          padding: 0.35rem 0.9rem; font: inherit; font-size: 0.75rem; font-weight: 500; border-radius: 0.4rem;
          border: 1px solid hsl(var(--primary)); background: hsl(var(--primary)); color: hsl(var(--primary-foreground)); cursor: pointer;
        }
        .oi-v-calc:disabled { opacity: 0.5; cursor: not-allowed; }
        .oi-v-details > summary { cursor: pointer; color: hsl(var(--muted-foreground)); font-size: 0.74rem; margin-bottom: 0.5rem; }
      `}</style>

      <div className="oi-v-card">
        <div className="oi-v-title">Ranking</div>
        <table className="oi-v-rank">
          <tbody>
            {ranked.map((each, at) => (
              <tr key={each.vessel_id} data-on={each.vessel_id === draft.vessel_id} onClick={() => setVesselId(each.vessel_id)}>
                <td className="oi-v-muted">{at + 1}</td>
                <td>{each.vessel_id}</td>
                <td data-num="true"><span className="oi-v-muted">revenue </span>{money(each.result.revenue)}</td>
                <td data-num="true"><span className="oi-v-muted">profit </span>{money(each.result.profit)}</td>
              </tr>
            ))}
            {waiting.map((each) => (
              <tr key={each.vessel_id} data-on={each.vessel_id === draft.vessel_id} onClick={() => setVesselId(each.vessel_id)}>
                <td className="oi-v-muted">–</td>
                <td>{each.vessel_id}</td>
                <td className="oi-v-muted" colSpan={2}>{statusText(each)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="oi-v-card">
        <div className="oi-v-title">Order fields · same for every vessel</div>
        {renderGroups("order")}
      </div>

      <div className="oi-v-card">
        <div className="oi-t-tabs">
          {drafts.map((each) => (
            <button
              key={each.vessel_id}
              className={each.vessel_id === draft.vessel_id ? "oi-t-tab oi-t-tab-on" : "oi-t-tab"}
              onClick={() => setVesselId(each.vessel_id)}
            >
              {each.vessel_id}
              {stillNeeded(each).length > 0 && <span className="oi-v-dot" title="Needs input" />}
            </button>
          ))}
        </div>
        <div className="oi-v-title" style={{ marginTop: "0.6rem" }}>
          {draft.vessel_id}
          {draft.result
            ? ` · revenue ${money(draft.result.revenue)} · profit ${money(draft.result.profit)}`
            : ` · ${statusText(draft).toLowerCase()}`}
        </div>
        {draft.problems.map((problem) => (
          <div className="oi-v-problem" key={problem}>{problem}</div>
        ))}
        {renderGroups("vessel")}
      </div>

      <div className="oi-v-bar">
        <span className="oi-t-notice">
          {notice ||
            (waitingCount
              ? `${waitingCount} ${waitingCount === 1 ? "vessel needs" : "vessels need"} input before you can calculate`
              : "")}
        </span>
        <button className="oi-v-calc" disabled={busy || waitingCount > 0} onClick={calculate}>
          {busy ? "Calculating…" : "Calculate"}
        </button>
      </div>
    </div>
  );
}

export default function Results() {
  const sets = (props.sets ?? []).filter((set) => (set.rows ?? []).length);
  const seq = Number(props.seq) || 0;
  const [tab, setTab] = useState(0);
  const [panelId, setPanelId] = useState(null);
  const [, setChanges] = useState(0);

  useEffect(() => {
    const store = mountedPanels();
    store.mounts += 1;
    const id = store.mounts;
    store.panels.set(id, { seq, order: id });
    const onChange = () => setChanges((count) => count + 1);
    window.addEventListener(CHANGE_EVENT, onChange);
    setPanelId(id);
    window.dispatchEvent(new Event(CHANGE_EVENT));
    return () => {
      store.panels.delete(id);
      window.removeEventListener(CHANGE_EVENT, onChange);
      window.dispatchEvent(new Event(CHANGE_EVENT));
    };
  }, [seq]);

  // an older panel mounted beside a newer one renders nothing; mounted alone,
  // as when its message link is clicked, it is the newest and shows
  if (!sets.length || !isNewestPanel(panelId)) return null;

  const active = sets[Math.min(tab, sets.length - 1)];
  return (
    <div className="oi-t-tabwrap">
      {sets.length > 1 && (
        <div className="oi-t-tabs">
          {sets.map((set, at) => (
            <button
              key={set.label ?? set.noun}
              className={at === tab ? "oi-t-tab oi-t-tab-on" : "oi-t-tab"}
              onClick={() => setTab(at)}
            >
              {set.label ?? set.noun} ({set.rows.length})
            </button>
          ))}
        </div>
      )}
      {active.voyages?.length ? (
        <>
          <Voyages key={active.label} set={active} />
          <details className="oi-v-details">
            <summary>Matched vessels table ({active.rows.length})</summary>
            <Table key={active.label ?? active.noun} {...active} />
          </details>
        </>
      ) : (
        <Table key={active.label ?? active.noun} {...active} />
      )}
    </div>
  );
}
