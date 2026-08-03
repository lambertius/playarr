import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Edit3, Eye, EyeOff, Plus, Trash2 } from "lucide-react";
import { metadataManagerApi } from "@/lib/api";
import type { GenreConsolidationAggregate, GenreSuggestion } from "@/types";
import { useToast } from "@/components/Toast";
import { Skeleton } from "@/components/Feedback";
import { ConsolidationColumnsEditor, type ConsolidationColumnsDraft } from "@/components/ConsolidationColumnsEditor";
import { useGenreBlacklist, useUpdateGenreBlacklist } from "@/hooks/queries";

type GenreDraft = ConsolidationColumnsDraft & { stableId?: string; sourceSuggestionId?: string; revision: number };

export function GenreConsolidationEditor() {
  const queryClient = useQueryClient();
  const [items, setItems] = useState<GenreConsolidationAggregate[]>([]);
  const [suggestions, setSuggestions] = useState<GenreSuggestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<GenreDraft | null>(null);
  const [view, setView] = useState<"in_place" | "suggested">("in_place");
  const { data: genres = [] } = useGenreBlacklist();
  const updateVisibility = useUpdateGenreBlacklist();
  const { toast } = useToast();

  const refreshGenreViews = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["genreBlacklist"] }),
      queryClient.invalidateQueries({ queryKey: ["genres"] }),
      queryClient.invalidateQueries({ queryKey: ["library"] }),
    ]);
  }, [queryClient]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [saved, suggested] = await Promise.all([
        metadataManagerApi.genreConsolidationsV2(),
        metadataManagerApi.genreSuggestions(),
      ]);
      setItems(saved);
      const covered = new Set(saved.flatMap(item => [item.mask_name, ...item.target_genres.map(member => member.raw_name)]).map(name => name.toLocaleLowerCase()));
      setSuggestions(suggested.filter(item => ![item.master_name, ...item.aliases.map(alias => alias.name)].every(name => covered.has(name.toLocaleLowerCase()))));
    }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const search = useCallback(async (query: string) => (
    await metadataManagerApi.genreSearch(query)
  ).map(result => ({ name: result.name, video_count: result.video_count })), []);

  const edit = (item: GenreConsolidationAggregate) => setDraft({
    stableId: item.stable_id,
    revision: item.revision,
    maskName: item.mask_name,
    targets: item.target_genres.map(member => ({ rawName: member.raw_name, videoCount: member.linked_video_count })),
    mbids: [],
  });

  const save = async () => {
    if (!draft?.maskName.trim()) return;
    const body = {
      mask_name: draft.maskName.trim(),
      target_genres: draft.targets.filter(target => target.rawName.trim()).map(target => ({ raw_name: target.rawName.trim() })),
    };
    setSaving(true);
    try {
      if (draft.stableId) await metadataManagerApi.updateGenreConsolidationV2(draft.stableId, { ...body, expected_revision: draft.revision });
      else await metadataManagerApi.createGenreConsolidationV2(body);
      setDraft(null);
      await Promise.all([load(), refreshGenreViews()]);
      toast({ type: "success", title: "Genre consolidation saved; source tags were retained" });
    } catch {
      toast({ type: "error", title: "Could not save; reload if another device changed this consolidation" });
    } finally { setSaving(false); }
  };

  const remove = async (item: GenreConsolidationAggregate) => {
    try {
      await metadataManagerApi.deleteGenreConsolidationV2(item.stable_id, item.revision);
      await Promise.all([load(), refreshGenreViews()]);
      toast({ type: "success", title: "Genre consolidation deleted; source tags were retained" });
    } catch { toast({ type: "error", title: "Could not delete genre consolidation" }); }
  };

  const reviewSuggestion = (suggestion: GenreSuggestion) => setDraft({
    sourceSuggestionId: suggestion.suggestion_id,
    revision: 0,
    maskName: suggestion.master_name,
    targets: [
      { rawName: suggestion.master_name },
      ...suggestion.aliases.map(alias => ({ rawName: alias.name, videoCount: alias.video_count })),
    ],
    mbids: [],
  });

  const acceptSuggestion = async (suggestion: GenreSuggestion) => {
    try {
      await metadataManagerApi.createGenreConsolidationV2({
        mask_name: suggestion.master_name,
        target_genres: [suggestion.master_name, ...suggestion.aliases.map(alias => alias.name)].map(raw_name => ({ raw_name, provenance_json: { source: "regex_suggestion" } })),
      });
      await Promise.all([load(), refreshGenreViews()]);
      toast({ type: "success", title: "Suggested genre consolidation accepted" });
    } catch { toast({ type: "error", title: "Could not accept genre consolidation" }); }
  };

  const dismissSuggestion = async (suggestion: GenreSuggestion) => {
    await metadataManagerApi.dismissConsolidationSuggestion("genre", suggestion.suggestion_id);
    if (draft?.sourceSuggestionId === suggestion.suggestion_id) setDraft(null);
    await load();
    toast({ type: "success", title: "Suggested genre consolidation dissolved" });
  };

  const toggleVisibility = (item: GenreConsolidationAggregate) => {
    const linked = genres.find(genre => genre.name.toLocaleLowerCase() === item.mask_name.toLocaleLowerCase());
    if (!linked) {
      toast({ type: "info", title: "No stored genre rows are attached to this consolidation" });
      return;
    }
    const shouldBlacklist = !linked.blacklisted;
    updateVisibility.mutate({ genre_ids: linked.genre_ids, blacklisted: shouldBlacklist }, {
      onSuccess: () => toast({ type: "success", title: shouldBlacklist ? "Genre consolidation hidden" : "Genre consolidation visible" }),
      onError: () => toast({ type: "error", title: "Could not change genre visibility" }),
    });
  };

  if (loading) return <Skeleton className="h-48 rounded-lg" />;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <p className="text-xs text-text-muted">Genre masks and raw target tags use the same workflow as artists. Provider values are retained.</p>
        <button className="btn-primary btn-sm" onClick={() => setDraft({ revision: 0, maskName: "", targets: [], mbids: [] })}><Plus size={13} /> New consolidation</button>
      </div>

      <div className="flex gap-1 border-b border-surface-border">
        <button className={`px-4 py-2 text-xs font-medium ${view === "in_place" ? "border-b-2 border-primary text-text-primary" : "text-text-muted"}`} onClick={() => { setView("in_place"); setDraft(null); }}>In place <span className="ml-1 text-text-muted">{items.length}</span></button>
        <button className={`px-4 py-2 text-xs font-medium ${view === "suggested" ? "border-b-2 border-primary text-text-primary" : "text-text-muted"}`} onClick={() => { setView("suggested"); setDraft(null); }}>Suggested <span className="ml-1 text-text-muted">{suggestions.length}</span></button>
      </div>

      {draft && !draft.stableId && !draft.sourceSuggestionId && <ConsolidationColumnsEditor kind="genre" value={draft} onChange={value => setDraft({ ...draft, ...value })} onSave={() => void save()} onCancel={() => setDraft(null)} saving={saving} search={search} />}

      <div className="rounded-lg border border-surface-border overflow-hidden">
        <div className="grid grid-cols-[minmax(180px,1fr)_minmax(260px,2fr)_auto] gap-3 bg-surface-dark px-4 py-2 text-[10px] font-semibold uppercase tracking-wide text-text-muted"><span>Mask name</span><span>Target genres</span><span>Actions</span></div>
        {view === "in_place" && items.length === 0 && <p className="p-6 text-center text-xs text-text-muted">No genre consolidations are in place.</p>}
        {view === "suggested" && suggestions.length === 0 && <p className="p-6 text-center text-xs text-text-muted">No unresolved regex suggestions.</p>}
        {view === "in_place" && items.map(item => {
          const linked = genres.find(genre => genre.name.toLocaleLowerCase() === item.mask_name.toLocaleLowerCase());
          const blacklisted = linked?.blacklisted ?? false;
          return (
            <div key={item.stable_id} className="border-t border-surface-border">
            <div className="grid grid-cols-[minmax(180px,1fr)_minmax(260px,2fr)_auto] gap-3 items-start px-4 py-3">
              <span className="font-medium text-sm text-text-primary">{item.mask_name}</span>
              <div className="flex flex-wrap gap-1">{item.target_genres.length ? item.target_genres.map(member => <span key={member.id} className="rounded bg-surface-light px-2 py-1 text-xs text-text-secondary">{member.raw_name} <span className="text-text-muted">{member.linked_video_count}</span></span>) : <span className="text-xs text-text-muted">No targets</span>}</div>
              <div className="flex flex-wrap justify-end gap-1">
                <button className="btn-ghost btn-xs" onClick={() => edit(item)}><Edit3 size={12} /> Edit</button>
                <button className="btn-ghost btn-xs" onClick={() => toggleVisibility(item)}>{blacklisted ? <Eye size={12} /> : <EyeOff size={12} />}{blacklisted ? "Whitelist" : "Blacklist"}</button>
                <button className="btn-ghost btn-xs text-red-400" onClick={() => void remove(item)}><Trash2 size={12} /> Dissolve</button>
              </div>
            </div>
            {draft?.stableId === item.stable_id && <div className="p-3"><ConsolidationColumnsEditor kind="genre" value={draft} onChange={value => setDraft({ ...draft, ...value })} onSave={() => void save()} onCancel={() => setDraft(null)} saving={saving} search={search} /></div>}
            </div>
          );
        })}
        {view === "suggested" && suggestions.map(suggestion => (
          <div key={suggestion.suggestion_id} className="border-t border-amber-400/20 bg-amber-400/5">
          <div className="grid grid-cols-[minmax(180px,1fr)_minmax(260px,2fr)_auto] gap-3 items-start px-4 py-3">
            <div><span className="font-medium text-sm text-text-primary">{suggestion.master_name}</span><div className="mt-1 text-[10px] text-amber-300">Suggested</div></div>
            <div className="flex flex-wrap gap-1">{suggestion.aliases.map(alias => <span key={alias.id} className="rounded bg-surface-light px-2 py-1 text-xs text-text-secondary">{alias.name} <span className="text-text-muted">{alias.video_count}</span></span>)}</div>
            <div className="flex flex-wrap justify-end gap-1"><button className="btn-ghost btn-xs text-amber-300" onClick={() => reviewSuggestion(suggestion)}><Edit3 size={12} /> Edit</button><button className="btn-ghost btn-xs text-emerald-400" onClick={() => void acceptSuggestion(suggestion)}>Accept</button><button className="btn-ghost btn-xs text-red-400" onClick={() => void dismissSuggestion(suggestion)}><Trash2 size={12} /> Dissolve</button></div>
          </div>
          {draft?.sourceSuggestionId === suggestion.suggestion_id && <div className="p-3"><ConsolidationColumnsEditor kind="genre" value={draft} onChange={value => setDraft({ ...draft, ...value })} onSave={() => void save()} onCancel={() => setDraft(null)} saving={saving} search={search} /></div>}
          </div>
        ))}
      </div>
    </div>
  );
}
