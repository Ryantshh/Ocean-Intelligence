import { useEffect, useState } from "react";

const COMPOSER_SELECTORS = ["#message-composer", "#chat-input"];
const GAP_ABOVE_COMPOSER = 10;

export default function ContextGauge() {
  const [box, setBox] = useState(null);

  useEffect(() => {
    let observed = null;
    const observer = new ResizeObserver(() => measure());

    // Re-queried every pass rather than captured once: React can replace the node,
    // and the gauge hangs off its geometry rather than its own position in the flow.
    const findComposer = () => {
      for (const selector of COMPOSER_SELECTORS) {
        const found = document.querySelector(selector);
        if (found) return found;
      }
      return null;
    };

    const measure = () => {
      const composer = findComposer();
      if (!composer) return;
      if (composer !== observed) {
        if (observed) observer.unobserve(observed);
        observer.observe(composer);
        observed = composer;
      }
      const rect = composer.getBoundingClientRect();
      if (!rect.width) return;
      setBox({
        left: rect.left,
        width: rect.width,
        bottom: window.innerHeight - rect.top + GAP_ABOVE_COMPOSER,
      });
    };

    measure();
    const retry = setTimeout(measure, 300);
    window.addEventListener("resize", measure);
    return () => {
      clearTimeout(retry);
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  const percent = Math.max(0, Math.min(100, props.percent ?? 0));
  const used = props.used ?? 0;
  const usable = props.usable ?? 0;
  const spent = props.spent ?? 0;

  const fill =
    percent >= 80
      ? "hsl(0 72% 51%)"
      : percent >= 50
        ? "hsl(38 92% 50%)"
        : "hsl(var(--primary))";

  const label = percent > 0 && percent < 1 ? "<1%" : `${Math.round(percent)}%`;

  if (!box) return null;

  return (
    <div
      className="oi-gauge-rail"
      style={{ left: box.left, width: box.width, bottom: box.bottom }}
    >
      <style>{`
        .oi-gauge-rail {
          position: fixed;
          display: flex;
          justify-content: flex-end;
          pointer-events: none;
          z-index: 20;
        }
        .oi-gauge {
          display: inline-flex;
          align-items: center;
          gap: 0.6rem;
          padding: 0.4rem 0.75rem;
          border-radius: 9999px;
          border: 1px solid hsl(var(--border));
          background: hsl(var(--background));
          color: hsl(var(--foreground));
          font-size: 0.78rem;
          font-variant-numeric: tabular-nums;
          line-height: 1;
          box-shadow: 0 1px 3px rgb(0 0 0 / 0.12);
          pointer-events: auto;
        }
        .oi-gauge-caption { color: hsl(var(--muted-foreground)); }
        .oi-gauge-track {
          width: 4.5rem;
          height: 6px;
          border-radius: 9999px;
          background: hsl(var(--muted));
          overflow: hidden;
        }
        .oi-gauge-fill {
          height: 100%;
          width: 100%;
          transform-origin: left center;
          transition: transform 0.3s ease;
        }
        @media (max-width: 768px) {
          .oi-gauge-track { width: 3rem; }
          .oi-gauge-caption { display: none; }
        }
      `}</style>
      <div
        className="oi-gauge"
        title={`Conversation using ${used.toLocaleString()} of ${usable.toLocaleString()} usable tokens. Last question cost ${spent.toLocaleString()}.`}
      >
        <span className="oi-gauge-caption">context</span>
        <div className="oi-gauge-track">
          <div
            className="oi-gauge-fill"
            style={{
              transform: `scaleX(${Math.max(percent, 1.5) / 100})`,
              background: fill,
            }}
          />
        </div>
        <span>{label}</span>
      </div>
    </div>
  );
}
