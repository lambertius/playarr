import type { ReactNode } from "react";

interface SectionProps {
  heading: string;
  children: ReactNode;
}

/** A labelled section divider with a grid of children. */
export function GroupedSection({ heading, children }: SectionProps) {
  return (
    <section className="mb-8">
      {/* Section header — sticky label */}
      <div className="flex items-center gap-3 mb-4 sticky top-0 z-20 bg-surface/90 backdrop-blur-sm py-2 -mx-4 px-4 md:-mx-6 md:px-6">
        <h2 className="text-lg font-bold text-accent tracking-wide">
          {heading}
        </h2>
        <div className="flex-1 h-px bg-surface-border" />
      </div>

      {/* Items grid */}
      <div className="grid grid-cols-[repeat(auto-fill,150px)] gap-4">
        {children}
      </div>
    </section>
  );
}
