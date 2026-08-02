import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Play, Save, Search, Volume2, X } from "lucide-react";
import { reviewApi } from "@/lib/api";
import { formatBytes } from "@/lib/utils";
import { useToast } from "@/components/Toast";

interface ReviewCaseItem {
  video_id: number | null; video_stable_id: string; facts: Record<string, unknown>;
  preview_url: string | null; poster_url: string | null;
}
interface ReviewEdge { id: number; left_video_stable_id: string; right_video_stable_id: string; evidence_type: string; score: number; evidence: Record<string, boolean>; }
interface ReviewCase { stable_id: string; category: string; status: string; revision: number; trigger_code: string; items: ReviewCaseItem[]; edges: ReviewEdge[]; }
export interface ReviewCasePage { items: ReviewCase[]; total: number; page: number; page_size: number; total_pages: number; }

type PlannedAction = { type: string; video_stable_id?: string; version_type?: string; canonical_track_id?: number };
const VERSION_TYPES = ["normal", "live", "cover", "alternate", "uncensored", "18+"];

function Fact({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined || value === "") return null;
  return <div><span className="text-text-muted">{label}: </span>{String(value)}</div>;
}

function MediaPanel({ item, active, onPlay }: { item: ReviewCaseItem; active: boolean; onPlay: () => void }) {
  const facts = item.facts;
  return (
    <article className="min-w-0 overflow-hidden rounded-lg border border-surface-border bg-surface-light">
      <div className="relative aspect-video min-h-[190px] bg-black">
        {active && item.preview_url ? <video src={item.preview_url} controls autoPlay className="h-full w-full object-contain" />
          : item.poster_url ? <img src={item.poster_url} alt="" className="h-full w-full object-cover" />
          : <div className="flex h-full items-center justify-center text-text-muted">No preview</div>}
        {!active && item.preview_url && <button aria-label="Play this preview" onClick={onPlay} className="absolute inset-0 m-auto flex h-14 w-14 items-center justify-center rounded-full bg-black/70 text-white"><Play /></button>}
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 p-3 text-xs">
        <div className="col-span-2 truncate text-sm font-semibold">{String(facts.artist || "Unknown")} — {String(facts.title || "Untitled")}</div>
        <Fact label="Added" value={facts.added_at} /><Fact label="Resolution" value={facts.resolution} />
        <Fact label="Video" value={facts.video_codec} /><Fact label="Bitrate" value={facts.video_bitrate} />
        <Fact label="Audio" value={facts.audio_codec} /><Fact label="Duration" value={facts.duration_seconds} />
        <Fact label="Size" value={typeof facts.file_size_bytes === "number" ? formatBytes(facts.file_size_bytes) : facts.file_size_bytes} />
        <Fact label="Version" value={facts.version_type} /><Fact label="Source" value={facts.source} />
      </div>
    </article>
  );
}

function Reclassify({ onSelect }: { onSelect: (version: string) => void }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const choices = VERSION_TYPES.filter(value => value.includes(search.toLowerCase()));
  return (
    <div className="relative">
      <button className="btn btn-sm" onClick={() => setOpen(value => !value)}>Reclassify</button>
      {open && <div className="absolute bottom-full right-0 z-30 mb-1 max-h-60 w-56 overflow-auto rounded-lg border border-surface-border bg-surface p-2 shadow-xl">
        <label className="flex items-center gap-1 rounded border border-surface-border px-2"><Search size={13} /><input autoFocus value={search} onChange={event => setSearch(event.target.value)} className="min-w-0 bg-transparent py-1 text-sm outline-none" placeholder="Find version" /></label>
        {choices.map(value => <button key={value} className="mt-1 block w-full rounded px-2 py-1 text-left text-sm hover:bg-surface-light" onClick={() => { onSelect(value); setOpen(false); }}>{value}</button>)}
      </div>}
    </div>
  );
}

function CaseCard({ reviewCase }: { reviewCase: ReviewCase }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [activePreview, setActivePreview] = useState<string | null>(null);
  const [plan, setPlan] = useState<Record<string, PlannedAction>>({});
  const refresh = () => qc.invalidateQueries({ queryKey: ["reviewCases"] });
  const dismiss = useMutation({ mutationFn: () => reviewApi.dismissCase(reviewCase.stable_id, reviewCase.revision), onSuccess: refresh });
  const commit = useMutation({
    mutationFn: async () => {
      const actions = Object.values(plan);
      if (!actions.length) actions.push({ type: "no_change" });
      actions.push({ type: "keep" });
      const staged = await reviewApi.stageCasePlan(reviewCase.stable_id, reviewCase.revision, actions);
      if (!window.confirm(`Save this resolution plan?\n\n${JSON.stringify(staged.consequences, null, 2)}`)) throw new Error("cancelled");
      return reviewApi.commitCasePlan(reviewCase.stable_id, staged.plan_id);
    },
    onSuccess: () => { refresh(); toast({ type: "success", title: "Review plan saved" }); },
    onError: error => { if ((error as Error).message !== "cancelled") toast({ type: "error", title: "Review plan failed", description: (error as Error).message }); },
  });
  const setAction = (item: ReviewCaseItem, action: PlannedAction) => setPlan(value => ({ ...value, [item.video_stable_id]: { ...action, video_stable_id: item.video_stable_id } }));
  const evidence = useMemo(() => reviewCase.edges.map(edge => edge.evidence_type.replaceAll("_", " ")).join(" · "), [reviewCase.edges]);
  return (
    <section className="rounded-xl border border-surface-border bg-surface p-4">
      <div className="mb-3 flex items-start gap-3"><div className="flex-1"><h3 className="font-semibold capitalize">{reviewCase.category.replaceAll("_", " ")}</h3><p className="text-xs text-text-muted">{reviewCase.trigger_code} {evidence && `· ${evidence}`}</p></div><button className="btn btn-sm" onClick={() => dismiss.mutate()}><X size={14} /> Dismiss case</button></div>
      <div className="grid gap-3 lg:grid-cols-2">
        {reviewCase.items.map(item => <div key={item.video_stable_id}><MediaPanel item={item} active={activePreview === item.video_stable_id} onPlay={() => setActivePreview(item.video_stable_id)} /><div className="mt-2 flex flex-wrap justify-end gap-1"><button className="btn btn-sm" onClick={() => setAction(item, { type: "keep" })}>Keep</button><button className="btn btn-sm" onClick={() => setAction(item, { type: "no_change" })}>No change</button><button className="btn btn-sm" onClick={() => setAction(item, { type: "rescrape" })}>Rescrape</button><button className="btn btn-sm" onClick={() => setAction(item, { type: "normalise" })}>Normalise</button><button className="btn btn-sm" onClick={() => { const value = window.prompt("Canonical track ID"); if (value && Number.isInteger(Number(value))) setAction(item, { type: "relink", canonical_track_id: Number(value) }); }}>Relink</button><button className="btn btn-sm text-red-300" onClick={() => setAction(item, { type: "delete" })}>Delete</button><Reclassify onSelect={version_type => setAction(item, { type: "reclassify", version_type })} /></div>{plan[item.video_stable_id] && <div className="mt-1 text-right text-xs text-accent">Planned: {plan[item.video_stable_id].type}{plan[item.video_stable_id].version_type ? ` as ${plan[item.video_stable_id].version_type}` : ""}</div>}</div>)}
      </div>
      <div className="mt-3 flex items-center justify-between"><span className="flex items-center gap-1 text-xs text-text-muted"><Volume2 size={13} /> Starting another preview stops the previous one.</span><button className="btn-primary btn-sm" disabled={commit.isPending} onClick={() => commit.mutate()}><Save size={14} /> Save changes</button></div>
    </section>
  );
}

export function ReviewCasesPanel() {
  const [page, setPage] = useState(1);
  const topRef = useRef<HTMLDivElement>(null);
  const query = useQuery({ queryKey: ["reviewCases", page], queryFn: () => reviewApi.cases({ page, page_size: 20 }) });
  if (!query.data?.items.length) return null;
  return (
    <div ref={topRef} className="mb-6 space-y-3">
      <div className="flex items-center justify-between"><div><h2 className="text-lg font-semibold">Evidence cases</h2><p className="text-sm text-text-muted">Compare media and stage a revision-checked resolution plan.</p></div><span className="text-sm text-text-muted">{query.data.total} open</span></div>
      {query.data.items.map(item => <CaseCard key={item.stable_id} reviewCase={item} />)}
      <div className="flex justify-end gap-2"><button className="btn btn-sm" disabled={page <= 1} onClick={() => { setPage(value => value - 1); topRef.current?.scrollIntoView(); }}><ChevronLeft size={14} /> Previous</button><span className="self-center text-xs">{page} / {query.data.total_pages}</span><button className="btn btn-sm" disabled={page >= query.data.total_pages} onClick={() => { setPage(value => value + 1); topRef.current?.scrollIntoView(); }}>Next <ChevronRight size={14} /></button></div>
    </div>
  );
}
