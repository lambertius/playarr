import { useState, useMemo } from "react";
import ReactDOM from "react-dom";
import {
  Bot, Eye, Check, ChevronDown, ChevronUp, Loader2,
  AlertTriangle, Fingerprint, Undo2, Lock, CheckCheck, Zap, FolderSync,
  Settings2, ShieldCheck, X,
} from "lucide-react";
import {
  useAIComparison, useAIResults, useAIScenes,
  useAIEnrich, useAIApplyFields,
  useAIUndo, useAIFingerprint, useAISettings, useAIDismissScrape,
} from "@/hooks/queries";
import { useToast } from "@/components/Toast";
import { Tooltip } from "@/components/Tooltip";
import type { AIFieldComparison, FingerprintResult, AIIdentityVerification, AIMismatchInfo } from "@/types";

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  gemini: "Gemini",
  claude: "Claude",
  local: "Local LLM",
  none: "None",
};

/** model_name values that indicate a scrape metadata scan (vs pure AI enrichment). */
const SCRAPE_MODEL_NAMES = new Set(["ai_auto_analyse", "wikipedia_scrape", "musicbrainz_scrape"]);

export function AIPanel({ videoId }: { videoId: number }) {
  const { toast } = useToast();

  // Global settings (read-only on this page)
  const aiSettings = useAISettings();

  // queries
  const comparison = useAIComparison(videoId);
  const results = useAIResults(videoId);
  const scenes = useAIScenes(videoId);

  // mutations
  const enrichMutation = useAIEnrich();
  const applyMutation = useAIApplyFields();
  const undoMutation = useAIUndo();
  const fingerprintMutation = useAIFingerprint();
  const dismissMutation = useAIDismissScrape();

  const [expanded, setExpanded] = useState(true);
  const [renameFiles, setRenameFiles] = useState(false);

  const latestResult = results.data?.[0];
  const comp = comparison.data;
  const hasComparison = comp && comp.fields.length > 0;
  const settings = aiSettings.data;

  // Find the latest scrape metadata result (not pure AI enrichment)
  const latestScrapeResult = useMemo(() => {
    if (!results.data) return null;
    return results.data.find((r) => SCRAPE_MODEL_NAMES.has(r.model_name ?? "")) ?? null;
  }, [results.data]);

  // Analysis summary
  const summary = useMemo(() => {
    if (!comp || comp.fields.length === 0) return null;
    const changed = comp.fields.filter((f) => f.changed && f.ai_value != null && !f.locked);
    const verified = comp.fields.filter((f) => !f.changed && f.ai_value != null);
    const unchanged = comp.fields.filter((f) => f.ai_value == null);
    const highConf = changed.filter((f) => f.ai_confidence >= 0.85);
    return { changed, verified, unchanged, highConf, total: comp.fields.length };
  }, [comp]);

  // Only show tile when a scrape metadata scan result exists and hasn't been dismissed
  if (!latestScrapeResult || latestScrapeResult.dismissed_at) return null;

  return (
    <div className="card">
      {/* ─── Header ─── */}
      <div className="flex items-center gap-2 w-full">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-2 flex-1 text-left"
        >
          <Bot size={16} className="text-accent" />
          <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide flex-1">
            Scrape Metadata
          </h3>
          {summary && summary.changed.length > 0 && (
            summary.changed.every(f => f.accepted) ? (
              <span className="px-2 py-0.5 rounded-full bg-green-500/15 text-green-400 text-[11px] font-medium flex items-center gap-1">
                <CheckCheck size={11} /> All applied
              </span>
            ) : summary.changed.some(f => f.accepted) ? (
              <span className="px-2 py-0.5 rounded-full bg-accent/15 text-accent text-[11px] font-medium">
                {summary.changed.filter(f => !f.accepted).length} of {summary.changed.length} pending
              </span>
            ) : (
              <span className="px-2 py-0.5 rounded-full bg-accent/15 text-accent text-[11px] font-medium">
                {summary.changed.length} correction{summary.changed.length !== 1 ? "s" : ""} available
              </span>
            )
          )}
          {expanded ? <ChevronUp size={14} className="text-text-muted" /> : <ChevronDown size={14} className="text-text-muted" />}
        </button>
        <button
          onClick={() => {
            dismissMutation.mutate(videoId);
          }}
          className="btn-ghost btn-icon flex-shrink-0"
          aria-label="Dismiss"
        >
          <Tooltip content="Close — reappears after a new metadata scan">
          <X size={14} className="text-text-muted" />
          </Tooltip>
        </button>
      </div>

      {expanded && (
        <div className="mt-4 space-y-5">

          {/* ─── Section 1: Provider Status (read-only) ─── */}
          <ProviderStatus settings={settings} latestResult={latestResult} />

          {/* ─── Section 3: Analysis Status ─── */}
          {(latestResult || enrichMutation.isPending) && (
            <AnalysisStatus
              result={latestResult}
              isPending={enrichMutation.isPending}
              summary={summary}
              changeSummary={comp?.change_summary}
              model={comp?.model}
            />
          )}

          {/* ─── Section 4: Identity Verification (AI-detected) ─── */}
          {comp?.mismatch_report && typeof comp.mismatch_report === "object" && "ai_identity" in comp.mismatch_report && (
            <IdentityVerification
              identity={(comp.mismatch_report as { ai_identity?: AIIdentityVerification | null }).ai_identity ?? null}
              aiMismatch={(comp.mismatch_report as { ai_mismatch?: AIMismatchInfo | null }).ai_mismatch ?? null}
            />
          )}

          {/* ─── Section 5: Mismatch Details ─── */}
          {comp?.mismatch_report && typeof comp.mismatch_report === "object" && "overall_score" in comp.mismatch_report && (comp.mismatch_report as { overall_score: number }).overall_score > 0.3 && (
            <MismatchDetails
              report={comp.mismatch_report as { overall_score: number; is_suspicious: boolean; signals?: Array<{ name: string; score: number; weight: number; details?: string | null }> }}
              fields={comp.fields}
            />
          )}

          {/* ─── Section 6: Fingerprint Results ─── */}
          {(comp?.fingerprint_result || fingerprintMutation.data) && (
            <FingerprintResults
              result={fingerprintMutation.data ?? comp?.fingerprint_result ?? null}
            />
          )}

          {/* ─── Section 7: Suggested Corrections ─── */}
          {hasComparison && comp!.ai_result_id != null && (
            <CorrectionTable
              fields={comp!.fields}
              aiResultId={comp!.ai_result_id}
              renameFiles={renameFiles}
              onRenameToggle={setRenameFiles}
              onApply={(aiResultId, fieldNames) =>
                applyMutation.mutate(
                  { videoId, data: { ai_result_id: aiResultId, fields: fieldNames, rename_files: renameFiles } },
                  {
                    onSuccess: () => toast({ type: "success", title: "Changes applied" }),
                    onError: () => toast({ type: "error", title: "Apply failed" }),
                  },
                )
              }
              onUndo={(aiResultId) =>
                undoMutation.mutate(
                  { videoId, data: { ai_result_id: aiResultId } },
                  {
                    onSuccess: () => toast({ type: "success", title: "Metadata restored" }),
                    onError: () => toast({ type: "error", title: "Undo failed" }),
                  },
                )
              }
              isPending={applyMutation.isPending}
              isUndoPending={undoMutation.isPending}
              overallConfidence={comp?.overall_confidence ?? undefined}
              artworkUpdates={comp?.artwork_updates}
              model={comp?.model}
            />
          )}

          {/* ─── Section 8: Scene status ─── */}
          {scenes.data && (
            <div className="text-xs text-text-muted flex items-center gap-2">
              <Eye size={12} />
              <span>
                Scene analysis: {scenes.data.total_scenes} scenes detected
                {scenes.data.duration_seconds != null && (
                  <> in {scenes.data.duration_seconds.toFixed(0)}s</>
                )}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   Provider Status — read-only indicator from global settings
   ═══════════════════════════════════════════════════════════ */

function ProviderStatus({
  settings,
  latestResult,
}: {
  settings?: { provider: string; model_selection_mode: string; model_default?: string | null; auto_tier_preference: string } | null;
  latestResult?: { provider: string; model_name?: string | null } | null;
}) {
  if (!settings) return null;

  const providerLabel = PROVIDER_LABELS[settings.provider] || settings.provider;
  const mode = settings.model_selection_mode === "manual" ? "Manual" : "Auto";
  const activeModel = latestResult?.model_name || settings.model_default || "—";

  if (settings.provider === "none") {
    return (
      <div className="flex items-center gap-3 text-xs bg-surface-light rounded-lg px-3 py-2.5 text-text-muted">
        <Settings2 size={14} />
        <span>No AI provider configured.</span>
        <span className="text-accent">Configure in Settings → AI / Summaries</span>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs bg-surface-light rounded-lg px-3 py-2.5">
      <span className="text-text-muted">Provider: <strong className="text-text-primary">{providerLabel}</strong></span>
      <span className="text-text-muted">Routing: <strong className="text-text-primary">{mode}</strong></span>
      <span className="text-text-muted">Active Model: <strong className="text-text-primary font-mono">{activeModel}</strong></span>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   Analysis Status — clear status messaging
   ═══════════════════════════════════════════════════════════ */

function scanLabel(model?: string | null): { statusText: string; suggestionLabel: string } {
  if (model === "wikipedia_scrape") return { statusText: "Wiki scrape complete", suggestionLabel: "Wiki Suggestion" };
  if (model === "musicbrainz_scrape") return { statusText: "MusicBrainz scrape complete", suggestionLabel: "MusicBrainz Suggestion" };
  if (model === "ai_auto_analyse") return { statusText: "AI analysis complete", suggestionLabel: "AI Suggestion" };
  return { statusText: "AI analysis complete", suggestionLabel: "AI Suggestion" };
}

function AnalysisStatus({
  result,
  isPending,
  summary,
  changeSummary,
  model,
}: {
  result?: {
    status: string;
    provider: string;
    model_name?: string | null;
    confidence_score: number;
    created_at?: string | null;
    error_message?: string | null;
  } | null;
  isPending: boolean;
  summary: { changed: AIFieldComparison[]; verified: AIFieldComparison[]; unchanged: AIFieldComparison[]; highConf: AIFieldComparison[]; total: number } | null;
  changeSummary?: string | null;
  model?: string | null;
}) {
  if (isPending) {
    return (
      <div className="flex items-center gap-2 text-xs text-text-muted bg-surface-light rounded-lg px-3 py-2.5">
        <Loader2 size={14} className="animate-spin text-accent" />
        <span>Analysing metadata with AI...</span>
      </div>
    );
  }

  if (!result) return null;

  if (result.status === "failed") {
    return (
      <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 rounded-lg px-3 py-2.5">
        <AlertTriangle size={14} />
        <span>Analysis failed{result.error_message ? `: ${result.error_message}` : ""}</span>
      </div>
    );
  }

  const { statusText } = scanLabel(model ?? result?.model_name);

  return (
    <div className="space-y-2">
      <div className="rounded-lg bg-surface-light px-3 py-2.5 space-y-1.5">
        {/* Top line: AI analysis complete */}
        <div className="flex items-center gap-2 text-xs">
          <ShieldCheck size={14} className="text-green-400" />
          <span className="text-text-primary font-medium">{statusText}</span>
          {result.created_at && (
            <span className="text-text-muted ml-auto">{new Date(result.created_at).toLocaleString()}</span>
          )}
        </div>

        {/* Summary counts */}
        {summary && (
          <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-text-muted ml-5">
            {summary.changed.length > 0 && (
              <span className="text-accent font-medium">
                {summary.changed.length} correction{summary.changed.length !== 1 ? "s" : ""} suggested
              </span>
            )}
            {summary.verified.length > 0 && (
              <span className="text-green-400">
                {summary.verified.length} field{summary.verified.length !== 1 ? "s" : ""} verified correct
              </span>
            )}
            {summary.unchanged.length > 0 && (
              <span>
                {summary.unchanged.length} field{summary.unchanged.length !== 1 ? "s" : ""} unchanged
              </span>
            )}
            {summary.highConf.length > 0 && (
              <span className="text-blue-400">
                {summary.highConf.length} high-confidence
              </span>
            )}
          </div>
        )}

        {/* Change summary from backend */}
        {changeSummary && (
          <p className="text-[11px] text-text-muted ml-5 leading-relaxed">{changeSummary}</p>
        )}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   Identity Verification — AI-detected identity & mismatch evidence
   ═══════════════════════════════════════════════════════════ */

function IdentityVerification({
  identity,
  aiMismatch,
}: {
  identity: AIIdentityVerification | null;
  aiMismatch: AIMismatchInfo | null;
}) {
  if (!identity && !aiMismatch) return null;

  const isMismatch = aiMismatch?.is_mismatch;
  const severity = aiMismatch?.severity || "none";
  const reasons = aiMismatch?.reasons || [];
  const evidence = identity?.evidence;

  // Determine color scheme based on mismatch severity
  const colors = isMismatch
    ? severity === "high"
      ? "text-red-400 bg-red-500/10 border-red-500/20"
      : severity === "medium"
        ? "text-orange-400 bg-orange-500/10 border-orange-500/20"
        : "text-yellow-400 bg-yellow-500/10 border-yellow-500/20"
    : "text-green-400 bg-green-500/10 border-green-500/20";

  const icon = isMismatch ? <AlertTriangle size={14} /> : <ShieldCheck size={14} />;
  const heading = isMismatch
    ? `AI detected metadata mismatch (${severity})`
    : "AI verified identity";

  return (
    <div className={`rounded-lg border px-3 py-2.5 ${colors} space-y-2`}>
      <div className="flex items-center gap-2 text-xs font-medium">
        {icon}
        <span>{heading}</span>
      </div>

      {/* Candidate identity */}
      {identity?.candidate_artist && (
        <div className="text-[11px] ml-5 space-y-0.5">
          <div>
            <span className="text-text-muted">Identified as: </span>
            <strong>{identity.candidate_artist}</strong>
            {identity.candidate_title && <> — <strong>{identity.candidate_title}</strong></>}
          </div>
        </div>
      )}

      {/* Evidence flags */}
      {evidence && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 ml-5">
          {Object.entries(evidence).map(([key, value]) => (
            <span
              key={key}
              className={`text-[10px] px-1.5 py-0.5 rounded ${
                value
                  ? "bg-green-500/15 text-green-400"
                  : "bg-red-500/15 text-red-400"
              }`}
            >
              {value ? "✓" : "✗"} {key.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      )}

      {/* Mismatch reasons */}
      {reasons.length > 0 && (
        <div className="ml-5 space-y-0.5">
          {reasons.map((reason, i) => (
            <div key={i} className="text-[11px] flex items-start gap-1.5">
              <span className="text-red-400 mt-0.5">•</span>
              <span>{reason}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   Mismatch Details — actionable per-field mismatch info
   ═══════════════════════════════════════════════════════════ */

function MismatchDetails({
  report,
  fields,
}: {
  report: { overall_score: number; is_suspicious: boolean; signals?: Array<{ name: string; score: number; weight: number; details?: string | null }> };
  fields: AIFieldComparison[];
}) {
  const [showDetails, setShowDetails] = useState(false);
  const severity = report.overall_score >= 0.6 ? "text-red-400 bg-red-500/10 border-red-500/20" : "text-yellow-400 bg-yellow-500/10 border-yellow-500/20";

  // Build per-field mismatch descriptions
  const fieldMismatches = fields.filter((f) => f.changed && f.ai_value != null).map((f) => {
    const conf = f.ai_confidence;
    const desc = conf >= 0.85 ? "high confidence correction available"
      : conf >= 0.6 ? "moderate confidence correction available"
      : "low confidence — review recommended";
    return { field: f.field, confidence: conf, description: desc };
  });

  return (
    <div className={`rounded-lg border px-3 py-2.5 ${severity}`}>
      <button onClick={() => setShowDetails(!showDetails)} className="flex items-center gap-2 w-full text-left text-xs font-medium">
        <AlertTriangle size={14} />
        <span>Metadata mismatch detected</span>
        <ChevronDown size={12} className={`ml-auto transition-transform ${showDetails ? "rotate-180" : ""}`} />
      </button>

      {/* Field-level mismatch summary (always visible) */}
      {fieldMismatches.length > 0 && (
        <div className="mt-2 space-y-0.5 ml-5">
          {fieldMismatches.map((fm) => (
            <div key={fm.field} className="text-[11px] flex items-center gap-2">
              <span className="capitalize font-medium min-w-[60px]">{fm.field}</span>
              <span className="text-text-muted">— {fm.description}</span>
            </div>
          ))}
        </div>
      )}

      {/* Detailed signals (expandable) */}
      {showDetails && report.signals && Array.isArray(report.signals) && (
        <div className="mt-3 pt-2 border-t border-white/10 space-y-1">
          <span className="text-[10px] uppercase tracking-wider text-text-muted">Detection Signals</span>
          {report.signals.map((s, i) => (
            <div key={s.name ?? i} className="flex items-center gap-2 text-[11px]">
              <span className="font-mono w-10 text-right">{(s.score * 100).toFixed(0)}%</span>
              <span className="capitalize">{(s.name ?? "").replace(/_/g, " ")}</span>
              {s.details && <span className="text-text-muted">— {s.details}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   Fingerprint Results
   ═══════════════════════════════════════════════════════════ */

function FingerprintResults({ result }: { result: FingerprintResult | null }) {
  if (!result) return null;

  if (result.error) {
    return (
      <div className="text-xs text-text-muted bg-surface-light rounded-lg px-3 py-2.5">
        <Fingerprint size={12} className="inline mr-1" />
        Fingerprint: {result.error}
      </div>
    );
  }

  if (!result.matches || result.matches.length === 0) {
    return (
      <div className="text-xs text-text-muted bg-surface-light rounded-lg px-3 py-2.5">
        <Fingerprint size={12} className="inline mr-1" />
        No fingerprint matches found
      </div>
    );
  }

  return (
    <div className="text-xs bg-cyan-500/5 border border-cyan-500/15 rounded-lg px-3 py-2.5 space-y-1">
      <div className="flex items-center gap-1.5 text-cyan-400 font-medium mb-1">
        <Fingerprint size={12} />
        Audio Fingerprint Matches ({result.matches.length})
      </div>
      {result.matches.slice(0, 5).map((m, i) => (
        <div key={i} className="flex items-center gap-2 text-text-primary">
          <span className="font-mono text-[10px] w-8 text-right text-cyan-400">{(m.confidence * 100).toFixed(0)}%</span>
          <span className="break-words">{m.artist} — {m.title}</span>
          {m.album && <span className="text-text-muted">({m.album})</span>}
          {m.year && <span className="text-text-muted">{m.year}</span>}
        </div>
      ))}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   Correction Table — 3-column diff layout
   ═══════════════════════════════════════════════════════════ */

function CorrectionTable({
  fields,
  aiResultId,
  renameFiles,
  onRenameToggle,
  onApply,
  onUndo,
  isPending,
  isUndoPending,
  artworkUpdates,
  model,
}: {
  fields: AIFieldComparison[];
  aiResultId: number;
  renameFiles: boolean;
  onRenameToggle: (v: boolean) => void;
  onApply: (aiResultId: number, fields: string[]) => void;
  onUndo: (aiResultId: number) => void;
  isPending: boolean;
  isUndoPending: boolean;
  overallConfidence?: number;
  artworkUpdates?: import("../types").ArtworkUpdate[];
  model?: string | null;
}) {
  const [accepted, setAccepted] = useState<Record<string, boolean>>({});

  const toggle = (field: string) => {
    setAccepted((prev) => ({ ...prev, [field]: !prev[field] }));
  };

  const artworks = artworkUpdates ?? [];
  const changeableArtworks = artworks.filter((a) => !a.unchanged);

  const selectAll = () => {
    const next: Record<string, boolean> = {};
    fields.forEach((f) => {
      if (f.changed && f.ai_value != null && !f.locked && !f.accepted) next[f.field] = true;
    });
    changeableArtworks.forEach((a) => { next[a.asset_type] = true; });
    setAccepted(next);
  };

  const deselectAll = () => setAccepted({});

  const anyAccepted = Object.values(accepted).some(Boolean);
  const acceptedCount = Object.values(accepted).filter(Boolean).length;
  const changedFields = fields.filter((f) => f.changed && f.ai_value != null && !f.locked);
  const highConfFields = changedFields.filter((f) => f.ai_confidence >= 0.85);
  const totalChanges = changedFields.length + changeableArtworks.length;

  const applyFields = (fieldNames: string[]) => {
    onApply(aiResultId, fieldNames);
  };

  const applyAccepted = () => {
    const fieldNames = fields.filter((f) => accepted[f.field]).map((f) => f.field);
    const artFieldNames = artworks.filter((a) => accepted[a.asset_type]).map((a) => a.asset_type);
    applyFields([...fieldNames, ...artFieldNames]);
  };

  const applyAll = () => applyFields([...changedFields.map((f) => f.field), ...changeableArtworks.map((a) => a.asset_type)]);
  const applyHighConfidence = () => applyFields(highConfFields.map((f) => f.field));

  // Separate changed and verified fields for visual grouping
  const changedRows = fields.filter((f) => f.changed && f.ai_value != null);
  const verifiedRows = fields.filter((f) => !f.changed && f.ai_value != null);
  const { suggestionLabel } = scanLabel(model);

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
          Suggested Corrections
        </h4>
        <div className="flex items-center gap-3">
          <Tooltip content="Rename the video file and folder to match the corrected metadata">
          <label className="flex items-center gap-1.5 text-xs text-text-muted cursor-pointer select-none">
            <input
              type="checkbox"
              checked={renameFiles}
              onChange={(e) => onRenameToggle(e.target.checked)}
              className="accent-accent"
            />
            <FolderSync size={11} /> Rename files
          </label>
          </Tooltip>
          {totalChanges > 1 && (
            <Tooltip content={anyAccepted ? "Deselect all corrections" : "Select all corrections for applying"}>
            <button
              onClick={anyAccepted ? deselectAll : selectAll}
              className="text-[11px] text-accent hover:underline"
            >
              {anyAccepted ? "Deselect all" : "Select all"}
            </button>
            </Tooltip>
          )}
        </div>
      </div>

      {/* Diff table */}
      <div className="border border-white/5 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[11px] text-text-muted bg-surface-light/50">
              <th className="px-3 py-2 w-24">Field</th>
              <th className="px-3 py-2">Current Value</th>
              <th className="px-3 py-2">{suggestionLabel}</th>
              <th className="px-3 py-2 w-16 text-center">Conf</th>
              <th className="px-3 py-2 w-14 text-center">Apply</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {/* Changed fields first */}
            {changedRows.map((f) => (
              <DiffRow
                key={f.field}
                field={f}
                isAccepted={!!accepted[f.field]}
                onToggle={() => toggle(f.field)}
              />
            ))}
            {/* Artwork rows */}
            {artworks.map((a) => (
              <ArtworkDiffRow key={a.asset_type} artwork={a} isAccepted={!!accepted[a.asset_type]} onToggle={() => toggle(a.asset_type)} />
            ))}
            {/* Verified (unchanged) fields */}
            {verifiedRows.map((f) => (
              <DiffRow
                key={f.field}
                field={f}
                isAccepted={false}
                onToggle={() => {}}
              />
            ))}
          </tbody>
        </table>
      </div>

      {/* Action buttons */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {anyAccepted && (
          <Tooltip content="Apply only the corrections you've selected above">
          <button onClick={applyAccepted} disabled={isPending} className="btn-primary btn-sm">
            {isPending ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            Apply Selected ({acceptedCount})
          </button>
          </Tooltip>
        )}
        {totalChanges > 0 && !anyAccepted && (
          <Tooltip content="Apply all suggested corrections at once">
          <button onClick={applyAll} disabled={isPending} className="btn-secondary btn-sm">
            <CheckCheck size={14} />
            Apply All Changes ({totalChanges})
          </button>
          </Tooltip>
        )}
        {highConfFields.length > 0 && highConfFields.length < changedFields.length && !anyAccepted && (
          <Tooltip content="Apply only corrections with 85%+ confidence">
          <button onClick={applyHighConfidence} disabled={isPending} className="btn-secondary btn-sm">
            <Zap size={14} />
            Apply High Confidence ({highConfFields.length})
          </button>
          </Tooltip>
        )}
        <div className="flex-1" />
        <Tooltip content="Undo: Restore metadata from before AI was applied">
        <button
          onClick={() => onUndo(aiResultId)}
          disabled={isUndoPending}
          className="btn-secondary btn-sm text-red-400"
        >
          {isUndoPending ? <Loader2 size={14} className="animate-spin" /> : <Undo2 size={14} />}
          Undo Last Enrichment
        </button>
        </Tooltip>
      </div>
    </div>
  );
}

/* ─── Single diff row ─── */

function DiffRow({
  field,
  isAccepted,
  onToggle,
}: {
  field: AIFieldComparison;
  isAccepted: boolean;
  onToggle: () => void;
}) {
  const isDiff = field.changed;
  const isLocked = field.locked;
  const isApplied = field.accepted;
  const scrapedStr = formatValue(field.scraped_value);
  const aiStr = formatValue(field.ai_value);

  const rowStyle = isApplied
    ? "bg-green-500/5 border-l-2 border-l-green-500/60"
    : isAccepted
      ? "bg-accent/8 border-l-2 border-l-accent"
      : isLocked ? "opacity-40" : "";

  return (
    <tr className={`${rowStyle} group transition-colors`}>
      {/* Field name */}
      <td className="px-3 py-2 text-xs font-medium text-text-primary capitalize align-top">
        <span className="flex items-center gap-1">
          {field.field.replace(/_/g, " ")}
          {isLocked && <Lock size={10} className="text-yellow-500" />}
        </span>
      </td>

      {/* Current value - full text, wrapping enabled */}
      <td className="px-3 py-2 text-xs text-text-muted align-top">
        <div className="break-words whitespace-pre-wrap max-w-[300px]" title={scrapedStr}>
          {isApplied ? <span className="line-through opacity-60">{scrapedStr}</span> : scrapedStr}
        </div>
      </td>

      {/* AI suggestion - full text, wrapping enabled */}
      <td className={`px-3 py-2 text-xs align-top ${isDiff ? (isApplied ? "text-green-400 font-medium" : "text-accent font-medium") : "text-text-muted"}`}>
        <div className="break-words whitespace-pre-wrap max-w-[300px]" title={aiStr}>
          {isApplied ? (
            <span className="flex items-center gap-1">
              <CheckCheck size={11} /> {aiStr}
            </span>
          ) : isDiff ? aiStr : (
            <span className="flex items-center gap-1 text-green-400/70">
              <Check size={11} /> Verified
            </span>
          )}
        </div>
      </td>

      {/* Confidence */}
      <td className="px-3 py-2 text-center align-top">
        {field.ai_confidence != null && field.ai_confidence > 0 ? (
          <ConfidenceBadge value={field.ai_confidence} />
        ) : isDiff ? null : (
          <span className="text-green-400 text-xs">✓</span>
        )}
      </td>

      {/* Apply checkbox */}
      <td className="px-3 py-2 text-center align-top">
        {isDiff && field.ai_value != null && !isLocked ? (
          isApplied ? (
            <Tooltip content="Applied">
            <span className="text-green-400">
              <CheckCheck size={14} />
            </span>
            </Tooltip>
          ) : (
            <Tooltip content={isAccepted ? "Deselect" : "Select for apply"}>
            <button
              onClick={onToggle}
              className={`p-1 rounded transition-colors ${
                isAccepted
                  ? "bg-green-500/20 text-green-400"
                  : "hover:bg-surface-light text-text-muted"
              }`}
            >
              <Check size={14} />
            </button>
            </Tooltip>
          )
        ) : null}
      </td>
    </tr>
  );
}

/* ─── Artwork diff row ─── */

function ArtworkDiffRow({
  artwork,
  isAccepted,
  onToggle,
}: {
  artwork: import("../types").ArtworkUpdate;
  isAccepted: boolean;
  onToggle: () => void;
}) {
  const [enlarged, setEnlarged] = useState<string | null>(null);
  const label =
    artwork.asset_type === "poster" ? "Poster" :
    artwork.asset_type === "thumb" ? "Thumbnail" :
    artwork.asset_type === "artist_thumb" ? "Artist Art" :
    artwork.asset_type === "album_thumb" ? "Album Art" :
    artwork.asset_type;

  const currentSrc = artwork.current_asset_id
    ? `/api/playback/asset/${artwork.current_asset_id}?v=${artwork.current_asset_id}`
    : null;
  const proposedSrc = artwork.proposed_asset_id
    ? `/api/playback/asset/${artwork.proposed_asset_id}?v=${artwork.proposed_asset_id}`
    : null;
  const isUnchanged = artwork.unchanged ?? false;
  const isApplied = isUnchanged && !proposedSrc;

  const rowStyle = isApplied
    ? "bg-green-500/5 border-l-2 border-l-green-500/60"
    : isAccepted
      ? "bg-accent/8 border-l-2 border-l-accent"
      : isUnchanged ? "opacity-60" : "";

  return (
    <>
      <tr className={`group transition-colors ${rowStyle}`}>
        <td className="px-3 py-2 text-xs font-medium text-text-primary capitalize align-middle">
          {label}
        </td>
        <td className="px-3 py-2 align-middle">
          {currentSrc ? (
            <img
              src={currentSrc}
              alt="Current"
              className="w-16 h-16 rounded border border-surface-border object-cover cursor-pointer hover:ring-2 hover:ring-accent/50 transition-all"
              onClick={() => setEnlarged(currentSrc)}
            />
          ) : (
            <span className="text-xs text-text-muted">—</span>
          )}
        </td>
        <td className="px-3 py-2 align-middle">
          {proposedSrc ? (
            <img
              src={proposedSrc}
              alt="Proposed"
              className="w-16 h-16 rounded border border-accent/40 object-cover cursor-pointer hover:ring-2 hover:ring-accent/50 transition-all"
              onClick={() => setEnlarged(proposedSrc)}
            />
          ) : isApplied ? (
            <span className="flex items-center gap-1 text-xs text-green-400">
              <CheckCheck size={11} /> Applied
            </span>
          ) : isUnchanged ? (
            <span className="text-xs text-text-muted italic">Unchanged</span>
          ) : (
            <span className="text-xs text-text-muted">—</span>
          )}
        </td>
        <td className="px-3 py-2 text-center align-middle" />
        <td className="px-3 py-2 text-center align-middle">
          {isApplied ? (
            <Tooltip content="Applied">
            <span className="text-green-400">
              <CheckCheck size={14} />
            </span>
            </Tooltip>
          ) : !isUnchanged ? (
            <Tooltip content={isAccepted ? "Deselect" : "Select for apply"}>
            <button
              onClick={onToggle}
              className={`p-1 rounded transition-colors ${
                isAccepted
                  ? "bg-green-500/20 text-green-400"
                  : "hover:bg-surface-light text-text-muted"
              }`}
            >
              <Check size={14} />
            </button>
            </Tooltip>
          ) : null}
        </td>
      </tr>
      {enlarged &&
        ReactDOM.createPortal(
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm cursor-pointer"
            onClick={() => setEnlarged(null)}
          >
            <img
              src={enlarged}
              alt="Enlarged artwork"
              className="max-w-[90vw] max-h-[90vh] rounded-lg shadow-2xl object-contain"
              onClick={(e) => e.stopPropagation()}
            />
            <button
              onClick={() => setEnlarged(null)}
              className="absolute top-4 right-4 p-2 rounded-full bg-black/50 text-white hover:bg-black/70 transition-colors"
            >
              <X size={20} />
            </button>
          </div>,
          document.body
        )}
    </>
  );
}

/* ═══════════════════════════════════════════════════════════
   Helpers
   ═══════════════════════════════════════════════════════════ */

function formatValue(val: unknown): string {
  if (val == null) return "—";
  if (Array.isArray(val)) {
    if (val.length === 0) return "—";
    // Handle actors array [{name, role}]
    if (typeof val[0] === "object" && val[0] !== null && "name" in val[0]) {
      return val.map((v: { name: string; role?: string }) => v.role ? `${v.name} (${v.role})` : v.name).join(", ");
    }
    return val.join(", ");
  }
  return String(val);
}

function ConfidenceBadge({ value }: { value: number }) {
  const pct = (value * 100).toFixed(0);
  const color =
    value >= 0.85 ? "text-green-400" :
    value >= 0.6 ? "text-yellow-400" :
    "text-red-400";
  return <span className={`text-xs font-mono ${color}`}>{pct}%</span>;
}
