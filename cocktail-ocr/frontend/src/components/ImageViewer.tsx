import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Hit } from "../types";

interface Props {
  src: string;
  hits: Hit[];
  showBoxes: boolean;
  focus: Hit | null;
  zoomCmd: { kind: "fit" | "full" | "in" | "out"; n: number };
}

const COLORS: Record<string, string> = {
  exact: "#22c55e", spaced: "#22c55e", confusion: "#f59e0b", near: "#f59e0b", compound: "#f59e0b", excluded: "#94a3b8",
};

export default function ImageViewer({ src, hits, showBoxes, focus, zoomCmd }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null);
  const [scale, setScale] = useState(1);
  const [fit, setFit] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => { setNat(null); setFit(true); setFailed(false); }, [src]);

  const fitScale = () => {
    const el = scrollRef.current;
    if (!el || !nat) return 1;
    return Math.min(1, (el.clientWidth - 8) / nat.w, (el.clientHeight - 8) / nat.h);
  };

  useLayoutEffect(() => {
    if (!nat) return;
    if (fit) setScale(fitScale());
    const on = () => fit && setScale(fitScale());
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nat, fit]);

  useEffect(() => {
    if (!nat || zoomCmd.n === 0) return;
    if (zoomCmd.kind === "fit") { setFit(true); setScale(fitScale()); }
    if (zoomCmd.kind === "full") { setFit(false); setScale(1); }
    if (zoomCmd.kind === "in") { setFit(false); setScale((s) => Math.min(8, s * 1.4)); }
    if (zoomCmd.kind === "out") { setFit(false); setScale((s) => Math.max(0.05, s / 1.4)); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoomCmd]);

  // Zoom to a selected hit: set the scale, then scroll once the enlarged stage is laid out.
  const pendingScroll = useRef<{ cx: number; cy: number } | null>(null);
  useEffect(() => {
    if (!focus || !nat || !scrollRef.current) return;
    const xs = focus.poly.map((p) => p[0]);
    const ys = focus.poly.map((p) => p[1]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const el = scrollRef.current;
    const s = Math.min(4, Math.max(0.2, Math.min(el.clientWidth / ((x1 - x0) * 4 || 1), el.clientHeight / ((y1 - y0) * 8 || 1))));
    pendingScroll.current = { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2 };
    setFit(false);
    setScale(s);
  }, [focus, nat]);
  useLayoutEffect(() => {
    const el = scrollRef.current;
    const t = pendingScroll.current;
    if (!el || !t) return;
    pendingScroll.current = null;
    el.scrollLeft = t.cx * scale - el.clientWidth / 2;
    el.scrollTop = t.cy * scale - el.clientHeight / 2;
  }, [scale]);

  const onWheel = (e: React.WheelEvent) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    setFit(false);
    setScale((s) => Math.min(8, Math.max(0.05, s * (e.deltaY < 0 ? 1.15 : 1 / 1.15))));
  };

  const w = nat ? nat.w * scale : undefined;
  const h = nat ? nat.h * scale : undefined;
  return (
    <div ref={scrollRef} className="scroll" onWheel={onWheel}>
      {failed && <div style={{ color: "#fff", padding: 20 }}>This image cannot be displayed (it may be corrupted). Download the original to inspect it.</div>}
      <div className="stage" style={{ width: w, height: h }}>
        <img src={src} alt="" style={{ width: w, height: h }} onError={() => setFailed(true)}
          onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} />
        {nat && showBoxes && (
          <svg viewBox={`0 0 ${nat.w} ${nat.h}`} width={w} height={h}>
            {hits.map((hh) => (
              <polygon key={hh.id} points={hh.poly.map((p) => p.join(",")).join(" ")} fill={focus?.id === hh.id ? "rgba(37,87,214,.18)" : "none"}
                stroke={COLORS[hh.kind] ?? "#f00"} strokeWidth={focus?.id === hh.id ? 3 : 2} vectorEffect="non-scaling-stroke"
                strokeDasharray={hh.box_approx ? "6 3" : undefined} />
            ))}
          </svg>
        )}
      </div>
    </div>
  );
}
