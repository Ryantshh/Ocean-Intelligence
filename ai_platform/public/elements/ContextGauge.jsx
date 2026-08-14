import { useEffect, useRef, useState } from "react";

const COMPOSER_SELECTORS = ["#message-composer", "#chat-input"];
const GAP_ABOVE_COMPOSER = 10;
const FALLBACK_BOTTOM = 96;

export default function ContextGauge() {
  const anchorRef = useRef(null);
  const [box, setBox] = useState(null);

  useEffect(() => {
    let observed = null;

    const findComposer = () => {
      for (const selector of COMPOSER_SELECTORS) {
        const found = document.querySelector(selector);
        if (found) return found;
      }
      return null;
    };

    // Always produces a box. Anchoring to the composer lines the gauge up with
    // the chat edge; failing that it falls back to our own anchor, which is in
    // the flow of whatever column we were rendered into and therefore always
    // measurable.
    const measure = () => {
      const composer = findComposer();
      if (composer && composer !== observed) {
        if (observed) observer.unobserve(observed);
        observer.observe(composer);
        observed = composer;
      }
      const rect = composer?.getBoundingClientRect();
      const own = anchorRef.current?.getBoundingClientRect();
      const next = rect?.width
        ? {
            left: rect.left,
            width: rect.width,
            bottom: window.innerHeight - rect.top + GAP_ABOVE_COMPOSER,
            anchored: true,
          }
        : {
            left: own?.width ? own.left : 0,
            width: own?.width || window.innerWidth,
            bottom: FALLBACK_BOTTOM,
            anchored: false,
          };
      setBox((current) =>
        current &&
        current.left === next.left &&
        current.width === next.width &&
        current.bottom === next.bottom &&
        current.anchored === next.anchored
          ? current
          : next,
      );
    };

    // ResizeObserver reports size changes, never position. The composer is
    // width-capped, so widening the window past the cap only re-centres it and
    // the observer stays silent. Sampling while a pointer is held covers the
    // drag of a panel divider, which is when that happens.
    let frame = 0;
    const followDrag = () => {
      measure();
      frame = requestAnimationFrame(followDrag);
    };
    const startFollow = () => {
      if (!frame) frame = requestAnimationFrame(followDrag);
    };
    const stopFollow = () => {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      measure();
    };

    const observer = new ResizeObserver(measure);
    measure();
    if (anchorRef.current) observer.observe(anchorRef.current);
    const retry = setTimeout(measure, 400);
    window.addEventListener("resize", measure);
    document.addEventListener("pointerdown", startFollow);
    document.addEventListener("pointerup", stopFollow);
    document.addEventListener("pointercancel", stopFollow);
    return () => {
      clearTimeout(retry);
      if (frame) cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", measure);
      document.removeEventListener("pointerdown", startFollow);
      document.removeEventListener("pointerup", stopFollow);
      document.removeEventListener("pointercancel", stopFollow);
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

  return (
    <div className="oi-gauge-anchor" ref={anchorRef}>
      <style>{`
        .oi-gauge-anchor { height: 0; width: 100%; margin: 0; padding: 0; }
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
      {box && (
        <div
          className="oi-gauge-rail"
          style={{ left: box.left, width: box.width, bottom: box.bottom }}
        >
          <div
            className="oi-gauge"
            title={
              `Conversation using ${used.toLocaleString()} of ` +
              `${usable.toLocaleString()} usable tokens. ` +
              `Last question cost ${spent.toLocaleString()}.` +
              (box.anchored ? "" : " Not anchored: no #message-composer found.")
            }
          >
            <span className="oi-gauge-caption">
              {box.anchored ? "context" : "context ~"}
            </span>
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
      )}
    </div>
  );
}
