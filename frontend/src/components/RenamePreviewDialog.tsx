import { AlertTriangle, Check, FolderSync, Loader2 } from "lucide-react";

import { PopupOverlay } from "@/components/PopupOverlay";

export interface RenamePreviewPlan {
  old_folder: string;
  new_folder: string;
  collisions: { source: string; destination: string; reason: string }[];
  active_stream_usage: boolean;
  cross_volume: boolean;
  case_only: boolean;
  steps: { role: string; source: string; destination: string; size_bytes: number }[];
}

const leaf = (path: string) => path.split(/[/\\]/).filter(Boolean).at(-1) ?? path;

export function RenamePreviewDialog({
  plan,
  isPending,
  onRename,
  onClose,
}: {
  plan: RenamePreviewPlan;
  isPending: boolean;
  onRename: () => void;
  onClose: () => void;
}) {
  const blocked = plan.collisions.length > 0;
  return (
    <PopupOverlay onClose={onClose}>
      <h2 className="mb-1 text-lg font-semibold text-text-primary">Rename preview</h2>
      <p className="mb-4 text-sm text-text-secondary">
        Playarr will journal and verify every listed path before updating the library database.
      </p>

      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <PathValue label="Current folder" value={leaf(plan.old_folder)} />
        <PathValue label="Expected folder" value={leaf(plan.new_folder)} accent />
      </div>

      {(plan.active_stream_usage || plan.cross_volume || plan.case_only) && (
        <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          {plan.active_stream_usage && <div>Playback will be stopped before the media file moves.</div>}
          {plan.cross_volume && <div>Cross-volume files will be copied and checksum-verified before removal.</div>}
          {plan.case_only && <div>A staged temporary name will be used for the case-only rename.</div>}
        </div>
      )}

      <div className="mb-4 max-h-64 space-y-2 overflow-y-auto" aria-label="Paths that will change">
        {plan.steps.map((step) => (
          <div key={step.source} className="rounded-lg bg-surface-light/50 px-3 py-2 text-xs">
            <div className="mb-1 font-medium uppercase tracking-wide text-text-muted">{step.role}</div>
            <div className="break-all font-mono text-text-secondary">{step.source}</div>
            <div className="my-0.5 text-text-muted">→</div>
            <div className="break-all font-mono text-text-primary">{step.destination}</div>
          </div>
        ))}
      </div>

      {blocked ? (
        <div className="mb-4 flex gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          {plan.collisions.length} destination collision{plan.collisions.length === 1 ? "" : "s"} must be resolved before renaming.
        </div>
      ) : (
        <div className="mb-4 flex items-center gap-2 text-sm text-green-400">
          <Check size={14} /> {plan.steps.length} paths checked; no collisions
        </div>
      )}

      <div className="flex justify-end gap-3">
        <button onClick={onClose} className="btn-secondary btn-sm">Cancel</button>
        <button onClick={onRename} disabled={isPending || blocked} className="btn-primary btn-sm">
          {isPending ? <Loader2 size={14} className="animate-spin" /> : <FolderSync size={14} />}
          Rename {plan.steps.length} files
        </button>
      </div>
    </PopupOverlay>
  );
}

function PathValue({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <span className="text-xs uppercase tracking-wider text-text-muted">{label}</span>
      <div className={`mt-1 break-all rounded-lg px-3 py-2 font-mono text-sm ${accent ? "bg-accent/10 text-accent" : "bg-surface-light/50 text-text-primary"}`}>
        {value}
      </div>
    </div>
  );
}
