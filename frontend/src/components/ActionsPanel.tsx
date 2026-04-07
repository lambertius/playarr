import { useState, useMemo, useRef, useEffect } from "react";
import ReactDOM from "react-dom";
import {
  RefreshCw, Volume2, Download, Undo2, Trash2,
  Wrench, Wand2, FileText, Sparkles, Fingerprint,
  FileCheck, Globe, Loader2, Bot, Check, ChevronDown,
  AlertTriangle, Lock, CheckCheck, Zap, FolderSync,
  ShieldCheck, Music, Link2, Film, Ban,
  FolderOpen, X, Hash,
} from "lucide-react";
import type {
  QualitySignature, AIFieldComparison,
  FingerprintMatch, AIIdentityVerification, AIMismatchInfo,
  ProcessingState, SourceUpdate,
} from "@/types";
import {
  useRescan, useNormalize, useUndoRescan, useDeleteVideo, useScrapeMetadata,
  useAIComparison, useAIResults,
  useAIEnrich, useAIApplyFields,
  useAIUndo, useAIFingerprint, useAISettings, useRedownload,
  useRenameToExpected, useSetExcludeFromScan, qk,
  useSettings, useUpdateVideo,
} from "@/hooks/queries";
import { useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ConfirmDialog";
import { libraryApi, jobsApi } from "@/lib/api";
import { addToVideoEditorQueue } from "@/pages/VideoEditorPage";
import { SourceBadge } from "@/components/Badges";
import { Tooltip } from "@/components/Tooltip";

/* ═══════════════════════════════════════════════════════════
   Types
   ═══════════════════════════════════════════════════════════ */

interface ActionsPanelProps {
  videoId: number;
  hasUndoable: boolean;
  quality?: QualitySignature | null;
  onDeleted: () => void;
  /** Video data for pre-computing filename match */
  filePath?: string | null;
  artist?: string | null;
  title?: string | null;
  resolutionLabel?: string | null;
  processingState?: ProcessingState | null;
  versionType?: string | null;
  alternateVersionLabel?: string | null;
  isLocked?: boolean;
  hasArchive?: boolean;
  excludeFromEditorScan?: boolean;
  className?: string;
}

interface ActionItem {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  pending?: boolean;
  variant?: "default" | "danger" | "success";
  tooltip?: string;
}

/* ═══════════════════════════════════════════════════════════
   Popup Components
   ═══════════════════════════════════════════════════════════ */

function PopupOverlay({ children, onClose, wide }: { children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return ReactDOM.createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div
        className={`relative z-10 w-full ${wide ? "max-w-3xl max-h-[85vh] overflow-y-auto" : "max-w-lg max-h-[85vh] overflow-y-auto"} rounded-xl border border-surface-border bg-surface-light p-6 shadow-[0_0_40px_rgba(225,29,46,0.08)] animate-slide-up`}
        style={{ background: "linear-gradient(135deg, rgba(21,25,35,0.97) 0%, rgba(28,34,48,0.92) 100%)" }}
        role="dialog"
        aria-modal="true"
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

/* ─── Normalize Audio Popup ─── */
function NormalizePopup({
  currentLufs,
  onConfirm,
  onClose,
  isPending,
}: {
  currentLufs?: number | null;
  onConfirm: (targetLufs: number) => void;
  onClose: () => void;
  isPending: boolean;
}) {
  const [targetLufs, setTargetLufs] = useState(-14);

  return (
    <PopupOverlay onClose={onClose}>
      <h2 className="text-lg font-semibold text-text-primary mb-1">Normalise Audio</h2>
      <p className="text-sm text-text-secondary mb-4">
        Adjust audio loudness to a target LUFS level. This is a local operation — no AI tokens used.
      </p>

      {currentLufs != null && (
        <div className="flex items-center gap-2 text-sm mb-3 bg-surface-light/50 rounded-lg px-3 py-2">
          <Volume2 size={14} className="text-text-muted" />
          <span className="text-text-muted">Current loudness:</span>
          <span className="text-text-primary font-mono">{currentLufs.toFixed(1)} LUFS</span>
        </div>
      )}

      <div className="space-y-2 mb-5">
        <label className="text-sm text-text-muted">Target LUFS</label>
        <input
          type="number"
          value={targetLufs}
          onChange={(e) => setTargetLufs(Number(e.target.value))}
          step={0.5}
          min={-30}
          max={0}
          className="input-field w-full"
        />
        <div className="flex gap-2 text-[11px] text-text-muted">
          <button onClick={() => setTargetLufs(-14)} className={`px-2 py-0.5 rounded ${targetLufs === -14 ? "bg-accent/20 text-accent" : "bg-surface-light hover:bg-surface-lighter"}`}>
            -14 (Spotify)
          </button>
          <button onClick={() => setTargetLufs(-16)} className={`px-2 py-0.5 rounded ${targetLufs === -16 ? "bg-accent/20 text-accent" : "bg-surface-light hover:bg-surface-lighter"}`}>
            -16 (YouTube)
          </button>
          <button onClick={() => setTargetLufs(-23)} className={`px-2 py-0.5 rounded ${targetLufs === -23 ? "bg-accent/20 text-accent" : "bg-surface-light hover:bg-surface-lighter"}`}>
            -23 (EBU R128)
          </button>
        </div>
      </div>

      <div className="flex justify-end gap-3">
        <button onClick={onClose} className="btn-secondary btn-sm">Cancel</button>
        <button
          onClick={() => onConfirm(targetLufs)}
          disabled={isPending}
          className="btn-primary btn-sm"
        >
          {isPending ? <Loader2 size={14} className="animate-spin" /> : <Volume2 size={14} />}
          Normalize
        </button>
      </div>
    </PopupOverlay>
  );
}

/* ─── Redownload Confirmation Popup ─── */
function RedownloadPopup({
  videoId,
  onConfirm,
  onClose,
}: {
  videoId: number;
  onConfirm: (formatSpec?: string) => void;
  onClose: () => void;
}) {
  const [resolutions, setResolutions] = useState<{ height: number; label: string; format_id: string }[]>([]);
  const [selectedHeight, setSelectedHeight] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    jobsApi.formats(videoId)
      .then((data) => {
        if (cancelled) return;
        setResolutions(data.resolutions);
        if (data.resolutions.length > 0) {
          setSelectedHeight(data.resolutions[0].height);
        }
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.response?.data?.detail || "Failed to fetch formats");
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [videoId]);

  const buildFormatSpec = () => {
    if (!selectedHeight) return undefined;
    return `bestvideo[height<=${selectedHeight}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=${selectedHeight}]+bestaudio/best[height<=${selectedHeight}]`;
  };

  return (
    <PopupOverlay onClose={onClose}>
      <div className="flex items-start gap-3 mb-4">
        <AlertTriangle size={22} className="text-orange-400 flex-shrink-0 mt-0.5" />
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Redownload Video</h2>
          <p className="text-sm text-text-secondary mt-1">
            This will re-download the video from its original source URL, replacing the current file. The old file will be archived.
          </p>
        </div>
      </div>

      {/* Resolution Picker */}
      <div className="mb-4">
        <label className="text-xs font-medium text-text-muted uppercase tracking-wider mb-2 block">
          Resolution
        </label>
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-text-secondary">
            <Loader2 size={14} className="animate-spin" /> Fetching available resolutions…
          </div>
        ) : error ? (
          <div className="text-sm text-red-400">{error}</div>
        ) : resolutions.length === 0 ? (
          <div className="text-sm text-text-secondary">No video formats found</div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {resolutions.map((r) => (
              <button
                key={r.height}
                onClick={() => setSelectedHeight(r.height)}
                className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                  selectedHeight === r.height
                    ? "bg-accent/20 text-accent ring-1 ring-accent/40"
                    : "bg-surface-light text-text-secondary hover:bg-surface-lighter"
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex justify-end gap-3">
        <button onClick={onClose} className="btn-secondary btn-sm">Cancel</button>
        <button
          onClick={() => onConfirm(buildFormatSpec())}
          disabled={loading || resolutions.length === 0}
          className="btn-primary btn-sm"
        >
          <Download size={14} /> Redownload{selectedHeight ? ` (${selectedHeight}p)` : ""}
        </button>
      </div>
    </PopupOverlay>
  );
}

/* ─── Check Filename Popup ─── */
function CheckFilenamePopup({
  currentFilename,
  expectedFilename,
  isMatch,
  onRename,
  onClose,
  isPending,
}: {
  currentFilename: string;
  expectedFilename: string;
  isMatch: boolean;
  onRename: () => void;
  onClose: () => void;
  isPending: boolean;
}) {
  return (
    <PopupOverlay onClose={onClose}>
      <h2 className="text-lg font-semibold text-text-primary mb-1">Check Filename</h2>
      <p className="text-sm text-text-secondary mb-4">
        Compare the current filename against the expected naming pattern based on metadata. This is a local operation.
      </p>

      <div className="space-y-3 mb-5">
        <div>
          <span className="text-xs text-text-muted uppercase tracking-wider">Current</span>
          <div className="mt-1 text-sm text-text-primary font-mono bg-surface-light/50 rounded-lg px-3 py-2 break-all">
            {currentFilename}
          </div>
        </div>
        <div>
          <span className="text-xs text-text-muted uppercase tracking-wider">Expected</span>
          <div className={`mt-1 text-sm font-mono rounded-lg px-3 py-2 break-all ${isMatch ? "text-green-400 bg-green-500/10" : "text-accent bg-accent/10"}`}>
            {expectedFilename}
          </div>
        </div>

        {isMatch ? (
          <div className="flex items-center gap-2 text-sm text-green-400">
            <Check size={14} /> Filename matches expected pattern
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-orange-400">
            <AlertTriangle size={14} /> Filename does not match expected pattern
          </div>
        )}
      </div>

      <div className="flex justify-end gap-3">
        <button onClick={onClose} className="btn-secondary btn-sm">Close</button>
        {!isMatch && (
          <button onClick={onRename} disabled={isPending} className="btn-primary btn-sm">
            {isPending ? <Loader2 size={14} className="animate-spin" /> : <FolderSync size={14} />}
            Rename to Expected
          </button>
        )}
      </div>
    </PopupOverlay>
  );
}

/* ─── Scrape Metadata Popup ─── */
function ScrapeMetadataPopup({
  onConfirm,
  onClose,
  isPending,
  providerConfigured,
}: {
  onConfirm: (opts: { aiAutoAnalyse?: boolean; aiOnly?: boolean; scrapeWikipedia?: boolean; wikipediaUrl?: string; scrapeMusicbrainz?: boolean; musicbrainzUrl?: string; scrapeTmvdb?: boolean; isCover?: boolean; isLive?: boolean; isAlternate?: boolean; isUncensored?: boolean; alternateVersionLabel?: string; findSourceVideo?: boolean; normalizeAudio?: boolean }) => void;
  onClose: () => void;
  isPending: boolean;
  providerConfigured: boolean;
}) {
  const [aiAutoAnalyse, setAiAutoAnalyse] = useState(false);
  const [aiOnly, setAiOnly] = useState(false);
  const [scrapeWikipedia, setScrapeWikipedia] = useState(false);
  const [wikiUrl, setWikiUrl] = useState("");
  const [scrapeMusicbrainz, setScrapeMusicbrainz] = useState(false);
  const [mbUrl, setMbUrl] = useState("");
  const [scrapeTmvdb, setScrapeTmvdb] = useState(false);
  const [isCover, setIsCover] = useState(false);
  const [isLive, setIsLive] = useState(false);
  const [isAlternate, setIsAlternate] = useState(false);
  const [isUncensored, setIsUncensored] = useState(false);
  const [alternateLabel, setAlternateLabel] = useState("");
  const [findSourceVideo, setFindSourceVideo] = useState(false);
  const [normalizeAudio, setNormalizeAudio] = useState(false);

  const { data: settings } = useSettings();

  useEffect(() => {
    if (!settings) return;
    const getBool = (key: string, fallback: boolean) => {
      const s = settings.find((s: { key: string; value: string }) => s.key === key);
      return s ? s.value === "true" : fallback;
    };
    setAiAutoAnalyse(getBool("import_ai_auto", false));
    setAiOnly(getBool("import_ai_only", false));
    setScrapeWikipedia(getBool("import_scrape_wikipedia", true));
    setScrapeMusicbrainz(getBool("import_scrape_musicbrainz", true));
    setScrapeTmvdb(getBool("import_scrape_tmvdb", false));
    setFindSourceVideo(getBool("import_find_source_video", false));
    setNormalizeAudio(getBool("auto_normalize_on_import", true));
  }, [settings]);

  const hasSelection = aiAutoAnalyse || aiOnly || scrapeWikipedia || scrapeMusicbrainz || scrapeTmvdb || findSourceVideo;

  const Toggle = ({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) => (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors duration-200 ease-in-out ${checked ? "bg-accent" : "bg-surface-lighter"} ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
    >
      <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-200 ease-in-out mt-0.5 ${checked ? "translate-x-4 ml-0.5" : "translate-x-0.5"}`} />
    </button>
  );

  return (
    <PopupOverlay onClose={onClose}>
      <h2 className="text-lg font-semibold text-text-primary mb-1">Scrape Metadata</h2>
      <p className="text-sm text-text-secondary mb-4">
        Choose a metadata source to query. Results will be shown for review before applying.
      </p>

      <div className="space-y-3 mb-5">
        {/* ── AI Auto Analyse ── */}
        <div className={`rounded-lg border px-3 py-2.5 transition-colors ${aiAutoAnalyse ? "border-accent/40 bg-accent/5" : "border-surface-border bg-surface-light/30"}`}>
          <label className="flex items-center justify-between gap-3 text-sm text-text-secondary cursor-pointer select-none">
            <div className="flex items-center gap-2 flex-1">
              <Sparkles size={14} className={aiAutoAnalyse ? "text-accent" : "text-text-muted"} />
              <div>
                <span className="font-medium text-text-primary">AI Auto Analyse</span>
                <p className="text-[11px] text-text-muted mt-0.5">
                  Full import-style pipeline — AI identifies the track, then scrapes MusicBrainz, Wikipedia, and IMDB. Uses AI tokens.
                </p>
              </div>
            </div>
            <Toggle
              checked={aiAutoAnalyse}
              disabled={!providerConfigured}
              onChange={(v) => { setAiAutoAnalyse(v); if (v) { setAiOnly(false); setScrapeWikipedia(false); setScrapeMusicbrainz(false); setScrapeTmvdb(false); } }}
            />
          </label>
        </div>

        {/* ── AI Only ── */}
        <div className={`rounded-lg border px-3 py-2.5 transition-colors ${aiOnly ? "border-accent/40 bg-accent/5" : "border-surface-border bg-surface-light/30"}`}>
          <label className="flex items-center justify-between gap-3 text-sm text-text-secondary cursor-pointer select-none">
            <div className="flex items-center gap-2 flex-1">
              <Bot size={14} className={aiOnly ? "text-accent" : "text-text-muted"} />
              <div>
                <span className="font-medium text-text-primary">AI Only</span>
                <p className="text-[11px] text-text-muted mt-0.5">
                  AI enrichment only — no external lookups. Useful for rare or independent tracks with no MusicBrainz/Wikipedia presence. Uses AI tokens.
                </p>
              </div>
            </div>
            <Toggle
              checked={aiOnly}
              disabled={!providerConfigured}
              onChange={(v) => { setAiOnly(v); if (v) { setAiAutoAnalyse(false); setScrapeWikipedia(false); setScrapeMusicbrainz(false); setScrapeTmvdb(false); } }}
            />
          </label>
        </div>

        {!providerConfigured && (
          <p className="text-[11px] text-text-muted px-1">AI options require a configured provider (Settings → AI / Summaries).</p>
        )}

        <div className="border-t border-surface-border" />

        {/* ── Scrape Wikipedia ── */}
        <div className={`rounded-lg border px-3 py-2.5 transition-colors ${scrapeWikipedia ? "border-accent/40 bg-accent/5" : "border-surface-border bg-surface-light/30"}`}>
          <label className="flex items-center justify-between gap-3 text-sm text-text-secondary cursor-pointer select-none">
            <div className="flex items-center gap-2 flex-1">
              <Globe size={14} className={scrapeWikipedia ? "text-accent" : "text-text-muted"} />
              <div>
                <span className="font-medium text-text-primary">Scrape Wikipedia</span>
                <p className="text-[11px] text-text-muted mt-0.5">
                  Search and scrape Wikipedia for album, year, genres, description, and poster. No AI tokens used.
                </p>
              </div>
            </div>
            <Toggle checked={scrapeWikipedia} onChange={(v) => { setScrapeWikipedia(v); if (v) { setAiAutoAnalyse(false); setAiOnly(false); setScrapeMusicbrainz(false); } }} />
          </label>
          {scrapeWikipedia && (
            <div className="mt-2 ml-5">
              <label className="text-[11px] text-text-muted mb-1 block">
                Wikipedia URL (optional — auto-search if blank)
              </label>
              <input
                type="url"
                value={wikiUrl}
                onChange={(e) => setWikiUrl(e.target.value)}
                placeholder="https://en.wikipedia.org/wiki/..."
                className="input-field w-full text-xs"
              />
            </div>
          )}
        </div>

        {/* ── Scrape MusicBrainz ── */}
        <div className={`rounded-lg border px-3 py-2.5 transition-colors ${scrapeMusicbrainz ? "border-accent/40 bg-accent/5" : "border-surface-border bg-surface-light/30"}`}>
          <label className="flex items-center justify-between gap-3 text-sm text-text-secondary cursor-pointer select-none">
            <div className="flex items-center gap-2 flex-1">
              <Music size={14} className={scrapeMusicbrainz ? "text-accent" : "text-text-muted"} />
              <div>
                <span className="font-medium text-text-primary">Scrape MusicBrainz</span>
                <p className="text-[11px] text-text-muted mt-0.5">
                  Search MusicBrainz for the single's recording — returns artist, album, year, and genres. No AI tokens used.
                </p>
              </div>
            </div>
            <Toggle checked={scrapeMusicbrainz} onChange={(v) => { setScrapeMusicbrainz(v); if (v) { setAiAutoAnalyse(false); setAiOnly(false); setScrapeWikipedia(false); } }} />
          </label>
          {scrapeMusicbrainz && (
            <div className="mt-2 ml-5">
              <label className="text-[11px] text-text-muted mb-1 block">
                MusicBrainz recording URL (optional — auto-search if blank)
              </label>
              <input
                type="url"
                value={mbUrl}
                onChange={(e) => setMbUrl(e.target.value)}
                placeholder="https://musicbrainz.org/recording/..."
                className="input-field w-full text-xs"
              />
            </div>
          )}
        </div>

        {/* ── Retrieve from TMVDB ── */}
        <div className="rounded-lg border px-3 py-2.5 transition-colors border-surface-border bg-surface-light/30 opacity-40 cursor-not-allowed">
          <label className="flex items-center justify-between gap-3 text-sm text-text-secondary cursor-not-allowed select-none">
            <div className="flex items-center gap-2 flex-1">
              <Globe size={14} className="text-text-muted" />
              <div>
                <span className="font-medium text-text-primary">Retrieve from TMVDB</span>
                <p className="text-[11px] text-text-muted mt-0.5">
                  Look up metadata from The Music Video DB community database. (Coming soon)
                </p>
              </div>
            </div>
            <Toggle checked={false} onChange={() => {}} disabled />
          </label>
        </div>
      </div>

      {/* ── YouTube Source Matching ── */}
      <div className="space-y-3 mb-5">
        <div className={`rounded-lg border px-3 py-2.5 transition-colors ${findSourceVideo ? "border-accent/40 bg-accent/5" : "border-surface-border bg-surface-light/30"}`}>
          <label className="flex items-center justify-between gap-3 text-sm text-text-secondary cursor-pointer select-none">
            <div className="flex items-center gap-2 flex-1">
              <Link2 size={14} className={findSourceVideo ? "text-accent" : "text-text-muted"} />
              <div>
                <span className="font-medium text-text-primary">YouTube Source Matching</span>
                <p className="text-[11px] text-text-muted mt-0.5">
                  Search YouTube for the official music video. If an existing YouTube link is present, it will be verified first.
                </p>
              </div>
            </div>
            <Toggle checked={findSourceVideo} onChange={(v) => setFindSourceVideo(v)} />
          </label>
        </div>

        {/* ── Normalize Audio ── */}
        <div className={`rounded-lg border px-3 py-2.5 transition-colors ${normalizeAudio ? "border-accent/40 bg-accent/5" : "border-surface-border bg-surface-light/30"}`}>
          <label className="flex items-center justify-between gap-3 text-sm text-text-secondary cursor-pointer select-none">
            <div className="flex items-center gap-2 flex-1">
              <Volume2 size={14} className={normalizeAudio ? "text-accent" : "text-text-muted"} />
              <div>
                <span className="font-medium text-text-primary">Normalise Audio</span>
                <p className="text-[11px] text-text-muted mt-0.5">
                  Apply loudness normalisation (EBU R128) after scraping. Skipped if audio is already at target level.
                </p>
              </div>
            </div>
            <Toggle checked={normalizeAudio} onChange={(v) => setNormalizeAudio(v)} />
          </label>
        </div>
      </div>

      {/* ── Version Type Hints ── */}
      <div className="border-t border-surface-border pt-3 mb-5">
        <p className="text-xs font-medium text-text-secondary mb-2">Version Type (optional)</p>
        <div className="space-y-1.5">
          <label className="flex items-center gap-3 text-sm text-text-secondary cursor-pointer">
            <input
              type="checkbox"
              checked={isCover}
              onChange={(e) => { setIsCover(e.target.checked); if (e.target.checked) { setIsLive(false); setIsAlternate(false); setIsUncensored(false); } }}
              className="h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent"
            />
            This is a cover version
          </label>
          <label className="flex items-center gap-3 text-sm text-text-secondary cursor-pointer">
            <input
              type="checkbox"
              checked={isLive}
              onChange={(e) => { setIsLive(e.target.checked); if (e.target.checked) { setIsCover(false); setIsAlternate(false); setIsUncensored(false); } }}
              className="h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent"
            />
            This is a live performance
          </label>
          <label className="flex items-center gap-3 text-sm text-text-secondary cursor-pointer">
            <input
              type="checkbox"
              checked={isAlternate}
              onChange={(e) => { setIsAlternate(e.target.checked); if (e.target.checked) { setIsCover(false); setIsLive(false); setIsUncensored(false); } }}
              className="h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent"
            />
            This is an alternate version
          </label>
          <label className="flex items-center gap-3 text-sm text-text-secondary cursor-pointer">
            <input
              type="checkbox"
              checked={isUncensored}
              onChange={(e) => { setIsUncensored(e.target.checked); if (e.target.checked) { setIsCover(false); setIsLive(false); setIsAlternate(false); } }}
              className="h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent"
            />
            This is an uncensored version
          </label>
          {isAlternate && (
            <input
              type="text"
              value={alternateLabel}
              onChange={(e) => setAlternateLabel(e.target.value)}
              placeholder="Label (e.g. Acoustic, Director's Cut)"
              className="input-field mt-1 w-full text-xs"
            />
          )}
        </div>
      </div>

      <div className="flex justify-end gap-3">
        <button onClick={onClose} className="btn-secondary btn-sm">Cancel</button>
        <button
          onClick={() => onConfirm({
            aiAutoAnalyse,
            aiOnly,
            scrapeWikipedia,
            wikipediaUrl: wikiUrl || undefined,
            scrapeMusicbrainz,
            musicbrainzUrl: mbUrl || undefined,
            scrapeTmvdb: scrapeTmvdb || undefined,
            isCover: isCover || undefined,
            isLive: isLive || undefined,
            isAlternate: isAlternate || undefined,
            isUncensored: isUncensored || undefined,
            alternateVersionLabel: alternateLabel.trim() || undefined,
            findSourceVideo: findSourceVideo || undefined,
            normalizeAudio: normalizeAudio || undefined,
          })}
          disabled={isPending || !hasSelection}
          className="btn-primary btn-sm"
        >
          {isPending ? <Loader2 size={14} className="animate-spin" /> : <Globe size={14} />}
          Scrape
        </button>
      </div>
    </PopupOverlay>
  );
}

/* ─── Edit Track IDs Popup ─── */
function EditTrackIDsPopup({
  videoId,
  onClose,
}: {
  videoId: number;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const { toast } = useToast();
  const qc = useQueryClient();
  const updateMutation = useUpdateVideo(videoId);

  const [fields, setFields] = useState({
    mb_artist_id: "",
    mb_recording_id: "",
    mb_release_id: "",
    mb_release_group_id: "",
    mb_track_id: "",
    playarr_video_id: "",
    playarr_track_id: "",
  });
  const [provenance, setProvenance] = useState<Record<string, string>>({});
  const [original, setOriginal] = useState(fields);

  useEffect(() => {
    libraryApi.get(videoId).then((video) => {
      const f = {
        mb_artist_id: video.mb_artist_id || "",
        mb_recording_id: video.mb_recording_id || "",
        mb_release_id: video.mb_release_id || "",
        mb_release_group_id: video.mb_release_group_id || "",
        mb_track_id: video.mb_track_id || "",
        playarr_video_id: video.playarr_video_id || "",
        playarr_track_id: video.playarr_track_id || "",
      };
      setFields(f);
      setOriginal(f);
      setProvenance(video.field_provenance || {});
      setLoading(false);
    });
  }, [videoId]);

  const fieldDefs: { key: keyof typeof fields; label: string; group: string; placeholder: string }[] = [
    { key: "mb_artist_id", label: "Artist ID", group: "MusicBrainz", placeholder: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" },
    { key: "mb_recording_id", label: "Recording ID", group: "MusicBrainz", placeholder: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" },
    { key: "mb_release_id", label: "Release ID", group: "MusicBrainz", placeholder: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" },
    { key: "mb_release_group_id", label: "Release Group ID", group: "MusicBrainz", placeholder: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" },
    { key: "mb_track_id", label: "Track ID", group: "MusicBrainz", placeholder: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" },
    { key: "playarr_video_id", label: "Video ID", group: "Playarr", placeholder: "PVD-xxxxxxxxxxxx" },
    { key: "playarr_track_id", label: "Track ID", group: "Playarr", placeholder: "PTR-xxxxxxxxxxxx" },
  ];

  const hasChanges = Object.keys(fields).some(
    (k) => fields[k as keyof typeof fields] !== original[k as keyof typeof fields],
  );

  const handleSave = () => {
    const update: Record<string, string | null> = {};
    for (const k of Object.keys(fields) as (keyof typeof fields)[]) {
      if (fields[k] !== original[k]) {
        update[k] = fields[k].trim() || null;
      }
    }
    setSaving(true);
    updateMutation.mutate(update as any, {
      onSuccess: () => {
        toast({ type: "success", title: "Track IDs updated" });
        qc.invalidateQueries({ queryKey: qk.video(videoId) });
        onClose();
      },
      onError: () => {
        toast({ type: "error", title: "Failed to update Track IDs" });
        setSaving(false);
      },
    });
  };

  const provenanceLabel = (key: string) => {
    const src = provenance[key];
    if (!src) return null;
    const colors: Record<string, string> = {
      manual: "text-blue-400",
      musicbrainz: "text-orange-400",
      wikipedia: "text-green-400",
      ai: "text-purple-400",
      acoustid: "text-cyan-400",
      computed: "text-emerald-400",
    };
    return (
      <span className={`text-[10px] ${colors[src] || "text-text-muted"}`}>
        {src}
      </span>
    );
  };

  const groups = ["MusicBrainz", "Playarr"] as const;

  return (
    <PopupOverlay onClose={onClose}>
      <h2 className="text-lg font-semibold text-text-primary mb-1">Edit Track IDs</h2>
      <p className="text-sm text-text-secondary mb-4">
        View and edit MusicBrainz and Playarr identifiers. Changes are tracked via provenance.
      </p>

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-8 text-text-muted">
          <Loader2 size={16} className="animate-spin" /> Loading…
        </div>
      ) : (
        <div className="space-y-4 mb-5">
          {groups.map((group) => (
            <div key={group}>
              <h4 className="text-[11px] font-medium text-text-muted uppercase tracking-wider mb-2 flex items-center gap-1.5">
                {group === "MusicBrainz" ? <Music size={12} /> : <Hash size={12} />}
                {group}
              </h4>
              <div className="space-y-2">
                {fieldDefs
                  .filter((f) => f.group === group)
                  .map((f) => (
                    <div key={f.key}>
                      <div className="flex items-center justify-between mb-0.5">
                        <label className="text-xs text-text-secondary">{f.label}</label>
                        {provenanceLabel(f.key)}
                      </div>
                      <input
                        type="text"
                        value={fields[f.key]}
                        onChange={(e) => setFields((prev) => ({ ...prev, [f.key]: e.target.value }))}
                        placeholder={f.placeholder}
                        className={`input-field w-full text-xs font-mono ${
                          fields[f.key] !== original[f.key] ? "!border-accent/60" : ""
                        }`}
                      />
                    </div>
                  ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-3">
        <button onClick={onClose} className="btn-secondary btn-sm">Cancel</button>
        <button
          onClick={handleSave}
          disabled={!hasChanges || saving}
          className="btn-primary btn-sm"
        >
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
          Save
        </button>
      </div>
    </PopupOverlay>
  );
}

/* ═══════════════════════════════════════════════════════════
   Main Actions Panel
   ═══════════════════════════════════════════════════════════ */

export function ActionsPanel({ videoId, hasUndoable, quality: q, onDeleted, filePath, artist, title, resolutionLabel, processingState, versionType, alternateVersionLabel, isLocked, hasArchive: _hasArchive, excludeFromEditorScan, className }: ActionsPanelProps) {
  const { toast } = useToast();
  const { confirm, dialog } = useConfirm();
  const qc = useQueryClient();

  /* ── Helper: check if a processing step was completed ── */
  const isStepDone = (step: string) =>
    processingState?.[step]?.completed === true;

  /* ── Pre-compute filename match ── */
  const buildExpectedName = (a: string, t: string, res: string, vt?: string | null, avl?: string | null) => {
    const sanitize = (s: string) => s.replace(/[<>:"/\\|?*]/g, "").replace(/\s+/g, " ").trim();
    let suffix = "";
    if (vt === "cover") suffix = " (Cover)";
    else if (vt === "live") suffix = " (Live)";
    else if (vt === "18+") suffix = " (18+)";
    else if (vt === "uncensored") suffix = " (Uncensored)";
    else if (vt === "alternate" && avl) suffix = ` (${sanitize(avl)})`;
    else if (vt === "alternate") suffix = " (Alternate Version)";
    return `${sanitize(a)} - ${sanitize(t)}${suffix} [${res}]`;
  };

  const filenameMatches = useMemo(() => {
    if (!filePath || !artist || !title || !resolutionLabel) return false;
    const expected = buildExpectedName(artist, title, resolutionLabel, versionType, alternateVersionLabel);
    const currentFolder = filePath.split(/[/\\]/).slice(-2, -1)[0] || "";
    return currentFolder.toLowerCase() === expected.toLowerCase();
  }, [filePath, artist, title, resolutionLabel, versionType, alternateVersionLabel]);

  /* ── Popup state ── */
  const [showNormalize, setShowNormalize] = useState(false);
  const [showRedownload, setShowRedownload] = useState(false);
  const [showCheckFilename, setShowCheckFilename] = useState(false);
  const [showScrape, setShowScrape] = useState(false);
  const [showEditTrackIDs, setShowEditTrackIDs] = useState(false);
  const [showAIResults, setShowAIResults] = useState(false);
  const [pollingScrapeResult, setPollingScrapeResult] = useState(false);
  const prevCompResultId = useRef<number | null>(null);
  const pollJobId = useRef<number | null>(null);

  /* ── AI section state ── */
  const [renameFiles, setRenameFiles] = useState(false);

  /* ── Check filename local state ── */
  const [filenameResult, setFilenameResult] = useState<{
    current: string; expected: string; match: boolean;
  } | null>(null);

  /* ── Mutations ── */
  const rescanMutation = useRescan();
  const normalizeMutation = useNormalize();
  const undoMutation = useUndoRescan(videoId);
  const deleteMutation = useDeleteVideo();
  const scrapeMutation = useScrapeMetadata();
  const redownloadMutation = useRedownload();
  const renameMutation = useRenameToExpected();
  const excludeFromScanMutation = useSetExcludeFromScan();

  // AI mutations
  const enrichMutation = useAIEnrich();
  const applyMutation = useAIApplyFields();
  const aiUndoMutation = useAIUndo();
  const fingerprintMutation = useAIFingerprint();

  // AI queries
  const aiSettings = useAISettings();
  const comparison = useAIComparison(videoId);
  const results = useAIResults(videoId);

  const latestResult = results.data?.[0];
  const comp = comparison.data;
  const hasComparison = comp && comp.fields.length > 0;
  const settings = aiSettings.data;
  const providerConfigured = settings != null && settings.provider !== "none";

  // ── Poll comparison after scrape to detect new results ──
  useEffect(() => {
    if (!pollingScrapeResult) return;
    let cancelled = false;
    const iv = setInterval(async () => {
      try {
        // Refresh video data each tick so artwork / canonical track update live
        qc.invalidateQueries({ queryKey: qk.video(videoId) });

        // Check if comparison has a new ai_result_id (scrape produced results)
        const { data } = await comparison.refetch();
        if (!cancelled && data?.ai_result_id != null && data.ai_result_id !== prevCompResultId.current) {
          setPollingScrapeResult(false);
          setShowAIResults(true);
          return;
        }
        // Also check if the job itself has finished (handles no-results case)
        if (!cancelled && pollJobId.current != null) {
          const job = await jobsApi.get(pollJobId.current);
          if (job.status === "complete" || job.status === "failed" || job.status === "cancelled") {
            setPollingScrapeResult(false);
            setShowAIResults(true);
            // Final refresh to pick up all deferred-task results (artwork, canonical)
            qc.invalidateQueries({ queryKey: qk.video(videoId) });
          }
        }
      } catch { /* ignore transient errors during polling */ }
    }, 3_000);
    const timeout = setTimeout(() => setPollingScrapeResult(false), 300_000);
    return () => { cancelled = true; clearInterval(iv); clearTimeout(timeout); };
  }, [pollingScrapeResult]);

  // Analysis summary
  const summary = useMemo(() => {
    if (!comp || comp.fields.length === 0) return null;
    const changed = comp.fields.filter((f) => f.changed && f.ai_value != null && !f.locked);
    const verified = comp.fields.filter((f) => !f.changed && f.ai_value != null);
    const unchanged = comp.fields.filter((f) => f.ai_value == null);
    const highConf = changed.filter((f) => f.ai_confidence >= 0.85);
    return { changed, verified, unchanged, highConf, total: comp.fields.length };
  }, [comp]);

  /* ── Build action groups ── */
  const guardLocked = async (action: () => void | Promise<void>) => {
    if (isLocked) {
      const ok = await confirm({
        title: "Metadata is locked",
        description: "This video's metadata is locked. Running this action may overwrite locked data. Continue anyway?",
        confirmLabel: "Continue",
        variant: "danger",
      });
      if (!ok) return;
    }
    await action();
  };

  const blockIfLocked = async () => {
    if (!isLocked) return false;
    await confirm({
      title: "Metadata is locked",
      description: "This action cannot proceed while metadata is locked. Unlock metadata first using the lock toggle in the Metadata tile.",
      confirmLabel: "OK",
    });
    return true;
  };

  const metadataActions: ActionItem[] = [
    {
      label: "Rescan from Disk",
      icon: <RefreshCw size={14} />,
      tooltip: "Re-read all metadata from the .playarr.xml sidecar file — restores ratings, sources, loudness, and processing history. Local operation — no AI tokens used.",
      pending: rescanMutation.isPending,
      variant: isStepDone("metadata_resolved") ? "success" as const : undefined,
      onClick: () =>
        guardLocked(() =>
          rescanMutation.mutate({ videoId, fromDisk: true }, {
            onSuccess: () => toast({ type: "success", title: "Rescan from disk queued" }),
          }),
        ),
    },
    {
      label: "Scrape Metadata",
      icon: <Globe size={14} />,
      tooltip: "Scrape metadata from external sources (AI, MusicBrainz, Wikipedia). Configure options in the popup.",
      pending: scrapeMutation.isPending,
      variant: isStepDone("metadata_scraped") ? "success" as const : undefined,
      onClick: async () => { if (await blockIfLocked()) return; setShowScrape(true); },
    },
    {
      label: "Identify Track",
      icon: <Fingerprint size={14} />,
      tooltip: "Generate an audio fingerprint locally using Chromaprint, then look up the track on MusicBrainz/AcoustID. Local processing + remote lookup — no AI tokens used.",
      pending: fingerprintMutation.isPending,
      variant: isStepDone("track_identified") ? "success" as const : undefined,
      onClick: () =>
        guardLocked(() =>
          fingerprintMutation.mutate(videoId, {
            onSuccess: (res) => {
              if (res.matches?.length) {
                toast({ type: "success", title: `Found ${res.matches.length} fingerprint match(es)` });
              } else {
                toast({ type: "info", title: res.error || "No fingerprint matches found" });
              }
            },
            onError: () => toast({ type: "error", title: "Fingerprint failed" }),
          }),
        ),
    },
    {
      label: "Check Filename",
      icon: filenameMatches ? <Check size={14} /> : <FileCheck size={14} />,
      tooltip: filenameMatches
        ? "Filename matches expected pattern (Artist - Title [Resolution]). Click to view details."
        : "Compare current filename against expected naming pattern (Artist - Title [Resolution]). Local operation — no AI tokens used.",
      variant: filenameMatches ? "success" as const : undefined,
      onClick: () => {
        libraryApi.get(videoId).then((video) => {
          const expected = buildExpectedName(
            video.artist, video.title, video.resolution_label || "1080p",
            video.version_type, video.alternate_version_label,
          );
          const currentFolder = video.file_path
            ? video.file_path.split(/[/\\]/).slice(-2, -1)[0] || ""
            : "";
          setFilenameResult({
            current: currentFolder,
            expected,
            match: currentFolder.toLowerCase() === expected.toLowerCase(),
          });
          setShowCheckFilename(true);
        });
      },
    },
    {
      label: "Undo Rescan",
      icon: <Undo2 size={14} />,
      tooltip: "Restore metadata from the previous snapshot before the last rescan. No file changes.",
      disabled: !hasUndoable,
      pending: undoMutation.isPending,
      onClick: async () => {
        const ok = await confirm({
          title: "Undo last rescan?",
          description: "This will restore metadata from the previous snapshot.",
        });
        if (ok) {
          undoMutation.mutate(undefined, {
            onSuccess: () => toast({ type: "success", title: "Rescan undone" }),
            onError: () => toast({ type: "error", title: "Undo failed" }),
          });
        }
      },
    },
    {
      label: "Edit Track IDs",
      icon: <Hash size={14} />,
      tooltip: "View and edit MusicBrainz IDs and Playarr Content IDs. Changes are tracked via provenance.",
      onClick: () => setShowEditTrackIDs(true),
    },
  ];

  const mediaActions: ActionItem[] = [
    {
      label: `Normalise${q?.loudness_lufs != null ? ` (${q.loudness_lufs.toFixed(1)} LUFS)` : ""}`,
      icon: <Volume2 size={14} />,
      tooltip: "Normalise audio loudness to a target LUFS level using ffmpeg. Local operation — no AI tokens used.",
      pending: normalizeMutation.isPending,
      variant: isStepDone("audio_normalized") ? "success" as const : undefined,
      onClick: () => guardLocked(() => setShowNormalize(true)),
    },
    {
      label: "Redownload",
      icon: <Download size={14} />,
      tooltip: "Re-download the video from its original source URL. The current file will be archived. No AI tokens used.",
      pending: redownloadMutation.isPending,
      onClick: () => setShowRedownload(true),
    },
    {
      label: "Send to Video Editor",
      icon: <Film size={14} />,
      tooltip: "Add this video to the Video Editor queue for cropping / re-encoding.",
      onClick: () => {
        addToVideoEditorQueue([videoId]);
        toast({ type: "success", title: "Added to Video Editor queue" });
      },
    },
    {
      label: excludeFromEditorScan ? "Editor Included" : "Editor Excluded",
      icon: <Ban size={14} />,
      tooltip: excludeFromEditorScan
        ? "This video is currently excluded from Video Editor letterbox scans. Click to re-include."
        : "Exclude this video from future Video Editor letterbox scans (e.g. for false positives).",
      variant: excludeFromEditorScan ? "danger" as const : undefined,
      pending: excludeFromScanMutation.isPending,
      onClick: () => {
        const newExclude = !excludeFromEditorScan;
        excludeFromScanMutation.mutate({ videoId, exclude: newExclude }, {
          onSuccess: () => {
            toast({ type: "info", title: newExclude ? "Excluded from future editor scans" : "Re-included in editor scans" });
            qc.invalidateQueries({ queryKey: qk.video(videoId) });
          },
          onError: () => toast({ type: "error", title: "Failed to update scan exclusion" }),
        });
      },
    },
  ];

  const fileActions: ActionItem[] = [
    {
      label: "Open Folder",
      icon: <FolderOpen size={14} />,
      tooltip: "Open the video's containing folder in the file manager.",
      disabled: !filePath,
      onClick: async () => {
        try {
          await libraryApi.openFolder(videoId);
        } catch (err: any) {
          const detail = err?.response?.data?.detail;
          toast({ type: "error", title: detail || "Failed to open folder" });
        }
      },
    },
    {
      label: "Delete Video",
      icon: <Trash2 size={14} />,
      tooltip: "Permanently delete the video file, folder, and all associated metadata from the database.",
      variant: "danger",
      onClick: async () => {
        const ok = await confirm({
          title: "Delete this video?",
          description: "The video file and all metadata will be permanently removed.",
          confirmLabel: "Delete",
          variant: "danger",
        });
        if (ok) {
          deleteMutation.mutate(videoId, {
            onSuccess: () => {
              toast({ type: "success", title: "Video deleted" });
              onDeleted();
            },
          });
        }
      },
    },
  ];

  const renderGroup = (label: string, icon: React.ReactNode, actions: ActionItem[]) => (
    <div key={label}>
      <h4 className="text-[11px] font-medium text-text-muted uppercase tracking-wider mb-1.5 flex items-center gap-1.5">
        {icon} {label}
      </h4>
      <div className="grid grid-cols-2 gap-1.5">
        {actions.map((action) => {
          const btn = (
            <button
              key={action.label}
              onClick={action.onClick}
              disabled={action.disabled || action.pending}
              className={
                `${action.variant === "danger" ? "btn-danger"
                  : action.variant === "success" ? "btn-secondary !border-green-500/40 !text-green-400 hover:!bg-green-500/10"
                  : "btn-secondary"
                } btn-sm w-full justify-center text-[11px] whitespace-nowrap overflow-hidden`
              }
            >
              {action.pending ? (
                <RefreshCw size={12} className="animate-spin" />
              ) : (
                <span className="[&>svg]:size-3 shrink-0">{action.icon}</span>
              )}
              <span className="truncate">{action.label}</span>
            </button>
          );
          return action.tooltip ? (
            <Tooltip key={action.label} content={action.tooltip}>{btn}</Tooltip>
          ) : btn;
        })}
      </div>
    </div>
  );

  return (
    <div className={`card flex flex-col${className ? ` ${className}` : ""}`}>
      {/* ═══ Panel Header ═══ */}
      <h3 className="text-sm font-semibold text-text-secondary uppercase tracking-wide flex items-center gap-1.5 mb-3">
        <Wrench size={14} /> Actions
      </h3>

      {/* ═══ Action Groups ═══ */}
      <div className="flex flex-col gap-3 flex-1">
        {renderGroup("Metadata", <Wand2 size={14} />, metadataActions)}
        {renderGroup("Media", <Wrench size={14} />, mediaActions)}
        {renderGroup("File", <FileText size={14} />, fileActions)}
      </div>

      {/* ═══ Popups ═══ */}
      {showNormalize && (
        <NormalizePopup
          currentLufs={q?.loudness_lufs}
          isPending={normalizeMutation.isPending}
          onClose={() => setShowNormalize(false)}
          onConfirm={(targetLufs) => {
            normalizeMutation.mutate(
              { video_ids: [videoId], target_lufs: targetLufs },
              {
                onSuccess: () => {
                  toast({ type: "success", title: "Normalisation queued" });
                  setShowNormalize(false);
                },
              },
            );
          }}
        />
      )}

      {showRedownload && (
        <RedownloadPopup
          videoId={videoId}
          onClose={() => setShowRedownload(false)}
          onConfirm={(formatSpec) => {
            redownloadMutation.mutate({ videoId, formatSpec }, {
              onSuccess: () => {
                toast({ type: "success", title: "Redownload started" });
                setShowRedownload(false);
              },
              onError: () => {
                toast({ type: "error", title: "Redownload failed" });
                setShowRedownload(false);
              },
            });
          }}
        />
      )}



      {showCheckFilename && filenameResult && (
        <CheckFilenamePopup
          currentFilename={filenameResult.current}
          expectedFilename={filenameResult.expected}
          isMatch={filenameResult.match}
          isPending={renameMutation.isPending}
          onClose={() => {
            setShowCheckFilename(false);
            setFilenameResult(null);
          }}
          onRename={() => {
            renameMutation.mutate(videoId, {
              onSuccess: () => {
                toast({ type: "success", title: "Files renamed to expected pattern" });
                setShowCheckFilename(false);
                setFilenameResult(null);
              },
              onError: (err: any) => {
                toast({ type: "error", title: err?.response?.data?.detail || "Rename failed" });
              },
            });
          }}
        />
      )}

      {showEditTrackIDs && (
        <EditTrackIDsPopup
          videoId={videoId}
          onClose={() => setShowEditTrackIDs(false)}
        />
      )}

      {showScrape && (
        <ScrapeMetadataPopup
          isPending={scrapeMutation.isPending}
          providerConfigured={providerConfigured}
          onClose={() => setShowScrape(false)}
          onConfirm={(opts) => {
            prevCompResultId.current = comp?.ai_result_id ?? null;
            scrapeMutation.mutate(
              {
                videoId,
                aiAutoAnalyse: opts.aiAutoAnalyse,
                aiOnly: opts.aiOnly,
                scrapeWikipedia: opts.scrapeWikipedia,
                wikipediaUrl: opts.wikipediaUrl,
                scrapeMusicbrainz: opts.scrapeMusicbrainz,
                musicbrainzUrl: opts.musicbrainzUrl,
                isCover: opts.isCover,
                isLive: opts.isLive,
                isAlternate: opts.isAlternate,
                isUncensored: opts.isUncensored,
                alternateVersionLabel: opts.alternateVersionLabel,
                findSourceVideo: opts.findSourceVideo,
                normalizeAudio: opts.normalizeAudio,
              },
              {
                onSuccess: (res) => {
                  pollJobId.current = res.job_id;
                  toast({ type: "success", title: "Scraping metadata — results will appear shortly…" });
                  setShowScrape(false);
                  setPollingScrapeResult(true);
                },
                onError: () => toast({ type: "error", title: "Scrape failed" }),
              },
            );
          }}
        />
      )}

      {showAIResults && (
        <PopupOverlay onClose={() => { setShowAIResults(false); qc.invalidateQueries({ queryKey: qk.video(videoId) }); }} wide>
          <h2 className="text-lg font-semibold text-text-primary mb-1">Metadata Results</h2>
          <p className="text-sm text-text-secondary mb-4">
            Review the proposed changes below. Apply or dismiss corrections, then close this dialog to finalise.
          </p>

          <div className="space-y-5">
            {/* Analysis Status */}
            {(latestResult || enrichMutation.isPending) && (
              <AnalysisStatus
                result={latestResult}
                isPending={enrichMutation.isPending}
                summary={summary}
                changeSummary={comp?.change_summary}
                model={comp?.model}
              />
            )}

            {/* Identity Verification */}
            {comp?.mismatch_report && typeof comp.mismatch_report === "object" && "ai_identity" in comp.mismatch_report && (
              <IdentityVerification
                identity={(comp.mismatch_report as { ai_identity?: AIIdentityVerification | null }).ai_identity ?? null}
                aiMismatch={(comp.mismatch_report as { ai_mismatch?: AIMismatchInfo | null }).ai_mismatch ?? null}
              />
            )}

            {/* Mismatch Details */}
            {comp?.mismatch_report && typeof comp.mismatch_report === "object" && "overall_score" in comp.mismatch_report && (comp.mismatch_report as { overall_score: number }).overall_score > 0.3 && (
              <MismatchDetails
                report={comp.mismatch_report as { overall_score: number; is_suspicious: boolean; signals?: Array<{ name: string; score: number; weight: number; details?: string | null }> }}
                fields={comp.fields}
              />
            )}

            {/* Fingerprint Results */}
            {(comp?.fingerprint_result || fingerprintMutation.data) && (
              <FingerprintResults
                result={fingerprintMutation.data ?? (comp?.fingerprint_result as { fpcalc_available?: boolean; match_count?: number; best_match?: FingerprintMatch | null; matches: FingerprintMatch[]; error?: string | null } | null)}
              />
            )}

            {/* No new data fallback */}
            {!enrichMutation.isPending && !latestResult && !hasComparison && !(comp?.fingerprint_result || fingerprintMutation.data) && (
              <div className="flex items-center gap-2 text-xs text-text-muted bg-surface-light rounded-lg px-3 py-2.5">
                <AlertTriangle size={14} className="text-amber-400" />
                <span>Scrape complete — no new metadata was found from this source.</span>
              </div>
            )}

            {/* Suggested Corrections */}
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
                  aiUndoMutation.mutate(
                    { videoId, data: { ai_result_id: aiResultId } },
                    {
                      onSuccess: () => toast({ type: "success", title: "Metadata restored" }),
                      onError: () => toast({ type: "error", title: "Undo failed" }),
                    },
                  )
                }
                isPending={applyMutation.isPending}
                isUndoPending={aiUndoMutation.isPending}
                overallConfidence={comp?.overall_confidence ?? undefined}
                artworkUpdates={comp?.artwork_updates}
                sourceUpdates={comp?.source_updates?.filter((s) => s.pending)}
                model={comp?.model}
              />
            )}

            {/* Source Links (confirmed only) */}
            {comp?.source_updates && comp.source_updates.filter((s) => !s.pending).length > 0 && (
              <SourceLinksSection sources={comp.source_updates.filter((s) => !s.pending)} />
            )}
          </div>

          <div className="mt-6 flex justify-end">
            <button onClick={() => { setShowAIResults(false); qc.invalidateQueries({ queryKey: qk.video(videoId) }); }} className="btn-primary btn-sm">
              Close
            </button>
          </div>
        </PopupOverlay>
      )}

      {dialog}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   Analysis Status
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
        <div className="flex items-center gap-2 text-xs">
          <ShieldCheck size={14} className="text-green-400" />
          <span className="text-text-primary font-medium">{statusText}</span>
          {result.created_at && (
            <span className="text-text-muted ml-auto">{new Date(result.created_at).toLocaleString()}</span>
          )}
        </div>
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
        {changeSummary && (
          <p className="text-[11px] text-text-muted ml-5 leading-relaxed">{changeSummary}</p>
        )}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   Identity Verification
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
      {identity?.candidate_artist && (
        <div className="text-[11px] ml-5 space-y-0.5">
          <div>
            <span className="text-text-muted">Identified as: </span>
            <strong>{identity.candidate_artist}</strong>
            {identity.candidate_title && <> — <strong>{identity.candidate_title}</strong></>}
          </div>
        </div>
      )}
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
   Mismatch Details
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

function FingerprintResults({ result }: { result: { fpcalc_available?: boolean; match_count?: number; best_match?: FingerprintMatch | null; matches: FingerprintMatch[]; error?: string | null } | null }) {
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
   Correction Table
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
  sourceUpdates,
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
  sourceUpdates?: SourceUpdate[];
  model?: string | null;
}) {
  const [accepted, setAccepted] = useState<Record<string, boolean>>({});

  const toggle = (field: string) => {
    setAccepted((prev) => ({ ...prev, [field]: !prev[field] }));
  };

  const artworks = artworkUpdates ?? [];
  const changeableArtworks = artworks.filter((a) => !a.unchanged);
  const pendingSources = sourceUpdates ?? [];

  const sourceFieldKey = (s: SourceUpdate) => `source:${s.provider}:${s.source_type || "video"}`;

  const selectAll = () => {
    const next: Record<string, boolean> = {};
    fields.forEach((f) => {
      if (f.changed && f.ai_value != null && !f.locked && !f.accepted) next[f.field] = true;
    });
    changeableArtworks.forEach((a) => { next[a.asset_type] = true; });
    pendingSources.forEach((s) => { next[sourceFieldKey(s)] = true; });
    setAccepted(next);
  };

  const deselectAll = () => setAccepted({});

  const anyAccepted = Object.values(accepted).some(Boolean);
  const acceptedCount = Object.values(accepted).filter(Boolean).length;
  const changedFields = fields.filter((f) => f.changed && f.ai_value != null && !f.locked);
  const highConfFields = changedFields.filter((f) => f.ai_confidence >= 0.85);
  const totalChanges = changedFields.length + changeableArtworks.length + pendingSources.length;

  const applyFields = (fieldNames: string[]) => {
    onApply(aiResultId, fieldNames);
    setAccepted({});
  };

  const applyAccepted = () => {
    const fieldNames = fields.filter((f) => accepted[f.field]).map((f) => f.field);
    const artFieldNames = artworks.filter((a) => accepted[a.asset_type]).map((a) => a.asset_type);
    const srcFieldNames = pendingSources.filter((s) => accepted[sourceFieldKey(s)]).map(sourceFieldKey);
    applyFields([...fieldNames, ...artFieldNames, ...srcFieldNames]);
  };

  const applyAll = () => applyFields([...changedFields.map((f) => f.field), ...changeableArtworks.map((a) => a.asset_type), ...pendingSources.map(sourceFieldKey)]);
  const applyHighConfidence = () => applyFields(highConfFields.map((f) => f.field));

  const changedRows = fields.filter((f) => f.changed && f.ai_value != null);
  const changedFieldNames = new Set(changedRows.map((f) => f.field));
  const verifiedRows = fields.filter((f) => !changedFieldNames.has(f.field));
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
            {changedRows.map((f) => (
              <DiffRow key={f.field} field={f} isAccepted={!!accepted[f.field]} onToggle={() => toggle(f.field)} />
            ))}
            {artworks.map((a) => (
              <ArtworkDiffRow key={a.asset_type} artwork={a} isAccepted={!!accepted[a.asset_type]} onToggle={() => toggle(a.asset_type)} />
            ))}
            {pendingSources.map((s) => (
              <SourceDiffRow key={sourceFieldKey(s)} source={s} isAccepted={!!accepted[sourceFieldKey(s)]} onToggle={() => toggle(sourceFieldKey(s))} />
            ))}
            {verifiedRows.map((f) => (
              <DiffRow key={f.field} field={f} isAccepted={false} onToggle={() => {}} />
            ))}
          </tbody>
        </table>
      </div>

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
      <td className="px-3 py-2 text-xs font-medium text-text-primary capitalize align-top">
        <span className="flex items-center gap-1">
          {field.field.replace(/_/g, " ")}
          {isLocked && <Lock size={10} className="text-yellow-500" />}
        </span>
      </td>
      <td className="px-3 py-2 text-xs text-text-muted align-top">
        <div className="break-words whitespace-pre-wrap max-w-[300px]" title={scrapedStr}>
          {isApplied ? <span className="line-through opacity-60">{scrapedStr}</span> : scrapedStr}
        </div>
      </td>
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
      <td className="px-3 py-2 text-center align-top">
        {field.ai_confidence != null && field.ai_confidence > 0 ? (
          <ConfidenceBadge value={field.ai_confidence} />
        ) : isDiff ? null : (
          <span className="text-green-400 text-xs">✓</span>
        )}
      </td>
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
            className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm cursor-pointer"
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

/* ─── Source diff row ─── */

function SourceDiffRow({
  source,
  isAccepted,
  onToggle,
}: {
  source: SourceUpdate;
  isAccepted: boolean;
  onToggle: () => void;
}) {
  const typeLabel =
    source.source_type === "recording" ? "Recording" :
    source.source_type === "artist" ? "Artist" :
    source.source_type === "album" ? "Album" :
    source.source_type === "video" ? "Video" :
    source.source_type ?? "Link";

  return (
    <tr className="group">
      <td className="px-3 py-2 text-xs font-medium text-text-primary capitalize align-middle">
        <span className="flex items-center gap-1.5">
          <Link2 size={11} className="text-text-muted" />
          <SourceBadge provider={source.provider as import("@/types").SourceProvider} iconOnly />
          {typeLabel}
        </span>
      </td>
      <td className="px-3 py-2 text-xs text-text-muted align-middle">
        <span>—</span>
      </td>
      <td className="px-3 py-2 text-xs text-accent font-medium align-middle">
        <a
          href={source.original_url}
          target="_blank"
          rel="noopener noreferrer"
          className="hover:underline truncate block max-w-[300px]"
          title={source.original_url}
        >
          {source.original_url}
        </a>
      </td>
      <td className="px-3 py-2 text-center align-middle" />
      <td className="px-3 py-2 text-center align-middle">
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
      </td>
    </tr>
  );
}

/* ═══════════════════════════════════════════════════════════
   Source Links Section
   ═══════════════════════════════════════════════════════════ */

const sourceTypeLabels: Record<string, string> = {
  video: "Video",
  artist: "Artist",
  album: "Album",
  single: "Single",
  recording: "Recording",
};

function SourceLinksSection({ sources }: { sources: SourceUpdate[] }) {
  // Group by source_type
  const grouped = useMemo(() => {
    const providerOrder: Record<string, number> = { youtube: 0, vimeo: 1, musicbrainz: 2, imdb: 3, wikipedia: 4 };
    const map: Record<string, SourceUpdate[]> = {};
    for (const s of sources) {
      let key = s.source_type || "video";
      // Strict: wikipedia/musicbrainz can NEVER be "video"
      if (key === "video" && (s.provider === "wikipedia" || s.provider === "musicbrainz")) key = "single";
      (map[key] ??= []).push(s);
    }
    // Sort within each group: musicbrainz before wikipedia
    for (const key of Object.keys(map)) {
      map[key].sort((a, b) => (providerOrder[a.provider] ?? 3) - (providerOrder[b.provider] ?? 3));
    }
    return map;
  }, [sources]);

  const categories = Object.keys(grouped);
  if (categories.length === 0) return null;

  return (
    <div className="rounded-lg bg-surface-light px-3 py-2.5 space-y-2">
      <h4 className="text-[11px] font-semibold text-text-muted uppercase tracking-wider flex items-center gap-1.5">
        <Globe size={12} /> Source Links
      </h4>
      <div className="space-y-1">
        {categories.map((cat) =>
          grouped[cat].map((s, i) => (
            <div key={`${cat}-${i}`} className="flex items-center gap-2 text-xs">
              <span className="text-text-muted w-12 shrink-0">{sourceTypeLabels[cat] ?? cat}</span>
              <SourceBadge provider={s.provider as import("@/types").SourceProvider} iconOnly />
              <a
                href={s.original_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent hover:underline truncate"
              >
                {s.original_url}
              </a>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
