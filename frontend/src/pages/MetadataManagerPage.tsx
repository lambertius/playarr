import { useState, useRef, useEffect, useCallback } from "react";
import {
  Database, Users, AlertTriangle, Check, Search, Eye, EyeOff,
  Plus, Tags, BarChart3, ExternalLink, Loader2, RefreshCw, Edit3,
  X, Undo2, Sparkles, Layers, Image, Wrench, ChevronLeft, ChevronRight,
  Upload, Trash2, Link, User, Disc3, Film, Move,
} from "lucide-react";
import { PieChart, Pie, Cell, Tooltip as RTooltip, ResponsiveContainer } from "recharts";
import {
  useMbidStats, useArtistConflicts, useConsolidateArtist,
  useGenreBlacklist, useUpdateGenreBlacklist, useCreateGenre,
  useGenreConsolidations, useGenreSuggestions,
  useConsolidateGenres, useConsolidateGenresManual, useUnconsolidateGenre,
  useAddGenreToTile, useBlacklistTile, useCreateTile,
  useArtworkStats, useArtworkEntities, useArtworkBulkRepair,
  useEntitySources, useUpdateEntitySources,
} from "@/hooks/queries";
import { metadataManagerApi } from "@/lib/api";
import type { GenreSearchResult, ArtworkEntityRow } from "@/types";
import { useToast } from "@/components/Toast";
import { Tooltip } from "@/components/Tooltip";
import { Skeleton } from "@/components/Feedback";
import { useQueryClient } from "@tanstack/react-query";

// ─── Tab definitions ─────────────────────────────────────

type TabId = "overview" | "artists" | "genres" | "genre-consolidation" | "artwork";

const TABS: { id: TabId; label: string; icon: typeof Database }[] = [
  { id: "overview", label: "MBID Coverage", icon: BarChart3 },
  { id: "artists", label: "Artist Consolidation", icon: Users },
  { id: "genre-consolidation", label: "Genre Consolidation", icon: Sparkles },
  { id: "genres", label: "Genre Manager", icon: Tags },
  { id: "artwork", label: "Artwork Manager", icon: Image },
];

// ─── Main Page ───────────────────────────────────────────

export function MetadataManagerPage() {
  const [activeTab, setActiveTab] = useState<TabId>("overview");

  return (
    <div className="p-4 md:p-6 max-w-4xl">
      <h1 className="text-2xl font-bold text-text-primary mb-1">Metadata Manager</h1>
      <p className="text-sm text-text-muted mb-4">
        MusicBrainz ID coverage, artist name consolidation, and genre management.
      </p>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-white/10 mb-6 overflow-x-auto">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors border-b-2 -mb-px ${
              activeTab === tab.id
                ? "border-accent text-accent"
                : "border-transparent text-text-muted hover:text-text-secondary hover:border-white/20"
            }`}
          >
            <tab.icon size={16} />
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="space-y-6">
        {activeTab === "overview" && <MbidOverview />}
        {activeTab === "artists" && <ArtistConsolidation />}
        {activeTab === "genre-consolidation" && <GenreConsolidation />}
        {activeTab === "genres" && <GenreManager />}
        {activeTab === "artwork" && <ArtworkManager />}
      </div>
    </div>
  );
}

// ─── MBID Coverage Overview ──────────────────────────────

function MbidOverview() {
  const { data: stats, isLoading } = useMbidStats();

  if (isLoading || !stats) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 rounded-lg" />
        <Skeleton className="h-48 rounded-lg" />
      </div>
    );
  }

  const pct = (n: number) => stats.total_videos > 0 ? Math.round((n / stats.total_videos) * 100) : 0;

  const coverageRows = [
    { label: "Artist ID", value: stats.with_artist_id, desc: "mb_artist_id" },
    { label: "Recording ID", value: stats.with_recording_id, desc: "mb_recording_id" },
    { label: "Release ID", value: stats.with_release_id, desc: "mb_release_id" },
    { label: "Release Group ID", value: stats.with_release_group_id, desc: "mb_release_group_id" },
    { label: "Track ID", value: stats.with_track_id, desc: "mb_track_id" },
  ];

  const contentIdRows = [
    { label: "Playarr Video ID", value: stats.with_playarr_video_id, desc: "playarr_video_id" },
    { label: "Playarr Track ID", value: stats.with_playarr_track_id, desc: "playarr_track_id" },
  ];

  return (
    <div className="space-y-6">
      {/* Total videos + conflicts banner */}
      <div className="card px-4 py-3 flex items-center justify-between">
        <div>
          <p className="text-[10px] uppercase tracking-wider text-text-muted mb-0.5">Total Videos</p>
          <span className="text-2xl font-bold text-text-primary">{stats.total_videos.toLocaleString()}</span>
        </div>
        {stats.artist_conflicts > 0 && (
          <div className="flex items-center gap-2 text-yellow-400">
            <AlertTriangle size={14} />
            <span className="text-xs font-medium">{stats.artist_conflicts} artist conflict{stats.artist_conflicts !== 1 ? "s" : ""}</span>
            <Tooltip content="Videos where the same MusicBrainz artist ID maps to different artist name strings">
              <span className="text-yellow-400/60 cursor-help text-[10px]">?</span>
            </Tooltip>
          </div>
        )}
      </div>

      {/* MBID coverage pie charts */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <PieStatCard
          label="Artist MBID"
          segments={[
            { name: "With Artist ID", value: stats.with_artist_id, color: "#6366f1" },
            { name: "Without", value: stats.total_videos - stats.with_artist_id, color: "#3f3f46" },
          ]}
          centerValue={stats.with_artist_id}
          centerSub={`${pct(stats.with_artist_id)}%`}
        />
        <PieStatCard
          label="Album MBID"
          segments={[
            { name: "With Release Group", value: stats.with_release_group_id, color: "#8b5cf6" },
            { name: "Without", value: stats.total_videos - stats.with_release_group_id, color: "#3f3f46" },
          ]}
          centerValue={stats.with_release_group_id}
          centerSub={`${pct(stats.with_release_group_id)}%`}
        />
        <PieStatCard
          label="Recording MBID"
          segments={[
            { name: "With Recording ID", value: stats.with_recording_id, color: "#06b6d4" },
            { name: "Without", value: stats.total_videos - stats.with_recording_id, color: "#3f3f46" },
          ]}
          centerValue={stats.with_recording_id}
          centerSub={`${pct(stats.with_recording_id)}%`}
        />
        <PieStatCard
          label="Complete"
          segments={[
            { name: "Complete", value: stats.with_complete, color: "#22c55e" },
            { name: "Incomplete", value: stats.total_videos - stats.with_complete, color: "#3f3f46" },
          ]}
          centerValue={stats.with_complete}
          centerSub={`${pct(stats.with_complete)}%`}
        />
      </div>

      {/* Coverage breakdown */}
      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
          Coverage Breakdown
        </h2>
        <div className="card">
          <div className="divide-y divide-white/5">
            {coverageRows.map((row) => (
              <div key={row.desc} className="flex items-center justify-between px-4 py-3">
                <div className="flex items-center gap-3 min-w-0">
                  <Database size={14} className="text-text-muted shrink-0" />
                  <div>
                    <span className="text-sm text-text-primary">{row.label}</span>
                    <span className="text-[10px] text-text-muted ml-2 font-mono">{row.desc}</span>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <div className="w-32 h-1.5 bg-surface-light/30 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-accent rounded-full transition-all"
                      style={{ width: `${pct(row.value)}%` }}
                    />
                  </div>
                  <span className="text-sm text-text-secondary w-16 text-right">
                    {row.value} <span className="text-text-muted text-[10px]">({pct(row.value)}%)</span>
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Playarr Content IDs */}
      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
          Playarr Content IDs
        </h2>
        <div className="card">
          <div className="divide-y divide-white/5">
            {contentIdRows.map((row) => (
              <div key={row.desc} className="flex items-center justify-between px-4 py-3">
                <div className="flex items-center gap-3 min-w-0">
                  <Database size={14} className="text-text-muted shrink-0" />
                  <div>
                    <span className="text-sm text-text-primary">{row.label}</span>
                    <span className="text-[10px] text-text-muted ml-2 font-mono">{row.desc}</span>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <div className="w-32 h-1.5 bg-surface-light/30 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-emerald-500 rounded-full transition-all"
                      style={{ width: `${pct(row.value)}%` }}
                    />
                  </div>
                  <span className="text-sm text-text-secondary w-16 text-right">
                    {row.value} <span className="text-text-muted text-[10px]">({pct(row.value)}%)</span>
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* MusicBrainz link */}
      <p className="text-xs text-text-muted flex items-center gap-1.5">
        <ExternalLink size={12} />
        MBIDs are populated automatically during MusicBrainz scraping. Edit individual tracks to manually set IDs.
      </p>
    </div>
  );
}

function PieStatCard({ label, segments, centerValue, centerSub, alert }: {
  label: string;
  segments: { name: string; value: number; color: string }[];
  centerValue: number;
  centerSub?: string;
  alert?: boolean;
}) {
  const nonZero = segments.filter(s => s.value > 0);
  // If all zero, show a single grey ring
  const data = nonZero.length > 0 ? nonZero : [{ name: "None", value: 1, color: "#3f3f46" }];
  return (
    <div className={`card px-4 py-3 flex flex-col items-center ${alert ? "border-yellow-500/30" : ""}`}>
      <p className="text-[10px] uppercase tracking-wider text-text-muted mb-1 self-start">{label}</p>
      <div className="relative w-24 h-24">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              cx="50%"
              cy="50%"
              innerRadius={28}
              outerRadius={42}
              strokeWidth={0}
              paddingAngle={data.length > 1 ? 2 : 0}
            >
              {data.map((s, i) => (
                <Cell key={i} fill={s.color} />
              ))}
            </Pie>
            <RTooltip
              contentStyle={{ background: "#1e1e2e", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8 }}
              itemStyle={{ color: "#e0e0e0", fontSize: 12 }}
            />
          </PieChart>
        </ResponsiveContainer>
        {/* Center label */}
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <span className={`text-lg font-bold leading-none ${alert ? "text-yellow-400" : "text-text-primary"}`}>
            {centerValue.toLocaleString()}
          </span>
          {centerSub && (
            <span className="text-[10px] text-text-muted leading-none mt-0.5">{centerSub}</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Artist Consolidation ────────────────────────────────

function ArtistConsolidation() {
  const { data: conflicts, isLoading, refetch } = useArtistConflicts();
  const consolidateMutation = useConsolidateArtist();
  const { toast } = useToast();
  const [manualInputs, setManualInputs] = useState<Record<string, string>>({});
  const [editingMbId, setEditingMbId] = useState<string | null>(null);
  const [consolidated, setConsolidated] = useState<{ mbId: string; name: string }[]>([]);

  if (isLoading) {
    return <Skeleton className="h-48 rounded-lg" />;
  }

  // Filter out consolidated entries from the conflict list
  const consolidatedMbIds = new Set(consolidated.map((c) => c.mbId));
  const activeConflicts = (conflicts ?? []).filter((c) => !consolidatedMbIds.has(c.mb_artist_id));

  const handleConsolidate = (mbId: string, canonicalName: string) => {
    consolidateMutation.mutate(
      { mb_artist_id: mbId, canonical_name: canonicalName },
      {
        onSuccess: (result) => {
          toast({
            type: "success",
            title: `Updated ${result.updated} video${result.updated !== 1 ? "s" : ""} to "${canonicalName}"`,
          });
          setConsolidated((prev) => [...prev, { mbId, name: canonicalName }]);
          setEditingMbId(null);
        },
        onError: () => toast({ type: "error", title: "Failed to consolidate artist" }),
      },
    );
  };

  const handleManualSubmit = (mbId: string) => {
    const name = manualInputs[mbId]?.trim();
    if (!name) return;
    handleConsolidate(mbId, name);
  };

  const handleUnconsolidate = (mbId: string) => {
    setConsolidated((prev) => prev.filter((c) => c.mbId !== mbId));
  };

  // Helper: extract primary artist (before ;)
  const primaryArtist = (name: string) => name.split(";")[0].trim();

  // Filter dual-artist entries: if primary matches another entry in the same group, hide it
  const filterDualArtists = (entries: { name: string; video_count: number }[]) => {
    const primaryNames = new Set(
      entries.filter((e) => !e.name.includes(";")).map((e) => e.name.toLowerCase())
    );
    return entries.filter((e) => {
      if (!e.name.includes(";")) return true;
      const primary = primaryArtist(e.name).toLowerCase();
      return !primaryNames.has(primary);
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-text-muted leading-relaxed">
          These artists share the same MusicBrainz ID but appear with different names in your library.
          Click a name variant to apply it everywhere, or enter a custom name.
        </p>
        <Tooltip content="Rescan for artist conflicts">
          <button
            onClick={() => refetch()}
            className="btn-sm text-xs px-3 py-1.5 rounded-lg btn-ghost flex items-center gap-1.5 shrink-0 ml-3"
          >
            <RefreshCw size={13} /> Rescan
          </button>
        </Tooltip>
      </div>

      {/* Consolidated entries (scrollable box) */}
      {consolidated.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-secondary mb-2">
            Consolidated ({consolidated.length})
          </h3>
          <div className="max-h-40 overflow-y-auto rounded-lg border border-white/5 bg-surface-dark/50">
            {consolidated.map((c) => (
              <div
                key={c.mbId}
                className="flex items-center justify-between px-4 py-2 hover:bg-surface-light/30 transition-colors border-b border-white/5 last:border-0"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Check size={13} className="text-green-400 shrink-0" />
                  <span className="text-sm text-text-primary truncate">{c.name}</span>
                  <span className="text-[10px] text-text-muted font-mono truncate">{c.mbId.slice(0, 8)}…</span>
                </div>
                <Tooltip content="Undo — return to unconsolidated state">
                  <button
                    onClick={() => handleUnconsolidate(c.mbId)}
                    className="btn-sm text-[11px] px-2 py-1 rounded text-yellow-400 hover:bg-yellow-400/10 flex items-center gap-1 shrink-0"
                  >
                    <Undo2 size={12} /> Undo
                  </button>
                </Tooltip>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Active conflicts */}
      {activeConflicts.length === 0 && (
        <div className="card px-6 py-10 text-center">
          <Check size={32} className="mx-auto text-green-400 mb-3" />
          <p className="text-sm text-text-primary font-medium mb-1">No artist name conflicts</p>
          <p className="text-xs text-text-muted">
            All videos with the same MusicBrainz Artist ID use consistent naming.
          </p>
        </div>
      )}

      {activeConflicts.map((conflict) => {
        const filteredNames = filterDualArtists(conflict.names);
        return (
          <div key={conflict.mb_artist_id} className="card">
            <div className="px-4 py-3 border-b border-white/5 flex items-center gap-2">
              <AlertTriangle size={14} className="text-yellow-400 shrink-0" />
              <span className="text-[10px] font-mono text-text-muted truncate">{conflict.mb_artist_id}</span>
              <span className="text-[10px] text-text-muted ml-auto">
                {conflict.total_videos} video{conflict.total_videos !== 1 ? "s" : ""}
              </span>
            </div>
            <div className="divide-y divide-white/5">
              {filteredNames.map((entry) => (
                <div
                  key={entry.name}
                  className="flex items-center justify-between px-4 py-2.5 hover:bg-surface-light/30 transition-colors"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-sm text-text-primary truncate">{entry.name}</span>
                    <span className="text-[10px] text-text-muted shrink-0">
                      {entry.video_count} video{entry.video_count !== 1 ? "s" : ""}
                    </span>
                  </div>
                  <Tooltip content={`Apply "${entry.name}" to all ${conflict.total_videos} videos`}>
                    <button
                      onClick={() => handleConsolidate(conflict.mb_artist_id, entry.name)}
                      disabled={consolidateMutation.isPending}
                      className="btn-sm text-[11px] px-2.5 py-1 rounded text-accent hover:bg-accent/10 flex items-center gap-1 shrink-0"
                    >
                      {consolidateMutation.isPending ? (
                        <Loader2 size={12} className="animate-spin" />
                      ) : (
                        <Check size={12} />
                      )}
                      Use this
                    </button>
                  </Tooltip>
                </div>
              ))}
              {/* Manual edit row */}
              {editingMbId === conflict.mb_artist_id ? (
                <div className="flex items-center gap-2 px-4 py-2.5">
                  <input
                    type="text"
                    value={manualInputs[conflict.mb_artist_id] ?? ""}
                    onChange={(e) =>
                      setManualInputs((prev) => ({ ...prev, [conflict.mb_artist_id]: e.target.value }))
                    }
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleManualSubmit(conflict.mb_artist_id);
                      if (e.key === "Escape") setEditingMbId(null);
                    }}
                    placeholder="Enter custom artist name…"
                    className="input-field text-xs py-1.5 flex-1"
                    autoFocus
                  />
                  <button
                    onClick={() => handleManualSubmit(conflict.mb_artist_id)}
                    disabled={!manualInputs[conflict.mb_artist_id]?.trim() || consolidateMutation.isPending}
                    className="btn-sm text-[11px] px-2.5 py-1 rounded bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-40 flex items-center gap-1"
                  >
                    <Check size={12} /> Apply
                  </button>
                  <button
                    onClick={() => setEditingMbId(null)}
                    className="btn-sm text-[11px] px-2 py-1 rounded text-text-muted hover:bg-surface-light/30"
                  >
                    <X size={12} />
                  </button>
                </div>
              ) : (
                <div className="px-4 py-2.5">
                  <button
                    onClick={() => setEditingMbId(conflict.mb_artist_id)}
                    className="btn-sm text-[11px] px-2.5 py-1 rounded text-text-muted hover:text-text-secondary hover:bg-surface-light/30 flex items-center gap-1"
                  >
                    <Edit3 size={12} /> Manual edit
                  </button>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── Genre Consolidation ─────────────────────────────────

// ─── Genre Autofill Input ────────────────────────────────

function GenreAutofillInput({
  onSelect,
  excludeTileId,
  placeholder = "Search genres…",
  className = "",
  value: externalValue,
  onValueChange,
  onKeyDown: externalKeyDown,
  autoFocus = false,
}: {
  onSelect: (genre: GenreSearchResult) => void;
  excludeTileId?: number;
  placeholder?: string;
  className?: string;
  value?: string;
  onValueChange?: (val: string) => void;
  onKeyDown?: (e: React.KeyboardEvent) => void;
  autoFocus?: boolean;
}) {
  const [internalQuery, setInternalQuery] = useState("");
  const query = externalValue !== undefined ? externalValue : internalQuery;
  const setQuery = externalValue !== undefined
    ? (v: string) => onValueChange?.(v)
    : setInternalQuery;
  const [results, setResults] = useState<GenreSearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const search = useCallback((q: string) => {
    if (q.length < 1) { setResults([]); setOpen(false); return; }
    setLoading(true);
    metadataManagerApi.genreSearch(q, excludeTileId)
      .then((r) => { setResults(r); setOpen(r.length > 0); })
      .catch(() => setResults([]))
      .finally(() => setLoading(false));
  }, [excludeTileId]);

  const handleChange = (val: string) => {
    setQuery(val);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => search(val), 200);
  };

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      <input
        type="text"
        value={query}
        onChange={(e) => handleChange(e.target.value)}
        onFocus={() => { if (results.length > 0) setOpen(true); }}
        onKeyDown={externalKeyDown}
        placeholder={placeholder}
        className="input-field text-xs py-1.5 w-full"
        autoFocus={autoFocus}
      />
      {loading && <Loader2 size={12} className="absolute right-2 top-1/2 -translate-y-1/2 animate-spin text-text-muted" />}
      {open && results.length > 0 && (
        <div className="absolute z-50 top-full left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-lg border border-white/10 bg-[#1a1a2e] shadow-2xl">
          {results.map((r) => (
            <button
              key={r.id}
              onClick={() => { onSelect(r); setQuery(externalValue !== undefined ? r.name : ""); setResults([]); setOpen(false); }}
              className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-surface-light/30 transition-colors"
            >
              <span className="text-xs text-text-primary truncate">{r.name}</span>
              <span className="text-[10px] text-text-muted shrink-0 ml-2">
                {r.video_count} video{r.video_count !== 1 ? "s" : ""}
                {r.already_consolidated && " · consolidated"}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Genre Consolidation ─────────────────────────────────

function GenreConsolidation() {
  const { data: consolidations, isLoading: loadingConsolidations } = useGenreConsolidations();
  const { data: suggestions, isLoading: loadingSuggestions, refetch: refetchSuggestions } = useGenreSuggestions();
  const consolidateMutation = useConsolidateGenres();
  const consolidateManualMutation = useConsolidateGenresManual();
  const unconsolidateMutation = useUnconsolidateGenre();
  const addToTileMutation = useAddGenreToTile();
  const blacklistTileMutation = useBlacklistTile();
  const createTileMutation = useCreateTile();
  const { toast } = useToast();
  const [manualInputs, setManualInputs] = useState<Record<number, string>>({});
  const [editingSuggIdx, setEditingSuggIdx] = useState<number | null>(null);
  const [addingToTile, setAddingToTile] = useState<number | null>(null);
  const [creatingTile, setCreatingTile] = useState(false);
  const [newTileName, setNewTileName] = useState("");

  const isLoading = loadingConsolidations || loadingSuggestions;

  if (isLoading) {
    return <Skeleton className="h-48 rounded-lg" />;
  }

  const handleApplySuggestion = (masterGenreId: number, aliasIds: number[]) => {
    consolidateMutation.mutate(
      { alias_genre_ids: aliasIds, master_genre_id: masterGenreId },
      {
        onSuccess: (result) => {
          toast({
            type: "success",
            title: `${result.updated} genre${result.updated !== 1 ? "s" : ""} mapped to "${result.master_name}"`,
          });
        },
        onError: () => toast({ type: "error", title: "Failed to consolidate genres" }),
      },
    );
  };

  const handleManualConsolidate = (aliasIds: number[], index: number) => {
    const name = manualInputs[index]?.trim();
    if (!name) return;
    consolidateManualMutation.mutate(
      { alias_genre_ids: aliasIds, master_genre_name: name },
      {
        onSuccess: (result) => {
          toast({
            type: "success",
            title: `${result.updated} genre${result.updated !== 1 ? "s" : ""} mapped to "${result.master_name}"`,
          });
          setEditingSuggIdx(null);
          setManualInputs((prev) => {
            const next = { ...prev };
            delete next[index];
            return next;
          });
        },
        onError: () => toast({ type: "error", title: "Failed to consolidate genres" }),
      },
    );
  };

  const handleUnconsolidate = (genreId: number) => {
    unconsolidateMutation.mutate(
      { genre_id: genreId },
      {
        onSuccess: (result) => {
          toast({ type: "success", title: `"${result.name}" restored as independent genre` });
        },
        onError: () => toast({ type: "error", title: "Failed to unconsolidate genre" }),
      },
    );
  };

  const handleAddToTile = (tileId: number, genre: GenreSearchResult) => {
    addToTileMutation.mutate(
      { genre_id: genre.id, master_genre_id: tileId },
      {
        onSuccess: (result) => {
          toast({ type: "success", title: `"${result.name}" added to "${result.master_name}"` });
        },
        onError: () => toast({ type: "error", title: "Failed to add genre to tile" }),
      },
    );
  };

  const handleBlacklistTile = (masterId: number, blacklisted: boolean) => {
    blacklistTileMutation.mutate(
      { master_genre_id: masterId, blacklisted },
      {
        onSuccess: (result) => {
          toast({
            type: "success",
            title: `${result.updated} genre${result.updated !== 1 ? "s" : ""} ${result.blacklisted ? "hidden" : "restored"}`,
          });
        },
        onError: () => toast({ type: "error", title: "Failed to update blacklist" }),
      },
    );
  };

  const handleCreateTile = () => {
    const name = newTileName.trim();
    if (!name) return;
    createTileMutation.mutate(
      { alias_genre_ids: [], master_genre_name: name },
      {
        onSuccess: (result) => {
          toast({ type: "success", title: `Tile "${result.master_name}" created` });
          setNewTileName("");
          setCreatingTile(false);
        },
        onError: () => toast({ type: "error", title: "Failed to create tile" }),
      },
    );
  };

  return (
    <div className="space-y-6">
      <p className="text-xs text-text-muted leading-relaxed">
        Map multiple genre variations to a single master genre. For example, "Alt Rock", "alt rock", and "Alt. Rock"
        can all display as "Alternative Rock". Changes are reflected across the library instantly.
      </p>

      {/* Active consolidations */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
            Active Consolidations ({consolidations?.length ?? 0})
          </h3>
          <div className="flex items-center gap-2">
            {!creatingTile ? (
              <button
                onClick={() => setCreatingTile(true)}
                className="btn-sm text-xs px-3 py-1.5 rounded-lg btn-ghost flex items-center gap-1.5"
              >
                <Plus size={13} /> New Tile
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={newTileName}
                  onChange={(e) => setNewTileName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleCreateTile();
                    if (e.key === "Escape") { setCreatingTile(false); setNewTileName(""); }
                  }}
                  placeholder="Master genre name…"
                  className="input-field text-xs py-1.5 w-44"
                  autoFocus
                />
                <button
                  onClick={handleCreateTile}
                  disabled={!newTileName.trim() || createTileMutation.isPending}
                  className="btn-sm text-[11px] px-2.5 py-1 rounded bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-40 flex items-center gap-1"
                >
                  <Check size={12} /> Create
                </button>
                <button
                  onClick={() => { setCreatingTile(false); setNewTileName(""); }}
                  className="btn-sm text-[11px] px-2 py-1 rounded text-text-muted hover:bg-surface-light/30"
                >
                  <X size={12} />
                </button>
              </div>
            )}
          </div>
        </div>

        {consolidations && consolidations.length > 0 ? (
          <div className="max-h-[420px] overflow-y-auto rounded-lg border border-white/5 bg-surface-dark/50 space-y-0">
            {consolidations.map((c) => (
              <div
                key={c.master_genre_id}
                className={`border-b border-white/5 last:border-0 ${c.blacklisted ? "opacity-60" : ""}`}
              >
                {/* Tile header */}
                <div className={`px-4 py-2.5 flex items-center gap-2 ${c.blacklisted ? "bg-red-900/15" : "bg-surface-light/10"}`}>
                  {c.blacklisted ? (
                    <EyeOff size={13} className="text-red-400 shrink-0" />
                  ) : (
                    <Check size={13} className="text-green-400 shrink-0" />
                  )}
                  <span className="text-sm font-medium text-text-primary">{c.master_genre}</span>
                  {c.blacklisted && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-400 font-medium">blacklisted</span>
                  )}
                  <span className="text-[10px] text-text-muted">
                    {c.total_videos} video{c.total_videos !== 1 ? "s" : ""} · {c.aliases.length + 1} genre{c.aliases.length > 0 ? "s" : ""}
                  </span>
                  <div className="ml-auto flex items-center gap-1">
                    <Tooltip content={addingToTile === c.master_genre_id ? "Close" : "Add genre to this tile"}>
                      <button
                        onClick={() => setAddingToTile(addingToTile === c.master_genre_id ? null : c.master_genre_id)}
                        className="btn-sm text-[11px] px-2 py-0.5 rounded text-accent hover:bg-accent/10 flex items-center gap-1"
                      >
                        <Plus size={11} />
                      </button>
                    </Tooltip>
                    <Tooltip content={c.blacklisted ? "Whitelist this tile" : "Blacklist this tile"}>
                      <button
                        onClick={() => handleBlacklistTile(c.master_genre_id, !c.blacklisted)}
                        disabled={blacklistTileMutation.isPending}
                        className={`btn-sm text-[11px] px-2 py-0.5 rounded flex items-center gap-1 ${
                          c.blacklisted
                            ? "text-green-400 hover:bg-green-400/10"
                            : "text-red-400 hover:bg-red-400/10"
                        }`}
                      >
                        {c.blacklisted ? <Eye size={11} /> : <EyeOff size={11} />}
                      </button>
                    </Tooltip>
                  </div>
                </div>
                {/* Autofill add row */}
                {addingToTile === c.master_genre_id && (
                  <div className="px-4 py-2 bg-surface-light/5 border-b border-white/5">
                    <GenreAutofillInput
                      excludeTileId={c.master_genre_id}
                      onSelect={(genre) => handleAddToTile(c.master_genre_id, genre)}
                      placeholder="Type to search and add a genre…"
                    />
                  </div>
                )}
                {/* Aliases */}
                <div className="pl-8">
                  {c.aliases.map((alias) => (
                    <div
                      key={alias.id}
                      className="flex items-center justify-between px-4 py-1.5 hover:bg-surface-light/20 transition-colors"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-xs text-text-muted">→</span>
                        <span className="text-xs text-text-secondary truncate">{alias.name}</span>
                        <span className="text-[10px] text-text-muted">
                          {alias.video_count} video{alias.video_count !== 1 ? "s" : ""}
                        </span>
                      </div>
                      <button
                        onClick={() => handleUnconsolidate(alias.id)}
                        disabled={unconsolidateMutation.isPending}
                        className="btn-sm text-[11px] px-2 py-0.5 rounded text-yellow-400 hover:bg-yellow-400/10 flex items-center gap-1 shrink-0"
                      >
                        <Undo2 size={11} /> Remove
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="card px-6 py-6 text-center">
            <p className="text-xs text-text-muted">No active consolidations. Apply a suggestion below or create a new tile.</p>
          </div>
        )}
      </section>

      {/* Suggested consolidations */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
            Suggested Consolidations
          </h3>
          <Tooltip content="Re-scan genres for consolidation suggestions">
            <button
              onClick={() => refetchSuggestions()}
              className="btn-sm text-xs px-3 py-1.5 rounded-lg btn-ghost flex items-center gap-1.5"
            >
              <RefreshCw size={13} /> Rescan
            </button>
          </Tooltip>
        </div>

        {(!suggestions || suggestions.length === 0) ? (
          <div className="card px-6 py-8 text-center">
            <Check size={28} className="mx-auto text-green-400 mb-2" />
            <p className="text-sm text-text-primary font-medium mb-1">No genre consolidations suggested</p>
            <p className="text-xs text-text-muted">
              All genres appear to be unique. Create a new tile above to manually consolidate genres.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {suggestions.map((sugg, idx) => {
              const allIds = [sugg.master_id, ...sugg.aliases.map((a) => a.id)];
              const aliasIds = sugg.aliases.map((a) => a.id);
              return (
                <div key={idx} className="card">
                  <div className="px-4 py-3 border-b border-white/5 flex items-center gap-2">
                    <Sparkles size={14} className="text-accent shrink-0" />
                    <span className="text-sm font-medium text-text-primary">{sugg.master_name}</span>
                    <span className="text-[10px] text-text-muted ml-auto">
                      {sugg.aliases.length + 1} variant{sugg.aliases.length > 0 ? "s" : ""}
                    </span>
                  </div>
                  <div className="divide-y divide-white/5">
                    {/* Master (suggested) */}
                    <div className="flex items-center justify-between px-4 py-2 bg-surface-light/10">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-sm text-accent font-medium truncate">{sugg.master_name}</span>
                        <span className="text-[10px] text-text-muted">(master)</span>
                      </div>
                      <button
                        onClick={() => handleApplySuggestion(sugg.master_id, aliasIds)}
                        disabled={consolidateMutation.isPending}
                        className="btn-sm text-[11px] px-2.5 py-1 rounded text-accent hover:bg-accent/10 flex items-center gap-1 shrink-0"
                      >
                        {consolidateMutation.isPending ? (
                          <Loader2 size={12} className="animate-spin" />
                        ) : (
                          <Check size={12} />
                        )}
                        Use this
                      </button>
                    </div>
                    {/* Aliases */}
                    {sugg.aliases.map((alias) => (
                      <div
                        key={alias.id}
                        className="flex items-center justify-between px-4 py-2 hover:bg-surface-light/30 transition-colors"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-sm text-text-primary truncate">{alias.name}</span>
                          <span className="text-[10px] text-text-muted shrink-0">
                            {alias.video_count} video{alias.video_count !== 1 ? "s" : ""}
                          </span>
                        </div>
                        <button
                          onClick={() => handleApplySuggestion(alias.id, allIds.filter((id) => id !== alias.id))}
                          disabled={consolidateMutation.isPending}
                          className="btn-sm text-[11px] px-2.5 py-1 rounded text-accent hover:bg-accent/10 flex items-center gap-1 shrink-0"
                        >
                          <Check size={12} /> Use this
                        </button>
                      </div>
                    ))}
                    {/* Manual edit */}
                    {editingSuggIdx === idx ? (
                      <div className="flex items-center gap-2 px-4 py-2.5">
                        <GenreAutofillInput
                          value={manualInputs[idx] ?? ""}
                          onValueChange={(val) => setManualInputs((prev) => ({ ...prev, [idx]: val }))}
                          onSelect={(genre) => setManualInputs((prev) => ({ ...prev, [idx]: genre.name }))}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleManualConsolidate(allIds, idx);
                            if (e.key === "Escape") setEditingSuggIdx(null);
                          }}
                          placeholder="Enter custom master genre name…"
                          className="flex-1"
                          autoFocus
                        />
                        <button
                          onClick={() => handleManualConsolidate(allIds, idx)}
                          disabled={!manualInputs[idx]?.trim() || consolidateManualMutation.isPending}
                          className="btn-sm text-[11px] px-2.5 py-1 rounded bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-40 flex items-center gap-1"
                        >
                          <Check size={12} /> Apply
                        </button>
                        <button
                          onClick={() => setEditingSuggIdx(null)}
                          className="btn-sm text-[11px] px-2 py-1 rounded text-text-muted hover:bg-surface-light/30"
                        >
                          <X size={12} />
                        </button>
                      </div>
                    ) : (
                      <div className="px-4 py-2.5">
                        <button
                          onClick={() => setEditingSuggIdx(idx)}
                          className="btn-sm text-[11px] px-2.5 py-1 rounded text-text-muted hover:text-text-secondary hover:bg-surface-light/30 flex items-center gap-1"
                        >
                          <Edit3 size={12} /> Manual edit
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}

// ─── Genre Manager ───────────────────────────────────────

function GenreManager() {
  const { data: genres, isLoading } = useGenreBlacklist();
  const updateMutation = useUpdateGenreBlacklist();
  const createMutation = useCreateGenre();
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<"visible" | "hidden">("visible");
  const [newGenreName, setNewGenreName] = useState("");

  if (isLoading || !genres) {
    return <Skeleton className="h-32 rounded-lg" />;
  }

  // Hide alias genres (those with master_genre_id) — they're managed via the consolidation tab
  const standalone = genres.filter((g) => g.master_genre_id === null);

  const visible = standalone.filter((g) => !g.blacklisted);
  const hidden = standalone.filter((g) => g.blacklisted);
  const list = tab === "visible" ? visible : hidden;
  const filtered = search
    ? list.filter((g) => g.name.toLowerCase().includes(search.toLowerCase()))
    : list;

  const toggle = (ids: number[], blacklisted: boolean) => {
    updateMutation.mutate(
      { genre_ids: ids, blacklisted },
      {
        onSuccess: () =>
          toast({
            type: "success",
            title: `${ids.length} genre${ids.length !== 1 ? "s" : ""} ${blacklisted ? "hidden" : "restored"}`,
          }),
        onError: () => toast({ type: "error", title: "Failed to update genres" }),
      },
    );
  };

  const handleAddGenre = () => {
    const name = newGenreName.trim();
    if (!name) return;
    createMutation.mutate(name, {
      onSuccess: () => {
        toast({ type: "success", title: `Genre "${name}" created` });
        setNewGenreName("");
      },
      onError: () => toast({ type: "error", title: "Failed to create genre (may already exist)" }),
    });
  };

  return (
    <div>
      <p className="text-xs text-text-muted leading-relaxed mb-3">
        Hidden genres are still stored on each track but won't appear in the Genres page or on track metadata tiles.
        Use this to hide noisy or irrelevant genre tags. Consolidated genres are managed in the Genre Consolidation tab.
      </p>

      {/* Tab toggle */}
      <div className="flex items-center gap-2 mb-3">
        <button
          onClick={() => setTab("visible")}
          className={`btn-sm text-xs px-3 py-1.5 rounded-lg flex items-center gap-1.5 ${
            tab === "visible"
              ? "bg-accent/20 text-accent border border-accent/30"
              : "btn-ghost"
          }`}
        >
          <Eye size={13} /> Visible ({visible.length})
        </button>
        <button
          onClick={() => setTab("hidden")}
          className={`btn-sm text-xs px-3 py-1.5 rounded-lg flex items-center gap-1.5 ${
            tab === "hidden"
              ? "bg-accent/20 text-accent border border-accent/30"
              : "btn-ghost"
          }`}
        >
          <EyeOff size={13} /> Hidden ({hidden.length})
        </button>
        <div className="flex-1" />
        <div className="relative">
          <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter genres…"
            className="input-field text-xs pl-7 py-1.5 w-48"
          />
        </div>
      </div>

      {/* Add new genre */}
      <div className="flex items-center gap-2 mb-3">
        <input
          type="text"
          value={newGenreName}
          onChange={(e) => setNewGenreName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && newGenreName.trim()) handleAddGenre(); }}
          placeholder="New genre name…"
          className="input-field text-xs py-1.5 flex-1"
        />
        <button
          onClick={handleAddGenre}
          disabled={!newGenreName.trim() || createMutation.isPending}
          className="btn-sm text-xs px-3 py-1.5 rounded-lg bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 disabled:opacity-40 flex items-center gap-1"
        >
          <Plus size={13} /> Add
        </button>
      </div>

      {/* Genre list */}
      <div className="max-h-72 overflow-y-auto rounded-lg border border-white/5">
        {filtered.length === 0 ? (
          <p className="text-xs text-text-muted text-center py-6">
            {search ? "No matching genres" : tab === "visible" ? "All genres are hidden" : "No hidden genres"}
          </p>
        ) : (
          <div className="divide-y divide-white/5">
            {filtered.map((g) => (
              <div
                key={g.id}
                className="flex items-center justify-between px-3 py-2 hover:bg-surface-light/30 transition-colors"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-sm text-text-primary truncate">{g.name}</span>
                  {g.alias_count > 0 && (
                    <Tooltip content={`Consolidated genre covering ${g.alias_count + 1} variant${g.alias_count > 0 ? "s" : ""}`}>
                      <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-accent/15 text-accent font-medium shrink-0">
                        <Layers size={10} /> {g.alias_count + 1}
                      </span>
                    </Tooltip>
                  )}
                  <span className="text-[10px] text-text-muted shrink-0">
                    {g.video_count} video{g.video_count !== 1 ? "s" : ""}
                  </span>
                </div>
                <button
                  onClick={() => toggle([g.id], tab === "visible")}
                  disabled={updateMutation.isPending}
                  className={`btn-sm text-[11px] px-2 py-1 rounded flex items-center gap-1 ${
                    tab === "visible"
                      ? "text-red-400 hover:bg-red-400/10"
                      : "text-green-400 hover:bg-green-400/10"
                  }`}
                  title={tab === "visible" ? "Hide this genre from the Genres page and track tiles" : "Restore this genre to the Genres page and track tiles"}
                >
                  {tab === "visible" ? <><EyeOff size={12} /> Hide</> : <><Eye size={12} /> Show</>}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Artwork Manager ─────────────────────────────────────

function ArtworkManager() {
  const { data: stats, isLoading: statsLoading } = useArtworkStats();
  const [entityType, setEntityType] = useState<"artist" | "album" | "poster">("artist");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [sortOrder, setSortOrder] = useState<string>("name_asc");
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(50);
  const [pageInput, setPageInput] = useState("1");
  const { data: entitiesData, isLoading: entitiesLoading, refetch } = useArtworkEntities(
    entityType, statusFilter || undefined, page, perPage, debouncedSearch || undefined, sortOrder,
  );
  const repairMutation = useArtworkBulkRepair();
  const { toast } = useToast();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);
  const [lightboxName, setLightboxName] = useState("");
  const [lightboxEntity, setLightboxEntity] = useState<{ entityType: string; entityId: number; cropPosition?: string | null } | null>(null);
  const [lbCropping, setLbCropping] = useState(false);
  const [lbCropX, setLbCropX] = useState(50);
  const [lbCropY, setLbCropY] = useState(50);
  const [lbSaving, setLbSaving] = useState(false);
  const lbCropRef = useRef<HTMLDivElement>(null);

  // Debounce search input
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchQuery), 300);
    return () => clearTimeout(t);
  }, [searchQuery]);

  // Reset page when filters change
  useEffect(() => { setPage(1); setPageInput("1"); setSelected(new Set()); }, [entityType, statusFilter, debouncedSearch, sortOrder, perPage]);

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const selectAllMissing = () => {
    if (!entitiesData) return;
    const ids = entitiesData.items.filter((e) => e.category === "missing").map((e) => e.id);
    setSelected(new Set(ids));
  };

  const handleRepair = () => {
    if (selected.size === 0) return;
    repairMutation.mutate(
      { entity_type: entityType, entity_ids: Array.from(selected) },
      {
        onSuccess: (result) => {
          toast({
            type: "success",
            title: `Repaired ${result.repaired}, already OK ${result.already_ok}, still missing ${result.still_missing}`,
          });
          setSelected(new Set());
          refetch();
        },
        onError: () => toast({ type: "error", title: "Repair failed" }),
      },
    );
  };

  if (statsLoading || !stats) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 rounded-lg" />
        <Skeleton className="h-48 rounded-lg" />
      </div>
    );
  }

  const videoPct = (n: number) => stats.videos.total > 0 ? Math.round((n / stats.videos.total) * 100) : 0;
  const totalPages = entitiesData ? Math.ceil(entitiesData.total / entitiesData.per_page) : 1;

  const handlePageInputSubmit = () => {
    const p = parseInt(pageInput, 10);
    if (p >= 1 && p <= totalPages) { setPage(p); }
    else { setPageInput(String(page)); }
  };

  const Pagination = () => totalPages > 1 ? (
    <div className="flex items-center justify-between py-2">
      <span className="text-xs text-text-muted">
        {entitiesData?.total ?? 0} {entityType}{(entitiesData?.total ?? 0) !== 1 ? "s" : ""}
      </span>
      <div className="flex items-center gap-2">
        <select
          value={perPage}
          onChange={(e) => setPerPage(Number(e.target.value))}
          className="input-field text-xs py-1 px-1.5 w-16"
        >
          {[25, 50, 100, 200].map(n => <option key={n} value={n}>{n}</option>)}
        </select>
        <button
          onClick={() => { setPage(1); setPageInput("1"); }}
          disabled={page <= 1}
          className="btn-sm btn-ghost px-1.5 py-1 rounded text-xs disabled:opacity-30"
        >
          First
        </button>
        <button
          onClick={() => { const p = Math.max(1, page - 1); setPage(p); setPageInput(String(p)); }}
          disabled={page <= 1}
          className="btn-sm btn-ghost px-2 py-1 rounded disabled:opacity-30"
        >
          <ChevronLeft size={14} />
        </button>
        <div className="flex items-center gap-1 text-xs text-text-secondary">
          <input
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value)}
            onBlur={handlePageInputSubmit}
            onKeyDown={(e) => e.key === "Enter" && handlePageInputSubmit()}
            className="input-field w-12 text-center text-xs py-1 px-1"
          />
          <span>/ {totalPages}</span>
        </div>
        <button
          onClick={() => { const p = Math.min(totalPages, page + 1); setPage(p); setPageInput(String(p)); }}
          disabled={page >= totalPages}
          className="btn-sm btn-ghost px-2 py-1 rounded disabled:opacity-30"
        >
          <ChevronRight size={14} />
        </button>
        <button
          onClick={() => { setPage(totalPages); setPageInput(String(totalPages)); }}
          disabled={page >= totalPages}
          className="btn-sm btn-ghost px-1.5 py-1 rounded text-xs disabled:opacity-30"
        >
          Last
        </button>
      </div>
    </div>
  ) : null;

  return (
    <div className="space-y-6">
      {/* ─── Overview cards ──────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Poster Art Breakdown */}
        <div className="card px-4 py-3">
          <p className="text-[10px] uppercase tracking-wider text-text-muted mb-2">Poster Art Breakdown</p>
          <div className="flex items-center gap-4">
            <div className="relative w-28 h-28 shrink-0">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={[
                      { name: "Source Art", value: stats.videos.poster_from_source },
                      { name: "Thumbnail Fallback", value: stats.videos.poster_from_thumb },
                      { name: "No Poster", value: Math.max(0, stats.videos.total - stats.videos.with_poster) },
                    ].filter(s => s.value > 0)}
                    dataKey="value"
                    cx="50%"
                    cy="50%"
                    innerRadius={28}
                    outerRadius={46}
                    strokeWidth={0}
                    paddingAngle={2}
                  >
                    {stats.videos.poster_from_source > 0 && <Cell fill="#6366f1" />}
                    {stats.videos.poster_from_thumb > 0 && <Cell fill="#f59e0b" />}
                    {stats.videos.total - stats.videos.with_poster > 0 && <Cell fill="#3f3f46" />}
                  </Pie>
                  <RTooltip
                    contentStyle={{ background: "#1e1e2e", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8 }}
                    itemStyle={{ color: "#e0e0e0", fontSize: 12 }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                <span className="text-lg font-bold text-text-primary leading-none">{stats.videos.with_poster.toLocaleString()}</span>
                <span className="text-[10px] text-text-muted leading-none mt-0.5">posters</span>
              </div>
            </div>
            <div className="grid grid-cols-1 gap-2 text-[11px] flex-1">
              <div className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: "#6366f1" }} />
                <span className="text-text-muted">Source Art:</span>
                <span className="text-text-primary font-medium">{stats.videos.poster_from_source}</span>
                <span className="text-text-muted text-[10px]">({videoPct(stats.videos.poster_from_source)}%)</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: "#f59e0b" }} />
                <span className="text-text-muted">Thumb Fallback:</span>
                <span className="text-text-primary font-medium">{stats.videos.poster_from_thumb}</span>
                <span className="text-text-muted text-[10px]">({videoPct(stats.videos.poster_from_thumb)}%)</span>
              </div>
              {stats.videos.total - stats.videos.with_poster > 0 && (
                <div className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-full" style={{ background: "#3f3f46" }} />
                  <span className="text-text-muted">No Poster:</span>
                  <span className="text-text-primary font-medium">{stats.videos.total - stats.videos.with_poster}</span>
                </div>
              )}
              <p className="text-text-muted/60 text-[10px] mt-1">of {stats.videos.total.toLocaleString()} videos</p>
            </div>
          </div>
        </div>

        {/* Artist source coverage */}
        <SourceCoveragePie label="Artist Sources" stats={stats.artists} />

        {/* Album source coverage */}
        <SourceCoveragePie label="Album Sources" stats={stats.albums} />
      </div>

      {/* ─── Entity browser ──────────────────── */}
      <section>
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary">
            Entity Artwork
          </h2>
          <div className="flex-1" />

          {/* Entity type toggle */}
          <div className="flex gap-1">
            {(["artist", "album", "poster"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setEntityType(t)}
                className={`btn-sm text-xs px-3 py-1.5 rounded-lg ${
                  entityType === t
                    ? "bg-accent/20 text-accent border border-accent/30"
                    : "btn-ghost text-text-muted"
                }`}
              >
                {t === "artist" ? "Artists" : t === "album" ? "Albums" : "Posters"}
              </button>
            ))}
          </div>

          {/* Status filter */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="input-field text-xs py-1.5 px-2 w-36"
          >
            <option value="">All statuses</option>
            <option value="filled">Filled</option>
            <option value="missing">Missing (has source)</option>
            <option value="unavailable">Unavailable</option>
          </select>

          {/* Sort */}
          <select
            value={sortOrder}
            onChange={(e) => setSortOrder(e.target.value)}
            className="input-field text-xs py-1.5 px-2 w-32"
          >
            <option value="name_asc">A → Z</option>
            <option value="name_desc">Z → A</option>
            <option value="date_desc">Newest first</option>
            <option value="date_asc">Oldest first</option>
          </select>

          <Tooltip content="Refresh">
            <button onClick={() => refetch()} className="btn-sm btn-ghost px-2 py-1.5 rounded-lg">
              <RefreshCw size={14} />
            </button>
          </Tooltip>
        </div>

        {/* Search bar */}
        <div className="relative mb-3">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
          <input
            type="text"
            placeholder={`Search ${entityType}s by name…`}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="input-field w-full text-sm py-2 pl-9 pr-8"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary"
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Action bar */}
        {selected.size > 0 && (
          <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-accent/10 border border-accent/20">
            <span className="text-xs text-accent font-medium">{selected.size} selected</span>
            <div className="flex-1" />
            <button
              onClick={handleRepair}
              disabled={repairMutation.isPending}
              className="btn-sm text-xs px-3 py-1.5 rounded-lg bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 flex items-center gap-1.5"
            >
              {repairMutation.isPending ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />}
              Repair Selected
            </button>
            <button
              onClick={() => setSelected(new Set())}
              className="btn-sm text-xs px-2 py-1.5 rounded-lg text-text-muted hover:bg-surface-light/30"
            >
              <X size={12} />
            </button>
          </div>
        )}

        {/* Quick actions — artists/albums only */}
        {entityType !== "poster" && (
          <div className="flex items-center gap-2 mb-3">
            <button
              onClick={selectAllMissing}
              className="btn-sm text-[11px] px-2.5 py-1 rounded text-text-muted hover:text-text-secondary hover:bg-surface-light/30 flex items-center gap-1"
            >
              <AlertTriangle size={11} /> Select all missing
            </button>
          </div>
        )}

        {/* Top pagination */}
        <Pagination />

        {/* Entity list */}
        {entitiesLoading ? (
          <Skeleton className="h-48 rounded-lg" />
        ) : !entitiesData || entitiesData.items.length === 0 ? (
          <div className="card px-6 py-10 text-center">
            <Check size={32} className="mx-auto text-green-400 mb-3" />
            <p className="text-sm text-text-primary font-medium mb-1">
              {statusFilter === "missing" ? "No missing artwork" : debouncedSearch ? "No matches" : "No entities found"}
            </p>
            <p className="text-xs text-text-muted">
              {statusFilter === "missing"
                ? `All ${entityType}s with known sources have artwork.`
                : debouncedSearch ? "Try a different search term." : "Try adjusting filters."}
            </p>
          </div>
        ) : (
          <>
            <div className="space-y-2">
              {entitiesData.items.map((entity) => (
                <ArtworkEntityTile
                  key={entity.id}
                  entity={entity}
                  selected={selected.has(entity.id)}
                  expanded={expandedId === entity.id}
                  onToggleSelect={() => toggleSelect(entity.id)}
                  onToggleExpand={() => setExpandedId(expandedId === entity.id ? null : entity.id)}
                  onRefreshed={refetch}
                  onViewArt={(url, name, ent) => {
                    setLightboxUrl(url);
                    setLightboxName(name);
                    setLightboxEntity({ entityType: ent.entity_type, entityId: ent.id, cropPosition: ent.crop_position });
                    const cp = ent.crop_position;
                    if (cp) {
                      const parts = cp.split(/\s+/);
                      setLbCropX(parseInt(parts[0]) || 50);
                      setLbCropY(parseInt(parts[1]) || 50);
                    } else {
                      setLbCropX(50);
                      setLbCropY(50);
                    }
                    setLbCropping(false);
                  }}
                />
              ))}
            </div>

            {/* Bottom pagination */}
            <Pagination />
          </>
        )}
      </section>

      {/* Artwork lightbox */}
      {lightboxUrl && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm"
          onClick={() => { setLightboxUrl(null); setLbCropping(false); }}
        >
          {/* Close button */}
          <button
            onClick={() => { setLightboxUrl(null); setLbCropping(false); }}
            className="absolute top-4 right-4 p-2 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors z-10"
          >
            <X size={20} />
          </button>

          {/* Crop mode toggle */}
          <div className="absolute top-4 left-4 flex gap-2 z-10">
            <Tooltip content={lbCropping ? "Exit crop mode" : "Adjust crop position"}>
              <button
                onClick={(e) => { e.stopPropagation(); setLbCropping(!lbCropping); }}
                className={`p-2 rounded-full transition-colors ${
                  lbCropping ? "bg-accent text-white" : "bg-white/10 hover:bg-white/20 text-white"
                }`}
              >
                <Move size={20} />
              </button>
            </Tooltip>
            {lbCropping && lightboxEntity && (
              <>
                <button
                  onClick={async (e) => {
                    e.stopPropagation();
                    setLbSaving(true);
                    try {
                      await metadataManagerApi.updateEntityCrop(
                        lightboxEntity.entityType, lightboxEntity.entityId,
                        `${lbCropX}% ${lbCropY}%`,
                      );
                      toast({ type: "success", title: "Crop position saved" });
                      refetch();
                    } catch {
                      toast({ type: "error", title: "Failed to save crop" });
                    } finally {
                      setLbSaving(false);
                    }
                  }}
                  disabled={lbSaving}
                  className="px-3 py-1.5 rounded-full bg-green-500/80 hover:bg-green-500 text-white text-sm font-medium transition-colors"
                >
                  {lbSaving ? "Saving…" : "Save"}
                </button>
                <button
                  onClick={async (e) => {
                    e.stopPropagation();
                    setLbCropX(50);
                    setLbCropY(50);
                    setLbSaving(true);
                    try {
                      await metadataManagerApi.updateEntityCrop(
                        lightboxEntity.entityType, lightboxEntity.entityId, null,
                      );
                      toast({ type: "success", title: "Crop position reset" });
                      refetch();
                    } catch {
                      toast({ type: "error", title: "Failed to reset crop" });
                    } finally {
                      setLbSaving(false);
                    }
                  }}
                  className="px-3 py-1.5 rounded-full bg-white/10 hover:bg-white/20 text-white text-sm transition-colors"
                >
                  Reset
                </button>
              </>
            )}
          </div>

          <div className="flex flex-col items-center gap-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between w-full max-w-[90vw] -mb-2">
              <span className="text-sm text-white/80 font-medium truncate max-w-[60vw]">{lightboxName}</span>
            </div>
            {lbCropping ? (
              <>
                <div
                  ref={lbCropRef}
                  className="relative w-64 h-64 rounded-lg overflow-hidden border-2 border-accent shadow-2xl cursor-crosshair"
                  onClick={(e) => {
                    const rect = lbCropRef.current?.getBoundingClientRect();
                    if (!rect) return;
                    const x = Math.round(((e.clientX - rect.left) / rect.width) * 100);
                    const y = Math.round(((e.clientY - rect.top) / rect.height) * 100);
                    setLbCropX(Math.max(0, Math.min(100, x)));
                    setLbCropY(Math.max(0, Math.min(100, y)));
                  }}
                >
                  <img
                    src={lightboxUrl}
                    alt={lightboxName}
                    className="w-full h-full object-cover"
                    style={{ objectPosition: `${lbCropX}% ${lbCropY}%` }}
                    draggable={false}
                  />
                  <div
                    className="absolute w-3 h-3 border-2 border-white rounded-full shadow-lg pointer-events-none"
                    style={{
                      left: `${lbCropX}%`,
                      top: `${lbCropY}%`,
                      transform: "translate(-50%, -50%)",
                      boxShadow: "0 0 0 1px rgba(0,0,0,0.5), 0 0 8px rgba(225,29,46,0.5)",
                    }}
                  />
                </div>
                <p className="text-xs text-white/60">Click to set focal point · {lbCropX}% {lbCropY}%</p>
              </>
            ) : (
              <img
                src={lightboxUrl}
                alt={lightboxName}
                className="max-w-full max-h-[85vh] object-contain rounded-xl shadow-2xl border border-surface-border"
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function SourceCoveragePie({ label, stats }: { label: string; stats: { total: number; with_art: number; with_source: number; missing_with_source: number; missing_no_source: number } }) {
  const srcPct = stats.total > 0 ? Math.round((stats.with_source / stats.total) * 100) : 0;
  const withoutSource = stats.total - stats.with_source;
  const segments = [
    { name: "With source", value: stats.with_source, color: "#22c55e" },
    { name: "No source", value: withoutSource, color: "#3f3f46" },
  ].filter(s => s.value > 0);

  return (
    <div className="card px-4 py-3">
      <p className="text-[10px] uppercase tracking-wider text-text-muted mb-2">{label}</p>
      <div className="flex items-center gap-4">
        <div className="relative w-28 h-28 shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={segments}
                dataKey="value"
                cx="50%"
                cy="50%"
                innerRadius={28}
                outerRadius={46}
                strokeWidth={0}
                paddingAngle={segments.length > 1 ? 2 : 0}
              >
                {segments.map((s, i) => (
                  <Cell key={i} fill={s.color} />
                ))}
              </Pie>
              <RTooltip
                contentStyle={{ background: "#1e1e2e", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8 }}
                itemStyle={{ color: "#e0e0e0", fontSize: 12 }}
              />
            </PieChart>
          </ResponsiveContainer>
          <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
            <span className="text-lg font-bold text-text-primary leading-none">{srcPct}%</span>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-2 text-[11px] flex-1">
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-green-400" />
            <span className="text-text-muted">With source:</span>
            <span className="text-text-primary font-medium">{stats.with_source}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-zinc-600" />
            <span className="text-text-muted">No source:</span>
            <span className="text-text-primary font-medium">{withoutSource}</span>
          </div>
          <p className="text-text-muted/60 text-[10px] mt-1">{stats.total.toLocaleString()} total</p>
        </div>
      </div>
    </div>
  );
}

function ArtworkEntityTile({
  entity, selected, expanded, onToggleSelect, onToggleExpand, onRefreshed, onViewArt,
}: {
  entity: ArtworkEntityRow;
  selected: boolean;
  expanded: boolean;
  onToggleSelect: () => void;
  onToggleExpand: () => void;
  onRefreshed: () => void;
  onViewArt: (url: string, name: string, entity: ArtworkEntityRow) => void;
}) {
  const { toast } = useToast();
  const qc = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [imgError, setImgError] = useState(false);
  const [showSources, setShowSources] = useState(false);
  const [imgKey, setImgKey] = useState(0);
  const [tileDragging, setTileDragging] = useState(false);

  const cropPos = entity.crop_position || undefined;
  const cropStyle = cropPos ? { objectPosition: cropPos } : undefined;

  const artUrl = entity.has_art
    ? metadataManagerApi.entityArtworkUrl(entity.entity_type, entity.id) + `?t=${imgKey}`
    : null;

  useEffect(() => { setImgError(false); }, [entity.has_art, imgKey]);

  const handleUpload = async (file: File) => {
    if (!file.type.startsWith("image/")) {
      toast({ type: "error", title: "Only image files are allowed" });
      return;
    }
    setUploading(true);
    try {
      await metadataManagerApi.uploadEntityArtwork(entity.entity_type, entity.id, file);
      toast({ type: "success", title: "Artwork uploaded" });
      setImgKey((k) => k + 1);
      setImgError(false);
      qc.invalidateQueries({ queryKey: ["artworkEntities"] });
      qc.invalidateQueries({ queryKey: ["artworkStats"] });
      onRefreshed();
    } catch {
      toast({ type: "error", title: "Upload failed" });
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async () => {
    try {
      await metadataManagerApi.deleteEntityArtwork(entity.entity_type, entity.id);
      toast({ type: "success", title: "Artwork deleted" });
      setImgKey((k) => k + 1);
      qc.invalidateQueries({ queryKey: ["artworkEntities"] });
      qc.invalidateQueries({ queryKey: ["artworkStats"] });
      onRefreshed();
    } catch {
      toast({ type: "error", title: "Delete failed" });
    }
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await metadataManagerApi.artworkBulkRepair(entity.entity_type, [entity.id]);
      toast({ type: "success", title: "Refresh complete" });
      setImgKey((k) => k + 1);
      qc.invalidateQueries({ queryKey: ["artworkEntities"] });
      qc.invalidateQueries({ queryKey: ["artworkStats"] });
      onRefreshed();
    } catch {
      toast({ type: "error", title: "Refresh failed" });
    } finally {
      setRefreshing(false);
    }
  };

  const categoryColors: Record<string, string> = {
    filled: "bg-green-400/15 text-green-400 border-green-400/30",
    missing: "bg-yellow-400/15 text-yellow-400 border-yellow-400/30",
    unavailable: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
  };

  const eType = entity.entity_type as string;
  const Icon = eType === "artist" ? User : eType === "poster" ? Film : Disc3;

  return (
    <>
      <div className={`card overflow-hidden transition-colors ${selected ? "ring-1 ring-accent/40" : ""}`}>
        <div className="flex gap-3 p-3">
          {/* Checkbox — not for poster view */}
          {eType !== "poster" && (
            <div className="flex items-start pt-1">
              <input
                type="checkbox"
                checked={selected}
                onChange={onToggleSelect}
                className="shrink-0 accent-accent"
              />
            </div>
          )}

          {/* Artwork tile */}
          <div
            className={`group relative shrink-0 w-32 h-32 rounded-lg overflow-hidden border bg-surface-light transition-all ${
              tileDragging
                ? "border-accent border-2 bg-accent/10 shadow-[0_0_20px_rgba(225,29,46,0.2)]"
                : "border-surface-border"
            }`}
            onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setTileDragging(true); }}
            onDragLeave={(e) => { e.preventDefault(); e.stopPropagation(); setTileDragging(false); }}
            onDrop={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setTileDragging(false);
              const file = e.dataTransfer.files?.[0];
              if (file) handleUpload(file);
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleUpload(f);
                e.target.value = "";
              }}
            />
            {uploading ? (
              <div className="w-full h-full flex items-center justify-center text-accent">
                <RefreshCw size={18} className="animate-spin" />
              </div>
            ) : tileDragging ? (
              <div className="w-full h-full flex flex-col items-center justify-center gap-2 text-accent">
                <Upload size={24} />
                <span className="text-[10px] uppercase tracking-wider">Drop image</span>
              </div>
            ) : artUrl && !imgError ? (
              <img
                src={artUrl}
                alt={entity.name}
                className="w-full h-full object-cover cursor-pointer"
                style={cropStyle}
                onError={() => setImgError(true)}
                onClick={() => artUrl && onViewArt(artUrl, entity.name, entity)}
                loading="lazy"
              />
            ) : (
              <div
                className="w-full h-full flex flex-col items-center justify-center gap-2 text-text-muted cursor-pointer"
                onClick={() => fileInputRef.current?.click()}
              >
                <Icon size={36} />
                <span className="text-[10px] uppercase tracking-wider">Drop or click</span>
              </div>
            )}

            {/* Hover action overlay */}
            {!tileDragging && (
            <div
              className="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 pointer-events-none group-hover:pointer-events-auto transition-opacity flex items-end justify-center gap-1.5 p-2 cursor-pointer"
              onClick={() => artUrl && onViewArt(artUrl, entity.name, entity)}
            >
              <Tooltip content="Upload">
                <button
                  onClick={(e) => { e.stopPropagation(); fileInputRef.current?.click(); }}
                  className="p-1.5 rounded bg-white/20 hover:bg-white/30 text-white"
                >
                  <Upload size={13} />
                </button>
              </Tooltip>
              <Tooltip content="Refresh from sources">
                <button
                  onClick={(e) => { e.stopPropagation(); handleRefresh(); }}
                  disabled={refreshing}
                  className="p-1.5 rounded bg-white/20 hover:bg-white/30 text-white"
                >
                  <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} />
                </button>
              </Tooltip>
              {entity.has_art && (
                <Tooltip content="Delete artwork">
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDelete(); }}
                    className="p-1.5 rounded bg-red-500/40 hover:bg-red-500/60 text-white"
                  >
                    <Trash2 size={13} />
                  </button>
                </Tooltip>
              )}
              {eType !== "poster" && (
                <Tooltip content="Edit sources">
                  <button
                    onClick={(e) => { e.stopPropagation(); setShowSources(true); }}
                    className="p-1.5 rounded bg-blue-500/30 hover:bg-blue-500/50 text-white"
                  >
                    <Link size={13} />
                  </button>
                </Tooltip>
              )}
            </div>
            )}
            <span className="absolute bottom-0 left-0 right-0 text-center text-[8px] uppercase tracking-wider bg-black/50 text-white/80 py-0.5">
              {entity.entity_type}
            </span>
          </div>

          {/* Name + info + children */}
          <div className="flex-1 min-w-0">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                {eType === "poster" ? (
                  <a
                    href={`/video/${entity.id}`}
                    className="text-sm font-medium text-text-primary hover:text-accent transition-colors text-left truncate block w-full"
                  >
                    {entity.name}
                  </a>
                ) : (
                  <button
                    onClick={onToggleExpand}
                    className="text-sm font-medium text-text-primary hover:text-accent transition-colors text-left truncate block w-full"
                  >
                    {entity.name}
                  </button>
                )}
                <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                  <span className="text-[10px] text-text-muted">
                    {entity.video_count} video{entity.video_count !== 1 ? "s" : ""}
                  </span>
                  {entity.provenance && (
                    <span className="text-[10px] text-text-muted font-mono">{entity.provenance}</span>
                  )}
                  {entity.source_providers.length > 0 && (
                    <span className="text-[10px] text-text-muted">src: {entity.source_providers.join(", ")}</span>
                  )}
                </div>
              </div>
              <span className={`inline-flex text-[10px] px-2 py-0.5 rounded-full border font-medium shrink-0 ${categoryColors[entity.category] || ""}`}>
                {entity.category}
              </span>
            </div>

            {/* Children links — always visible (compact) */}
            {entity.children.length > 0 && (
              <div className={`mt-2 ${expanded ? "" : "max-h-16 overflow-hidden"}`}>
                <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                  {(expanded ? entity.children : entity.children.slice(0, 6)).map((child) => (
                    <a
                      key={child.id}
                      href={`/video/${child.id}`}
                      className="text-[11px] text-accent/80 hover:text-accent hover:underline truncate max-w-[200px] flex items-center gap-1"
                      title={`${child.artist || ""} — ${child.title}`}
                    >
                      <ChevronLeft size={8} className="rotate-180 shrink-0" />
                      {entity.entity_type === "album"
                        ? child.title
                        : `${child.artist || ""} – ${child.title}`
                      }
                    </a>
                  ))}
                </div>
                {!expanded && entity.children.length > 6 && (
                  <button
                    onClick={onToggleExpand}
                    className="text-[10px] text-text-muted hover:text-accent mt-0.5"
                  >
                    +{entity.children.length - 6} more…
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Edit Sources Modal */}
      {showSources && (
        <EditSourcesModal
          entityType={entity.entity_type}
          entityId={entity.id}
          entityName={entity.name}
          onClose={() => setShowSources(false)}
          onSaved={() => { onRefreshed(); }}
        />
      )}
    </>
  );
}


function EditSourcesModal({
  entityType, entityId, entityName, onClose, onSaved,
}: {
  entityType: string;
  entityId: number;
  entityName: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { data: sources, isLoading } = useEntitySources(entityType, entityId);
  const updateMutation = useUpdateEntitySources();
  const { toast } = useToast();
  const [mbId, setMbId] = useState("");
  const [wikiUrl, setWikiUrl] = useState("");
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    if (sources && !initialized) {
      setMbId(sources.mb_id || "");
      setWikiUrl(sources.sources.find(s => s.provider === "wikipedia")?.url || "");
      setInitialized(true);
    }
  }, [sources, initialized]);

  const handleSave = () => {
    updateMutation.mutate(
      { entity_type: entityType, entity_id: entityId, mb_id: mbId || null, wiki_url: wikiUrl || null },
      {
        onSuccess: () => {
          toast({ type: "success", title: "Sources updated" });
          onSaved();
          onClose();
        },
        onError: () => toast({ type: "error", title: "Save failed" }),
      },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="bg-surface border border-surface-border rounded-xl shadow-xl w-full max-w-md mx-4 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-text-primary">Edit Sources — {entityName}</h3>
          <button onClick={onClose} className="text-text-muted hover:text-text-secondary">
            <X size={16} />
          </button>
        </div>

        {isLoading ? (
          <Skeleton className="h-24 rounded-lg" />
        ) : (
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">
                MusicBrainz ID
              </label>
              <input
                type="text"
                value={mbId}
                onChange={(e) => setMbId(e.target.value)}
                placeholder="e.g. 12345678-abcd-…"
                className="input-field w-full text-sm py-2"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-1">
                Wikipedia URL
              </label>
              <input
                type="text"
                value={wikiUrl}
                onChange={(e) => setWikiUrl(e.target.value)}
                placeholder="https://en.wikipedia.org/wiki/…"
                className="input-field w-full text-sm py-2"
              />
            </div>

            {sources && sources.sources.length > 0 && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-text-muted mb-1">Existing Sources</p>
                {sources.sources.map((s, i) => (
                  <div key={i} className="flex items-center gap-2 text-[11px] text-text-muted">
                    <span className="font-mono">{s.provider}</span>
                    <a href={s.url} target="_blank" rel="noreferrer" className="text-accent/70 hover:text-accent truncate">
                      {s.url}
                    </a>
                    {s.provenance && <span className="text-[10px]">({s.provenance})</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="btn-sm text-xs px-4 py-2 rounded-lg btn-ghost">
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={updateMutation.isPending}
            className="btn-sm text-xs px-4 py-2 rounded-lg bg-accent text-white hover:bg-accent/90 flex items-center gap-1.5"
          >
            {updateMutation.isPending && <Loader2 size={12} className="animate-spin" />}
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
