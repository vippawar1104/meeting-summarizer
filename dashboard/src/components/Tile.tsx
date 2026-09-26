import { Icon, type IconName } from "./Icon";

export function Tile({ label, value, hint, icon }: { label: string; value: string; hint?: string; icon: IconName }) {
  return (
    <div className="tile">
      <div className="tile-icon">
        <Icon name={icon} size={15} />
      </div>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}
