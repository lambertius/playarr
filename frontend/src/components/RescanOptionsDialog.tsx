import { useState, useEffect } from "react";
import ReactDOM from "react-dom";
import { X, RefreshCw, Loader2, Info } from "lucide-react";
import { useSettings } from "@/hooks/queries";
import { Tooltip } from "@/components/Tooltip";

interface Props {
  open: boolean;
  count: number;
  onClose: () => void;
  onConfirm: (opts: RescanOptions) => void;
  isPending: boolean;
}

export interface RescanOptions {
  scrape_wikipedia: boolean;
  scrape_musicbrainz: boolean;
  scrape_tmvdb: boolean;
  ai_auto: boolean;
  ai_only: boolean;
  hint_cover: boolean;
  hint_live: boolean;
  hint_alternate: boolean;
  normalize: boolean;
  find_source_video: boolean;
}

function getBoolSetting(settings: { key: string; value: string }[] | undefined, key: string, fallback: boolean): boolean {
  const s = settings?.find((s) => s.key === key);
  if (!s) return fallback;
  return s.value === "true";
}

export function RescanOptionsDialog({ open, count, onClose, onConfirm, isPending }: Props) {
  const [scrapeWiki, setScrapeWiki] = useState(true);
  const [scrapeMusicbrainz, setScrapeMusicbrainz] = useState(true);
  const [scrapeTmvdb, setScrapeTmvdb] = useState(false);
  const [aiAuto, setAiAuto] = useState(false);
  const [aiOnly, setAiOnly] = useState(false);
  const [isCover, setIsCover] = useState(false);
  const [isLive, setIsLive] = useState(false);
  const [isAlternate, setIsAlternate] = useState(false);
  const [normalize, setNormalize] = useState(true);
  const [findSourceVideo, setFindSourceVideo] = useState(false);

  const { data: settings } = useSettings();

  // Initialise toggles from settings defaults
  useEffect(() => {
    if (!settings) return;
    setScrapeWiki(getBoolSetting(settings, "import_scrape_wikipedia", true));
    setScrapeMusicbrainz(getBoolSetting(settings, "import_scrape_musicbrainz", true));
    setScrapeTmvdb(getBoolSetting(settings, "import_scrape_tmvdb", false));
    setAiAuto(getBoolSetting(settings, "import_ai_auto", false));
    setAiOnly(getBoolSetting(settings, "import_ai_only", false));
    setNormalize(getBoolSetting(settings, "auto_normalize_on_import", true));
    setFindSourceVideo(getBoolSetting(settings, "import_find_source_video", false));
  }, [settings]);

  if (!open) return null;

  const handleConfirm = () => {
    onConfirm({
      scrape_wikipedia: scrapeWiki,
      scrape_musicbrainz: scrapeMusicbrainz,
      scrape_tmvdb: scrapeTmvdb,
      ai_auto: aiAuto,
      ai_only: aiOnly,
      hint_cover: isCover,
      hint_live: isLive,
      hint_alternate: isAlternate,
      normalize,
      find_source_video: findSourceVideo,
    });
  };

  return ReactDOM.createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div
        className="relative z-10 w-full max-w-md max-h-[90vh] overflow-y-auto rounded-xl border border-surface-border bg-surface-light p-6 shadow-[0_0_40px_rgba(225,29,46,0.1)]"
        style={{ background: "linear-gradient(135deg, rgba(21,25,35,0.97) 0%, rgba(28,34,48,0.92) 100%)" }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="rescan-options-title"
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-text-muted hover:text-text-primary"
          aria-label="Close"
        >
          <X size={18} />
        </button>

        <h2 id="rescan-options-title" className="text-lg font-semibold text-text-primary mb-1 flex items-center gap-2">
          <RefreshCw size={18} /> Rescan Selected
        </h2>
        <p className="text-sm text-text-muted mb-4">
          {count} video{count !== 1 ? "s" : ""} selected — metadata will be re-scraped. Locked fields are preserved.
        </p>

        <div className="space-y-4">
          {/* Metadata Sources */}
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
                onChange={(e) => { setIsCover(e.target.checked); if (e.target.checked) { setIsLive(false); setIsAlternate(false); } }}
                className="h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent"
              />
              This is a cover version
            </label>
            <label className="flex items-center gap-3 text-sm text-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={isLive}
                onChange={(e) => { setIsLive(e.target.checked); if (e.target.checked) { setIsCover(false); setIsAlternate(false); } }}
                className="h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent"
              />
              This is a live performance
            </label>
            <label className="flex items-center gap-3 text-sm text-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={isAlternate}
                onChange={(e) => { setIsAlternate(e.target.checked); if (e.target.checked) { setIsCover(false); setIsLive(false); } }}
                className="h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent"
              />
              This is an alternate version
            </label>
          </div>

          {/* Post-processing options */}
          <div className="space-y-2 rounded-lg border border-surface-border/50 p-3">
            <div className="flex items-center gap-1.5">
              <p className="text-xs font-medium text-text-muted uppercase tracking-wide">Post-Processing</p>
              <Tooltip content={"Additional operations applied after metadata scraping is complete."}>
                <span><Info size={12} className="text-text-muted" /></span>
              </Tooltip>
            </div>
            <ToggleRow label="Normalise Audio" description="Apply loudness normalisation (EBU R128) to ensure consistent volume across all tracks." checked={normalize} onChange={(v) => setNormalize(v)} />
            <ToggleRow label="YouTube Source Matching" description="Search YouTube for the official music video source. If an existing YouTube link is present, it will be verified first." checked={findSourceVideo} onChange={(v) => setFindSourceVideo(v)} />
          </div>

          {/* Action buttons */}
          <div className="flex gap-3 pt-2">
            <button
              onClick={onClose}
              className="btn-ghost flex-1"
              disabled={isPending}
            >
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              disabled={isPending}
              className="btn-primary flex-1"
            >
              {isPending ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  Queuing...
                </>
              ) : (
                <>
                  <RefreshCw size={14} />
                  Rescan {count} Video{count !== 1 ? "s" : ""}
                </>
              )}
            </button>
          </div>
        </div>
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
