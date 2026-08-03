import { useDeferredValue, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  AlertTriangle, BrainCircuit, Check, ChevronLeft, ChevronRight,
  Copy, FileQuestion, GitCompare, Play, RefreshCw, RotateCcw,
  Save, Search, Tag, Trash2, Volume2, X,
} from "lucide-react";

import { useConfirm } from "@/components/ConfirmDialog";
import { EmptyState, ErrorState, Skeleton } from "@/components/Feedback";
import { RescanOptionsDialog, type RescanOptions } from "@/components/RescanOptionsDialog";
import { useToast } from "@/components/Toast";
import { jobsApi, reviewApi } from "@/lib/api";
import { getPref, setPref } from "@/lib/preferences";
import { cn, formatBytes, timeAgo } from "@/lib/utils";

export interface ReviewCaseItem {
  video_id: number | null;
  video_stable_id: string;
  facts: Record<string, unknown>;
  preview_url: string | null;
  poster_url: string | null;
}

export interface ReviewEdge {
  id: number;
  left_video_stable_id: string;
  right_video_stable_id: string;
  evidence_type: string;
  score: number;
  evidence: Record<string, boolean>;
}

export interface ReviewCase {
  stable_id: string;
  category: string;
  status: string;
  revision: number;
  trigger_code: string;
  evidence?: Record<string, unknown>;
  items: ReviewCaseItem[];
  edges: ReviewEdge[];
}

export interface ReviewCasePage {
  items: ReviewCase[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  category_counts: Record<string, number>;
  group_counts: Record<string, number>;
}

type ReviewGroup = "all" | "duplicates" | "versions" | "enrichment" | "normalization" | "untracked" | "other";
type PlannedAction = {
  type: string;
  video_stable_id?: string;
  version_type?: string;
  canonical_track_id?: number;
};
type Confirm = (props: { title: string; description?: string; confirmLabel?: string; variant?: "danger" | "default" }) => Promise<boolean>;

const VERSION_TYPES = ["normal", "cover", "live", "alternate", "remix", "acoustic", "uncensored", "18+"];
const PAGE_SIZES = [10, 20, 25, 50, 100];
const GROUPS: Array<{ value: ReviewGroup; label: string; icon: typeof GitCompare }> = [
  { value: "all", label: "All", icon: FileQuestion },
  { value: "duplicates", label: "Duplicates", icon: GitCompare },
  { value: "versions", label: "Versions", icon: Tag },
  { value: "enrichment", label: "Enrichment", icon: BrainCircuit },
  { value: "normalization", label: "Volume", icon: Volume2 },
  { value: "untracked", label: "Untracked", icon: FileQuestion },
  { value: "other", label: "Other", icon: AlertTriangle },
];
const ENRICHMENT_FILTERS = [
  { value: "", label: "All" },
  { value: "no_ai", label: "No AI" },
  { value: "no_thumbnails", label: "No thumbnails" },
  { value: "no_scene_analysis", label: "No scene analysis" },
  { value: "no_wikipedia", label: "No Wikipedia" },
  { value: "no_mbid", label: "No MBID" },
];

const CATEGORY_LABELS: Record<string, string> = {
  duplicate: "Suspected duplicate",
  version_ambiguity: "Version needs confirmation",
  version_detection: "Version needs confirmation",
  low_certainty_import: "Low-certainty import",
  requested_step_incomplete: "Requested processing incomplete",
  ai_pending: "AI enrichment missing",
  ai_partial: "AI enrichment incomplete",
  enrichment_incomplete: "Enrichment incomplete",
  normalization_failure: "Normalization failed",
  normalization_mismatch: "Volume target mismatch",
  orphan_file: "Untracked library media",
  scanned: "Untracked file imported by scan",
};

const EVIDENCE_LABELS: Record<string, string> = {
  same_title: "Same artist and title",
  similar_title: "Similar title or version suffix",
  audio_fingerprint: "Same audio fingerprint",
  recording_id: "Same recording ID",
  perceptual_hash: "Same video fingerprint",
  track_identity: "Same Playarr track identity",
  video_identity: "Same Playarr video identity",
  legacy_duplicate_signal: "Legacy duplicate signal",
};

function text(value: unknown, fallback = "Unknown"): string {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function addedDate(value: unknown): string {
  if (!value) return "Unknown";
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.toLocaleDateString()} (${timeAgo(String(value))})`;
}

function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] || category.replaceAll("_", " ");
}

function evidenceLabels(reviewCase: ReviewCase): string[] {
  const labels = new Set<string>();
  for (const edge of reviewCase.edges) {
    for (const code of edge.evidence_type.split("+")) {
      if (code) labels.add(EVIDENCE_LABELS[code] || code.replaceAll("_", " "));
    }
  }
  return [...labels];
}

function planLabel(action: PlannedAction): string {
  if (action.type === "reclassify") return `Reclassify as ${action.version_type}`;
  if (action.type === "normalise") return "Normalize to current target";
  if (action.type === "rescrape") return "Re-run metadata and enrichment";
  if (action.type === "delete") return "Delete and archive recovery copy";
  return action.type.replaceAll("_", " ");
}

function consequenceSummary(consequences: Record<string, unknown[]>): string {
  const parts: string[] = [];
  const metadata = consequences.metadata?.length || 0;
  const files = consequences.files?.length || 0;
  const jobs = consequences.jobs?.length || 0;
  const relationships = consequences.relationships?.length || 0;
  if (metadata) parts.push(`${metadata} metadata change${metadata === 1 ? "" : "s"}`);
  if (files) parts.push(`${files} recoverable file deletion${files === 1 ? "" : "s"}`);
  if (jobs) parts.push(`${jobs} queued repair job${jobs === 1 ? "" : "s"}`);
  if (relationships) parts.push(`${relationships} relationship change${relationships === 1 ? "" : "s"}`);
  return parts.length ? parts.join(", ") : "Resolve this review case without changing either video.";
}

function ReclassifySelect({ currentType, onSelect }: { currentType: unknown; onSelect: (version: string) => void }) {
  return (
    <label className="flex min-w-0 items-center gap-2 text-xs text-text-muted">
      <span className="shrink-0">Reclassify</span>
      <select
        aria-label="Choose version classification"
        value=""
        onChange={(event) => event.target.value && onSelect(event.target.value)}
        className="input-field h-8 min-w-0 max-w-44 flex-1 py-1 text-xs"
      >
        <option value="">Current: {text(currentType, "normal")}</option>
        {VERSION_TYPES.map((version) => <option key={version} value={version}>{version}</option>)}
      </select>
    </label>
  );
}

function Preview({ item, active, onPlay }: { item: ReviewCaseItem; active: boolean; onPlay: () => void }) {
  return (
    <div className="relative aspect-video overflow-hidden rounded-lg bg-black">
      {active && item.preview_url ? (
        <video
          key={item.video_stable_id}
          src={item.preview_url}
          poster={item.poster_url || undefined}
          controls
          autoPlay
          className="h-full w-full object-contain"
        />
      ) : item.poster_url ? (
        <img src={item.poster_url} alt="" className="h-full w-full object-cover" />
      ) : (
        <div className="flex h-full items-center justify-center text-sm text-text-muted">Preview unavailable</div>
      )}
      {!active && item.preview_url && (
        <button
          type="button"
          aria-label={`Play ${text(item.facts.artist)} — ${text(item.facts.title)}`}
          onClick={onPlay}
          className="absolute inset-0 m-auto flex h-14 w-14 items-center justify-center rounded-full border border-white/20 bg-black/75 text-white shadow-lg hover:scale-105"
        >
          <Play size={23} fill="currentColor" />
        </button>
      )}
    </div>
  );
}

function Facts({ item }: { item: ReviewCaseItem }) {
  const facts = item.facts;
  const bitrate = typeof facts.video_bitrate === "number" ? `${Math.round(facts.video_bitrate / 1000)} kbps` : facts.video_bitrate;
  const duration = typeof facts.duration_seconds === "number"
    ? `${Math.floor(facts.duration_seconds / 60)}:${String(Math.round(facts.duration_seconds % 60)).padStart(2, "0")}`
    : facts.duration_seconds;
  return (
    <div className="min-w-0">
      <div className="mb-2 min-w-0">
        {item.video_id ? <Link to={`/video/${item.video_id}`} className="block truncate text-sm font-semibold text-text-primary hover:text-accent hover:underline">{text(facts.artist)} — {text(facts.title, "Untitled")}</Link> : <h3 className="truncate text-sm font-semibold text-text-primary">{text(facts.artist)} — {text(facts.title, "Untitled")}</h3>}
        <p className="truncate text-xs text-text-muted">Video #{item.video_id ?? "untracked"}</p>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-text-muted">Added</dt><dd className="truncate text-right text-text-secondary">{addedDate(facts.added_at)}</dd>
        <dt className="text-text-muted">Quality</dt><dd className="truncate text-right text-text-secondary">{text(facts.resolution)} · {text(facts.video_codec)} · {text(bitrate)}</dd>
        <dt className="text-text-muted">Audio</dt><dd className="truncate text-right text-text-secondary">{text(facts.audio_codec)}{facts.loudness_lufs != null ? ` · ${facts.loudness_lufs} LUFS` : ""}</dd>
        <dt className="text-text-muted">Duration</dt><dd className="text-right text-text-secondary">{text(duration)}</dd>
        <dt className="text-text-muted">Size</dt><dd className="text-right text-text-secondary">{typeof facts.file_size_bytes === "number" ? formatBytes(facts.file_size_bytes) : text(facts.file_size_bytes)}</dd>
        <dt className="text-text-muted">Version</dt><dd className="text-right capitalize text-text-secondary">{text(facts.version_type, "normal")}</dd>
        <dt className="text-text-muted">Source</dt><dd className="text-right capitalize text-text-secondary">{text(facts.source)}</dd>
      </dl>
      {facts.legacy_trigger_detail ? <p className="mt-2 rounded-md bg-surface-lighter px-2 py-1.5 text-xs text-text-secondary">{String(facts.legacy_trigger_detail)}</p> : null}
      {Array.isArray(facts.missing_enrichment) && facts.missing_enrichment.length > 0 ? <p className="mt-2 text-xs text-amber-300">Missing: {(facts.missing_enrichment as string[]).map(value => value.replace("no_", "").replaceAll("_", " ")).join(" · ")}</p> : null}
    </div>
  );
}

function CandidateActions({
  item, category, action, onAction, onEnrich, onSceneAnalysis,
}: {
  item: ReviewCaseItem;
  category: string;
  action?: PlannedAction;
  onAction: (action: PlannedAction) => void;
  onEnrich: () => void;
  onSceneAnalysis: () => void;
}) {
  const canClassify = category === "duplicate" || category.includes("version");
  const canNormalize = category.includes("normalization") || category === "requested_step_incomplete";
  const canEnrich = ["enrichment_incomplete", "low_certainty_import", "requested_step_incomplete", "ai_pending", "ai_partial"].includes(category);
  return (
    <div className="mt-3 border-t border-surface-border pt-3">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {canClassify && <ReclassifySelect currentType={item.facts.version_type} onSelect={(version_type) => onAction({ type: "reclassify", version_type })} />}
        {canEnrich && <button className="btn-secondary btn-sm" onClick={onEnrich}><BrainCircuit size={13} /> Enrichment options</button>}
        {canEnrich && <button className="btn-secondary btn-sm" onClick={onSceneAnalysis}>Scene analysis only</button>}
        {canNormalize && <button className="btn-secondary btn-sm" onClick={() => onAction({ type: "normalise" })}><Volume2 size={13} /> Normalize</button>}
        <button className="btn-ghost btn-sm text-danger" onClick={() => onAction({ type: "delete" })}><Trash2 size={13} /> Delete</button>
      </div>
      {action && (
        <div className={cn(
          "mt-2 flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs",
          action.type === "delete" ? "bg-danger/10 text-danger" : "bg-accent/10 text-accent",
        )}>
          <Check size={13} /> Planned: {planLabel(action)}
        </div>
      )}
    </div>
  );
}

function CaseCard({ reviewCase, confirm, onImportUntracked, selectable, selected, onSelect, onEnrich, onSceneAnalysis }: {
  reviewCase: ReviewCase;
  confirm: Confirm;
  onImportUntracked: () => void;
  selectable?: boolean;
  selected?: boolean;
  onSelect: (selected: boolean) => void;
  onEnrich: (videoIds: number[]) => void;
  onSceneAnalysis: (videoIds: number[]) => void;
}) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [activePreview, setActivePreview] = useState<string | null>(null);
  const [plan, setPlan] = useState<Record<string, PlannedAction>>({});
  const refresh = () => qc.invalidateQueries({ queryKey: ["reviewCases"] });
  const dismiss = useMutation({
    mutationFn: () => reviewApi.dismissCase(reviewCase.stable_id, reviewCase.revision),
    onSuccess: () => { refresh(); toast({ type: "success", title: "Review case dismissed" }); },
    onError: (error) => toast({ type: "error", title: "Could not dismiss case", description: (error as Error).message }),
  });
  const commit = useMutation({
    mutationFn: async () => {
      const actions: PlannedAction[] = Object.entries(plan).map(([video_stable_id, action]) => ({ ...action, video_stable_id }));
      actions.push({ type: "keep" });
      const staged = await reviewApi.stageCasePlan(reviewCase.stable_id, reviewCase.revision, actions);
      const ok = await confirm({
        title: "Save review changes?",
        description: consequenceSummary(staged.consequences),
        confirmLabel: "Save changes",
        variant: staged.consequences.files?.length ? "danger" : "default",
      });
      if (!ok) throw new Error("cancelled");
      return reviewApi.commitCasePlan(reviewCase.stable_id, staged.plan_id);
    },
    onSuccess: () => {
      setPlan({});
      refresh();
      toast({ type: "success", title: "Review changes saved" });
    },
    onError: (error) => {
      if ((error as Error).message !== "cancelled") toast({ type: "error", title: "Review changes failed", description: (error as Error).message });
    },
  });
  const evidence = useMemo(() => evidenceLabels(reviewCase), [reviewCase]);
  const isPair = reviewCase.category === "duplicate" && reviewCase.items.length === 2;
  const orphanEvidence = reviewCase.evidence || {};

  return (
    <article className="overflow-hidden rounded-xl border border-surface-border bg-surface">
      <header className="flex flex-wrap items-start gap-3 border-b border-surface-border bg-surface-light/50 px-4 py-3">
        {selectable && <input type="checkbox" aria-label="Select review case" checked={selected} onChange={(event) => onSelect(event.target.checked)} className="mt-1 h-4 w-4 rounded border-surface-border bg-surface" />}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-text-primary">{categoryLabel(reviewCase.category)}</h2>
            {isPair && <span className="rounded-full bg-purple-500/10 px-2 py-0.5 text-[11px] text-purple-300">A/B comparison</span>}
            {reviewCase.edges[0] && <span className="rounded-full bg-surface-lighter px-2 py-0.5 text-[11px] text-text-secondary">{Math.round(reviewCase.edges[0].score * 100)}% evidence</span>}
          </div>
          {evidence.length > 0 && <p className="mt-1 text-xs text-text-muted">Why flagged: {evidence.join(" · ")}</p>}
        </div>
        <button className="btn-ghost btn-sm text-text-secondary" disabled={dismiss.isPending} onClick={() => dismiss.mutate()}><X size={14} /> Dismiss</button>
      </header>

      {reviewCase.items.length > 0 ? (
        <div className={cn("grid gap-0", isPair && "lg:grid-cols-2 lg:divide-x lg:divide-surface-border")}>
          {reviewCase.items.map((item, index) => (
            <section key={item.video_stable_id} className="min-w-0 p-4">
              {isPair && <div className="mb-2 text-xs font-semibold text-purple-300">Candidate {index === 0 ? "A" : "B"}</div>}
              <div className={cn(!isPair && "grid gap-4 lg:grid-cols-2")}>
                <Preview item={item} active={activePreview === item.video_stable_id} onPlay={() => setActivePreview(item.video_stable_id)} />
                <div className={cn("mt-3", !isPair && "mt-0")}>
                  <Facts item={item} />
                  <CandidateActions
                    item={item}
                    category={reviewCase.category}
                    action={plan[item.video_stable_id]}
                    onAction={(action) => setPlan((current) => ({ ...current, [item.video_stable_id]: action }))}
                    onEnrich={() => item.video_id && onEnrich([item.video_id])}
                    onSceneAnalysis={() => item.video_id && onSceneAnalysis([item.video_id])}
                  />
                </div>
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="p-4">
          <p className="text-sm text-text-secondary">This media exists under the library root but has no database record.</p>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-text-muted">Folder</dt><dd className="break-all text-text-secondary">{text(orphanEvidence.folder_path)}</dd>
            <dt className="text-text-muted">Files</dt><dd className="text-text-secondary">{text(orphanEvidence.file_count, "0")}</dd>
            <dt className="text-text-muted">Size</dt><dd className="text-text-secondary">{typeof orphanEvidence.size_bytes === "number" ? formatBytes(orphanEvidence.size_bytes) : text(orphanEvidence.size_bytes)}</dd>
          </dl>
          <button className="btn-secondary btn-sm mt-3" onClick={onImportUntracked}>Scan and import untracked media</button>
        </div>
      )}

      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-surface-border bg-surface-light/40 px-4 py-3">
        <span className="flex items-center gap-1.5 text-xs text-text-muted"><Volume2 size={13} /> Starting another preview stops the previous one.</span>
        <div className="flex items-center gap-2">
          <button className="btn-secondary btn-sm" disabled={!Object.keys(plan).length || commit.isPending} onClick={() => setPlan({})}><RotateCcw size={14} /> Undo changes</button>
          <button className="btn-primary btn-sm" disabled={!Object.keys(plan).length || commit.isPending} onClick={() => commit.mutate()}><Save size={14} /> {commit.isPending ? "Saving…" : "Save changes"}</button>
        </div>
      </footer>
    </article>
  );
}

export function ReviewCasesPanel() {
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();
  const queryClient = useQueryClient();
  const savedPreferences = getPref<{ group?: string; categoryFilter?: string; pageSize?: number }>("review", {});
  const savedGroup = GROUPS.some((item) => item.value === (savedPreferences.group || savedPreferences.categoryFilter))
    ? (savedPreferences.group || savedPreferences.categoryFilter) as ReviewGroup
    : "all";
  const [group, setGroup] = useState<ReviewGroup>(savedGroup);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(
    PAGE_SIZES.includes(savedPreferences.pageSize || 0) ? savedPreferences.pageSize as number : 25,
  );
  const [search, setSearch] = useState("");
  const [missingFilter, setMissingFilter] = useState("");
  const [selectedCases, setSelectedCases] = useState<Set<string>>(new Set());
  const [enrichTarget, setEnrichTarget] = useState<number[]>([]);
  const deferredSearch = useDeferredValue(search.trim());
  const topRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["reviewCases", group, missingFilter, page, pageSize, deferredSearch],
    queryFn: () => reviewApi.cases({ group, missing: group === "enrichment" ? missingFilter || undefined : undefined, q: deferredSearch || undefined, page, page_size: pageSize }),
  });
  const duplicateScan = useMutation({
    mutationFn: () => jobsApi.libraryDuplicateScan(false),
    onSuccess: () => toast({ type: "success", title: "Duplicate scan queued", description: "Results will appear here as the scan completes." }),
    onError: (error) => toast({ type: "error", title: "Could not queue duplicate scan", description: (error as Error).message }),
  });
  const completenessScan = useMutation({
    mutationFn: async () => Promise.all([
      reviewApi.scanHealth(false),
      reviewApi.scanEnrichment(false),
    ]),
    onSuccess: ([health, enrichment]) => {
      queryClient.invalidateQueries({ queryKey: ["reviewCases"] });
      toast({ type: "success", title: "Completeness audit finished", description: `${health.flagged + enrichment.flagged} issue${health.flagged + enrichment.flagged === 1 ? "" : "s"} flagged.` });
    },
    onError: (error) => toast({ type: "error", title: "Completeness audit failed", description: (error as Error).message }),
  });
  const untrackedScan = useMutation({
    mutationFn: () => reviewApi.scanUntracked(),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["reviewCases"] });
      toast({ type: "success", title: "Untracked scan finished", description: `${result.orphans.length} untracked folder${result.orphans.length === 1 ? "" : "s"} found.` });
    },
    onError: (error) => toast({ type: "error", title: "Untracked scan failed", description: (error as Error).message }),
  });
  const importUntracked = useMutation({
    mutationFn: () => jobsApi.libraryScan(true, false),
    onSuccess: () => toast({ type: "success", title: "Library import queued" }),
    onError: (error) => toast({ type: "error", title: "Could not queue library import", description: (error as Error).message }),
  });
  const enrich = useMutation({
    mutationFn: ({ videoIds, options }: { videoIds: number[]; options: RescanOptions }) => reviewApi.batchScrape(videoIds, options),
    onSuccess: () => {
      setEnrichTarget([]);
      setSelectedCases(new Set());
      queryClient.invalidateQueries({ queryKey: ["reviewCases"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      toast({ type: "success", title: "Enrichment jobs queued" });
    },
    onError: (error) => toast({ type: "error", title: "Could not queue enrichment", description: (error as Error).message }),
  });
  const dismissSelected = useMutation({
    mutationFn: async () => {
      const cases = (query.data?.items ?? []).filter(item => selectedCases.has(item.stable_id));
      await Promise.all(cases.map(item => reviewApi.dismissCase(item.stable_id, item.revision)));
    },
    onSuccess: () => {
      setSelectedCases(new Set());
      queryClient.invalidateQueries({ queryKey: ["reviewCases"] });
      toast({ type: "success", title: "Selected enrichment cases dismissed" });
    },
  });
  const runSceneAnalysis = (videoIds: number[]) => enrich.mutate({ videoIds, options: {
    artist_override: "", title_override: "",
    scrape_wikipedia: false, scrape_musicbrainz: false, scrape_tmvdb: false,
    ai_auto: false, ai_only: false, hint_cover: false, hint_live: false,
    hint_alternate: false, normalize: false, find_source_video: false,
    hint_uncensored: false,
    from_disk: false, scene_analysis: true,
  } });

  const selectGroup = (next: ReviewGroup) => {
    setGroup(next);
    setPage(1);
    setMissingFilter("");
    setSelectedCases(new Set());
    setPref("review", { ...getPref("review", {}), group: next, categoryFilter: next, pageSize });
  };
  const goToPage = (next: number) => {
    setPage(next);
    topRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <main ref={topRef} className="mx-auto max-w-6xl p-4 md:p-6">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Review Queue</h1>
          <p className="mt-0.5 text-sm text-text-secondary">Compare evidence, stage changes, then commit each decision.</p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <label className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              value={search}
              onChange={(event) => { setSearch(event.target.value); setPage(1); }}
              placeholder="Search artist, title…"
              className="input-field w-56 py-1.5 pl-8 pr-3 text-sm"
            />
          </label>
          <button className="btn-ghost btn-sm" onClick={() => query.refetch()}><RefreshCw size={14} /> Refresh</button>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap gap-2 rounded-xl border border-surface-border bg-surface px-3 py-2">
        <span className="mr-1 self-center text-xs font-semibold uppercase tracking-wide text-text-muted">Scans</span>
        <button className="btn-secondary btn-sm" disabled={duplicateScan.isPending} onClick={() => duplicateScan.mutate()}><Copy size={14} /> {duplicateScan.isPending ? "Queuing…" : "Duplicates"}</button>
        <button className="btn-secondary btn-sm" disabled={completenessScan.isPending} onClick={() => completenessScan.mutate()}><BrainCircuit size={14} /> {completenessScan.isPending ? "Auditing…" : "Processing completeness"}</button>
        <button className="btn-secondary btn-sm" disabled={untrackedScan.isPending} onClick={() => untrackedScan.mutate()}><FileQuestion size={14} /> {untrackedScan.isPending ? "Scanning…" : "Untracked media"}</button>
      </div>

      <nav className="mb-4 border-b border-surface-border" aria-label="Review category">
        <div className="flex overflow-x-auto" role="tablist">
          {GROUPS.map(({ value, label, icon: Icon }) => {
            const count = query.data?.group_counts[value] || 0;
            return (
              <button
                key={value}
                role="tab"
                aria-selected={group === value}
                onClick={() => selectGroup(value)}
                className={cn(
                  "relative flex shrink-0 items-center gap-1.5 px-3 py-2.5 text-sm font-medium",
                  group === value ? "text-accent" : "text-text-muted hover:text-text-secondary",
                )}
              >
                <Icon size={14} /> {label}
                <span className="rounded-full bg-surface-lighter px-1.5 py-0.5 text-[10px]">{count}</span>
                {group === value && <span className="absolute inset-x-1 bottom-0 h-0.5 rounded-full bg-accent" />}
              </button>
            );
          })}
        </div>
      </nav>

      {group === "enrichment" && (
        <div className="mb-4 space-y-3 rounded-xl bg-surface px-3 py-3">
          <div className="flex flex-wrap gap-1" aria-label="Missing enrichment filter">
            {ENRICHMENT_FILTERS.map(filter => <button key={filter.value || "all"} className={cn("btn-sm", missingFilter === filter.value ? "btn-primary" : "btn-ghost")} onClick={() => { setMissingFilter(filter.value); setPage(1); setSelectedCases(new Set()); }}>{filter.label}</button>)}
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t border-surface-border pt-3">
            <label className="flex items-center gap-2 text-xs text-text-secondary"><input type="checkbox" checked={Boolean(query.data?.items.length) && query.data!.items.every(item => selectedCases.has(item.stable_id))} onChange={(event) => setSelectedCases(event.target.checked ? new Set(query.data?.items.map(item => item.stable_id) ?? []) : new Set())} /> Select visible</label>
            <span className="text-xs text-text-muted">{selectedCases.size} selected</span>
            <button className="btn-secondary btn-sm" disabled={!selectedCases.size} onClick={() => setEnrichTarget((query.data?.items ?? []).filter(item => selectedCases.has(item.stable_id)).flatMap(item => item.items.map(candidate => candidate.video_id).filter((id): id is number => id != null)))}><BrainCircuit size={13} /> Enrichment options</button>
            <button className="btn-secondary btn-sm" disabled={!selectedCases.size || enrich.isPending} onClick={() => runSceneAnalysis((query.data?.items ?? []).filter(item => selectedCases.has(item.stable_id)).flatMap(item => item.items.map(candidate => candidate.video_id).filter((id): id is number => id != null)))}>Scene analysis only</button>
            <button className="btn-ghost btn-sm text-danger" disabled={!selectedCases.size || dismissSelected.isPending} onClick={() => dismissSelected.mutate()}><X size={13} /> Dismiss selected</button>
          </div>
        </div>
      )}

      <div className="mb-3 flex items-center justify-between gap-3 text-xs text-text-muted">
        <span>{query.data ? `${query.data.total} case${query.data.total === 1 ? "" : "s"}` : "Loading review cases…"}</span>
        <label className="flex items-center gap-2">Show
          <select className="input-field h-8 py-1 text-xs" value={pageSize} onChange={(event) => {
            const next = Number(event.target.value);
            setPageSize(next);
            setPage(1);
            setPref("review", { ...getPref("review", {}), group, categoryFilter: group, pageSize: next });
          }}>
            {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
          </select>
        </label>
      </div>

      {query.isLoading ? (
        <div className="space-y-3">{Array.from({ length: 3 }, (_, index) => <Skeleton key={index} className="h-80 rounded-xl" />)}</div>
      ) : query.isError ? (
        <ErrorState message={(query.error as Error).message} onRetry={() => query.refetch()} />
      ) : !query.data?.items.length ? (
        <EmptyState icon={<Check size={42} />} title="No review cases here" description={deferredSearch ? "Try a different search or category." : "Run a scan to look for new library issues."} />
      ) : (
        <div className="space-y-4">
          {query.data.items.map((item) => (
            <CaseCard key={item.stable_id} reviewCase={item} confirm={confirm} onImportUntracked={() => importUntracked.mutate()} selectable={group === "enrichment"} selected={selectedCases.has(item.stable_id)} onSelect={(checked) => setSelectedCases(current => { const next = new Set(current); if (checked) next.add(item.stable_id); else next.delete(item.stable_id); return next; })} onEnrich={setEnrichTarget} onSceneAnalysis={runSceneAnalysis} />
          ))}
        </div>
      )}

      {query.data && query.data.total_pages > 1 && (
        <div className="mt-5 flex items-center justify-center gap-3">
          <button className="btn-secondary btn-sm" disabled={page <= 1} onClick={() => goToPage(page - 1)}><ChevronLeft size={14} /> Previous</button>
          <span className="text-xs text-text-muted">Page {page} of {query.data.total_pages}</span>
          <button className="btn-secondary btn-sm" disabled={page >= query.data.total_pages} onClick={() => goToPage(page + 1)}>Next <ChevronRight size={14} /></button>
        </div>
      )}
      <RescanOptionsDialog open={enrichTarget.length > 0} count={enrichTarget.length} onClose={() => setEnrichTarget([])} isPending={enrich.isPending} onConfirm={(options) => enrich.mutate({ videoIds: enrichTarget, options })} />
      {dialog}
    </main>
  );
}
