import { useState, useMemo, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  FolderOpen, Search, ArrowRight, ArrowLeft, Play, Check, X,
  FileText, Image, Music, AlertTriangle, Filter, ChevronDown, ChevronUp,
  SkipForward, RefreshCw, Copy, Clock, Star,
} from "lucide-react";
import { Tooltip } from "@/components/Tooltip";
import { useScanLibraryImport, useStartLibraryImport, usePreviewRegex, useSettings, useExistingDetails } from "@/hooks/queries";
import { useToast } from "@/components/Toast";
import { settingsApi } from "@/lib/api";
import type {
  LibraryImportScannedItem, LibraryImportOptions, LibraryImportScanRequest,
  DuplicateAction, ExistingVideoDetail,
} from "@/types";

/* ── Helpers ── */

function getBoolSetting(settings: { key: string; value: string }[] | undefined, key: string, fallback: boolean): boolean {
  const row = settings?.find((s) => s.key === key);
  if (!row) return fallback;
  return row.value.toLowerCase() === "true";
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

const STEPS = ["Source", "Options", "Preview", "Import"] as const;
type Step = (typeof STEPS)[number];

/* ── Default options ── */

const DEFAULT_OPTIONS: LibraryImportOptions = {
  mode: "simple",
  file_handling: "copy",
  custom_destination: null,
  normalize_audio: false,
  find_source_video: false,
  source_match_duration: true,
  source_match_min_confidence: 0.6,
  review_mode: "basic",
  critical_fields: [],
  confidence_threshold: 0.8,
  custom_regex: null,
  scrape_wikipedia: true,
  scrape_musicbrainz: false,
  scrape_tmvdb: false,
  ai_auto_analyse: false,
  ai_auto_fallback: false,
};

async function openDirectoryPicker(onSelect: (path: string) => void) {
  try {
    const resp = await settingsApi.browseDirectories();
    if (resp.path) onSelect(resp.path);
  } catch {
    // user cancelled or error — ignore
  }
}

/* ── Main Component ── */

export function ImportLibraryPage() {
  const { toast } = useToast();
  const navigate = useNavigate();

  const [step, setStep] = useState<Step>("Source");
  const [directory, setDirectory] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [customRegex, setCustomRegex] = useState("");
  const [options, setOptions] = useState<LibraryImportOptions>(DEFAULT_OPTIONS);
  const [scanResults, setScanResults] = useState<LibraryImportScannedItem[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [duplicateActions, setDuplicateActions] = useState<Record<string, DuplicateAction>>({});
  const [existingDetails, setExistingDetails] = useState<Record<number, ExistingVideoDetail>>({});

  const scanMutation = useScanLibraryImport();
  const startMutation = useStartLibraryImport();
  const regexMutation = usePreviewRegex();
  const existingDetailsMutation = useExistingDetails();
  const { data: settings } = useSettings();

  // Initialize metadata source options from global import defaults
  useEffect(() => {
    if (!settings) return;
    setOptions((prev) => ({
      ...prev,
      normalize_audio: getBoolSetting(settings, "auto_normalize_on_import", true),
      scrape_wikipedia: getBoolSetting(settings, "import_scrape_wikipedia", true),
      scrape_musicbrainz: getBoolSetting(settings, "import_scrape_musicbrainz", false),
      ai_auto_analyse: getBoolSetting(settings, "import_ai_auto", false),
      ai_auto_fallback: getBoolSetting(settings, "import_ai_only", false),
      find_source_video: getBoolSetting(settings, "import_find_source_video", false),
    }));
  }, [settings]);

  const stepIndex = STEPS.indexOf(step);

  /* ── Scan ── */
  const handleScan = () => {
    if (!directory.trim()) {
      toast({ type: "error", title: "Please enter a directory path" });
      return;
    }
    const req: LibraryImportScanRequest = {
      directory: directory.trim(),
      recursive,
      custom_regex: customRegex.trim() || null,
    };
    scanMutation.mutate(req, {
      onSuccess: (data) => {
        setScanResults(data.items);
        // Auto-select in_place when scanning inside the library directory
        if (data.scan_is_library) {
          setOptions((prev) => ({ ...prev, file_handling: "in_place" as const }));
        }
        // Select all new items by default
        const newItems = new Set(
          data.items.filter((i) => !i.already_exists).map((i) => i.file_path),
        );
        setSelected(newItems);
        setDuplicateActions({});
        // Fetch existing video details for comparison
        const existingIds = data.items
          .filter((i) => i.already_exists && i.existing_video_id)
          .map((i) => i.existing_video_id!);
        if (existingIds.length > 0) {
          existingDetailsMutation.mutate(
            { video_ids: [...new Set(existingIds)] },
            { onSuccess: (resp) => setExistingDetails(resp.videos) },
          );
        }
        toast({
          type: "success",
          title: `Found ${data.total_found} videos (${data.new_items} new)`,
        });
        setStep("Options");
      },
      onError: (err: unknown) => {
        const msg = err instanceof Error ? err.message : "Scan failed";
        toast({ type: "error", title: msg });
      },
    });
  };

  /* ── Start import ── */
  const handleStart = () => {
    // Include selected new items + existing items with overwrite/keep_both/review_later actions
    // skip items are excluded entirely; review_later items are sent so the backend
    // can mark existing library items for review (the pipeline will skip the actual import)
    const newPaths = [...selected];
    const dupPaths = Object.entries(duplicateActions)
      .filter(([, a]) => a.action !== "skip")
      .map(([fp]) => fp);
    const items = [...new Set([...newPaths, ...dupPaths])];
    if (!items.length) {
      toast({ type: "error", title: "No items selected" });
      return;
    }
    startMutation.mutate(
      { directory, items, options, duplicate_actions: duplicateActions },
      {
        onSuccess: (data) => {
          toast({
            type: "success",
            title: `Import started: ${data.total_items} videos (Job #${data.job_id})`,
          });
          navigate("/queue");
        },
        onError: (err: unknown) => {
          const msg = (err as any)?.response?.data?.detail ?? (err instanceof Error ? err.message : "Import failed to start");
          toast({ type: "error", title: String(msg) });
        },
      },
    );
  };

  /* ── Regex preview ── */
  const handleRegexPreview = () => {
    if (!customRegex.trim()) return;
    const sampleFilenames = scanResults.length > 0
      ? scanResults.slice(0, 10).map((i) => i.filename)
      : ["Artist - Title [1080p].mp4", "Some Band - Great Song.mp4"];

    regexMutation.mutate(
      { pattern: customRegex, filenames: sampleFilenames },
      {
        onSuccess: (data) => {
          toast({
            type: "info",
            title: `Regex matched ${data.match_count}/${data.total} files`,
          });
        },
      },
    );
  };

  const newItems = useMemo(() => scanResults.filter((i) => !i.already_exists), [scanResults]);
  const existingItems = useMemo(() => scanResults.filter((i) => i.already_exists), [scanResults]);

  return (
    <div className="p-4 md:p-6 max-w-4xl">
      <h1 className="text-2xl font-bold text-text-primary mb-2">Import Library</h1>
      <p className="text-sm text-text-muted mb-6">
        Import an existing video library from a directory on disk.
      </p>

      {/* Step indicator */}
      <div className="flex items-center gap-2 mb-8">
        {STEPS.map((s, i) => (
          <div key={s} className="flex items-center gap-2">
            <button
              onClick={() => i <= stepIndex && setStep(s)}
              disabled={i > stepIndex}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                s === step
                  ? "bg-accent text-white"
                  : i < stepIndex
                    ? "bg-accent/20 text-accent cursor-pointer hover:bg-accent/30"
                    : "bg-surface-light text-text-muted"
              }`}
            >
              {i < stepIndex ? <Check size={12} /> : <span>{i + 1}</span>}
              {s}
            </button>
            {i < STEPS.length - 1 && (
              <div className={`w-8 h-px ${i < stepIndex ? "bg-accent" : "bg-surface-light"}`} />
            )}
          </div>
        ))}
      </div>

      {/* Step content */}
      {step === "Source" && (
        <SourceStep
          directory={directory}
          setDirectory={setDirectory}
          recursive={recursive}
          setRecursive={setRecursive}
          customRegex={customRegex}
          setCustomRegex={setCustomRegex}
          onScan={handleScan}
          isScanning={scanMutation.isPending}
          onRegexPreview={handleRegexPreview}
          regexResults={regexMutation.data}
        />
      )}

      {step === "Options" && (
        <OptionsStep
          options={options}
          setOptions={setOptions}
          onNext={() => setStep("Preview")}
          onBack={() => setStep("Source")}
        />
      )}

      {step === "Preview" && (
        <PreviewStep
          items={scanResults}
          newItems={newItems}
          existingItems={existingItems}
          selected={selected}
          setSelected={setSelected}
          options={options}
          duplicateActions={duplicateActions}
          setDuplicateActions={setDuplicateActions}
          existingDetails={existingDetails}
          onNext={() => setStep("Import")}
          onBack={() => setStep("Options")}
        />
      )}

      {step === "Import" && (
        <ImportStep
          selectedCount={selected.size + Object.values(duplicateActions).filter(a => a.action !== "skip").length}
          options={options}
          onStart={handleStart}
          onBack={() => setStep("Preview")}
          isStarting={startMutation.isPending}
          duplicateActions={duplicateActions}
        />
      )}
    </div>
  );
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Step 1: Source Directory
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

function SourceStep({
  directory, setDirectory, recursive, setRecursive,
  customRegex, setCustomRegex, onScan, isScanning,
  onRegexPreview, regexResults,
}: {
  directory: string;
  setDirectory: (v: string) => void;
  recursive: boolean;
  setRecursive: (v: boolean) => void;
  customRegex: string;
  setCustomRegex: (v: string) => void;
  onScan: () => void;
  isScanning: boolean;
  onRegexPreview: () => void;
  regexResults?: { results: { filename: string; matched: boolean; artist?: string | null; title?: string | null }[]; match_count: number; total: number } | null;
}) {
  return (
    <div className="space-y-6">
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary">
          Source Directory
        </h2>

        <div>
          <label className="text-sm font-medium text-text-primary block mb-1">
            Directory Path
          </label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <FolderOpen size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
              <input
                type="text"
                value={directory}
                onChange={(e) => setDirectory(e.target.value)}
                placeholder="V:\Music Videos"
                className="input-field w-full pl-10 text-sm"
                onKeyDown={(e) => e.key === "Enter" && onScan()}
              />
            </div>
            <button
              type="button"
              onClick={() => openDirectoryPicker(setDirectory)}
              className="btn-secondary btn-sm flex items-center gap-1.5"
            >
              <FolderOpen size={14} />
              Browse
            </button>
          </div>
          <p className="text-xs text-text-muted mt-1">
            Enter the full path to the directory containing your music videos and NFO files.
          </p>
        </div>

        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={recursive}
            onChange={(e) => setRecursive(e.target.checked)}
            className="accent-accent"
          />
          <span className="text-sm text-text-primary">Scan subdirectories recursively</span>
        </label>
      </div>

      {/* Custom regex section */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary">
          Custom Filename Pattern (Optional)
        </h2>
        <p className="text-xs text-text-muted">
          For files without NFOs, provide a regex pattern with named groups to extract metadata.
          Use groups like <code className="text-accent">(?P&lt;artist&gt;...)</code>,{" "}
          <code className="text-accent">(?P&lt;title&gt;...)</code>,{" "}
          <code className="text-accent">(?P&lt;year&gt;...)</code>,{" "}
          <code className="text-accent">(?P&lt;resolution&gt;...)</code>.
        </p>
        <div className="flex gap-2">
          <input
            type="text"
            value={customRegex}
            onChange={(e) => setCustomRegex(e.target.value)}
            placeholder="(?P<artist>.+?) - (?P<title>.+?)(?:\s*\[(?P<resolution>\d+p)\])?\.\w+$"
            className="input-field flex-1 text-sm font-mono"
          />
          <button
            onClick={onRegexPreview}
            disabled={!customRegex.trim()}
            className="btn-secondary btn-sm"
          >
            Test
          </button>
        </div>

        {regexResults && regexResults.results.length > 0 && (
          <div className="text-xs space-y-1 mt-2">
            <p className="text-text-muted">
              Matched {regexResults.match_count}/{regexResults.total} files:
            </p>
            <div className="max-h-32 overflow-y-auto space-y-0.5">
              {regexResults.results.map((r, i) => (
                <div key={i} className={`flex items-center gap-2 ${r.matched ? "text-green-400" : "text-text-muted"}`}>
                  {r.matched ? <Check size={12} /> : <X size={12} />}
                  <span className="font-mono truncate">{r.filename}</span>
                  {r.matched && r.artist && (
                    <span className="text-accent ml-auto shrink-0">
                      {r.artist} — {r.title}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="flex justify-end">
        <button
          onClick={onScan}
          disabled={isScanning || !directory.trim()}
          className="btn-primary flex items-center gap-2"
        >
          {isScanning ? (
            <>
              <Search size={16} className="animate-spin" />
              Scanning…
            </>
          ) : (
            <>
              <ArrowRight size={16} />
              Next
            </>
          )}
        </button>
      </div>
    </div>
  );
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Step 2: Import Options
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

function OptionsStep({
  options, setOptions, onNext, onBack,
}: {
  options: LibraryImportOptions;
  setOptions: (o: LibraryImportOptions) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  const update = (partial: Partial<LibraryImportOptions>) =>
    setOptions({ ...options, ...partial });

  return (
    <div className="space-y-6">
      {/* Import Mode */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary">
          Import Mode
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <button
            onClick={() => update({ mode: "simple" })}
            className={`p-4 rounded-lg border text-left transition-colors ${
              options.mode === "simple"
                ? "border-accent bg-accent/10"
                : "border-white/10 bg-surface-light hover:border-white/20"
            }`}
          >
            <div className="font-medium text-text-primary text-sm">Simple Import</div>
            <p className="text-xs text-text-muted mt-1">
              Import as-is using NFO data and filenames. No external scraping.
            </p>
          </button>
          <button
            onClick={() => update({ mode: "advanced" })}
            className={`p-4 rounded-lg border text-left transition-colors ${
              options.mode === "advanced"
                ? "border-accent bg-accent/10"
                : "border-white/10 bg-surface-light hover:border-white/20"
            }`}
          >
            <div className="font-medium text-text-primary text-sm">Advanced Import</div>
            <p className="text-xs text-text-muted mt-1">
              Full pipeline: Wikipedia, MusicBrainz, entity resolution, Kodi export.
            </p>
          </button>
        </div>
      </div>

      {/* Metadata Sources — only shown for advanced mode */}
      {options.mode === "advanced" && (
        <div className="card space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary">
            Metadata Sources
          </h2>
          <ToggleOption
            checked={options.scrape_wikipedia}
            onChange={(v) => update({ scrape_wikipedia: v, ...(v ? { scrape_musicbrainz: false, ai_auto_analyse: false, ai_auto_fallback: false } : {}) })}
            label="Scrape Wikipedia"
            description="Fetch metadata from Wikipedia articles."
          />
          <ToggleOption
            checked={options.scrape_musicbrainz}
            onChange={(v) => update({ scrape_musicbrainz: v, ...(v ? { scrape_wikipedia: false, ai_auto_analyse: false, ai_auto_fallback: false } : {}) })}
            label="Scrape MusicBrainz"
            description="Fetch metadata from MusicBrainz database."
          />
          <ToggleOption
            checked={false}
            onChange={() => {}}
            label="Retrieve from TMVDB"
            description="Look up metadata from The Music Video DB community database. (Coming soon)"
            disabled
          />
          <ToggleOption
            checked={options.ai_auto_analyse}
            onChange={(v) => update({ ai_auto_analyse: v, ...(v ? { scrape_wikipedia: false, scrape_musicbrainz: false, ai_auto_fallback: false } : {}) })}
            label="AI Auto"
            description="Full AI enrichment (includes scrapers). Uses AI tokens."
          />
          <ToggleOption
            checked={options.ai_auto_fallback}
            onChange={(v) => update({ ai_auto_fallback: v, ...(v ? { scrape_wikipedia: false, scrape_musicbrainz: false, ai_auto_analyse: false } : {}) })}
            label="AI Only"
            description="AI enrichment only (no external scrapers). Uses AI tokens."
          />
          <p className="text-xs text-text-muted">
            Select one metadata source. Each option is exclusive.
          </p>
        </div>
      )}

      {/* File Handling */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary">
          File Handling
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {([
            { key: "in_place", label: "Keep in Place", desc: "Register videos where they are. No files are copied or moved." },
            { key: "copy", label: "Copy", desc: "Copy videos to the default library directory. Originals stay in place." },
            { key: "move", label: "Move", desc: "Move videos to the default library directory. Originals are removed." },
            { key: "copy_to", label: "Copy To", desc: "Copy videos to a custom destination directory. Originals stay in place." },
            { key: "move_to", label: "Move To", desc: "Move videos to a custom destination directory. Originals are removed." },
          ] as const).map(({ key, label, desc }) => (
            <button
              key={key}
              onClick={() => update({ file_handling: key, ...(key === "copy" || key === "move" || key === "in_place" ? { custom_destination: null } : {}) })}
              className={`p-4 rounded-lg border text-left transition-colors ${
                options.file_handling === key
                  ? "border-accent bg-accent/10"
                  : "border-white/10 bg-surface-light hover:border-white/20"
              }`}
            >
              <div className="font-medium text-text-primary text-sm">{label}</div>
              <p className="text-xs text-text-muted mt-1">{desc}</p>
            </button>
          ))}
        </div>

        {(options.file_handling === "copy_to" || options.file_handling === "move_to") && (
          <div>
            <label className="text-sm font-medium text-text-primary block mb-1">
              Destination Directory
            </label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <FolderOpen size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
                <input
                  type="text"
                  value={options.custom_destination || ""}
                  onChange={(e) => update({ custom_destination: e.target.value || null })}
                  placeholder="D:\My Music Videos"
                  className="input-field w-full pl-10 text-sm"
                />
              </div>
              <button
                type="button"
                onClick={() => openDirectoryPicker((path) => update({ custom_destination: path }))}
                className="btn-secondary btn-sm flex items-center gap-1.5"
              >
                <FolderOpen size={14} />
                Browse
              </button>
            </div>
            <p className="text-xs text-text-muted mt-1">
              This directory will be registered as an additional source directory.
            </p>
          </div>
        )}

        <label className="flex items-center gap-3 text-sm text-text-secondary cursor-pointer">
          <input
            type="checkbox"
            checked={options.normalize_audio}
            onChange={(e) => update({ normalize_audio: e.target.checked })}
            className="h-4 w-4 rounded border-surface-border bg-surface text-accent focus:ring-accent"
          />
          <div>
            <div className="text-sm font-medium text-text-primary">Volume Normalise</div>
            <p className="text-xs text-text-muted mt-0.5">Normalise audio levels to -14 LUFS on import.</p>
          </div>
        </label>
      </div>

      {/* YouTube Source Matching */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary">
          YouTube Source Matching
        </h2>
        <ToggleOption
          checked={options.find_source_video}
          onChange={(v) => update({ find_source_video: v })}
          label="Find Source Video"
          description="Search YouTube for the original source video and link it. Videos that already have a YouTube link are automatically skipped."
        />
        {options.find_source_video && (
          <div className="ml-6 space-y-3 border-l-2 border-white/10 pl-4">
            <ToggleOption
              checked={options.source_match_duration}
              onChange={(v) => update({ source_match_duration: v })}
              label="Duration Matching"
              description="Require similar video duration when matching."
            />
            <div>
              <label className="text-sm text-text-primary block mb-1">
                Minimum Confidence: {(options.source_match_min_confidence * 100).toFixed(0)}%
              </label>
              <input
                type="range"
                min={0.3}
                max={0.95}
                step={0.05}
                value={options.source_match_min_confidence}
                onChange={(e) =>
                  update({ source_match_min_confidence: parseFloat(e.target.value) })
                }
                className="w-full accent-accent"
              />
            </div>
          </div>
        )}
      </div>

      {/* Existing Data Handling */}
      <div className="card space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary">
          Existing Data Handling
        </h2>
        <p className="text-xs text-text-muted">
          Controls how videos with existing Playarr metadata (XML files) are handled during import.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {([
            { key: "skip" as const, label: "Rescan All", desc: "Ignore existing Playarr XMLs and rescan everything from scratch using the import settings above — as if each video were freshly downloaded via URL." },
            { key: "basic" as const, label: "Trust Existing", desc: "Import videos as if freshly downloaded (renaming, organizing, scraping) but skip videos that already have a valid Playarr XML — their metadata is kept as-is." },
            { key: "advanced" as const, label: "Trust & Review", desc: "Same as Trust Existing, but videos where the scraper returns low-confidence results are tagged for review and sent to the review queue with details of what triggered the low accuracy." },
          ]).map(({ key, label, desc }) => (
            <button
              key={key}
              onClick={() => update({ review_mode: key })}
              className={`p-3 rounded-lg border text-left transition-colors ${
                options.review_mode === key
                  ? "border-accent bg-accent/10"
                  : "border-white/10 bg-surface-light hover:border-white/20"
              }`}
            >
              <div className="font-medium text-text-primary text-sm">{label}</div>
              <p className="text-xs text-text-muted mt-1">{desc}</p>
            </button>
          ))}
        </div>

        {options.review_mode === "advanced" && (
          <div className="ml-0 space-y-3 border-l-2 border-white/10 pl-4 mt-3">
            <div>
              <label className="text-sm text-text-primary block mb-1">
                Confidence Threshold: {(options.confidence_threshold * 100).toFixed(0)}%
              </label>
              <input
                type="range"
                min={0.5}
                max={0.95}
                step={0.05}
                value={options.confidence_threshold}
                onChange={(e) =>
                  update({ confidence_threshold: parseFloat(e.target.value) })
                }
                className="w-full accent-accent"
              />
              <p className="text-xs text-text-muted mt-1">
                Items below this threshold will be sent to the review queue.
              </p>
            </div>
            <div>
              <label className="text-sm text-text-primary block mb-2">Critical Fields</label>
              <div className="flex flex-wrap gap-2">
                {["year", "album", "artist"].map((field) => (
                  <label
                    key={field}
                    className="flex items-center gap-1.5 cursor-pointer select-none"
                  >
                    <input
                      type="checkbox"
                      checked={options.critical_fields.includes(field)}
                      onChange={(e) => {
                        const next = e.target.checked
                          ? [...options.critical_fields, field]
                          : options.critical_fields.filter((f) => f !== field);
                        update({ critical_fields: next });
                      }}
                      className="accent-accent"
                    />
                    <span className="text-sm text-text-primary capitalize">{field}</span>
                  </label>
                ))}
              </div>
              <p className="text-xs text-text-muted mt-1">
                If scraped data disagrees with NFO data on critical fields, send to review.
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Navigation */}
      <div className="flex justify-between">
        <button onClick={onBack} className="btn-secondary flex items-center gap-2">
          <ArrowLeft size={16} /> Back
        </button>
        <button onClick={onNext} className="btn-primary flex items-center gap-2">
          Preview <ArrowRight size={16} />
        </button>
      </div>
    </div>
  );
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Step 3: Preview Scan Results
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

const VERSION_TYPES = ["alternate", "cover", "live", "acoustic", "remix", "instrumental"] as const;

function PreviewStep({
  items, newItems, existingItems, selected, setSelected, options, onNext, onBack,
  duplicateActions, setDuplicateActions, existingDetails,
}: {
  items: LibraryImportScannedItem[];
  newItems: LibraryImportScannedItem[];
  existingItems: LibraryImportScannedItem[];
  selected: Set<string>;
  setSelected: (s: Set<string>) => void;
  options: LibraryImportOptions;
  onNext: () => void;
  onBack: () => void;
  duplicateActions: Record<string, DuplicateAction>;
  setDuplicateActions: (a: Record<string, DuplicateAction>) => void;
  existingDetails: Record<number, ExistingVideoDetail>;
}) {
  const [filter, setFilter] = useState<"all" | "nfo" | "filename" | "no-meta">("all");
  const [expandedDups, setExpandedDups] = useState<Set<string>>(new Set());

  const filteredNew = useMemo(() => {
    if (filter === "all") return newItems;
    if (filter === "nfo") return newItems.filter((i) => i.has_nfo);
    if (filter === "filename") return newItems.filter((i) => !i.has_nfo && i.artist);
    if (filter === "no-meta") return newItems.filter((i) => !i.has_nfo && !i.artist);
    return newItems;
  }, [newItems, filter]);

  const toggleAll = () => {
    if (selected.size === filteredNew.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filteredNew.map((i) => i.file_path)));
    }
  };

  const toggleItem = (path: string) => {
    const next = new Set(selected);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    setSelected(next);
  };

  const setDupAction = useCallback((filePath: string, action: DuplicateAction) => {
    setDuplicateActions({ ...duplicateActions, [filePath]: action });
  }, [duplicateActions, setDuplicateActions]);

  const setAllDupAction = useCallback((action: DuplicateAction["action"]) => {
    const updated = { ...duplicateActions };
    for (const item of existingItems) {
      updated[item.file_path] = { action };
    }
    setDuplicateActions(updated);
  }, [duplicateActions, existingItems, setDuplicateActions]);

  const toggleDupExpanded = (fp: string) => {
    const next = new Set(expandedDups);
    if (next.has(fp)) next.delete(fp);
    else next.add(fp);
    setExpandedDups(next);
  };

  // Count duplicate actions
  const dupCounts = useMemo(() => {
    const counts = { skip: 0, overwrite: 0, keep_both: 0, review_later: 0, unresolved: 0 };
    for (const item of existingItems) {
      const a = duplicateActions[item.file_path];
      if (!a) counts.unresolved++;
      else counts[a.action]++;
    }
    return counts;
  }, [existingItems, duplicateActions]);

  return (
    <div className="space-y-6">
      {/* Summary */}
      <div className="card">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-center">
          <div>
            <div className="text-2xl font-bold text-text-primary">{items.length}</div>
            <div className="text-xs text-text-muted">Total Found</div>
          </div>
          <div>
            <div className="text-2xl font-bold text-green-400">{newItems.length}</div>
            <div className="text-xs text-text-muted">New</div>
          </div>
          <div>
            <div className="text-2xl font-bold text-yellow-400">{existingItems.length}</div>
            <div className="text-xs text-text-muted">Already Imported</div>
          </div>
          <div>
            <div className="text-2xl font-bold text-accent">{selected.size}</div>
            <div className="text-xs text-text-muted">Selected</div>
          </div>
        </div>
      </div>

      {/* Filter + select all */}
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <div className="flex items-center gap-2">
          <Filter size={14} className="text-text-muted" />
          <div className="flex rounded-lg overflow-hidden border border-white/10">
            {(["all", "nfo", "filename", "no-meta"] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1 text-xs font-medium transition-colors ${
                  filter === f
                    ? "bg-accent text-white"
                    : "bg-surface-light text-text-muted hover:text-text-primary"
                }`}
              >
                {f === "all" ? "All" : f === "nfo" ? "Has NFO" : f === "filename" ? "Filename Only" : "No Metadata"}
              </button>
            ))}
          </div>
        </div>
        <label className="flex items-center gap-2 cursor-pointer select-none ml-auto">
          <input
            type="checkbox"
            checked={selected.size === filteredNew.length && filteredNew.length > 0}
            onChange={toggleAll}
            className="accent-accent"
          />
          <span className="text-xs text-text-muted">Select all ({filteredNew.length})</span>
        </label>
      </div>

      {/* New items list */}
      <div className="card p-0">
        <div className="max-h-96 overflow-y-auto divide-y divide-white/5">
          {filteredNew.length === 0 ? (
            <div className="p-8 text-center text-text-muted text-sm">
              No items match the current filter.
            </div>
          ) : (
            filteredNew.map((item) => (
              <ImportItemRow
                key={item.file_path}
                item={item}
                isSelected={selected.has(item.file_path)}
                onToggle={() => toggleItem(item.file_path)}
              />
            ))
          )}
        </div>
      </div>

      {/* Existing items — duplicate review section */}
      {existingItems.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-yellow-400 flex items-center gap-2">
              <AlertTriangle size={14} />
              {existingItems.length} Already in Library — Review Duplicates
            </h3>
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-text-muted mr-1">Set all:</span>
              <button onClick={() => setAllDupAction("skip")} className="px-2 py-0.5 text-xs rounded bg-surface-light hover:bg-white/10 text-text-muted transition-colors">Skip</button>
              <button onClick={() => setAllDupAction("overwrite")} className="px-2 py-0.5 text-xs rounded bg-surface-light hover:bg-white/10 text-text-muted transition-colors">Overwrite</button>
              <button onClick={() => setAllDupAction("review_later")} className="px-2 py-0.5 text-xs rounded bg-surface-light hover:bg-white/10 text-text-muted transition-colors">Review</button>
            </div>
          </div>

          {dupCounts.unresolved > 0 && (
            <div className="text-xs text-yellow-400/80 bg-yellow-400/5 border border-yellow-400/10 rounded px-3 py-1.5">
              {dupCounts.unresolved} item{dupCounts.unresolved !== 1 ? "s" : ""} still need a decision. Unresolved items will be skipped.
            </div>
          )}

          <div className="space-y-2">
            {existingItems.map((item) => {
              const existing = item.existing_video_id ? existingDetails[item.existing_video_id] : null;
              const action = duplicateActions[item.file_path];
              const isExpanded = expandedDups.has(item.file_path);

              return (
                <DuplicateComparisonRow
                  key={item.file_path}
                  item={item}
                  existing={existing}
                  action={action}
                  isExpanded={isExpanded}
                  onToggleExpand={() => toggleDupExpanded(item.file_path)}
                  onSetAction={(a) => setDupAction(item.file_path, a)}
                />
              );
            })}
          </div>

          {/* Duplicate action summary */}
          <div className="card bg-surface-light/50">
            <div className="flex items-center gap-4 text-xs text-text-muted">
              <span>Duplicate actions:</span>
              {dupCounts.skip > 0 && <span className="text-text-muted"><SkipForward size={12} className="inline mr-0.5" />{dupCounts.skip} skip</span>}
              {dupCounts.overwrite > 0 && <span className="text-orange-400"><RefreshCw size={12} className="inline mr-0.5" />{dupCounts.overwrite} overwrite</span>}
              {dupCounts.keep_both > 0 && <span className="text-blue-400"><Copy size={12} className="inline mr-0.5" />{dupCounts.keep_both} keep both</span>}
              {dupCounts.review_later > 0 && <span className="text-purple-400"><Clock size={12} className="inline mr-0.5" />{dupCounts.review_later} review later</span>}
              {dupCounts.unresolved > 0 && <span className="text-yellow-400">{dupCounts.unresolved} unresolved</span>}
            </div>
          </div>
        </div>
      )}

      {/* Import mode summary */}
      <div className="card bg-surface-light/50">
        <div className="text-xs text-text-muted space-y-1">
          <div>
            <strong>Mode:</strong> {options.mode === "simple" ? "Simple (no scraping)" : "Advanced (full pipeline)"}
          </div>
          <div>
            <strong>Files:</strong> {{
              in_place: "Keep in place",
              copy: "Copy to library",
              move: "Move to library",
              copy_to: `Copy to ${options.custom_destination || "custom dir"}`,
              move_to: `Move to ${options.custom_destination || "custom dir"}`,
            }[options.file_handling]}
          </div>
          {options.normalize_audio && <div><strong>Normalise:</strong> Yes (-14 LUFS)</div>}
          {options.find_source_video && <div><strong>YouTube matching:</strong> Enabled</div>}
          <div>
            <strong>Review:</strong>{" "}
            {options.review_mode === "skip" ? "Auto-approve all" :
             options.review_mode === "basic" ? "All to review queue" :
             `Auto-approve above ${(options.confidence_threshold * 100).toFixed(0)}%`}
          </div>
        </div>
      </div>

      {/* Navigation */}
      <div className="flex justify-between">
        <button onClick={onBack} className="btn-secondary flex items-center gap-2">
          <ArrowLeft size={16} /> Back
        </button>
        <button
          onClick={onNext}
          disabled={selected.size === 0 && Object.values(duplicateActions).filter(a => a.action !== "skip").length === 0}
          className="btn-primary flex items-center gap-2"
        >
          Continue ({selected.size + Object.values(duplicateActions).filter(a => a.action !== "skip").length} selected) <ArrowRight size={16} />
        </button>
      </div>
    </div>
  );
}

/* ── Duplicate comparison row ── */

function DuplicateComparisonRow({
  item, existing, action, isExpanded, onToggleExpand, onSetAction,
}: {
  item: LibraryImportScannedItem;
  existing: ExistingVideoDetail | null;
  action: DuplicateAction | undefined;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onSetAction: (a: DuplicateAction) => void;
}) {
  const currentAction = action?.action;

  const actionStyles: Record<string, string> = {
    skip: "border-white/10",
    overwrite: "border-orange-400/30 bg-orange-400/5",
    keep_both: "border-blue-400/30 bg-blue-400/5",
    review_later: "border-purple-400/30 bg-purple-400/5",
  };

  return (
    <div className={`card p-0 border ${currentAction ? actionStyles[currentAction] : "border-yellow-400/20 bg-yellow-400/5"} transition-colors`}>
      {/* Header row */}
      <div className="flex items-center gap-3 p-3">
        <button onClick={onToggleExpand} className="text-text-muted hover:text-text-primary transition-colors">
          {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
        <Music size={14} className="text-text-muted shrink-0" />
        <div className="flex-1 min-w-0">
          <span className="text-sm font-medium text-text-primary truncate block">
            {item.artist || "?"} — {item.title || item.filename}
          </span>
          <div className="flex items-center gap-2 mt-0.5 text-xs text-text-muted">
            {item.resolution && <span className="px-1.5 py-0.5 rounded bg-accent/20 text-accent">{item.resolution}</span>}
            <span>{formatBytes(item.file_size_bytes)}</span>
            {item.album && <span>· {item.album}</span>}
          </div>
        </div>
        {/* Action buttons */}
        <div className="flex items-center gap-1 shrink-0">
          <ActionButton icon={SkipForward} label="Skip" active={currentAction === "skip"} color="text-text-muted" onClick={() => onSetAction({ action: "skip" })} />
          <ActionButton icon={RefreshCw} label="Overwrite" active={currentAction === "overwrite"} color="text-orange-400" onClick={() => onSetAction({ action: "overwrite" })} />
          <ActionButton icon={Copy} label="Keep Both" active={currentAction === "keep_both"} color="text-blue-400" onClick={() => onSetAction({ action: "keep_both", version_type: action?.version_type || "alternate" })} />
          <ActionButton icon={Clock} label="Review Later" active={currentAction === "review_later"} color="text-purple-400" onClick={() => onSetAction({ action: "review_later" })} />
        </div>
      </div>

      {/* Keep-both version type picker */}
      {currentAction === "keep_both" && (
        <div className="px-3 pb-3 flex items-center gap-2 border-t border-white/5 pt-2">
          <span className="text-xs text-text-muted">Import as:</span>
          <div className="flex flex-wrap gap-1">
            {VERSION_TYPES.map((vt) => (
              <button
                key={vt}
                onClick={() => onSetAction({ action: "keep_both", version_type: vt })}
                className={`px-2 py-0.5 text-xs rounded transition-colors capitalize ${
                  action?.version_type === vt
                    ? "bg-blue-400 text-white"
                    : "bg-surface-light text-text-muted hover:bg-white/10"
                }`}
              >
                {vt}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Expanded comparison */}
      {isExpanded && existing && (
        <div className="border-t border-white/5 p-3">
          <div className="grid grid-cols-2 gap-4 text-xs">
            <div>
              <div className="text-text-muted font-semibold mb-2 uppercase tracking-wide" style={{ fontSize: "0.65rem" }}>New File</div>
              <ComparisonFields
                artist={item.artist || "?"}
                title={item.title || item.filename}
                album={item.album}
                year={item.year}
                resolution={item.resolution}
                fileSize={item.file_size_bytes}
                hasPoster={item.has_poster}
                hasThumb={item.has_thumb}
                versionType={null}
                rating={null}
                created={null}
              />
            </div>
            <div>
              <div className="text-text-muted font-semibold mb-2 uppercase tracking-wide" style={{ fontSize: "0.65rem" }}>In Library</div>
              <ComparisonFields
                artist={existing.artist || "?"}
                title={existing.title || "?"}
                album={existing.album}
                year={existing.year}
                resolution={existing.resolution_label}
                fileSize={existing.file_size_bytes}
                hasPoster={existing.has_poster}
                hasThumb={existing.has_thumb}
                versionType={existing.version_type}
                rating={existing.song_rating}
                created={existing.created_at}
              />
            </div>
          </div>
        </div>
      )}

      {isExpanded && !existing && (
        <div className="border-t border-white/5 p-3 text-xs text-text-muted">
          Could not load details for existing library item.
        </div>
      )}
    </div>
  );
}

/* ── Comparison fields ── */

function ComparisonFields({
  artist, title, album, year, resolution, fileSize, hasPoster, hasThumb, versionType, rating, created,
}: {
  artist: string;
  title: string;
  album?: string | null;
  year?: number | null;
  resolution?: string | null;
  fileSize?: number | null;
  hasPoster: boolean;
  hasThumb: boolean;
  versionType?: string | null;
  rating?: number | null;
  created?: string | null;
}) {
  return (
    <div className="space-y-1.5">
      <div><span className="text-text-muted/60">Artist:</span> <span className="text-text-primary">{artist}</span></div>
      <div><span className="text-text-muted/60">Title:</span> <span className="text-text-primary">{title}</span></div>
      {album && <div><span className="text-text-muted/60">Album:</span> <span className="text-text-primary">{album}</span></div>}
      {year && <div><span className="text-text-muted/60">Year:</span> <span className="text-text-primary">{year}</span></div>}
      <div className="flex items-center gap-3">
        {resolution && <span className="px-1.5 py-0.5 rounded bg-accent/20 text-accent">{resolution}</span>}
        {fileSize != null && <span>{formatBytes(fileSize)}</span>}
      </div>
      <div className="flex items-center gap-2">
        {hasPoster && <span className="flex items-center gap-0.5 text-blue-400"><Image size={11} /> Poster</span>}
        {hasThumb && <span className="flex items-center gap-0.5 text-blue-400"><Image size={11} /> Thumb</span>}
        {!hasPoster && !hasThumb && <span className="text-text-muted/40">No artwork</span>}
      </div>
      {versionType && versionType !== "normal" && (
        <div><span className="text-text-muted/60">Version:</span> <span className="text-text-primary capitalize">{versionType}</span></div>
      )}
      {rating != null && (
        <div className="flex items-center gap-1"><Star size={11} className="text-yellow-400" /> <span className="text-text-primary">{rating}/5</span></div>
      )}
      {created && (
        <div><span className="text-text-muted/60">Added:</span> <span className="text-text-primary">{new Date(created).toLocaleDateString()}</span></div>
      )}
    </div>
  );
}

/* ── Action button ── */

function ActionButton({
  icon: Icon, label, active, color, onClick,
}: {
  icon: React.ComponentType<{ size?: number }>;
  label: string;
  active: boolean;
  color: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      className={`flex items-center gap-1 px-2 py-1 text-xs rounded transition-colors ${
        active
          ? `${color} bg-white/10 font-medium`
          : "text-text-muted hover:bg-white/5"
      }`}
    >
      <Icon size={12} />
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}

/* ── Item row in preview ── */

function ImportItemRow({
  item,
  isSelected,
  onToggle,
}: {
  item: LibraryImportScannedItem;
  isSelected: boolean;
  onToggle: () => void;
}) {
  return (
    <label className="flex items-start gap-3 p-3 cursor-pointer hover:bg-white/5 transition-colors">
      <input
        type="checkbox"
        checked={isSelected}
        onChange={onToggle}
        className="accent-accent mt-1"
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <Music size={14} className="text-text-muted shrink-0" />
          <span className="text-sm font-medium text-text-primary truncate">
            {item.artist || "Unknown Artist"} — {item.title || item.filename}
          </span>
          {item.resolution && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-accent/20 text-accent shrink-0">
              {item.resolution}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 mt-1 text-xs text-text-muted">
          {item.album && <span>Album: {item.album}</span>}
          {item.year && <span>Year: {item.year}</span>}
          <span>{formatBytes(item.file_size_bytes)}</span>
          <div className="flex items-center gap-1.5 ml-auto">
            {item.has_nfo && (
              <Tooltip content="This file has an NFO metadata sidecar">
              <span className="flex items-center gap-0.5 text-green-400">
                <FileText size={12} /> NFO
              </span>
              </Tooltip>
            )}
            {item.has_poster && (
              <Tooltip content="This file has a poster image sidecar">
              <span className="flex items-center gap-0.5 text-blue-400">
                <Image size={12} /> Poster
              </span>
              </Tooltip>
            )}
            {!item.has_nfo && !item.artist && (
              <Tooltip content="No metadata was found — filename parsing will be used">
              <span className="flex items-center gap-0.5 text-yellow-400">
                <AlertTriangle size={12} /> No metadata
              </span>
              </Tooltip>
            )}
            <span className="text-text-muted/60">
              via {item.metadata_source}
            </span>
          </div>
        </div>
      </div>
    </label>
  );
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Step 4: Confirm & Start Import
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

function ImportStep({
  selectedCount, options, onStart, onBack, isStarting, duplicateActions,
}: {
  selectedCount: number;
  options: LibraryImportOptions;
  onStart: () => void;
  onBack: () => void;
  isStarting: boolean;
  duplicateActions: Record<string, DuplicateAction>;
}) {
  const dupOverwrite = Object.values(duplicateActions).filter(a => a.action === "overwrite").length;
  const dupKeepBoth = Object.values(duplicateActions).filter(a => a.action === "keep_both").length;
  const dupReview = Object.values(duplicateActions).filter(a => a.action === "review_later").length;

  return (
    <div className="space-y-6">
      <div className="card text-center py-8">
        <Play size={40} className="mx-auto text-accent mb-4" />
        <h2 className="text-xl font-bold text-text-primary mb-2">
          Ready to Import
        </h2>
        <p className="text-text-muted">
          {selectedCount} video{selectedCount !== 1 ? "s" : ""} will be imported using{" "}
          <strong>{options.mode}</strong> mode.
        </p>
        {(dupOverwrite > 0 || dupKeepBoth > 0 || dupReview > 0) && (
          <div className="flex items-center justify-center gap-4 mt-3 text-xs">
            {dupOverwrite > 0 && <span className="text-orange-400"><RefreshCw size={12} className="inline mr-1" />{dupOverwrite} overwrite</span>}
            {dupKeepBoth > 0 && <span className="text-blue-400"><Copy size={12} className="inline mr-1" />{dupKeepBoth} keep both</span>}
            {dupReview > 0 && <span className="text-purple-400"><Clock size={12} className="inline mr-1" />{dupReview} review later</span>}
          </div>
        )}
        <p className="text-xs text-text-muted mt-2">
          Files will be {options.file_handling === "in_place" ? "registered where they are" : options.file_handling.startsWith("move") ? "moved" : "copied"}{options.file_handling !== "in_place" ? ` to ${options.file_handling.endsWith("_to") ? (options.custom_destination || "a custom directory") : "the library directory"}` : ""}.
          {options.normalize_audio && " Audio will be normalised."}
          {options.find_source_video && " YouTube sources will be searched."}
        </p>
      </div>

      <div className="flex justify-between">
        <button onClick={onBack} className="btn-secondary flex items-center gap-2">
          <ArrowLeft size={16} /> Back
        </button>
        <button
          onClick={onStart}
          disabled={isStarting}
          className="btn-primary flex items-center gap-2 text-lg px-6 py-3"
        >
          {isStarting ? (
            <>Starting…</>
          ) : (
            <>
              <Play size={20} />
              Start Import
            </>
          )}
        </button>
      </div>
    </div>
  );
}

/* ── Reusable toggle ── */

function ToggleOption({
  checked, onChange, label, description, disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  description: string;
  disabled?: boolean;
}) {
  return (
    <div className={`flex items-start gap-3 ${disabled ? "opacity-40 cursor-not-allowed" : ""}`}>
      <button
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className={`w-11 h-6 rounded-full transition-colors relative inline-flex items-center shrink-0 mt-0.5 ${
          disabled ? "bg-surface-light cursor-not-allowed" : checked ? "bg-accent" : "bg-surface-light"
        }`}
      >
        <span
          className={`inline-block h-4 w-4 rounded-full bg-white shadow transform transition-transform ${
            checked ? "translate-x-6" : "translate-x-1"
          }`}
        />
      </button>
      <div>
        <div className="text-sm font-medium text-text-primary">{label}</div>
        <p className="text-xs text-text-muted mt-0.5">{description}</p>
      </div>
    </div>
  );
}
