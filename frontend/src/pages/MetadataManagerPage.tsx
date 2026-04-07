import { useState } from "react";
import {
  Database, Users, AlertTriangle, Check, Search, Eye, EyeOff,
  Plus, Tags, BarChart3, ExternalLink, Loader2,
} from "lucide-react";
import { useMbidStats, useArtistConflicts, useConsolidateArtist, useGenreBlacklist, useUpdateGenreBlacklist, useCreateGenre } from "@/hooks/queries";
import { useToast } from "@/components/Toast";
import { Tooltip } from "@/components/Tooltip";
import { Skeleton } from "@/components/Feedback";

// ─── Tab definitions ─────────────────────────────────────

type TabId = "overview" | "artists" | "genres";

const TABS: { id: TabId; label: string; icon: typeof Database }[] = [
  { id: "overview", label: "MBID Coverage", icon: BarChart3 },
  { id: "artists", label: "Artist Consolidation", icon: Users },
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
  const { data: conflicts, isLoading } = useArtistConflicts();
  const consolidateMutation = useConsolidateArtist();
  const { toast } = useToast();

  if (isLoading) {
    return <Skeleton className="h-48 rounded-lg" />;
  }

  if (!conflicts || conflicts.length === 0) {
    return (
      <div className="card px-6 py-10 text-center">
        <Check size={32} className="mx-auto text-green-400 mb-3" />
        <p className="text-sm text-text-primary font-medium mb-1">No artist name conflicts</p>
        <p className="text-xs text-text-muted">
          All videos with the same MusicBrainz Artist ID use consistent naming.
        </p>
      </div>
    );
  }

  const handleConsolidate = (mbId: string, canonicalName: string) => {
    consolidateMutation.mutate(
      { mb_artist_id: mbId, canonical_name: canonicalName },
      {
        onSuccess: (result) => {
          toast({
            type: "success",
            title: `Updated ${result.updated} video${result.updated !== 1 ? "s" : ""} to "${canonicalName}"`,
          });
        },
        onError: () => toast({ type: "error", title: "Failed to consolidate artist" }),
      },
    );
  };

  return (
    <div className="space-y-4">
      <p className="text-xs text-text-muted leading-relaxed">
        These artists share the same MusicBrainz ID but appear with different names in your library.
        Click a name variant to apply it everywhere.
      </p>

      {conflicts.map((conflict) => (
        <div key={conflict.mb_artist_id} className="card">
          <div className="px-4 py-3 border-b border-white/5 flex items-center gap-2">
            <AlertTriangle size={14} className="text-yellow-400 shrink-0" />
            <span className="text-[10px] font-mono text-text-muted truncate">{conflict.mb_artist_id}</span>
            <span className="text-[10px] text-text-muted ml-auto">
              {conflict.total_videos} video{conflict.total_videos !== 1 ? "s" : ""}
            </span>
          </div>
          <div className="divide-y divide-white/5">
            {conflict.names.map((entry) => (
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
          </div>
        </div>
      ))}
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

  const visible = genres.filter((g) => !g.blacklisted);
  const hidden = genres.filter((g) => g.blacklisted);
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
        Use this to hide noisy or irrelevant genre tags.
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
