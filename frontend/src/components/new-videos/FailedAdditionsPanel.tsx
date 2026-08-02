import { RotateCcw, Undo2 } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { jobsApi, newVideosApi } from "@/lib/api";
import type { FailedNewVideoAddition } from "@/types";
import { useToast } from "@/components/Toast";

export function FailedAdditionsPanel({ items }: { items: FailedNewVideoAddition[] }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const retry = useMutation({
    mutationFn: (jobId: number) => jobsApi.retry(jobId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["newVideosFeed"] });
      qc.invalidateQueries({ queryKey: ["jobPage"] });
      toast({ type: "success", title: "Import queued again" });
    },
  });
  const restore = useMutation({
    mutationFn: (jobId: number) => newVideosApi.restoreFailed(jobId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["newVideosFeed"] });
      toast({ type: "success", title: "Suggestion restored" });
    },
  });

  if (!items.length) return null;
  return (
    <section aria-labelledby="failed-additions-title" className="rounded-lg border border-red-500/30 bg-red-500/5 p-4">
      <h2 id="failed-additions-title" className="font-semibold text-red-300">Failed additions</h2>
      <p className="mt-1 text-sm text-text-muted">Retry the import, or restore the suggestion to the discovery list.</p>
      <div className="mt-3 grid gap-2">
        {items.map(item => (
          <div key={item.job_id} className="flex items-center gap-3 rounded border border-surface-border bg-surface p-2">
            {item.suggestion.thumbnail_url && <img src={item.suggestion.thumbnail_url} alt="" className="h-12 w-20 rounded object-cover" />}
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{item.suggestion.artist ? `${item.suggestion.artist} — ` : ""}{item.suggestion.title}</div>
              <div className="truncate text-xs text-red-300" title={item.error}>{item.error}</div>
            </div>
            <button className="btn btn-sm" disabled={retry.isPending} onClick={() => retry.mutate(item.job_id)}><RotateCcw size={14} /> Retry</button>
            <button className="btn btn-sm" disabled={restore.isPending} onClick={() => restore.mutate(item.job_id)}><Undo2 size={14} /> Restore suggestion</button>
          </div>
        ))}
      </div>
    </section>
  );
}
