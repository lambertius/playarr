import { useState, useRef, useEffect, useCallback } from "react";
import {
  Database, Users, AlertTriangle, Check, Search, Eye, EyeOff,
  Plus, Tags, BarChart3, ExternalLink, Loader2, RefreshCw, Edit3,
  X, Undo2, Sparkles, Layers,
} from "lucide-react";
import {
  useMbidStats, useArtistConflicts, useConsolidateArtist,
  useGenreBlacklist, useUpdateGenreBlacklist, useCreateGenre,
  useGenreConsolidations, useGenreSuggestions,
  useConsolidateGenres, useConsolidateGenresManual, useUnconsolidateGenre,
  useAddGenreToTile, useBlacklistTile, useCreateTile,
} from "@/hooks/queries";
import { metadataManagerApi } from "@/lib/api";
import type { GenreSearchResult } from "@/types";
import { useToast } from "@/components/Toast";
import { Tooltip } from "@/components/Tooltip";
import { Skeleton } from "@/components/Feedback";

// ─── Tab definitions ─────────────────────────────────────

type TabId = "overview" | "artists" | "genres" | "genre-consolidation";

const TABS: { id: TabId; label: string; icon: typeof Database }[] = [
  { id: "overview", label: "MBID Coverage", icon: BarChart3 },
  { id: "artists", label: "Artist Consolidation", icon: Users },
  { id: "genre-consolidation", label: "Genre Consolidation", icon: Sparkles },
  { id: "genres", label: "Genre Manager", icon: Tags },
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
      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Total Videos" value={stats.total_videos} />
        <StatCard label="With Any MBID" value={stats.with_any_mbid} pct={pct(stats.with_any_mbid)} />
        <StatCard label="Artist Conflicts" value={stats.artist_conflicts} alert={stats.artist_conflicts > 0} />
        <StatCard label="Full Coverage" value={stats.with_recording_id} pct={pct(stats.with_recording_id)} />
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

function StatCard({ label, value, pct, alert }: { label: string; value: number; pct?: number; alert?: boolean }) {
  return (
    <div className={`card px-4 py-3 ${alert ? "border-yellow-500/30" : ""}`}>
      <p className="text-[10px] uppercase tracking-wider text-text-muted mb-1">{label}</p>
      <div className="flex items-end gap-2">
        <span className={`text-xl font-bold ${alert ? "text-yellow-400" : "text-text-primary"}`}>
          {value.toLocaleString()}
        </span>
        {pct !== undefined && (
          <span className="text-xs text-text-muted mb-0.5">{pct}%</span>
        )}
        {alert && <AlertTriangle size={14} className="text-yellow-400 mb-0.5" />}
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
