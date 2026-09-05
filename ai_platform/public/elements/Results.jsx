import { useEffect, useMemo, useRef, useState } from "react";

const PAGE_SIZE = 12;
const SELECTION_CAP = 25;
const PAGE_WINDOW = 2;
const BLANK = "(blank)";

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


export default function Results() {
  const sets = (props.sets ?? []).filter((set) => (set.rows ?? []).length);
  const [tab, setTab] = useState(0);

  if (!sets.length) return null;

  const active = sets[Math.min(tab, sets.length - 1)];
  return (
    <div className="oi-t-tabwrap">
      {sets.length > 1 && (
        <div className="oi-t-tabs">
          {sets.map((set, at) => (
            <button
              key={set.noun}
              className={at === tab ? "oi-t-tab oi-t-tab-on" : "oi-t-tab"}
              onClick={() => setTab(at)}
            >
              {set.noun} ({set.rows.length})
            </button>
          ))}
        </div>
      )}
      <Table key={active.noun} {...active} />
    </div>
  );
}
