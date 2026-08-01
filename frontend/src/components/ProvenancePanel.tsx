import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, UploadCloud, Eye, Fingerprint, Loader2, BadgeCheck, Bot, PencilLine, HelpCircle, RotateCcw, X } from "lucide-react";
import type { VideoItemDetail } from "@/types";
import { libraryApi, tmvdbApi } from "@/lib/api";
import { useToast } from "@/components/Toast";

interface ProvenancePanelProps {
  video: VideoItemDetail;
}

const CORE_FIELDS = ["artist", "title", "album", "year", "plot"] as const;

/** Derive a per-field trust level from the video's provenance maps. */
function fieldTrust(video: VideoItemDetail, field: string):
  "human_edited" | "human_verified" | "automated" | "unknown" {
  const source = video.field_provenance?.[field];
  const editedBy = video.field_provenance_users?.[field];
  const verified = video.field_verifications?.[field];
  if (editedBy || source === "manual") return "human_edited";
  if (verified) return "human_verified";
  if (source) return "automated";
  return "unknown";
}

const TRUST_META: Record<string, { label: string; cls: string; Icon: typeof BadgeCheck }> = {
  human_edited: { label: "Edited", cls: "text-emerald-400", Icon: PencilLine },
  human_verified: { label: "Verified", cls: "text-sky-400", Icon: BadgeCheck },
  automated: { label: "Auto", cls: "text-text-muted", Icon: Bot },
  unknown: { label: "Unknown", cls: "text-text-muted/60", Icon: HelpCircle },
};

export function ProvenancePanel({ video }: ProvenancePanelProps) {
  const { toast } = useToast();
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [actingId, setActingId] = useState<string | null>(null);

  const { data: contributions } = useQuery({
    queryKey: ["tmvdb-contributions", video.id],
    queryFn: () => tmvdbApi.contributions(video.id, 5),
    refetchInterval: 2500,
  });

  const { data: preview, isFetching: previewLoading } = useQuery({
    queryKey: ["tmvdb-preview", video.id],
    queryFn: () => tmvdbApi.preview(video.id),
    enabled: showPreview,
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ["video", video.id] });

  const confirmAll = async () => {
    setConfirming(true);
    try {
      const res = await libraryApi.confirmFields(video.id);
      toast({
        type: "success",
        title: res.verified.length
          ? `Verified ${res.verified.length} field(s)`
          : "Nothing to verify (all human-edited)",
      });
      refresh();
    } catch {
      toast({ type: "error", title: "Verification failed" });
    } finally {
      setConfirming(false);
    }
  };

  const push = async () => {
    setPushing(true);
    try {
      const res = await tmvdbApi.push(video.id);
      const t = res.status === "pending" ? "success" : res.status === "ineligible" ? "info" : "error";
      toast({ type: t as "success" | "info" | "error", title: res.message });
      qc.invalidateQueries({ queryKey: ["tmvdb-contributions", video.id] });
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast({ type: "error", title: msg || "Push failed — is TMVDB enabled in Settings?" });
    } finally {
      setPushing(false);
    }
  };

  const updateContribution = async (id: string, action: "cancel" | "retry") => {
    setActingId(id);
    try {
      await tmvdbApi[action](id);
      toast({
        type: "success",
        title: action === "cancel" ? "Contribution cancelled" : "Contribution queued for retry",
      });
      qc.invalidateQueries({ queryKey: ["tmvdb-contributions", video.id] });
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast({ type: "error", title: msg || `Could not ${action} contribution` });
    } finally {
      setActingId(null);
    }
  };

  const identity: [string, string | null | undefined][] = [
    ["Track ID", video.playarr_track_id],
    ["Video ID", video.playarr_video_id],
    ["AcoustID", video.acoustid_id],
    ["Audio FP", video.audio_fingerprint ? "present" : null],
    ["pHash", video.video_phash],
    ["Checksum", video.file_checksum ? video.file_checksum.slice(0, 12) + "…" : null],
  ];

  return (
    <div className="card space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide flex items-center gap-1.5">
          <ShieldCheck size={14} /> Provenance & Trust
        </h3>
        <div className="flex items-center gap-2">
          <button
            className="btn-secondary text-xs flex items-center gap-1"
            onClick={confirmAll}
            disabled={confirming}
            title="Mark auto-fetched values as human-verified"
          >
            {confirming ? <Loader2 size={12} className="animate-spin" /> : <BadgeCheck size={12} />}
            Confirm
          </button>
          <button
            className="btn-primary text-xs flex items-center gap-1"
            onClick={push}
            disabled={pushing}
            title="Contribute this metadata to The Music Video DB"
          >
            {pushing ? <Loader2 size={12} className="animate-spin" /> : <UploadCloud size={12} />}
            Push to TMVDB
          </button>
        </div>
      </div>

      {/* Per-field trust */}
      <dl className="space-y-1.5 text-sm">
        {CORE_FIELDS.map((f) => {
          const trust = fieldTrust(video, f);
          const meta = TRUST_META[trust];
          const source = video.field_provenance?.[f];
          return (
            <div key={f} className="flex justify-between items-center">
              <dt className="text-text-muted capitalize">{f}</dt>
              <dd className={`flex items-center gap-1.5 text-xs ${meta.cls}`} title={source ? `source: ${source}` : undefined}>
                <meta.Icon size={12} />
                {meta.label}
                {source && trust === "automated" && (
                  <span className="text-text-muted/70">· {source}</span>
                )}
              </dd>
            </div>
          );
        })}
      </dl>

      {/* Identity keys */}
      <div>
        <h4 className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
          <Fingerprint size={12} /> Identity
        </h4>
        <dl className="space-y-1 text-xs">
          {identity.map(([label, value]) =>
            value ? (
              <div key={label} className="flex justify-between items-center">
                <dt className="text-text-muted">{label}</dt>
                <dd className="text-text-primary font-mono">{value}</dd>
              </div>
            ) : null
          )}
        </dl>
      </div>

      {/* Contribution log */}
      {contributions && contributions.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-1.5">
            Recent contributions
          </h4>
          <ul className="space-y-1 text-xs">
            {contributions.map((c) => (
              <li key={c.id} className="flex justify-between items-center gap-2 rounded bg-bg-base/30 px-2 py-1.5">
                <span className="min-w-0 text-text-muted">
                  <span className="block">{c.created_at ? new Date(c.created_at).toLocaleString() : "—"}</span>
                  {c.operation_id && (
                    <span className="block truncate font-mono text-[9px]" title={c.operation_id}>{c.operation_id}</span>
                  )}
                  {c.error?.message && <span className="block text-red-300" title={c.error.message}>{c.error.message}</span>}
                </span>
                <span className="flex shrink-0 items-center gap-1.5">
                  <span className={
                    c.status === "submitted" ? "text-emerald-400"
                    : ["pending", "running", "retry"].includes(c.status) ? "text-amber-300"
                    : c.status === "cancelled" ? "text-text-muted"
                    : "text-red-400"
                  }>
                    {c.status}
                  </span>
                  {typeof c.id === "string" && ["pending", "retry"].includes(c.status) && (
                    <button
                      className="icon-btn"
                      disabled={actingId === c.id}
                      onClick={() => updateContribution(c.id as string, "cancel")}
                      title="Cancel pending contribution"
                    >
                      <X size={11} />
                    </button>
                  )}
                  {typeof c.id === "string" && c.status === "failed" && (
                    <button
                      className="icon-btn"
                      disabled={actingId === c.id}
                      onClick={() => updateContribution(c.id as string, "retry")}
                      title="Retry failed contribution"
                    >
                      <RotateCcw size={11} />
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Envelope preview */}
      <div>
        <button
          className="text-xs text-text-muted hover:text-text-secondary flex items-center gap-1"
          onClick={() => setShowPreview((v) => !v)}
        >
          <Eye size={12} /> {showPreview ? "Hide" : "Preview"} contribution envelope
        </button>
        {showPreview && (
          previewLoading ? (
            <div className="mt-2 text-xs text-text-muted flex items-center gap-1">
              <Loader2 size={12} className="animate-spin" /> Building…
            </div>
          ) : preview ? (
            <div className="mt-2 space-y-2 rounded bg-bg-base/50 p-2 text-[10px] text-text-muted">
              <div className="flex flex-wrap gap-1">
                {Object.entries(preview.eligibility).map(([field, state]) => (
                  <span
                    key={field}
                    className={`rounded px-1.5 py-0.5 ${state.eligible ? "bg-emerald-500/15 text-emerald-300" : "bg-surface text-text-muted"}`}
                    title={state.reason}
                  >
                    {field}: {state.eligible ? "eligible" : state.reason.replaceAll("_", " ")}
                  </span>
                ))}
              </div>
              {!preview.can_submit && (
                <p className="text-amber-300">Nothing will be submitted until at least one field is edited, verified, or locked.</p>
              )}
              <details>
                <summary className="cursor-pointer">Exact gated payload</summary>
                <pre className="mt-1 max-h-64 overflow-auto leading-relaxed">{JSON.stringify(preview.submission, null, 2)}</pre>
              </details>
            </div>
          ) : null
        )}
      </div>
    </div>
  );
}
