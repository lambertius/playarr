import { useState, useEffect, useMemo, type FormEvent } from "react";
import ReactDOM from "react-dom";
import { useNavigate } from "react-router-dom";
import { X, Link as LinkIcon, User, Music, Loader2, Info, CheckCircle2, ListVideo } from "lucide-react";
import { useImportVideo, useSettings } from "@/hooks/queries";
import { useToast } from "@/components/Toast";
import { Tooltip } from "@/components/Tooltip";

interface AddVideoModalProps {
  open: boolean;
  onClose: () => void;
  initialUrl?: string;
  onImportSuccess?: (url: string) => void;
}

function getBoolSetting(settings: { key: string; value: string }[] | undefined, key: string, fallback: boolean): boolean {
  const s = settings?.find((s) => s.key === key);
  if (!s) return fallback;
  return s.value === "true";
}

export function AddVideoModal({ open, onClose, initialUrl, onImportSuccess }: AddVideoModalProps) {
  const [url, setUrl] = useState(initialUrl ?? "");
  const [artistOverride, setArtistOverride] = useState("");
  const [titleOverride, setTitleOverride] = useState("");
  const [normalize, setNormalize] = useState(true);
  const [scrapeWiki, setScrapeWiki] = useState(true);
  const [scrapeMusicbrainz, setScrapeMusicbrainz] = useState(true);
  const [scrapeTmvdb, setScrapeTmvdb] = useState(false);
  const [aiAuto, setAiAuto] = useState(false);
  const [aiOnly, setAiOnly] = useState(false);
  const [isCover, setIsCover] = useState(false);
  const [isLive, setIsLive] = useState(false);
  const [isAlternate, setIsAlternate] = useState(false);
  const [isUncensored, setIsUncensored] = useState(false);
  const [alternateLabel, setAlternateLabel] = useState("");

  const importMutation = useImportVideo();
  const { data: settings } = useSettings();
  const { toast } = useToast();
  const navigate = useNavigate();

  // URL type detection
  const urlInfo = useMemo(() => {
    const trimmed = url.trim();
    if (!trimmed) return null;
    const isYouTube = /^https?:\/\/(www\.)?(youtube\.com|youtu\.be)\//i.test(trimmed);
    const isVimeo = /^https?:\/\/(www\.)?vimeo\.com\//i.test(trimmed);
    const isPlaylist = isYouTube && (/[?&]list=/i.test(trimmed) || /\/playlist\?/i.test(trimmed));
    if (isYouTube) return { provider: "YouTube", isPlaylist, valid: true } as const;
    if (isVimeo) return { provider: "Vimeo", isPlaylist: false, valid: true } as const;
    if (/^https?:\/\//i.test(trimmed)) return { provider: "Unknown", isPlaylist: false, valid: false } as const;
    return null;
  }, [url]);

  // Initialise toggles from settings defaults when settings load
  useEffect(() => {
    if (!settings) return;
    setNormalize(getBoolSetting(settings, "auto_normalize_on_import", true));
    setScrapeWiki(getBoolSetting(settings, "import_scrape_wikipedia", true));
    setScrapeMusicbrainz(getBoolSetting(settings, "import_scrape_musicbrainz", true));
    setScrapeTmvdb(getBoolSetting(settings, "import_scrape_tmvdb", false));
    setAiAuto(getBoolSetting(settings, "import_ai_auto", false));
    setAiOnly(getBoolSetting(settings, "import_ai_only", false));
  }, [settings]);

  // Sync initialUrl when modal opens with a new URL
  useEffect(() => {
    if (open && initialUrl) setUrl(initialUrl);
  }, [open, initialUrl]);

  if (!open) return null;

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;

    importMutation.mutate(
      {
        url: url.trim(),
        artist: artistOverride.trim() || undefined,
        title: titleOverride.trim() || undefined,
        normalize,
        scrape: scrapeWiki,
        scrape_musicbrainz: scrapeMusicbrainz,
        scrape_tmvdb: scrapeTmvdb || undefined,
        is_cover: isCover || undefined,
        is_live: isLive || undefined,
        is_alternate: isAlternate || undefined,
        is_uncensored: isUncensored || undefined,
        alternate_version_label: alternateLabel.trim() || undefined,
        ai_auto_analyse: aiAuto || undefined,
        ai_auto_fallback: aiOnly || undefined,
      },
      {
        onSuccess: () => {
          toast({ type: "success", title: "Import started", description: "Video has been queued — opening queue." });
          const importedUrl = url.trim();
          setUrl("");
          setArtistOverride("");
          setTitleOverride("");
          setIsCover(false);
          setIsLive(false);
          setIsAlternate(false);
          setIsUncensored(false);
          setAlternateLabel("");
          onClose();
          onImportSuccess?.(importedUrl);
          navigate("/queue");
        },
        onError: (err) => {
          const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "Import failed";
          toast({ type: "error", title: "Import failed", description: String(msg) });
        },
      }
    );
  };

  return ReactDOM.createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div
        className="relative z-10 w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-xl border border-surface-border bg-surface-light p-6 shadow-[0_0_40px_rgba(225,29,46,0.1)]"
        style={{ background: "linear-gradient(135deg, rgba(21,25,35,0.97) 0%, rgba(28,34,48,0.92) 100%)" }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-title"
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-text-muted hover:text-text-primary"
          aria-label="Close"
        >
          <X size={18} />
        </button>

        <h2 id="import-title" className="text-lg font-semibold text-text-primary mb-4">
          Add Video
        </h2>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* URL */}
          <div>
            <label htmlFor="import-url" className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-text-secondary">
              <LinkIcon size={14} /> Video URL
            </label>
            <input
              id="import-url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.youtube.com/watch?v=... or playlist URL"
              className="input-field"
              required
              autoFocus
            />
            <div className="mt-1 flex items-center gap-2 min-h-[18px]">
              {urlInfo ? (
                urlInfo.valid ? (
                  <span className="flex items-center gap-1 text-xs text-emerald-400">
                    <CheckCircle2 size={12} />
                    {urlInfo.provider}
                    {urlInfo.isPlaylist && (
                      <span className="flex items-center gap-1 text-blue-400 ml-1">
                        <ListVideo size={12} /> Playlist detected — all videos will be queued
                      </span>
                    )}
                  </span>
                ) : (
                  <span className="text-xs text-amber-400">
                    Unsupported URL — only YouTube and Vimeo are supported
                  </span>
                )
              ) : (
                <span className="text-xs text-text-muted">YouTube or Vimeo URL — playlists are supported</span>
              )}
            </div>
          </div>

          {/* Artist / Title override */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="import-artist" className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-text-secondary">
                <User size={14} /> Artist Override
                <Tooltip content="Override the auto-detected artist name. Use this when the video title doesn't contain the correct artist.">
                  <span><Info size={12} className="text-text-muted" /></span>
                </Tooltip>
              </label>
              <input
                id="import-artist"
                type="text"
                value={artistOverride}
                onChange={(e) => setArtistOverride(e.target.value)}
                placeholder="Auto-detect"
                className="input-field"
              />
            </div>
            <div>
              <label htmlFor="import-title-override" className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-text-secondary">
                <Music size={14} /> Title Override
                <Tooltip content="Override the auto-detected track title. Use this when the video title is formatted in a non-standard way.">
                  <span><Info size={12} className="text-text-muted" /></span>
                </Tooltip>
              </label>
              <input
                id="import-title-override"
                type="text"
                value={titleOverride}
                onChange={(e) => setTitleOverride(e.target.value)}
                placeholder="Auto-detect"
                className="input-field"
              />
            </div>
          </div>

          {/* Scrape & AI modes — Wiki+MB can coexist, AI modes are exclusive */}
          <div className="space-y-2 rounded-lg border border-surface-border/50 p-3">
            <div className="flex items-center gap-1.5">
              <p className="text-xs font-medium text-text-muted uppercase tracking-wide">Metadata Sources</p>
              <Tooltip content={"Choose how metadata is gathered:\n\n• Wikipedia + MusicBrainz can be enabled together for best results\n• AI modes are exclusive — they replace manual scraping"}>
                <span><Info size={12} className="text-text-muted" /></span>
              </Tooltip>
            </div>
            <ToggleRow label="Scrape Wikipedia" description="Search for a Wikipedia article to extract plot, genre, and background info. Artist is verified to prevent false positives." checked={scrapeWiki} onChange={(v) => { setScrapeWiki(v); if (v) { setAiAuto(false); setAiOnly(false); } }} />
            <ToggleRow label="Scrape MusicBrainz" description="Query MusicBrainz for structured metadata: album, release year, and genre tags." checked={scrapeMusicbrainz} onChange={(v) => { setScrapeMusicbrainz(v); if (v) { setAiAuto(false); setAiOnly(false); } }} />
            <ToggleRow label="Retrieve from TMVDB" description="Look up metadata from The Music Video DB community database. (Coming soon)" checked={false} onChange={() => {}} disabled />
            <ToggleRow label="AI Auto" description="Full AI-guided enrichment after scraping. Falls back to AI when scrapers miss data. Uses AI tokens." checked={aiAuto} onChange={(v) => { setAiAuto(v); if (v) { setScrapeWiki(false); setScrapeMusicbrainz(false); setScrapeTmvdb(false); setAiOnly(false); } }} />
            <ToggleRow label="AI Only" description="Skip all external scrapers — rely solely on AI for metadata. Uses AI tokens." checked={aiOnly} onChange={(v) => { setAiOnly(v); if (v) { setScrapeWiki(false); setScrapeMusicbrainz(false); setScrapeTmvdb(false); setAiAuto(false); } }} />
          </div>

          {/* Version hints */}
          <div className="space-y-2 rounded-lg border border-surface-border/50 p-3">
            <div className="flex items-center gap-1.5">
              <p className="text-xs font-medium text-text-muted uppercase tracking-wide">Version Type</p>
              <Tooltip content={"Tag special versions of a track so they're stored separately from the original.\nOnly one type can be selected."}>
                <span><Info size={12} className="text-text-muted" /></span>
              </Tooltip>
            </div>
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
            {isAlternate && (
              <input
                type="text"
                value={alternateLabel}
                onChange={(e) => setAlternateLabel(e.target.value)}
                placeholder="Label (e.g. Acoustic, Director's Cut)"
                className="input-field mt-1"
              />
            )}
            <label className="flex items-center gap-3 text-sm text-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={isUncensored}
                onChange={(e) => { setIsUncensored(e.target.checked); if (e.target.checked) { setIsCover(false); setIsLive(false); setIsAlternate(false); } }}
                className="h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent"
              />
              This is an uncensored version
            </label>
          </div>

          {/* Normalise toggle — styled consistently with ToggleRow */}
          <div className="space-y-2 rounded-lg border border-surface-border/50 p-3">
            <div className="flex items-center gap-1.5">
              <p className="text-xs font-medium text-text-muted uppercase tracking-wide">Post-Processing</p>
              <Tooltip content="Additional operations applied after the video is downloaded.">
                <span><Info size={12} className="text-text-muted" /></span>
              </Tooltip>
            </div>
            <ToggleRow label="Normalise Audio" description="Apply loudness normalisation (EBU R128) to ensure consistent volume across all imported tracks." checked={normalize} onChange={(v) => setNormalize(v)} />
          </div>

          {/* Submit */}
          <button
            type="submit"
            disabled={importMutation.isPending || !url.trim() || (urlInfo !== null && !urlInfo.valid)}
            className="btn-primary w-full"
          >
            {importMutation.isPending ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                Importing...
              </>
            ) : (
              "Import Video"
            )}
          </button>
        </form>
      </div>
    </div>,
    document.body,
  );
}

function ToggleRow({ label, description, checked, onChange, disabled }: { label: string; description?: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className={`flex items-center justify-between gap-3 text-sm text-text-secondary ${disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}>
      <div>
        <span className="font-medium">{label}</span>
        {description && <p className="text-[11px] text-text-muted mt-0.5">{description}</p>}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className={`relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors duration-200 ease-in-out ${disabled ? "bg-surface-lighter cursor-not-allowed" : checked ? "bg-accent" : "bg-surface-lighter"}`}
      >
        <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-200 ease-in-out mt-0.5 ${checked ? "translate-x-4 ml-0.5" : "translate-x-0.5"}`} />
      </button>
    </label>
  );
}
