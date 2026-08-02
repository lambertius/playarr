import { useCallback, useEffect, useState } from "react";
import { Check, Edit3, Loader2, Plus, Trash2, X } from "lucide-react";
import { metadataManagerApi } from "@/lib/api";
import type { GenreConsolidationAggregate } from "@/types";
import { useToast } from "@/components/Toast";
import { Skeleton } from "@/components/Feedback";

export function GenreConsolidationEditor() {
  const [items, setItems] = useState<GenreConsolidationAggregate[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<{ stableId?: string; revision: number; maskName: string; targets: string[] } | null>(null);
  const { toast } = useToast();
  const load = useCallback(async () => {
    setLoading(true);
    try { setItems(await metadataManagerApi.genreConsolidationsV2()); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const edit = (item: GenreConsolidationAggregate) => setDraft({
    stableId: item.stable_id, revision: item.revision, maskName: item.mask_name,
    targets: item.target_genres.map(member => member.raw_name),
  });
  const save = async () => {
    if (!draft?.maskName.trim()) return;
    const body = { mask_name: draft.maskName.trim(), target_genres: draft.targets.filter(value => value.trim()).map(raw_name => ({ raw_name: raw_name.trim() })) };
    setSaving(true);
    try {
      if (draft.stableId) await metadataManagerApi.updateGenreConsolidationV2(draft.stableId, { ...body, expected_revision: draft.revision });
      else await metadataManagerApi.createGenreConsolidationV2(body);
      setDraft(null); await load();
      toast({ type: "success", title: "Genre consolidation saved; source tags were retained" });
    } catch { toast({ type: "error", title: "Could not save; reload if another device changed this consolidation" }); }
    finally { setSaving(false); }
  };
  const remove = async (item: GenreConsolidationAggregate) => {
    try {
      await metadataManagerApi.deleteGenreConsolidationV2(item.stable_id, item.revision);
      await load(); toast({ type: "success", title: "Genre consolidation deleted; source tags were retained" });
    } catch { toast({ type: "error", title: "Could not delete genre consolidation" }); }
  };
  if (loading) return <Skeleton className="h-48 rounded-lg" />;
  return <div className="space-y-4">
    <div className="flex items-center justify-between gap-4"><p className="text-xs text-text-muted">Display masks group raw genre tags without rewriting provider values. Changes use optimistic revisions.</p><button className="btn-primary btn-sm" onClick={() => setDraft({ revision: 0, maskName: "", targets: [] })}><Plus size={13} /> New consolidation</button></div>
    <div className="rounded-lg border border-white/5 overflow-hidden"><div className="grid grid-cols-[1fr_2fr_auto] gap-3 bg-surface-light/20 px-4 py-2 text-[11px] uppercase text-text-muted"><span>Mask name</span><span>Target genres</span><span>Actions</span></div>
      {items.length === 0 ? <p className="p-6 text-center text-xs text-text-muted">No genre consolidations yet.</p> : items.map(item => <div key={item.stable_id} className="grid grid-cols-[1fr_2fr_auto] gap-3 items-center border-t border-white/5 px-4 py-3"><span className="font-medium text-sm">{item.mask_name}</span><span className="text-xs text-text-secondary">{item.target_genres.map(member => `${member.raw_name} (${member.linked_video_count})`).join(", ") || "No targets"}</span><div className="flex gap-1"><button className="btn-ghost btn-xs" onClick={() => edit(item)}><Edit3 size={12} /> Edit</button><button className="btn-ghost btn-xs text-red-400" onClick={() => void remove(item)}><Trash2 size={12} /> Delete</button></div></div>)}
    </div>
    {draft && <div className="card space-y-3"><div className="flex justify-between"><h3 className="text-sm font-semibold">{draft.stableId ? "Edit" : "New"} genre consolidation</h3><button className="btn-ghost btn-xs" aria-label="Close editor" onClick={() => setDraft(null)}><X size={13} /></button></div><label className="block text-xs text-text-secondary">Mask name<input className="input-field mt-1" value={draft.maskName} onChange={event => setDraft({ ...draft, maskName: event.target.value })} /></label>
      <div className="space-y-2"><div className="flex justify-between"><span className="text-xs font-medium text-text-secondary">Target genres</span><button className="btn-ghost btn-xs" onClick={() => setDraft({ ...draft, targets: [...draft.targets, ""] })}><Plus size={11} /> Add target</button></div>{draft.targets.map((target, index) => <div key={index} className="flex gap-2"><input aria-label={`Target genre ${index + 1}`} className="input-field flex-1" value={target} onChange={event => setDraft({ ...draft, targets: draft.targets.map((value, idx) => idx === index ? event.target.value : value) })} /><button className="btn-ghost btn-xs text-red-400" onClick={() => setDraft({ ...draft, targets: draft.targets.filter((_, idx) => idx !== index) })}><X size={12} /> Remove</button></div>)}</div>
      <div className="flex justify-end"><button className="btn-primary btn-sm" disabled={saving || !draft.maskName.trim()} onClick={() => void save()}>{saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Save</button></div>
    </div>}
  </div>;
}
