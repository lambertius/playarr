/**
 * ImportOptionsModal — Shows import options (metadata sources, normalize)
 * before batch-importing cart items.
 */
import { useState, useEffect } from "react";
import ReactDOM from "react-dom";
import { X, Download, Info } from "lucide-react";
import { useSettings } from "@/hooks/queries";
import { Tooltip } from "@/components/Tooltip";

export interface ImportOptions {
  normalize: boolean;
  scrape: boolean;
  scrape_musicbrainz: boolean;
  scrape_tmvdb: boolean;
  ai_auto_analyse: boolean;
  ai_auto_fallback: boolean;
}

interface ImportOptionsModalProps {
  open: boolean;
  onClose: () => void;
  onImport: (options: ImportOptions) => void;
  itemCount: number;
  isPending: boolean;
}

function getBoolSetting(settings: { key: string; value: string }[] | undefined, key: string, fallback: boolean): boolean {
  const s = settings?.find((s) => s.key === key);
  if (!s) return fallback;
  return s.value === "true";
}

export function ImportOptionsModal({ open, onClose, onImport, itemCount, isPending }: ImportOptionsModalProps) {
  const [normalize, setNormalize] = useState(true);
  const [scrapeWiki, setScrapeWiki] = useState(true);
  const [scrapeMusicbrainz, setScrapeMusicbrainz] = useState(true);
  const [scrapeTmvdb, setScrapeTmvdb] = useState(false);
  const [aiAuto, setAiAuto] = useState(false);
  const [aiOnly, setAiOnly] = useState(false);
  const { data: settings } = useSettings();

  useEffect(() => {
    if (!settings) return;
    setNormalize(getBoolSetting(settings, "auto_normalize_on_import", true));
    setScrapeWiki(getBoolSetting(settings, "import_scrape_wikipedia", true));
    setScrapeMusicbrainz(getBoolSetting(settings, "import_scrape_musicbrainz", true));
    setScrapeTmvdb(getBoolSetting(settings, "import_scrape_tmvdb", false));
    setAiAuto(getBoolSetting(settings, "import_ai_auto", false));
    setAiOnly(getBoolSetting(settings, "import_ai_only", false));
  }, [settings]);

  if (!open) return null;

  const handleSubmit = () => {
    onImport({
      normalize,
      scrape: scrapeWiki,
      scrape_musicbrainz: scrapeMusicbrainz,
      scrape_tmvdb: scrapeTmvdb,
      ai_auto_analyse: aiAuto,
      ai_auto_fallback: aiOnly,
    });
  };

  return ReactDOM.createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div
        className="relative z-10 w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-xl border border-surface-border bg-surface-light p-6 shadow-[0_0_40px_rgba(225,29,46,0.1)]"
        style={{ background: "linear-gradient(135deg, rgba(21,25,35,0.97) 0%, rgba(28,34,48,0.92) 100%)" }}
        role="dialog"
        aria-modal="true"
      >
        <button onClick={onClose} className="absolute top-4 right-4 text-text-muted hover:text-text-primary" aria-label="Close">
          <X size={18} />
        </button>

        <h2 className="text-lg font-semibold text-text-primary mb-1">Import Cart</h2>
        <p className="text-sm text-text-muted mb-4">
          Configure import options for {itemCount} item{itemCount !== 1 ? "s" : ""}
        </p>

        <div className="space-y-4">
          {/* Metadata sources */}
          <div className="space-y-2 rounded-lg border border-surface-border/50 p-3">
            <div className="flex items-center gap-1.5">
              <p className="text-xs font-medium text-text-muted uppercase tracking-wide">Metadata Sources</p>
              <Tooltip content={"Choose how metadata is gathered:\n\n• Wikipedia + MusicBrainz can be enabled together\n• AI modes are exclusive"}>
                <span><Info size={12} className="text-text-muted" /></span>
              </Tooltip>
            </div>
            <ToggleRow label="Scrape Wikipedia" description="Search for a Wikipedia article to extract plot, genre, and background info." checked={scrapeWiki} onChange={(v) => { setScrapeWiki(v); if (v) { setAiAuto(false); setAiOnly(false); } }} />
            <ToggleRow label="Scrape MusicBrainz" description="Query MusicBrainz for structured metadata: album, release year, and genre tags." checked={scrapeMusicbrainz} onChange={(v) => { setScrapeMusicbrainz(v); if (v) { setAiAuto(false); setAiOnly(false); } }} />
            <ToggleRow label="Retrieve from TMVDB" description="Look up metadata from The Music Video DB community database. (Coming soon)" checked={false} onChange={() => {}} disabled />
            <ToggleRow label="AI Auto" description="Full AI-guided enrichment after scraping. Falls back to AI when scrapers miss data." checked={aiAuto} onChange={(v) => { setAiAuto(v); if (v) { setScrapeWiki(false); setScrapeMusicbrainz(false); setScrapeTmvdb(false); setAiOnly(false); } }} />
            <ToggleRow label="AI Only" description="Skip all external scrapers — rely solely on AI for metadata." checked={aiOnly} onChange={(v) => { setAiOnly(v); if (v) { setScrapeWiki(false); setScrapeMusicbrainz(false); setScrapeTmvdb(false); setAiAuto(false); } }} />
          </div>

          {/* Normalize */}
          <label className="flex items-center gap-3 text-sm text-text-secondary cursor-pointer">
            <input
              type="checkbox"
              checked={normalize}
              onChange={(e) => setNormalize(e.target.checked)}
              className="h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent"
            />
            Normalise audio after download
            <Tooltip content="Apply loudness normalisation (EBU R128) to ensure consistent volume.">
              <span><Info size={12} className="text-text-muted" /></span>
            </Tooltip>
          </label>

          {/* Submit */}
          <button onClick={handleSubmit} disabled={isPending} className="btn-primary w-full">
            <Download size={16} className="mr-1" />
            {isPending ? "Importing..." : `Import ${itemCount} Video${itemCount !== 1 ? "s" : ""}`}
          </button>
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
