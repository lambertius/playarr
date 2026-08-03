import { useId, type ReactNode } from "react";

type Props = {
  label: ReactNode;
  description: ReactNode;
  children: ReactNode;
  controlId?: string;
  disabled?: boolean;
  className?: string;
  labelExtra?: ReactNode;
};

/** The only label/control/description layout used by Settings surfaces. */
export function SettingRowLayout({ label, description, children, controlId, disabled, className = "", labelExtra }: Props) {
  const descriptionId = useId();
  return (
    <div data-setting-row className={`grid grid-cols-1 gap-2 md:grid-cols-[minmax(9rem,0.8fr)_minmax(12rem,1fr)_minmax(14rem,1.25fr)] md:items-start md:gap-4 ${disabled ? "pointer-events-none opacity-40" : ""} ${className}`}>
      <div className="min-w-0 md:pt-2"><div className="flex items-center gap-1">
        <label htmlFor={controlId} className="text-sm font-medium text-text-primary">{label}</label>{labelExtra}
      </div></div>
      <div className="min-w-0" aria-describedby={descriptionId}>{children}</div>
      <p id={descriptionId} className="text-xs leading-relaxed text-text-muted md:pt-2">{description}</p>
    </div>
  );
}
