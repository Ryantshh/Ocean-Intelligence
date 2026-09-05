import { useEffect, useRef, useState } from "react";

const OTHER = "__other__";

function isFilled(chosen, typed) {
  const labels = [...chosen].filter((label) => label !== OTHER);
  const own = chosen.has(OTHER) ? typed.trim() : "";
  return labels.length > 0 || own.length > 0;
}

function answerOf(chosen, typed) {
  const labels = [...chosen].filter((label) => label !== OTHER);
  const own = chosen.has(OTHER) ? typed.trim() : "";
  if (own) labels.push(own);
  return labels.join(", ");
}

export default function AskUser() {
  const questions = props.questions ?? [];
  const [active, setActive] = useState(0);
  const [picked, setPicked] = useState({});
  const [typed, setTyped] = useState({});
  const [sent, setSent] = useState(false);
  const otherRef = useRef(null);

  const chosenOf = (q, map = picked) => map[q.question] ?? new Set();
  const typedOf = (q, map = typed) => map[q.question] ?? "";
  const answered = (q, pickMap = picked, typeMap = typed) =>
    isFilled(chosenOf(q, pickMap), typedOf(q, typeMap));
  const allAnswered =
    questions.length > 0 && questions.every((q) => answered(q));

  const nextUnanswered = (from, pickMap, typeMap) => {
    for (let step = 1; step <= questions.length; step += 1) {
      const at = (from + step) % questions.length;
      if (!answered(questions[at], pickMap, typeMap)) return at;
    }
    return -1;
  };

  const submit = () => {
    if (sent || !allAnswered) return;
    setSent(true);
    submitElement(
      Object.fromEntries(
        questions.map((q) => [q.question, answerOf(chosenOf(q), typedOf(q))]),
      ),
    );
  };

  const advance = (pickMap = picked, typeMap = typed) => {
    const next = nextUnanswered(active, pickMap, typeMap);
    if (next >= 0) setActive(next);
  };

  const choose = (q, label) => {
    if (sent) return;
    const chosen = new Set(chosenOf(q));
    if (q.multi_select) {
      if (chosen.has(label)) chosen.delete(label);
      else chosen.add(label);
    } else {
      chosen.clear();
      chosen.add(label);
    }
    const nextPicked = { ...picked, [q.question]: chosen };
    setPicked(nextPicked);
    if (!q.multi_select && label !== OTHER) advance(nextPicked, typed);
  };

  const current = questions[active];

  useEffect(() => {
    if (current && chosenOf(current).has(OTHER)) otherRef.current?.focus();
  }, [active, picked]);

  const onKey = (event) => {
    if (sent || !current) return;
    if (event.key === "Escape") {
      cancelElement();
      return;
    }
    if (event.target.tagName === "INPUT") {
      if (event.key === "Enter") {
        event.preventDefault();
        if (allAnswered) submit();
        else advance();
      }
      return;
    }
    const index = Number(event.key) - 1;
    const rows = [...current.options.map((o) => o.label), OTHER];
    if (index >= 0 && index < rows.length) {
      event.preventDefault();
      choose(current, rows[index]);
    } else if (event.key === "Enter" && allAnswered) {
      submit();
    }
  };

  if (!current) return null;
  const chosen = chosenOf(current);

  return (
    <div className="oi-ask" tabIndex={0} onKeyDown={onKey}>
      <style>{`
        .oi-ask {
          display: flex;
          flex-direction: column;
          gap: 0.7rem;
          padding: 0.9rem 1rem;
          border: 1px solid hsl(var(--border));
          border-radius: 0.625rem;
          background: hsl(var(--background));
          color: hsl(var(--foreground));
          font-size: 0.82rem;
          outline: none;
        }
        .oi-ask:focus-visible { box-shadow: 0 0 0 2px hsl(var(--ring) / 0.4); }
        .oi-ask-tabs { display: flex; gap: 0.3rem; flex-wrap: wrap; }
        .oi-ask-tab {
          max-width: 9rem;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          padding: 0.2rem 0.6rem;
          border-radius: 9999px;
          border: 1px solid hsl(var(--border));
          background: transparent;
          color: hsl(var(--muted-foreground));
          font-size: 0.72rem;
          cursor: pointer;
        }
        .oi-ask-tab[data-on="true"] {
          color: hsl(var(--foreground));
          border-color: hsl(var(--primary));
          background: hsl(var(--primary) / 0.1);
        }
        .oi-ask-tab[data-done="true"]::before { content: "\\2713 "; }
        .oi-ask-head { display: flex; align-items: baseline; gap: 0.5rem; }
        .oi-ask-chip {
          padding: 0.1rem 0.45rem;
          border-radius: 0.3rem;
          background: hsl(var(--muted));
          color: hsl(var(--muted-foreground));
          font-size: 0.68rem;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }
        .oi-ask-q { font-weight: 600; }
        .oi-ask-opts { display: flex; flex-direction: column; gap: 0.3rem; }
        .oi-ask-opt {
          display: grid;
          grid-template-columns: 1.4rem 1fr;
          gap: 0.2rem 0.5rem;
          align-items: baseline;
          text-align: left;
          padding: 0.45rem 0.6rem;
          border: 1px solid hsl(var(--border));
          border-radius: 0.5rem;
          background: transparent;
          color: hsl(var(--foreground));
          cursor: pointer;
        }
        .oi-ask-opt:hover { background: hsl(var(--muted)); }
        .oi-ask-opt[data-on="true"] {
          border-color: hsl(var(--primary));
          background: hsl(var(--primary) / 0.1);
        }
        .oi-ask-num {
          color: hsl(var(--muted-foreground));
          font-variant-numeric: tabular-nums;
          font-size: 0.72rem;
        }
        .oi-ask-label { font-weight: 600; }
        .oi-ask-desc {
          grid-column: 2;
          color: hsl(var(--muted-foreground));
          font-size: 0.76rem;
          line-height: 1.35;
        }
        .oi-ask-other {
          grid-column: 2;
          margin-top: 0.2rem;
          padding: 0.35rem 0.5rem;
          border: 1px solid hsl(var(--border));
          border-radius: 0.4rem;
          background: hsl(var(--background));
          color: hsl(var(--foreground));
          font-size: 0.8rem;
          outline: none;
        }
        .oi-ask-other:focus { border-color: hsl(var(--ring)); }
        .oi-ask-foot {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 0.5rem;
        }
        .oi-ask-hint { color: hsl(var(--muted-foreground)); font-size: 0.7rem; }
        .oi-ask-send {
          padding: 0.4rem 0.9rem;
          border: none;
          border-radius: 0.5rem;
          background: hsl(var(--primary));
          color: hsl(var(--primary-foreground));
          font-size: 0.8rem;
          font-weight: 600;
          cursor: pointer;
        }
        .oi-ask-send:disabled { opacity: 0.5; cursor: default; }
      `}</style>

      {questions.length > 1 && (
        <div className="oi-ask-tabs">
          {questions.map((q, at) => (
            <button
              key={q.question}
              className="oi-ask-tab"
              data-on={at === active}
              data-done={answered(q)}
              title={q.question}
              onClick={() => setActive(at)}
            >
              {q.header}
            </button>
          ))}
        </div>
      )}

      <div className="oi-ask-head">
        <span className="oi-ask-chip">{current.header}</span>
        <span className="oi-ask-q">{current.question}</span>
      </div>

      <div className="oi-ask-opts">
        {current.options.map((option, at) => (
          <button
            key={option.label}
            className="oi-ask-opt"
            data-on={chosen.has(option.label)}
            disabled={sent}
            onClick={() => choose(current, option.label)}
          >
            <span className="oi-ask-num">{at + 1}</span>
            <span className="oi-ask-label">{option.label}</span>
            <span className="oi-ask-desc">{option.description}</span>
          </button>
        ))}
        <button
          className="oi-ask-opt"
          data-on={chosen.has(OTHER)}
          disabled={sent}
          onClick={() => choose(current, OTHER)}
        >
          <span className="oi-ask-num">{current.options.length + 1}</span>
          <span className="oi-ask-label">Other</span>
          <span className="oi-ask-desc">Type your own answer</span>
          {chosen.has(OTHER) && (
            <input
              ref={otherRef}
              className="oi-ask-other"
              value={typedOf(current)}
              placeholder="your answer"
              disabled={sent}
              onClick={(event) => event.stopPropagation()}
              onChange={(event) =>
                setTyped({ ...typed, [current.question]: event.target.value })
              }
            />
          )}
        </button>
      </div>

      <div className="oi-ask-foot">
        <span className="oi-ask-hint">
          {current.multi_select ? "pick any that apply" : "1–" + (current.options.length + 1) + " to pick"}
          {questions.length > 1 ? " · Enter to continue" : " · Enter to submit"} · Esc to cancel
        </span>
        <button
          className="oi-ask-send"
          disabled={sent || !allAnswered}
          onClick={submit}
        >
          {sent ? "Sending…" : "Submit"}
        </button>
      </div>
    </div>
  );
}
