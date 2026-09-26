import { useCallback, useState } from "react";

export interface TipState {
  text: string;
  x: number;
  y: number;
}

export function useTooltip() {
  const [tip, setTip] = useState<TipState | null>(null);
  const show = useCallback((text: string, target: HTMLElement) => {
    const r = target.getBoundingClientRect();
    setTip({ text, x: r.left + r.width / 2, y: r.top });
  }, []);
  const hide = useCallback(() => setTip(null), []);
  return { tip, show, hide };
}

export function Tooltip({ tip }: { tip: TipState | null }) {
  if (!tip) return null;
  return (
    <div className="tip" role="tooltip" style={{ left: tip.x, top: tip.y - 8, transform: "translate(-50%, -100%)" }}>
      {tip.text}
    </div>
  );
}
