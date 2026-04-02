import type { ReviewStatus, ReviewParams } from "@/types";
import { cn } from "@/lib/utils";

interface Props {
  params: ReviewParams;
  onChange: (params: ReviewParams) => void;
  className?: string;
}

const statusOptions: { value: ReviewStatus | ""; label: string }[] = [
  { value: "", label: "All Needing Review" },
  { value: "needs_human_review", label: "Needs Human Review" },
  { value: "needs_ai_review", label: "Needs AI Review" },
];

const sortOptions: { value: ReviewParams["sort"]; label: string }[] = [
  { value: "updated_desc", label: "Recently Updated" },
  { value: "title_asc", label: "Title (A→Z)" },
  { value: "status_asc", label: "Status" },
];

export default function ReviewFilters({ params, onChange, className }: Props) {
  return (
    <div className={cn("flex flex-wrap items-center gap-3", className)}>
      {/* Search */}
      <div className="flex-1 min-w-[200px]">
        <input
          type="text"
          value={params.q ?? ""}
          onChange={(e) => onChange({ ...params, q: e.target.value || undefined, page: 1 })}
          placeholder="Search artist, title…"
          className="input-field w-full"
        />
      </div>

      {/* Status filter */}
      <select
        value={params.status ?? ""}
        onChange={(e) =>
          onChange({
            ...params,
            status: (e.target.value || null) as ReviewStatus | null,
            page: 1,
          })
        }
        className="input-field min-w-[150px]"
      >
        {statusOptions.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>

      {/* Sort */}
      <select
        value={params.sort ?? "updated_desc"}
        onChange={(e) =>
          onChange({ ...params, sort: e.target.value as ReviewParams["sort"] })
        }
        className="input-field min-w-[170px]"
      >
        {sortOptions.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}
