import { Icon, type IconName } from "./Icon";

export function SectionTitle({ icon, children }: { icon: IconName; children: string }) {
  return (
    <h2>
      <Icon name={icon} size={14} />
      {children}
    </h2>
  );
}
