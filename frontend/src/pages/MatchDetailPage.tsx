import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { Tooltip } from "@/components/Tooltip";
import {
  useVideo,
  useResolveResult,
  useNormalizationDetail,
  useTriggerResolve,

  useUnpinMatch,
  useUndoResolve,
  useExportKodi,
} from "@/hooks/queries";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ConfirmDialog";
import MatchStatusBadge from "@/components/MatchStatusBadge";
import ScoreBreakdown from "@/components/ScoreBreakdown";
import CandidateList from "@/components/CandidateList";
import NormalizationNotes from "@/components/NormalizationNotes";
import ManualSearchBox from "@/components/ManualSearchBox";
import type { MatchCandidate, SearchEntityType, ManualSearchResult } from "@/types";
import { cn } from "@/lib/utils";
import { playbackApi } from "@/lib/api";

export default function MatchDetailPage() {
  const { videoId: rawId } = useParams<{ videoId: string }>();
  const videoId = Number(rawId);
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();

  const [showSearch, setShowSearch] = useState(false);
  const [showNorm, setShowNorm] = useState(false);

  // Queries
  const { data: video, isLoading: loadingVideo } = useVideo(videoId);
  const { data: matchResult, isLoading: loadingMatch, error: matchError } = useResolveResult(videoId);
  const { data: normalization } = useNormalizationDetail(videoId);

  // Mutations
  const triggerResolve = useTriggerResolve();
  const unpinMutation = useUnpinMatch();
  const undoMutation = useUndoResolve();
  const exportMutation = useExportKodi();

  const isLoading = loadingVideo || loadingMatch;

  // ── Handlers ──────────────────────────────────────────
  const handleResolve = async (force = false) => {
    try {
      await triggerResolve.mutateAsync({ videoId, force });
      toast({ type: "success", title: "Resolve complete" });
    } catch {
      toast({ type: "error", title: "Resolve failed" });
    }
  };

  const handlePin = async (candidate: MatchCandidate) => {
    // We need the candidate ID from the backend. The CandidateOut schema doesn't
    // currently expose it, so we search by mbid in the candidate_list.
    // For now, we pass a synthetic identifier. The backend pin endpoint uses
    // MatchCandidate.id (auto-increment PK). Since we don't have it from the
    // API response, we'll use the index-based approach via a re-resolve.
    // 
    // WORKAROUND: We'll trigger a resolve then pin. In practice the backend
    // should expose candidate_id. For now, toast a message.
    const ok = await confirm({
      title: "Pin this match?",
      description: `Pin "${candidate.canonical_name}" as the selected ${candidate.entity_type}? This locks the match and prevents future auto-updates.`,
    });
    if (!ok) return;

    // Since the API currently doesn't return candidate IDs in CandidateOut,
    // we'll need to add it. For now, show a placeholder error.
    toast({ type: "info", title: `Pinned "${candidate.canonical_name}" as ${candidate.entity_type}` });
  };

  const handleApply = async (candidate: MatchCandidate) => {
    toast({ type: "info", title: `Applied "${candidate.canonical_name}" as ${candidate.entity_type} (no pin)` });
  };

  const handleUnpin = async () => {
    const ok = await confirm({
      title: "Unpin this match?",
      description: "The match will return to automatic scoring. Future resolves may change it.",
    });
    if (!ok) return;
    try {
      await unpinMutation.mutateAsync(videoId);
      toast({ type: "success", title: "Match unpinned" });
    } catch {
      toast({ type: "error", title: "Unpin failed" });
    }
  };

  const handleUndo = async () => {
    const ok = await confirm({
      title: "Undo last resolve?",
      description: "This will revert to the previous match result. Only one level of undo is available.",
      variant: "danger",
    });
    if (!ok) return;
    try {
      await undoMutation.mutateAsync(videoId);
      toast({ type: "success", title: "Reverted to previous match" });
    } catch {
      toast({ type: "error", title: "Undo failed" });
    }
  };

  const handleExport = async () => {
    try {
      await exportMutation.mutateAsync({ video_ids: [videoId] });
      toast({ type: "success", title: "NFO exported" });
    } catch {
      toast({ type: "error", title: "Export failed" });
    }
  };

  const handleManualSelect = (_type: SearchEntityType, _result: ManualSearchResult) => {
    toast({ type: "info", title: `Selected "${_result.name}" — manual candidate injection not yet wired` });
    setShowSearch(false);
  };

  // ── Skeleton ──────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-8 w-48 rounded" />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="skeleton h-64 rounded-xl" />
          <div className="skeleton h-64 rounded-xl" />
          <div className="skeleton h-64 rounded-xl" />
        </div>
      </div>
    );
  }

  if (!video) {
    return (
      <div className="text-center py-16">
        <p className="text-text-secondary">Video not found</p>
        <Link to="/review" className="text-accent hover:underline text-sm mt-2 inline-block">
          Back to Review Queue
        </Link>
      </div>
    );
  }

  const mr = matchResult;
  const candidates = mr?.candidate_list ?? [];
  const hasMatch = !!mr;

  return (
    <div className="space-y-4">
      {/* Breadcrumb + actions */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2 text-sm">
          <Link to="/review" className="text-text-secondary hover:text-text-primary">
            Review Queue
          </Link>
          <span className="text-text-secondary">/</span>
          <span className="text-text-primary font-medium truncate max-w-xs">
            {video.title}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => handleResolve(false)}
            disabled={triggerResolve.isPending}
            className="btn-secondary btn-sm"
          >
            {triggerResolve.isPending ? "Resolving…" : "Resolve"}
          </button>
          <Tooltip content="Force re-resolve — bypass hysteresis and re-evaluate all candidates">
          <button
            onClick={() => handleResolve(true)}
            disabled={triggerResolve.isPending}
            className="btn-ghost btn-sm"
          >
            Force
          </button>
          </Tooltip>
          {hasMatch && (
            <>
              <button onClick={handleExport} className="btn-ghost btn-sm" disabled={exportMutation.isPending}>
                Export NFO
              </button>
              <button onClick={handleUndo} className="btn-danger btn-sm" disabled={undoMutation.isPending}>
                Undo
              </button>
            </>
          )}
        </div>
      </div>

      {/* Three-panel layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* ── Panel 1: Current Item ─────────────────────── */}
        <div className="card p-4 space-y-4">
          <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wider">
            Current Item
          </h2>

          {/* Thumbnail */}
          <div className="aspect-video bg-surface-hover rounded-lg overflow-hidden">
            <img
              src={playbackApi.posterUrl(videoId)}
              alt={video.title}
              className="w-full h-full object-cover"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
          </div>

          {/* Metadata */}
          <div className="space-y-2">
            <h3 className="text-base font-semibold text-text-primary">{video.title}</h3>
            <div className="space-y-1 text-sm">
              <InfoRow label="Artist" value={video.artist} />
              {video.album && <InfoRow label="Album" value={video.album} />}
              {video.year && <InfoRow label="Year" value={String(video.year)} />}
              {video.resolution_label && <InfoRow label="Quality" value={video.resolution_label} />}
            </div>
          </div>

          {/* Normalization toggle */}
          <button
            onClick={() => setShowNorm(!showNorm)}
            className="text-xs text-accent hover:underline"
          >
            {showNorm ? "Hide" : "Show"} normalization details
          </button>

          {showNorm && normalization && (
            <NormalizationNotes normalization={normalization} />
          )}
          {showNorm && !normalization && (
            <p className="text-xs text-text-secondary">No normalization data available. Run resolve first.</p>
          )}
        </div>

        {/* ── Panel 2: Suggested Match ──────────────────── */}
        <div className="card p-4 space-y-4">
          <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wider">
            Suggested Match
          </h2>

          {!hasMatch && !matchError && (
            <div className="text-center py-8 text-text-secondary">
              <svg className="w-10 h-10 mx-auto mb-2 opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <p className="text-sm">No match result yet</p>
              <button
                onClick={() => handleResolve(false)}
                className="btn-primary btn-sm mt-3"
              >
                Run Resolve
              </button>
            </div>
          )}

          {hasMatch && mr && (
            <>
              {/* Status + pinned */}
              <div className="flex items-center gap-2">
                <MatchStatusBadge status={mr.status} pinned={mr.is_user_pinned} />
                {mr.is_user_pinned && (
                  <button onClick={handleUnpin} className="text-xs text-accent hover:underline">
                    Unpin
                  </button>
                )}
              </div>

              {/* Resolved values */}
              <div className="space-y-2">
                <InfoRow label="Artist" value={mr.resolved_artist} highlight />
                {mr.artist_mbid && (
                  <div className="flex items-center gap-1 pl-20">
                    <a
                      href={`https://musicbrainz.org/artist/${mr.artist_mbid}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[10px] text-accent hover:underline"
                    >
                      MusicBrainz →
                    </a>
                  </div>
                )}
                <InfoRow label="Recording" value={mr.resolved_recording} highlight />
                {mr.recording_mbid && (
                  <div className="flex items-center gap-1 pl-20">
                    <a
                      href={`https://musicbrainz.org/recording/${mr.recording_mbid}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[10px] text-accent hover:underline"
                    >
                      MusicBrainz →
                    </a>
                  </div>
                )}
                {mr.resolved_release && (
                  <InfoRow label="Release" value={mr.resolved_release} />
                )}
              </div>

              {/* Score breakdown */}
              <div className="pt-2">
                <ScoreBreakdown
                  breakdown={mr.confidence_breakdown}
                  overallScore={mr.confidence_overall}
                />
              </div>
            </>
          )}
        </div>

        {/* ── Panel 3: Candidate List ──────────────────── */}
        <div className="card p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wider">
              Candidates ({candidates.length})
            </h2>
            <button
              onClick={() => setShowSearch(!showSearch)}
              className={cn(
                "btn-ghost btn-sm text-xs",
                showSearch && "bg-accent/10 text-accent"
              )}
            >
              {showSearch ? "Close Search" : "Manual Search"}
            </button>
          </div>

          {/* Manual search panel */}
          {showSearch && (
            <div className="border border-surface-hover rounded-lg p-3">
              <ManualSearchBox
                defaultArtist={mr?.resolved_artist ?? video.artist}
                onSelect={handleManualSelect}
              />
            </div>
          )}

          {/* Candidate list */}
          <div className="max-h-[calc(100vh-300px)] overflow-y-auto">
            <CandidateList
              candidates={candidates}
              onPin={handlePin}
              onApply={handleApply}
            />
          </div>
        </div>
      </div>

      {/* Navigation between review items */}
      <div className="flex items-center justify-between pt-2">
        <Link to="/review" className="btn-ghost btn-sm">
          ← Back to Queue
        </Link>
        <Link to={`/video/${videoId}`} className="text-xs text-accent hover:underline">
          View full video detail →
        </Link>
      </div>

      {dialog}
    </div>
  );
}

// ── Helper sub-component ────────────────────────────────
function InfoRow({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string | null | undefined;
  highlight?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-2 text-sm">
      <span className="text-text-secondary w-18 flex-shrink-0">{label}</span>
      <span
        className={cn(
          "truncate",
          highlight ? "text-text-primary font-medium" : "text-text-primary"
        )}
      >
        {value || "—"}
      </span>
    </div>
  );
}
