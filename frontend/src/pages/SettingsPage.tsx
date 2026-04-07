import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Save, Database, Plus, X, FolderOpen, ScanLine, HeartPulse, FileText, RefreshCw, ChevronDown, ChevronUp, Info, AlertTriangle, HardDrive, Film, Sparkles, Play, Server, Compass, Download, Power, ScrollText, ExternalLink } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useSettings, useUpdateSetting, useLibraryScan, useLibraryExport } from "@/hooks/queries";
import { settingsApi, statsApi } from "@/lib/api";
import { ErrorState, Skeleton } from "@/components/Feedback";
import { useToast } from "@/components/Toast";
import { Tooltip } from "@/components/Tooltip";
import { CleanLibraryDialog } from "@/components/CleanLibraryDialog";
import { LogViewer } from "@/components/LogViewer";
import { NewVideosSettings } from "@/components/new-videos/NewVideosSettings";
import { AISettingsPanel } from "@/components/AISettingsPanel";
import { useArtworkSettings } from "@/stores/artworkSettingsStore";
import {
  loadExclusions,
  saveExclusions,
  DEFAULT_EXCLUSIONS,
  loadAnimationSettings,
  saveAnimationSettings,
} from "@/hooks/usePartyMode";
import type { PartyModeAnimationSettings } from "@/hooks/usePartyMode";
import type { AppSetting, PartyModeExclusions } from "@/types";

/* ── Setting metadata: group, human label, and description ── */

interface SettingMeta {
  group: string;
  subgroup?: string;
  label: string;
  description: string;
  tooltip?: string;
  options?: { value: string; label: string }[];
}

const SETTING_META: Record<string, SettingMeta> = {
  "library_dir": {
    group: "library",
    label: "Library Directory",
    description: "Primary directory where organised music videos are stored. New imports go here.",
  },
  "library_source_dirs": {
    group: "library",
    label: "Additional Source Directories",
    description: "Extra directories scanned for orphan detection, library imports, and cleanup.",
  },
  "library_naming_pattern": {
    group: "library",
    label: "File Naming Pattern",
    description: "Pattern for naming video files and their containing folders.",
  },
  "library_folder_structure": {
    group: "library",
    label: "Folder Structure",
    description: "How video folders are organised within the library directory.",
  },
  "preferred_container": {
    group: "av",
    subgroup: "video",
    label: "Preferred Container Format",
    description: "Container format for merging downloaded video + audio streams.",
    tooltip:
      "MKV — Best quality. Accepts all codecs without re-encoding, preserving the highest-quality audio (e.g. Opus 251 kbps). Incompatible audio is transcoded on-the-fly during playback.\n\n" +
      "MP4 — Most compatible. Works on nearly all devices and browsers, but only supports AAC/MP3 audio. The downloader will prefer AAC streams (often lower bitrate) or transcode during merge.\n\n" +
      "WebM — Open format. Uses VP8/VP9 video + Opus/Vorbis audio. Good quality but limited device support.\n\n" +
      "AVI / MOV — Legacy. Limited codec support, not recommended.",
    options: [
      { value: "mkv", label: "MKV — Best quality (recommended)" },
      { value: "mp4", label: "MP4 — Most compatible" },
      { value: "webm", label: "WebM — Open format" },
      { value: "avi", label: "AVI (legacy)" },
      { value: "mov", label: "MOV (legacy)" },
    ],
  },
  "transcode_audio_bitrate": {
    group: "av",
    subgroup: "playback",
    label: "Transcode Audio Bitrate (kbps)",
    description: "Audio bitrate used when transcoding incompatible audio on-the-fly during playback.",
    tooltip:
      "When a video has audio your browser can't play natively (e.g. PCM, AC3, DTS), " +
      "Playarr transcodes it to AAC in real-time.\n\n" +
      "128 — Low bandwidth, decent quality.\n" +
      "192 — Good balance of quality and bandwidth.\n" +
      "256 — High quality (default).\n" +
      "320 — Near-transparent, highest bandwidth.",
    options: [
      { value: "128", label: "128 kbps" },
      { value: "192", label: "192 kbps" },
      { value: "256", label: "256 kbps (default)" },
      { value: "320", label: "320 kbps" },
    ],
  },
  "normalization_target_lufs": {
    group: "av",
    subgroup: "audio",
    label: "Target Loudness (LUFS)",
    description: "Integrated loudness target for audio normalisation. Standard streaming is -14 LUFS.",
    tooltip:
      "-14 LUFS — Spotify / Apple Music standard.\n" +
      "-16 LUFS — YouTube default loudness target.\n" +
      "-23 LUFS — EBU R128 broadcast standard.\n\n" +
      "Lower values (e.g. -23) are quieter but more dynamic. Higher values (e.g. -14) are louder with less dynamic range.",
  },
  "normalization_lra": {
    group: "av",
    subgroup: "audio",
    label: "Loudness Range (LRA)",
    description: "Maximum allowed loudness range in LU. Lower values produce more consistent volume.",
    tooltip:
      "Controls how much the volume is allowed to vary within a track.\n\n" +
      "7 LU — Tight, even volume throughout (recommended for music videos).\n" +
      "11 LU — Moderate dynamic range.\n" +
      "20 LU — Wide dynamic range, preserves original dynamics.",
  },
  "normalization_tp": {
    group: "av",
    subgroup: "audio",
    label: "True Peak Maximum (dBTP)",
    description: "Maximum true peak level to prevent clipping. -1.0 to -2.0 is typical.",
    tooltip:
      "Limits the absolute peak level of the audio waveform.\n\n" +
      "-1.0 dBTP — Standard for streaming (Spotify, YouTube).\n" +
      "-2.0 dBTP — Conservative, prevents clipping on all DACs.\n" +
      "0.0 dBTP — Maximum, may clip on some playback systems.\n\n" +
      "Most users should leave this at -1.0.",
  },
  "auto_normalize_on_import": {
    group: "av",
    subgroup: "audio",
    label: "Normalise on Import",
    description: "Automatically normalise audio levels when importing new videos.",
    tooltip:
      "When enabled, every imported video is automatically normalised to the target LUFS level above.\n\n" +
      "This ensures consistent volume when playing videos back-to-back. The original audio is preserved — normalisation is applied via a separate audio stream.",
  },
  "preview_duration_sec": {
    group: "previews",
    label: "Preview Duration (seconds)",
    description: "Length of hover-preview clips generated for library cards.",
    tooltip:
      "How many seconds each hover-preview clip lasts.\n\n" +
      "3–5s — Quick peek, lower storage.\n" +
      "8–10s — Longer preview, higher storage.\n\n" +
      "Previews are generated once per video and cached.",
  },
  "preview_start_percent": {
    group: "previews",
    label: "Preview Start Position (%)",
    description: "Where in the video to start the preview clip, as a percentage of total duration.",
    tooltip:
      "Determines the starting point for preview generation.\n\n" +
      "25% — Skips intros (recommended for music videos).\n" +
      "0% — Start from the very beginning.\n" +
      "50% — Start from the midpoint.",
  },
  "ai_provider": {
    group: "ai",
    label: "AI Provider",
    description: "Service used for generating plot/description summaries. Set to \"none\" to disable.",
  },
  "ai_auto_enrich": {
    group: "ai",
    label: "Auto-Enrich",
    description: "Automatically run AI enrichment after importing videos.",
  },
  "ai_auto_scenes": {
    group: "ai",
    label: "Auto Scene Detection",
    description: "Automatically run AI scene detection on imported videos.",
  },
  "ai_model_selection_mode": {
    group: "ai",
    label: "Model Selection Mode",
    description: "How AI models are selected for tasks (auto or manual).",
  },
  "openai_api_key": {
    group: "ai",
    label: "API Key",
    description: "API key for the active AI provider.",
  },
  "ai_enrichable_fields": {
    group: "ai",
    label: "Enrichable Fields",
    description: "Which metadata fields AI enrichment is allowed to populate.",
  },
  "server.port": {
    group: "server",
    label: "Server Port",
    description: "Network port the Playarr server listens on. Requires a restart to take effect.",
    tooltip:
      "The TCP port the Playarr web server binds to. Default is 6969.\n\n" +
      "After changing, click 'Restart' under Server Management, then access the UI at the new port.",
  },
  "startup_with_system": {
    group: "server",
    label: "Start with Windows",
    description: "Automatically launch Playarr when you log in to Windows.",
    tooltip:
      "Adds Playarr to the Windows startup registry so it launches automatically on login.\n\n" +
      "Uses pythonw.exe (no console window) when available.",
  },
  "startup_delay_seconds": {
    group: "server",
    label: "Startup Delay (seconds)",
    description: "Wait this many seconds before launching the server on system start.",
    tooltip:
      "Useful to let other services (e.g. network, Redis) initialise first.\n\n" +
      "0 — Start immediately.\n" +
      "15–30 — Good for slower machines.\n" +
      "60+ — Wait for heavy startup loads to settle.",
  },
  "auto_open_browser": {
    group: "server",
    label: "Open Browser on Launch",
    description: "Automatically open the Playarr UI in your default browser when the server starts.",
  },
  "minimize_to_tray": {
    group: "server",
    label: "System Tray Icon",
    description: "Show a Playarr icon in the system tray for quick access.",
    tooltip:
      "When enabled, a tray icon appears with Open and Quit actions.\n\n" +
      "Requires pystray + Pillow (included by default). Takes effect on next server start.",
  },
  "startup_duplicate_scan": {
    group: "server",
    label: "Duplicate Scan on Startup",
    description: "Automatically scan for duplicate videos when the server starts.",
    tooltip:
      "Runs a background duplicate detection scan each time Playarr starts.\n\n" +
      "Flagged items appear in the Review Queue under the Duplicates tab.",
  },
  "startup_rename_scan": {
    group: "server",
    label: "Rename Scan on Startup",
    description: "Automatically scan for naming convention mismatches when the server starts.",
    tooltip:
      "Checks all files against the current naming convention each time Playarr starts.\n\n" +
      "Mismatched items appear in the Review Queue under the Renames tab.\n" +
      "Previously dismissed items are skipped.",
  },
  "import_scrape_wikipedia": {
    group: "import",
    label: "Scrape Wikipedia",
    description: "Pre-check Wikipedia scraping when adding or importing videos.",
    tooltip: "When enabled, the Wikipedia toggle will be on by default in the Add Video modal and library import wizard. Can be overridden per-import.",
  },
  "import_scrape_musicbrainz": {
    group: "import",
    label: "Scrape MusicBrainz",
    description: "Pre-check MusicBrainz scraping when adding or importing videos.",
    tooltip: "When enabled, the MusicBrainz toggle will be on by default in the Add Video modal and library import wizard. Can be overridden per-import.",
  },
  "import_ai_auto": {
    group: "import",
    label: "AI Auto (Scrapers + AI)",
    description: "Pre-check full AI enrichment with scrapers when adding or importing videos. Uses AI tokens.",
    tooltip: "Enables AI-guided metadata enrichment by default. AI identifies the track, then scrapes MusicBrainz, Wikipedia, and IMDB automatically. Mutually exclusive with the other scraping modes.",
  },
  "import_ai_only": {
    group: "import",
    label: "AI Only (No Scrapers)",
    description: "Pre-check AI-only enrichment (skips Wikipedia/MusicBrainz) when adding or importing videos. Uses AI tokens.",
    tooltip: "Enables AI-only metadata enrichment by default — no external lookups. Best for rare tracks without MusicBrainz/Wikipedia presence. Mutually exclusive with the other scraping modes.",
  },
  "import_find_source_video": {
    group: "import",
    label: "YouTube Source Matching",
    description: "Pre-check YouTube source matching when adding or importing videos.",
    tooltip: "When enabled, Playarr will search YouTube for the official music video and link it to the library entry. Useful for library-imported videos that don't have a source URL.",
  },
  "import_scrape_tmvdb": {
    group: "import",
    label: "Retrieve from TMVDB",
    description: "Pre-check TMVDB lookup when adding or importing videos.",
  },
  "max_concurrent_downloads": {
    group: "import",
    label: "Max Concurrent Downloads",
    description: "Number of simultaneous downloads during batch/playlist imports (1–16).",
    tooltip:
      "Controls how many videos download in parallel during a batch import.\n\n" +
      "1 — Safest, no risk of rate-limiting.\n" +
      "2–4 — Good balance of speed and stability (default: 4).\n" +
      "8+ — Fast, but may trigger YouTube throttling.\n\n" +
      "Takes effect on the next batch import (no restart needed).",
    options: [
      { value: "1", label: "1 — Serial" },
      { value: "2", label: "2" },
      { value: "4", label: "4 (default)" },
      { value: "8", label: "8" },
      { value: "16", label: "16 — Maximum" },
    ],
  },
  "tmvdb_enabled": {
    group: "tmvdb",
    label: "Enable TMVDB",
    description: "Enable The Music Video DB integration for community metadata sharing.",
    tooltip: "When enabled, Playarr can pull and push metadata to/from The Music Video DB, a community-driven music video metadata database.",
  },
  "tmvdb_api_key": {
    group: "tmvdb",
    label: "API Key",
    description: "Your TMVDB API key. Register at themusicvideodb.org to obtain one.",
    tooltip: "Required for TMVDB integration. Create a free account at themusicvideodb.org and generate an API key from your profile settings.",
  },
  "tmvdb_auto_pull": {
    group: "tmvdb",
    label: "Auto Pull",
    description: "Automatically retrieve metadata from TMVDB during import and rescan.",
    tooltip: "When enabled, Playarr will automatically check TMVDB for metadata during every import and rescan operation. Community-contributed data is merged with local metadata.",
  },
  "tmvdb_auto_push": {
    group: "tmvdb",
    label: "Auto Push",
    description: "Automatically submit verified metadata to TMVDB after successful imports.",
    tooltip: "When enabled, Playarr will contribute your verified metadata back to the TMVDB community database after each successful import. Only locked/confirmed data is pushed.",
  },
};

const GROUP_LABELS: Record<string, string> = {
  library: "Library & Storage",
  av: "Video Settings",
  import: "Import Defaults",
  previews: "Preview Generation",
  ai: "AI / Summaries",
  nowplaying: "Now Playing",
  server: "Server",
  tmvdb: "The Music Video DB",
};

/* ── Top-level tabs that group related setting sections ── */

interface SettingsTab {
  id: string;
  label: string;
  icon: React.ReactNode;
  groups: string[];           // setting groups rendered under this tab
  extras?: string[];          // hard-coded client-side sections (nowplaying, partymode)
}

const SETTINGS_TABS: SettingsTab[] = [
  { id: "library",  label: "Library",  icon: <HardDrive size={16} />, groups: ["library", "import"] },
  { id: "media",    label: "Media",    icon: <Film size={16} />,      groups: ["av", "previews"] },
  { id: "ai",       label: "AI",       icon: <Sparkles size={16} />,  groups: ["ai"] },
  { id: "playback", label: "Playback", icon: <Play size={16} />,      groups: ["nowplaying"], extras: ["nowplaying", "partymode"] },
  { id: "system",   label: "System",   icon: <Server size={16} />,    groups: ["server"], extras: ["system"] },
  { id: "tmvdb",    label: "TMVDB",    icon: <Compass size={16} />,   groups: ["tmvdb"] },
  { id: "discovery", label: "Discovery", icon: <Compass size={16} />,  groups: [], extras: ["newvideos"] },
  { id: "logs",      label: "Logs",      icon: <ScrollText size={16} />, groups: [], extras: ["logviewer"] },
];

export function SettingsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { toast } = useToast();
  const { data: settings, isLoading, isError, refetch } = useSettings();
  const updateMutation = useUpdateSetting();
  const [activeTab, setActiveTab] = useState("library");
  const [dirDefaults, setDirDefaults] = useState<{ library_dir: string } | null>(null);

  useEffect(() => {
    settingsApi.defaults().then(setDirDefaults).catch(() => {});
  }, []);

  if (isLoading) {
    return (
      <div className="p-6 space-y-4">
        <Skeleton className="h-8 w-40" />
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-14 rounded-lg" />
        ))}
      </div>
    );
  }

  if (isError || !settings) {
    return (
      <div className="p-6">
        <ErrorState message="Failed to load settings" onRetry={refetch} />
      </div>
    );
  }

  // Build grouped settings using metadata
  const settingsByKey: Record<string, AppSetting> = {};
  settings.forEach((s) => { settingsByKey[s.key] = s; });

  const groups: Record<string, AppSetting[]> = {};
  for (const key of Object.keys(SETTING_META)) {
    const meta = SETTING_META[key];
    const setting = settingsByKey[key];
    if (!setting) continue;
    if (!groups[meta.group]) groups[meta.group] = [];
    groups[meta.group].push(setting);
  }
  // Catch any settings not in the metadata map
  settings.forEach((s) => {
    if (!SETTING_META[s.key]) {
      if (!groups["other"]) groups["other"] = [];
      groups["other"].push(s);
    }
  });

  const currentTabDef = SETTINGS_TABS.find((t) => t.id === activeTab) ?? SETTINGS_TABS[0];
  const visibleGroups = currentTabDef.groups.filter((g) => groups[g]?.length);

  /* shared helper — renders SettingRow list for a group with import-exclusive logic */
  const settingRows = (items: AppSetting[]) =>
    items.map((s) => (
      <SettingRow
        key={s.key}
        setting={s}
        meta={SETTING_META[s.key]}
        onSave={(value, valueType) => {
          const IMPORT_EXCLUSIVE = [
            "import_scrape_wikipedia",
            "import_scrape_musicbrainz",
            "import_ai_auto",
            "import_ai_only",
          ];
          if (value === "true" && IMPORT_EXCLUSIVE.includes(s.key)) {
            for (const other of IMPORT_EXCLUSIVE) {
              if (other !== s.key) {
                const otherSetting = settingsByKey[other];
                if (otherSetting && otherSetting.value === "true") {
                  updateMutation.mutate(
                    { key: other, value: "false", value_type: otherSetting.value_type },
                  );
                }
              }
            }
          }
          updateMutation.mutate(
            { key: s.key, value, value_type: valueType },
            {
              onSuccess: () => toast({ type: "success", title: `${SETTING_META[s.key]?.label || s.key} saved` }),
              onError: () => toast({ type: "error", title: `Failed to save ${SETTING_META[s.key]?.label || s.key}` }),
            }
          );
        }}
        isPending={updateMutation.isPending}
      />
    ));

  /* ── Render a single group section ── */
  const renderGroup = (group: string) => {
    if (group === "library") {
      return (
        <section key={group}>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
            {GROUP_LABELS[group]}
          </h2>
          <div className="card space-y-5">
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Directories</h4>
              <div className="space-y-4 pl-2 border-l-2 border-border">

            {(() => {
              const libDirSetting = settingsByKey["library_dir"];
              if (!libDirSetting) return null;
              return (
                <DirectoryRow
                  label="Default Directory"
                  description="Primary library root. All newly downloaded videos are saved here."
                  setting={libDirSetting}
                  onSave={(value) => {
                    updateMutation.mutate(
                      { key: "library_dir", value, value_type: libDirSetting.value_type },
                      {
                        onSuccess: () => toast({ type: "success", title: "Library directory saved" }),
                        onError: () => toast({ type: "error", title: "Failed to save library directory" }),
                      }
                    );
                  }}
                  isPending={updateMutation.isPending}
                  defaultValue={dirDefaults?.library_dir}
                />
              );
            })()}

            <SourceDirectoriesEditor
              setting={settingsByKey["library_source_dirs"]}
              onSaved={() => {
                qc.invalidateQueries({ queryKey: ["settings"] });
              }}
            />

            {settingRows(groups[group].filter(s => s.key !== "library_dir" && s.key !== "library_source_dirs" && s.key !== "library_naming_pattern" && s.key !== "library_folder_structure"))}

              </div>
            </div>

            <div className="mt-4">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Import Library</h4>
              <div className="space-y-4 pl-2 border-l-2 border-border">
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-text-muted leading-relaxed">
                    Import an existing music video collection with full metadata scraping, source URL matching, and AI enrichment. Use this for first-time setup or migrating from another system.
                  </p>
                </div>
                <button
                  onClick={() => navigate("/library-import")}
                  className="btn-primary btn-sm flex items-center gap-1.5 shrink-0"
                >
                  <Database size={14} />
                  Import Library
                </button>
              </div>
              </div>
            </div>

            <div className="mt-4">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Naming Convention</h4>
              <div className="space-y-4 pl-2 border-l-2 border-border">
              <NamingConventionEditor
                namingPattern={settingsByKey["library_naming_pattern"]?.value ?? "{artist} - {title} [{quality}]"}
                folderStructure={settingsByKey["library_folder_structure"]?.value ?? "{artist}/{file_folder}"}
                onSave={(key, value) => {
                  updateMutation.mutate(
                    { key, value, value_type: "string" },
                    {
                      onSuccess: () => toast({ type: "success", title: `${key === "library_naming_pattern" ? "Naming pattern" : "Folder structure"} saved` }),
                      onError: () => toast({ type: "error", title: "Failed to save naming setting" }),
                    }
                  );
                }}
                isPending={updateMutation.isPending}
              />
              </div>
            </div>

            <div className="mt-4">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Maintenance</h4>
              <div className="space-y-4 pl-2 border-l-2 border-border">
                <LibraryMaintenanceContent />
              </div>
            </div>
          </div>
        </section>
      );
    }

    if (group === "av") {
      return (
        <section key={group}>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
            {GROUP_LABELS[group]}
          </h2>
          <div className="card space-y-5">
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Video</h4>
              <div className="space-y-4 pl-2 border-l-2 border-border">
                {settingRows(groups[group].filter(s => SETTING_META[s.key]?.subgroup === "video"))}
              </div>
            </div>
            <div className="mt-4">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Audio Normalization</h4>
              <div className="space-y-4 pl-2 border-l-2 border-border">
                {settingRows(groups[group].filter(s => SETTING_META[s.key]?.subgroup === "audio"))}
              </div>
            </div>
            <div className="mt-4">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Playback</h4>
              <div className="space-y-4 pl-2 border-l-2 border-border">
                {settingRows(groups[group].filter(s => SETTING_META[s.key]?.subgroup === "playback"))}
              </div>
            </div>
          </div>
        </section>
      );
    }

    if (group === "ai") {
      return (
        <section key={group}>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
            {GROUP_LABELS[group]}
          </h2>
          <div className="card space-y-5">
            <AISettingsPanel />
            {settingRows(groups[group].filter(s => {
              const managed = ["ai_provider", "ai_auto_enrich", "ai_auto_scenes", "ai_model_selection_mode", "openai_api_key", "ai_enrichable_fields", "ai_source_resolution", "ai_final_review"];
              return !managed.includes(s.key);
            }))}
          </div>
        </section>
      );
    }

    if (group === "import") {
      const scrapingDefaults = groups[group].filter(s => ["import_scrape_wikipedia", "import_scrape_musicbrainz", "import_find_source_video", "import_scrape_tmvdb"].includes(s.key));
      const aiDefaults = groups[group].filter(s => ["import_ai_auto", "import_ai_only"].includes(s.key));
      const downloadSettings = groups[group].filter(s => ["max_concurrent_downloads"].includes(s.key));
      return (
        <section key={group}>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
            {GROUP_LABELS[group]}
          </h2>
          <div className="card space-y-5">
            <p className="text-xs text-text-muted leading-relaxed">
              These settings control which options are pre-checked by default when you add a video by URL or run a library import. They don't affect videos already in your library.
            </p>
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Scraping Defaults</h4>
              <div className="space-y-4 pl-2 border-l-2 border-border">
                {settingRows(scrapingDefaults.filter(s => s.key !== "import_scrape_tmvdb"))}
                {/* TMVDB toggle — greyed out, not yet active */}
                <div className="opacity-40 pointer-events-none select-none">
                  {settingRows(scrapingDefaults.filter(s => s.key === "import_scrape_tmvdb"))}
                </div>
              </div>
            </div>
            <div className="mt-4">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">AI Defaults</h4>
              <div className="space-y-4 pl-2 border-l-2 border-border">
                {settingRows(aiDefaults)}
              </div>
            </div>
            <div className="mt-4">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Downloads</h4>
              <div className="space-y-4 pl-2 border-l-2 border-border">
                {settingRows(downloadSettings)}
              </div>
            </div>
          </div>
        </section>
      );
    }

    // Server — only render server.port; startup settings are handled by
    // the dedicated StartupControls component via the "system" extras.
    if (group === "server") {
      const portOnly = (groups[group] || []).filter(s => s.key === "server.port");
      if (portOnly.length === 0) return null;
      return (
        <section key={group}>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
            {GROUP_LABELS[group] || group}
          </h2>
          <div className="card space-y-5">
            <div className="space-y-4 pl-2 border-l-2 border-border">
              {settingRows(portOnly)}
            </div>
          </div>
        </section>
      );
    }

    // TMVDB — coming soon, greyed out
    if (group === "tmvdb") {
      return (
        <section key={group}>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
            {GROUP_LABELS[group]}
          </h2>
          <div className="card space-y-5 opacity-40 pointer-events-none select-none">
            <p className="text-xs text-text-muted italic">
              TMVDB integration is not yet available. These settings will become active in a future update.
            </p>
            <div className="space-y-4 pl-2 border-l-2 border-border">
              {settingRows(groups[group])}
            </div>
          </div>
        </section>
      );
    }

    // Default card rendering for other groups (previews, server, etc.)
    return (
      <section key={group}>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
          {GROUP_LABELS[group] || group}
        </h2>
        <div className="card space-y-5">
          <div className="space-y-4 pl-2 border-l-2 border-border">
            {settingRows(groups[group])}
          </div>
        </div>
      </section>
    );
  };

  return (
    <div className={`p-4 md:p-6 ${activeTab === "logs" ? "max-w-5xl" : "max-w-3xl"}`}>
      <h1 className="text-2xl font-bold text-text-primary mb-4">Settings</h1>

      {/* ── Tab bar ── */}
      <div className="flex gap-1 border-b border-white/10 mb-6 overflow-x-auto">
        {SETTINGS_TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors border-b-2 -mb-px ${
              activeTab === tab.id
                ? "border-accent text-accent"
                : "border-transparent text-text-muted hover:text-text-secondary hover:border-white/20"
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Tab content ── */}
      <div className="space-y-8">
        {visibleGroups.map((group) => renderGroup(group))}

        {/* Client-side extras for the active tab */}
        {currentTabDef.extras?.includes("nowplaying") && (
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
              {GROUP_LABELS.nowplaying}
            </h2>
            <div className="card space-y-5">
              <NowPlayingSettings />
            </div>
          </section>
        )}

        {currentTabDef.extras?.includes("partymode") && (
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
              Party Mode
            </h2>
            <div className="card space-y-5">
              <PartyModeSettings />
            </div>
          </section>
        )}

        {currentTabDef.extras?.includes("newvideos") && (
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
              New Videos Discovery
            </h2>
            <div className="card space-y-5">
              <NewVideosSettings />
            </div>
          </section>
        )}

        {currentTabDef.extras?.includes("system") && (
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
              System Information
            </h2>
            <div className="card space-y-5">
              <VersionInfo />
            </div>
          </section>
        )}
        {currentTabDef.extras?.includes("system") && (
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
              Startup & Behaviour
            </h2>
            <div className="card space-y-5">
              <StartupControls settings={settingsByKey} onSave={(key, value, valueType) => {
                updateMutation.mutate(
                  { key, value, value_type: valueType },
                  {
                    onSuccess: () => toast({ type: "success", title: `${SETTING_META[key]?.label || key} saved` }),
                    onError: () => toast({ type: "error", title: `Failed to save ${SETTING_META[key]?.label || key}` }),
                  }
                );
              }} />
            </div>
          </section>
        )}
        {currentTabDef.extras?.includes("system") && (
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
              Server Management
            </h2>
            <div className="card space-y-5">
              <RestartServerButton />
            </div>
          </section>
        )}

        {currentTabDef.extras?.includes("logviewer") && (
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-text-secondary mb-3">
              Log Viewer
            </h2>
            <div className="card">
              <LogViewer />
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

/* ── Startup & Behaviour Controls ── */

function StartupControls({
  settings,
  onSave,
}: {
  settings: Record<string, AppSetting>;
  onSave: (key: string, value: string, valueType: string) => void;
}) {
  const { toast } = useToast();
  const startupEnabled = settings["startup_with_system"]?.value === "true";
  const delaySec = settings["startup_delay_seconds"]?.value ?? "0";
  const autoOpen = settings["auto_open_browser"]?.value === "true";
  const trayIcon = settings["minimize_to_tray"]?.value === "true";
  const dupeScanStartup = settings["startup_duplicate_scan"]?.value === "true";
  const renameScanStartup = settings["startup_rename_scan"]?.value === "true";
  const [delayInput, setDelayInput] = useState(delaySec);
  const [syncing, setSyncing] = useState(false);

  useEffect(() => { setDelayInput(delaySec); }, [delaySec]);

  /** After toggling startup or changing delay, sync the Windows registry. */
  const syncStartup = async () => {
    setSyncing(true);
    try {
      await settingsApi.configureStartup();
    } catch {
      toast({ type: "error", title: "Failed to configure Windows startup" });
    } finally {
      setSyncing(false);
    }
  };

  const toggleStartup = () => {
    const next = startupEnabled ? "false" : "true";
    onSave("startup_with_system", next, "bool");
    // Sync registry after a short delay so the DB write lands first
    setTimeout(syncStartup, 600);
  };

  const saveDelay = () => {
    const clamped = Math.max(0, Math.min(300, parseInt(delayInput) || 0));
    setDelayInput(String(clamped));
    onSave("startup_delay_seconds", String(clamped), "int");
    if (startupEnabled) setTimeout(syncStartup, 600);
  };

  return (
    <div className="space-y-4">
      {/* Start with Windows */}
      <div className="flex items-center justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <p className="text-sm font-medium text-text-primary">Start with Windows</p>
            <Tooltip content="Adds Playarr to the Windows startup registry so it launches automatically on login. Uses pythonw.exe (no console window) when available.">
              <Info size={13} className="text-text-muted cursor-help" />
            </Tooltip>
          </div>
          <p className="text-xs text-text-muted mt-0.5">
            Automatically launch Playarr when you log in to Windows
          </p>
        </div>
        <button
          onClick={toggleStartup}
          disabled={syncing}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            startupEnabled ? "bg-accent" : "bg-surface-3"
          }`}
        >
          <span
            className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
              startupEnabled ? "translate-x-[18px]" : "translate-x-[3px]"
            }`}
          />
        </button>
      </div>

      {/* Delayed Start */}
      {startupEnabled && (
        <div className="flex items-center justify-between pl-4 border-l-2 border-border">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <p className="text-sm font-medium text-text-primary">Startup Delay</p>
              <Tooltip content="Useful to let other services (e.g. network, Redis) initialise first. Set to 0 for no delay.">
                <Info size={13} className="text-text-muted cursor-help" />
              </Tooltip>
            </div>
            <p className="text-xs text-text-muted mt-0.5">
              Seconds to wait before launching the server on system start
            </p>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              max={300}
              value={delayInput}
              onChange={(e) => setDelayInput(e.target.value)}
              onBlur={saveDelay}
              onKeyDown={(e) => e.key === "Enter" && saveDelay()}
              className="input-field w-20 text-center text-sm"
            />
            <span className="text-xs text-text-muted">sec</span>
          </div>
        </div>
      )}

      {/* Open Browser on Launch */}
      <div className="flex items-center justify-between">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-text-primary">Open Browser on Launch</p>
          <p className="text-xs text-text-muted mt-0.5">
            Automatically open the Playarr UI when the server starts
          </p>
        </div>
        <button
          onClick={() => onSave("auto_open_browser", autoOpen ? "false" : "true", "bool")}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            autoOpen ? "bg-accent" : "bg-surface-3"
          }`}
        >
          <span
            className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
              autoOpen ? "translate-x-[18px]" : "translate-x-[3px]"
            }`}
          />
        </button>
      </div>

      {/* System Tray Icon */}
      <div className="flex items-center justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <p className="text-sm font-medium text-text-primary">System Tray Icon</p>
            <Tooltip content="Show a Playarr icon in the system tray with Open and Quit actions. Takes effect on next server start.">
              <Info size={13} className="text-text-muted cursor-help" />
            </Tooltip>
          </div>
          <p className="text-xs text-text-muted mt-0.5">
            Show a tray icon for quick access (requires restart)
          </p>
        </div>
        <button
          onClick={() => onSave("minimize_to_tray", trayIcon ? "false" : "true", "bool")}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            trayIcon ? "bg-accent" : "bg-surface-3"
          }`}
        >
          <span
            className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
              trayIcon ? "translate-x-[18px]" : "translate-x-[3px]"
            }`}
          />
        </button>
      </div>

      {/* Duplicate Scan on Startup */}
      <div className="flex items-center justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <p className="text-sm font-medium text-text-primary">Duplicate Scan on Startup</p>
            <Tooltip content="Runs a background duplicate detection scan each time Playarr starts. Flagged items appear in the Review Queue.">
              <Info size={13} className="text-text-muted cursor-help" />
            </Tooltip>
          </div>
          <p className="text-xs text-text-muted mt-0.5">
            Automatically check for duplicate videos on launch
          </p>
        </div>
        <button
          onClick={() => onSave("startup_duplicate_scan", dupeScanStartup ? "false" : "true", "bool")}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            dupeScanStartup ? "bg-accent" : "bg-surface-3"
          }`}
        >
          <span
            className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
              dupeScanStartup ? "translate-x-[18px]" : "translate-x-[3px]"
            }`}
          />
        </button>
      </div>

      {/* Rename Scan on Startup */}
      <div className="flex items-center justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <p className="text-sm font-medium text-text-primary">Rename Scan on Startup</p>
            <Tooltip content="Checks all files against the naming convention each time Playarr starts. Mismatched items appear in the Review Queue. Previously dismissed items are skipped.">
              <Info size={13} className="text-text-muted cursor-help" />
            </Tooltip>
          </div>
          <p className="text-xs text-text-muted mt-0.5">
            Automatically check for naming convention mismatches on launch
          </p>
        </div>
        <button
          onClick={() => onSave("startup_rename_scan", renameScanStartup ? "false" : "true", "bool")}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            renameScanStartup ? "bg-accent" : "bg-surface-3"
          }`}
        >
          <span
            className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
              renameScanStartup ? "translate-x-[18px]" : "translate-x-[3px]"
            }`}
          />
        </button>
      </div>
    </div>
  );
}

/* ── Restart Server Button ── */

function RestartServerButton() {
  const { toast } = useToast();
  const [restarting, setRestarting] = useState(false);

  const handleRestart = async () => {
    if (!window.confirm("Restart the Playarr server? Any active downloads will be interrupted.")) return;
    setRestarting(true);
    try {
      await settingsApi.restart();
      toast({ type: "info", title: "Server is restarting…" });
      // Poll until the server comes back
      const poll = setInterval(async () => {
        try {
          const r = await fetch("/api/health");
          if (r.ok) {
            clearInterval(poll);
            toast({ type: "success", title: "Server restarted successfully" });
            setTimeout(() => window.location.reload(), 500);
          }
        } catch { /* still down */ }
      }, 1500);
      // Stop polling after 30s
      setTimeout(() => { clearInterval(poll); setRestarting(false); }, 30000);
    } catch {
      toast({ type: "error", title: "Failed to restart server" });
      setRestarting(false);
    }
  };

  return (
    <div className="flex items-center justify-between">
      <div>
        <p className="text-sm font-medium text-text-primary">Restart Server</p>
        <p className="text-xs text-text-muted mt-0.5">
          Restart the Playarr backend. Active downloads will be interrupted.
        </p>
      </div>
      <button
        onClick={handleRestart}
        disabled={restarting}
        className="btn-secondary btn-sm flex items-center gap-1.5"
      >
        <Power size={14} className={restarting ? "animate-spin" : ""} />
        {restarting ? "Restarting…" : "Restart"}
      </button>
    </div>
  );
}

/* ── Version Info ── */

function VersionInfo() {
  const [versionData, setVersionData] = useState<{
    app_version: string;
    db_version: string;
    version_mismatch: boolean;
  } | null>(null);

  useEffect(() => {
    statsApi.version().then(setVersionData).catch(() => {});
  }, []);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-text-primary">Version</p>
          <p className="text-xs text-text-muted mt-0.5">Current application version</p>
        </div>
        <span className="text-sm font-mono text-text-secondary">
          {versionData?.app_version ?? "…"}
        </span>
      </div>
      {versionData?.version_mismatch && (
        <div className="flex items-start gap-2 rounded-lg bg-amber-500/10 border border-amber-500/30 p-3">
          <AlertTriangle size={16} className="text-amber-400 mt-0.5 shrink-0" />
          <div className="text-xs text-amber-300">
            <p className="font-medium">Version Mismatch</p>
            <p className="mt-0.5">
              The database was last used by Playarr v{versionData.db_version}, which is newer
              than the current v{versionData.app_version}. Upgrade Playarr to avoid potential issues.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Open native OS folder picker via backend ── */

async function openDirectoryPicker(onSelect: (path: string) => void) {
  try {
    const res = await settingsApi.browseDirectories();
    if (res.path) onSelect(res.path);
  } catch {
    // user cancelled or dialog failed — ignore
  }
}

/* ── Directory Row (reusable for library_dir, source dirs, etc.) ── */

function DirectoryRow({
  label,
  description,
  setting,
  onSave,
  isPending,
  defaultValue,
}: {
  label: string;
  description: string;
  setting: AppSetting;
  onSave: (value: string) => void;
  isPending: boolean;
  defaultValue?: string;
}) {
  const [value, setValue] = useState(setting.value);
  const { toast } = useToast();

  const [prevValue, setPrevValue] = useState(setting.value);
  if (setting.value !== prevValue) {
    setValue(setting.value);
    setPrevValue(setting.value);
  }

  const isDirty = value !== setting.value;

  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-text-primary">{label}</label>
      <p className="text-xs text-text-muted leading-relaxed">{description}</p>
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="input-field flex-1 text-sm"
          onKeyDown={(e) => { if (e.key === "Enter" && isDirty) onSave(value); }}
        />
        <Tooltip content="Browse for directory">
          <button
            onClick={() => openDirectoryPicker((path) => setValue(path))}
            className="btn-secondary btn-sm flex items-center gap-1"
          >
            <FolderOpen size={14} />
          </button>
        </Tooltip>
        <Tooltip content="Open in file explorer">
          <button
            onClick={async () => {
              try {
                await settingsApi.openDirectory(value);
              } catch {
                toast({ type: "error", title: "Could not open directory — does it exist?" });
              }
            }}
            className="btn-secondary btn-sm flex items-center gap-1"
          >
            <ExternalLink size={14} />
          </button>
        </Tooltip>
        {defaultValue && value !== defaultValue && (
          <Tooltip content={`Restore default: ${defaultValue}`}>
            <button
              onClick={() => setValue(defaultValue)}
              className="btn-secondary btn-sm flex items-center gap-1"
            >
              <RefreshCw size={14} />
            </button>
          </Tooltip>
        )}
        {isDirty && (
          <Tooltip content="Save changes">
            <button onClick={() => onSave(value)} disabled={isPending} className="btn-primary btn-sm">
              <Save size={14} />
            </button>
          </Tooltip>
        )}
      </div>
    </div>
  );
}

/* ── Source Directories Editor ── */

function SourceDirectoriesEditor({
  setting,
  onSaved,
}: {
  setting?: AppSetting;
  onSaved?: () => void;
}) {
  const { toast } = useToast();
  const raw = setting?.value || "[]";
  let initial: string[] = [];
  try { initial = JSON.parse(raw); } catch { /* ignore */ }

  const [dirs, setDirs] = useState<string[]>(initial);
  const [prevRaw, setPrevRaw] = useState(raw);
  if (raw !== prevRaw) {
    let synced: string[] = [];
    try { synced = JSON.parse(raw); } catch { /* ignore */ }
    setDirs(synced);
    setPrevRaw(raw);
  }
  const [newDir, setNewDir] = useState("");
  const [saving, setSaving] = useState(false);

  const isDirty = JSON.stringify(dirs) !== raw;

  const addDir = () => {
    const trimmed = newDir.trim();
    if (!trimmed || dirs.includes(trimmed)) return;
    setDirs([...dirs, trimmed]);
    setNewDir("");
  };

  const removeDir = (idx: number) => {
    setDirs(dirs.filter((_, i) => i !== idx));
  };

  const save = async () => {
    setSaving(true);
    try {
      const res = await settingsApi.updateSourceDirs(dirs);
      const parts: string[] = ["Source directories saved"];
      if (res.added_dirs.length) parts.push(`${res.added_dirs.length} dir(s) added — videos auto-imported`);
      if (res.cleaned_count) parts.push(`${res.cleaned_count} video(s) cleaned from removed dir(s)`);
      toast({ type: "success", title: parts.join(". ") });
      onSaved?.();
    } catch {
      toast({ type: "error", title: "Failed to save source directories" });
    } finally {
      setSaving(false);
    }
  };

  const browseForNew = () => {
    openDirectoryPicker((path) => {
      if (!dirs.includes(path)) setDirs((prev) => [...prev, path]);
    });
  };

  const browseForIndex = (idx: number) => {
    openDirectoryPicker((path) => {
      setDirs((prev) => prev.map((d, i) => i === idx ? path : d));
    });
  };

  return (
    <div className="flex flex-col gap-2 border-t border-white/5 pt-4">
      <div>
        <label className="text-sm font-medium text-text-primary">Source Directories</label>
        <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
          Additional folders containing music videos. Adding a directory auto-imports its videos using local metadata only (NFO files, folder names). Removing a directory cleans its videos from the library.
        </p>
      </div>
      {dirs.map((d, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="input-field flex-1 text-sm py-1.5 px-2 bg-surface-light/50">{d}</span>
          <Tooltip content="Browse for directory">
            <button
              onClick={() => browseForIndex(i)}
              className="btn-secondary btn-sm flex items-center gap-1 p-1.5"
            >
              <FolderOpen size={14} />
            </button>
          </Tooltip>
          <Tooltip content="Open in file explorer">
            <button
              onClick={async () => {
                try {
                  await settingsApi.openDirectory(d);
                } catch {
                  toast({ type: "error", title: "Could not open directory — does it exist?" });
                }
              }}
              className="btn-secondary btn-sm flex items-center gap-1 p-1.5"
            >
              <ExternalLink size={14} />
            </button>
          </Tooltip>
          <Tooltip content="Remove this directory \u2014 videos inside will be cleaned from the library on save">
            <button
              onClick={() => removeDir(i)}
              className="text-text-muted hover:text-red-400 transition-colors p-1"
            >
              <X size={14} />
            </button>
          </Tooltip>
        </div>
      ))}
      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="e.g. V:\MusicVideos"
          value={newDir}
          onChange={(e) => setNewDir(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") addDir(); }}
          className="input-field flex-1 text-sm"
        />
        <Tooltip content="Browse for directory">
          <button
            onClick={browseForNew}
            className="btn-secondary btn-sm flex items-center gap-1"
          >
            <FolderOpen size={14} />
          </button>
        </Tooltip>
        <Tooltip content="Add directory \u2014 videos inside will be auto-imported on save">
          <button
            onClick={addDir}
            disabled={!newDir.trim()}
            className="btn-secondary btn-sm flex items-center gap-1"
          >
            <Plus size={14} />
            Add
          </button>
        </Tooltip>
      </div>
      {isDirty && (
        <div className="flex justify-end">
          <button onClick={save} disabled={saving} className="btn-primary btn-sm flex items-center gap-1.5">
            <Save size={14} />
            {saving ? "Saving…" : "Save Changes"}
          </button>
        </div>
      )}
    </div>
  );
}

/* ── Orphan folder cleanup section ── */

/* ── Library Naming Convention Editor ── */

const FOLDER_STRUCTURE_PRESETS = [
  { value: "{artist}/{file_folder}", label: "Artist / Video Folder", description: "Best for Kodi — artist artwork lives alongside videos" },
  { value: "{file_folder}", label: "Flat (Video Folder only)", description: "All video folders directly in library root" },
  { value: "{artist}/{album}/{file_folder}", label: "Artist / Album / Video Folder", description: "Deep nesting — groups videos by artist and album" },
  { value: "{album}/{file_folder}", label: "Album / Video Folder", description: "Groups videos by album name" },
];

const FILE_NAMING_PRESETS = [
  { value: "{artist} - {title} [{quality}]", label: "Artist - Title [Quality]", recommended: true },
  { value: "{artist} - {title} - {quality}", label: "Artist - Title - Quality" },
  { value: "{artist} - {title}", label: "Artist - Title (no quality)" },
  { value: "{artist} - {album} - {title}", label: "Artist - Album - Title" },
  { value: "{artist} - {album} - {title} [{quality}]", label: "Artist - Album - Title [Quality]" },
  { value: "{title} - {artist} [{quality}]", label: "Title - Artist [Quality]" },
];

function NamingConventionEditor({
  namingPattern,
  folderStructure,
  onSave,
  isPending,
}: {
  namingPattern: string;
  folderStructure: string;
  onSave: (key: string, value: string) => void;
  isPending: boolean;
}) {
  const [pattern, setPattern] = useState(namingPattern);
  const [structure, setStructure] = useState(folderStructure);
  const [preview, setPreview] = useState<{ artist: string; title: string; version_type: string; path: string }[]>([]);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [showPreview, setShowPreview] = useState(false);

  const patternDirty = pattern !== namingPattern;
  const structureDirty = structure !== folderStructure;

  const isDefaultConfig = pattern === "{artist} - {title} [{quality}]" && structure === "{artist}/{file_folder}";

  // Fetch preview whenever pattern or structure changes
  const fetchPreview = async () => {
    setLoadingPreview(true);
    try {
      const res = await settingsApi.namingPreview(pattern, structure);
      setPreview(res.examples);
    } catch {
      setPreview([]);
    } finally {
      setLoadingPreview(false);
    }
  };

  // Auto-fetch preview on mount and when values change
  useState(() => { fetchPreview(); });

  const handlePatternChange = (value: string) => {
    setPattern(value);
    // Debounced preview fetch
    setTimeout(() => fetchPreview(), 300);
  };

  const handleStructureChange = (value: string) => {
    setStructure(value);
    setTimeout(() => fetchPreview(), 300);
  };

  const savePattern = () => {
    onSave("library_naming_pattern", pattern);
  };

  const saveStructure = () => {
    onSave("library_folder_structure", structure);
  };

  return (
    <div className="space-y-4">
      {/* Folder Structure */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium text-text-primary">Folder Structure</label>
          {structure === "{artist}/{file_folder}" && (
            <Tooltip content="This layout is the most compatible with Kodi. Artist artwork and NFOs are organised into per-artist folders alongside their video subfolders.">
              <span className="px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-green-500/20 text-green-400 rounded">Kodi Recommended</span>
            </Tooltip>
          )}
        </div>
        <p className="text-xs text-text-muted mb-1.5">
          How video folders are organised within the library. Tokens: <code className="text-accent/80">{"{artist}"}</code>, <code className="text-accent/80">{"{album}"}</code>, <code className="text-accent/80">{"{file_folder}"}</code>
        </p>
        <div className="flex items-center gap-2">
          <select
            value={FOLDER_STRUCTURE_PRESETS.some(p => p.value === structure) ? structure : "__custom__"}
            onChange={(e) => {
              if (e.target.value !== "__custom__") {
                handleStructureChange(e.target.value);
              }
            }}
            className="input-field text-sm flex-1"
          >
            {FOLDER_STRUCTURE_PRESETS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
            {!FOLDER_STRUCTURE_PRESETS.some(p => p.value === structure) && (
              <option value="__custom__">Custom</option>
            )}
          </select>
          {structureDirty && (
            <button onClick={saveStructure} disabled={isPending} className="btn-primary btn-sm flex items-center gap-1">
              <Save size={14} /> Save
            </button>
          )}
        </div>
        {!FOLDER_STRUCTURE_PRESETS.some(p => p.value === structure) && (
          <input
            type="text"
            value={structure}
            onChange={(e) => handleStructureChange(e.target.value)}
            className="input-field text-sm w-full mt-1"
            placeholder="{artist}/{file_folder}"
          />
        )}
      </div>

      {/* File Naming Pattern */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium text-text-primary">File Naming Pattern</label>
          {pattern === "{artist} - {title} [{quality}]" && (
            <Tooltip content="This is the default naming convention. The quality tag in brackets helps identify resolution at a glance and is widely supported by media managers.">
              <span className="px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide bg-blue-500/20 text-blue-400 rounded">Default</span>
            </Tooltip>
          )}
        </div>
        <p className="text-xs text-text-muted mb-1.5">
          Pattern for video files and their containing folder. Tokens: <code className="text-accent/80">{"{artist}"}</code>, <code className="text-accent/80">{"{title}"}</code>, <code className="text-accent/80">{"{quality}"}</code>, <code className="text-accent/80">{"{album}"}</code>, <code className="text-accent/80">{"{year}"}</code>
        </p>
        <div className="flex items-center gap-2">
          <select
            value={FILE_NAMING_PRESETS.some(p => p.value === pattern) ? pattern : "__custom__"}
            onChange={(e) => {
              if (e.target.value !== "__custom__") {
                handlePatternChange(e.target.value);
              }
            }}
            className="input-field text-sm flex-1"
          >
            {FILE_NAMING_PRESETS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}{p.recommended ? " ★" : ""}
              </option>
            ))}
            {!FILE_NAMING_PRESETS.some(p => p.value === pattern) && (
              <option value="__custom__">Custom</option>
            )}
          </select>
          {patternDirty && (
            <button onClick={savePattern} disabled={isPending} className="btn-primary btn-sm flex items-center gap-1">
              <Save size={14} /> Save
            </button>
          )}
        </div>
        {!FILE_NAMING_PRESETS.some(p => p.value === pattern) && (
          <input
            type="text"
            value={pattern}
            onChange={(e) => handlePatternChange(e.target.value)}
            className="input-field text-sm w-full mt-1"
            placeholder="{artist} - {title} [{quality}]"
          />
        )}
      </div>

      {/* Default tooltip */}
      {isDefaultConfig && (
        <div className="flex items-start gap-2 p-2.5 rounded-lg bg-green-500/5 border border-green-500/20">
          <Info size={14} className="text-green-400 mt-0.5 shrink-0" />
          <p className="text-xs text-green-300/80">
            <strong>Kodi-optimized layout:</strong> Artist / Video Folder structure with quality tags in brackets.
            This is the most compatible layout for Kodi music video libraries — artist artwork, NFOs,
            and video subfolders are organised naturally for Kodi's scraper.
          </p>
        </div>
      )}

      {/* Live Preview */}
      <div className="space-y-2">
        <button
          onClick={() => { setShowPreview(!showPreview); if (!showPreview) fetchPreview(); }}
          className="flex items-center gap-1.5 text-xs font-medium text-text-muted hover:text-text-secondary transition-colors"
        >
          {showPreview ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          <FileText size={12} />
          Preview Example Paths
        </button>
        {showPreview && (
          <div className="bg-surface-light/30 rounded-lg p-3 space-y-1.5">
            {loadingPreview ? (
              <p className="text-xs text-text-muted">Loading preview…</p>
            ) : preview.length > 0 ? (
              preview.map((ex, i) => (
                <div key={i} className="text-xs">
                  <span className="text-text-muted">{ex.artist} — {ex.title}{ex.version_type !== "normal" ? ` (${ex.version_type})` : ""}:</span>
                  <div className="font-mono text-text-secondary pl-3 break-all">{ex.path}</div>
                </div>
              ))
            ) : (
              <p className="text-xs text-text-muted">No preview available</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function LibraryMaintenanceContent() {
  const { toast } = useToast();
  const scanMutation = useLibraryScan();
  const exportMutation = useLibraryExport();
  const [cleanDialogOpen, setCleanDialogOpen] = useState(false);
  const [exportMode, setExportMode] = useState<string>("skip_existing");

  return (
    <>
      <div className="space-y-4">
        <p className="text-sm font-medium text-text-primary">Library Maintenance</p>
        <div className="space-y-3">
          <div className="flex items-start gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-sm text-text-secondary">Scan Library</p>
              <p className="text-xs text-text-muted leading-relaxed">
                Scan the library directory for new files not tracked in the database. Creates library entries from NFO metadata or folder names.
              </p>
            </div>
            <button
              onClick={() =>
                scanMutation.mutate(true, {
                  onSuccess: () => toast({ type: "success", title: "Library scan started" }),
                })
              }
              disabled={scanMutation.isPending}
              className="btn-secondary btn-sm flex items-center gap-1.5 shrink-0"
            >
              <ScanLine size={14} />
              {scanMutation.isPending ? "Scanning…" : "Scan"}
            </button>
          </div>
          <div className="flex items-start gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-sm text-text-secondary">Clean Library</p>
              <p className="text-xs text-text-muted leading-relaxed">
                Check library health — find database entries with missing files and on-disk folders not tracked in the database.
              </p>
            </div>
            <button
              onClick={() => setCleanDialogOpen(true)}
              className="btn-secondary btn-sm flex items-center gap-1.5 shrink-0"
            >
              <HeartPulse size={14} />
              Clean
            </button>
          </div>
          <div className="flex items-start gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-sm text-text-secondary">Export Library</p>
              <p className="text-xs text-text-muted leading-relaxed">
                Export NFO files, Playarr XML sidecars, and artwork for all videos. Locked items are excluded from overwrites.
              </p>
              <div className="flex flex-wrap gap-2 mt-2">
                {[
                  { value: "skip_existing", label: "Skip Existing", desc: "Only write missing files" },
                  { value: "overwrite_new", label: "Overwrite New", desc: "Only overwrite changed files" },
                  { value: "overwrite_all", label: "Overwrite All", desc: "Overwrite all files" },
                ].map((opt) => (
                  <label
                    key={opt.value}
                    className={`flex items-center gap-1.5 text-xs px-2 py-1 rounded-md border cursor-pointer transition-colors ${
                      exportMode === opt.value
                        ? "border-accent bg-accent/10 text-accent"
                        : "border-border text-text-muted hover:border-text-muted"
                    }`}
                  >
                    <input
                      type="radio"
                      name="exportMode"
                      value={opt.value}
                      checked={exportMode === opt.value}
                      onChange={() => setExportMode(opt.value)}
                      className="sr-only"
                    />
                    <span>{opt.label}</span>
                  </label>
                ))}
              </div>
            </div>
            <button
              onClick={() =>
                exportMutation.mutate(exportMode, {
                  onSuccess: () => toast({ type: "success", title: "Library export started" }),
                })
              }
              disabled={exportMutation.isPending}
              className="btn-secondary btn-sm flex items-center gap-1.5 shrink-0"
            >
              <Download size={14} />
              {exportMutation.isPending ? "Exporting…" : "Export"}
            </button>
          </div>
        </div>
      </div>
      {cleanDialogOpen && (
        <CleanLibraryDialog open={cleanDialogOpen} onClose={() => setCleanDialogOpen(false)} />
      )}
    </>
  );
}

/* ── Now Playing artwork animation settings (client-side) ── */

function NowPlayingSettings() {
  const artworkSize = useArtworkSettings((s) => s.artworkSize);
  const scrollDuration = useArtworkSettings((s) => s.scrollDuration);
  const changeRate = useArtworkSettings((s) => s.changeRate);
  const fadeDuration = useArtworkSettings((s) => s.fadeDuration);
  const playbackRatio = useArtworkSettings((s) => s.playbackRatio);
  const queueOpacity = useArtworkSettings((s) => s.queueOpacity);
  const overlayDuration = useArtworkSettings((s) => s.overlayDuration);
  const artRepeatPenalty = useArtworkSettings((s) => s.artRepeatPenalty);
  const overlaySize = useArtworkSettings((s) => s.overlaySize);
  const queueClock = useArtworkSettings((s) => s.queueClock);
  const artChangeEnabled = useArtworkSettings((s) => s.artChangeEnabled);
  const artChangeCount = useArtworkSettings((s) => s.artChangeCount);
  const artChangeStyle = useArtworkSettings((s) => s.artChangeStyle);
  const setArtworkSize = useArtworkSettings((s) => s.setArtworkSize);
  const setScrollDuration = useArtworkSettings((s) => s.setScrollDuration);
  const setChangeRate = useArtworkSettings((s) => s.setChangeRate);
  const setFadeDuration = useArtworkSettings((s) => s.setFadeDuration);
  const setPlaybackRatio = useArtworkSettings((s) => s.setPlaybackRatio);
  const setQueueOpacity = useArtworkSettings((s) => s.setQueueOpacity);
  const setOverlayDuration = useArtworkSettings((s) => s.setOverlayDuration);
  const setOverlaySize = useArtworkSettings((s) => s.setOverlaySize);
  const setArtRepeatPenalty = useArtworkSettings((s) => s.setArtRepeatPenalty);
  const setQueueClock = useArtworkSettings((s) => s.setQueueClock);
  const setArtChangeEnabled = useArtworkSettings((s) => s.setArtChangeEnabled);
  const setArtChangeCount = useArtworkSettings((s) => s.setArtChangeCount);
  const setArtChangeStyle = useArtworkSettings((s) => s.setArtChangeStyle);

  return (
    <>
      <p className="text-xs text-text-muted leading-relaxed">
        Controls for the animated artwork grid shown on the Now Playing screen.
      </p>

      {/* ── Background Grid ── */}
      <div className="mt-2">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Background Grid</h4>
        <div className="space-y-4 pl-2 border-l-2 border-border">

      {/* Artwork Size */}
      <div className="flex flex-col sm:flex-row sm:items-start gap-2">
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Tile Size (px)</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            Fixed pixel size for each artwork tile in the background grid.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <input
            type="range"
            min={80}
            max={300}
            step={10}
            value={artworkSize}
            onChange={(e) => setArtworkSize(Number(e.target.value))}
            className="w-32 accent-accent"
          />
          <span className="text-sm text-text-secondary w-12 text-right">{artworkSize}</span>
        </div>
      </div>

      {/* Scroll Rate (duration) */}
      <div className="flex flex-col sm:flex-row sm:items-start gap-2">
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Scroll Speed</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            How quickly the artwork grid scrolls. Lower values = faster scrolling.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <input
            type="range"
            min={10}
            max={180}
            step={5}
            value={scrollDuration}
            onChange={(e) => setScrollDuration(Number(e.target.value))}
            className="w-32 accent-accent"
          />
          <span className="text-sm text-text-secondary w-12 text-right">{scrollDuration}s</span>
        </div>
      </div>

        </div>
      </div>

      {/* ── Tile Swapping ── */}
      <div className="mt-4">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Tile Swapping</h4>
        <div className="space-y-4 pl-2 border-l-2 border-border">

      {/* Artwork Swapping Toggle */}
      <div className="flex flex-col sm:flex-row sm:items-start gap-2">
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Enable Swapping</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            Periodically swap artwork tiles in the background grid.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <button
            onClick={() => setArtChangeEnabled(!artChangeEnabled)}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${artChangeEnabled ? "bg-accent" : "bg-white/20"}`}
          >
            <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${artChangeEnabled ? "translate-x-6" : "translate-x-1"}`} />
          </button>
        </div>
      </div>

      {/* Change Rate */}
      <div className={`flex flex-col sm:flex-row sm:items-start gap-2 transition-opacity ${artChangeEnabled ? "" : "opacity-40 pointer-events-none"}`}>
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Swap Interval (s)</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            Time period over which all tile swaps are distributed.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <input
            type="range"
            min={1}
            max={15}
            step={0.5}
            value={changeRate}
            onChange={(e) => setChangeRate(Number(e.target.value))}
            className="w-32 accent-accent"
          />
          <span className="text-sm text-text-secondary w-12 text-right">{changeRate}s</span>
        </div>
      </div>

      {/* Tiles Per Interval */}
      <div className={`flex flex-col sm:flex-row sm:items-start gap-2 transition-opacity ${artChangeEnabled ? "" : "opacity-40 pointer-events-none"}`}>
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Tiles Per Interval</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            Number of tiles to swap per interval, evenly spaced across the period.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <input
            type="range"
            min={1}
            max={50}
            step={1}
            value={artChangeCount}
            onChange={(e) => setArtChangeCount(Number(e.target.value))}
            className="w-32 accent-accent"
          />
          <span className="text-sm text-text-secondary w-12 text-right">{artChangeCount}</span>
        </div>
      </div>

      {/* Transition Style */}
      <div className={`flex flex-col sm:flex-row sm:items-start gap-2 transition-opacity ${artChangeEnabled ? "" : "opacity-40 pointer-events-none"}`}>
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Transition Style</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            Animation style when tiles swap. Random uses a mix of all styles.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <select
            value={artChangeStyle}
            onChange={(e) => setArtChangeStyle(e.target.value as "fade" | "flip" | "spin" | "random")}
            className="bg-surface-hover border border-border rounded-lg px-3 py-1.5 text-sm text-text-primary"
          >
            <option value="fade">Fade</option>
            <option value="flip">Flip</option>
            <option value="spin">Spin</option>
            <option value="random">Random</option>
          </select>
        </div>
      </div>

      {/* Fade Duration */}
      <div className={`flex flex-col sm:flex-row sm:items-start gap-2 transition-opacity ${artChangeEnabled ? "" : "opacity-40 pointer-events-none"}`}>
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Transition Duration (s)</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            How long each tile transition animation takes.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <input
            type="range"
            min={0.3}
            max={3}
            step={0.1}
            value={fadeDuration}
            onChange={(e) => setFadeDuration(Number(e.target.value))}
            className="w-32 accent-accent"
          />
          <span className="text-sm text-text-secondary w-12 text-right">{fadeDuration}s</span>
        </div>
      </div>

      {/* Artwork Repetition Penalty */}
      <div className={`flex flex-col sm:flex-row sm:items-start gap-2 transition-opacity ${artChangeEnabled ? "" : "opacity-40 pointer-events-none"}`}>
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Repeat Penalty</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            Reduces the chance of showing the same artwork again. Higher = less repetition.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <input
            type="range"
            min={0}
            max={100}
            step={10}
            value={artRepeatPenalty}
            onChange={(e) => setArtRepeatPenalty(Number(e.target.value))}
            className="w-32 accent-accent"
          />
          <span className="text-sm text-text-secondary w-12 text-right">{artRepeatPenalty === 0 ? "Off" : artRepeatPenalty}</span>
        </div>
      </div>

        </div>
      </div>

      {/* ── Playback & Overlay ── */}
      <div className="mt-4">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Playback &amp; Overlay</h4>
        <div className="space-y-4 pl-2 border-l-2 border-border">

      {/* Playback Area Ratio */}
      <div className="flex flex-col sm:flex-row sm:items-start gap-2">
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Playback Area Size (%)</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            Percentage of available vertical space used by the playback area.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <input
            type="range"
            min={25}
            max={90}
            step={5}
            value={playbackRatio}
            onChange={(e) => setPlaybackRatio(Number(e.target.value))}
            className="w-32 accent-accent"
          />
          <span className="text-sm text-text-secondary w-12 text-right">{playbackRatio}%</span>
        </div>
      </div>

      {/* Queue Opacity */}
      <div className="flex flex-col sm:flex-row sm:items-start gap-2">
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Queue Panel Opacity (%)</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            Transparency of the queue panel background. Lower = more transparent.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <input
            type="range"
            min={10}
            max={90}
            step={5}
            value={queueOpacity}
            onChange={(e) => setQueueOpacity(Number(e.target.value))}
            className="w-32 accent-accent"
          />
          <span className="text-sm text-text-secondary w-12 text-right">{queueOpacity}%</span>
        </div>
      </div>

      {/* Queue Clock */}
      <div className="flex flex-col sm:flex-row sm:items-start gap-2">
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Queue Clock</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            Show the current time and estimated start time for each track in the queue.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <button
            onClick={() => setQueueClock(!queueClock)}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${queueClock ? "bg-accent" : "bg-white/20"}`}
          >
            <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${queueClock ? "translate-x-6" : "translate-x-1"}`} />
          </button>
        </div>
      </div>

      {/* Metadata Overlay Duration */}
      <div className="flex flex-col sm:flex-row sm:items-start gap-2">
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Metadata Overlay Duration (s)</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            How long the video description overlay is shown when a new track starts. Set to 0 to disable.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <input
            type="range"
            min={0}
            max={90}
            step={5}
            value={overlayDuration}
            onChange={(e) => setOverlayDuration(Number(e.target.value))}
            className="w-32 accent-accent"
          />
          <span className="text-sm text-text-secondary w-12 text-right">{overlayDuration === 0 ? "Off" : `${overlayDuration}s`}</span>
        </div>
      </div>

      {/* Metadata Overlay Size */}
      <div className="flex flex-col sm:flex-row sm:items-start gap-2">
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">Infobox Display Size (%)</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            Height of the metadata infobox as a percentage of the video area.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <input
            type="range"
            min={20}
            max={60}
            step={5}
            value={overlaySize}
            onChange={(e) => setOverlaySize(Number(e.target.value))}
            className="w-32 accent-accent"
          />
          <span className="text-sm text-text-secondary w-12 text-right">{overlaySize}%</span>
        </div>
      </div>

        </div>
      </div>
    </>
  );
}

/* ── Party Mode exclusion settings (client-side, localStorage) ── */

const VERSION_TYPE_OPTIONS = [
  { value: "normal", label: "Normal" },
  { value: "cover", label: "Cover" },
  { value: "live", label: "Live" },
  { value: "alternate", label: "Alternate" },
  { value: "remix", label: "Remix" },
  { value: "acoustic", label: "Acoustic" },
  { value: "uncensored", label: "Uncensored" },
  { value: "18+", label: "18+" },
];

function PartyModeSettings() {
  const { toast } = useToast();
  const [exclusions, setExclusions] = useState<PartyModeExclusions>(loadExclusions);
  const [animation, setAnimation] = useState<PartyModeAnimationSettings>(loadAnimationSettings);
  const [artistInput, setArtistInput] = useState("");
  const [genreInput, setGenreInput] = useState("");
  const [albumInput, setAlbumInput] = useState("");

  const save = (next: PartyModeExclusions) => {
    setExclusions(next);
    saveExclusions(next);
    toast({ type: "success", title: "Party Mode exclusions saved" });
  };

  const toggleVersionType = (vt: string) => {
    const next = { ...exclusions };
    if (next.version_types.includes(vt)) {
      next.version_types = next.version_types.filter((v) => v !== vt);
    } else {
      next.version_types = [...next.version_types, vt];
    }
    save(next);
  };

  const addTag = (field: "artists" | "genres" | "albums", value: string, resetFn: (v: string) => void) => {
    const trimmed = value.trim();
    if (!trimmed || exclusions[field].includes(trimmed)) return;
    save({ ...exclusions, [field]: [...exclusions[field], trimmed] });
    resetFn("");
  };

  const removeTag = (field: "artists" | "genres" | "albums", value: string) => {
    save({ ...exclusions, [field]: exclusions[field].filter((v) => v !== value) });
  };

  const resetAll = () => {
    save({ ...DEFAULT_EXCLUSIONS });
    setArtistInput("");
    setGenreInput("");
    setAlbumInput("");
  };

  return (
    <>
      {/* ── Animation ── */}
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Animation</h4>
        <div className="space-y-4 pl-2 border-l-2 border-border">
          <div className="flex flex-col sm:flex-row sm:items-start gap-2">
            <div className="flex-1 min-w-0">
              <label className="text-sm font-medium text-text-primary">Startup Animation</label>
              <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
                Show a fireworks animation when Party Mode launches.
              </p>
            </div>
            <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
              <button
                onClick={() => {
                  const next = { ...animation, enabled: !animation.enabled };
                  setAnimation(next);
                  saveAnimationSettings(next);
                }}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${animation.enabled ? "bg-accent" : "bg-white/20"}`}
              >
                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${animation.enabled ? "translate-x-6" : "translate-x-1"}`} />
              </button>
            </div>
          </div>

          {animation.enabled && (
            <div className="flex flex-col sm:flex-row sm:items-start gap-2">
              <div className="flex-1 min-w-0">
                <label className="text-sm font-medium text-text-primary">Duration</label>
                <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
                  How long the startup fireworks animation plays.
                </p>
              </div>
              <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
                <input
                  type="range"
                  min={5}
                  max={15}
                  step={1}
                  value={animation.duration}
                  onChange={(e) => {
                    const next = { ...animation, duration: Number(e.target.value) };
                    setAnimation(next);
                    saveAnimationSettings(next);
                  }}
                  className="w-32 accent-accent"
                />
                <span className="text-sm text-text-secondary w-12 text-right">{animation.duration}s</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Content Filters ── */}
      <div className="mt-4">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Content Filters</h4>
        <div className="space-y-4 pl-2 border-l-2 border-border">
          <p className="text-xs text-text-muted leading-relaxed">
            Exclusions are applied every time Party Mode is activated. Excluded items are filtered out
            regardless of the active page filters.
          </p>

          <div className="flex flex-col gap-1.5">
            <label className="text-sm font-medium text-text-primary">Exclude Version Types</label>
            <div className="flex flex-wrap gap-2">
              {VERSION_TYPE_OPTIONS.map((vt) => (
                <label key={vt.value} className="flex items-center gap-1.5 text-sm text-text-secondary cursor-pointer">
                  <input
                    type="checkbox"
                    checked={exclusions.version_types.includes(vt.value)}
                    onChange={() => toggleVersionType(vt.value)}
                    className="accent-accent"
                  />
                  {vt.label}
                </label>
              ))}
            </div>
          </div>

          <div className="flex flex-col sm:flex-row sm:items-start gap-2">
            <div className="flex-1 min-w-0">
              <label className="text-sm font-medium text-text-primary">Min Song Rating</label>
              <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
                Only include songs with at least this rating.
              </p>
            </div>
            <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
              <select
                value={exclusions.min_song_rating ?? ""}
                onChange={(e) => {
                  const v = e.target.value ? Number(e.target.value) : null;
                  save({ ...exclusions, min_song_rating: v });
                }}
                className="input-field w-auto py-1.5 text-sm"
              >
                <option value="">Any</option>
                {[1, 2, 3, 4, 5].map((r) => (
                  <option key={r} value={r}>{r} star{r > 1 ? "s" : ""}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row sm:items-start gap-2">
            <div className="flex-1 min-w-0">
              <label className="text-sm font-medium text-text-primary">Min Video Rating</label>
              <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
                Only include videos with at least this rating.
              </p>
            </div>
            <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
              <select
                value={exclusions.min_video_rating ?? ""}
                onChange={(e) => {
                  const v = e.target.value ? Number(e.target.value) : null;
                  save({ ...exclusions, min_video_rating: v });
                }}
                className="input-field w-auto py-1.5 text-sm"
              >
                <option value="">Any</option>
                {[1, 2, 3, 4, 5].map((r) => (
                  <option key={r} value={r}>{r} star{r > 1 ? "s" : ""}</option>
                ))}
              </select>
            </div>
          </div>
        </div>
      </div>

      {/* ── Exclusion Lists ── */}
      <div className="mt-4">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">Exclusion Lists</h4>
        <div className="space-y-4 pl-2 border-l-2 border-border">
          <TagInputField
            label="Exclude Artists"
            description="Videos by these artists will be excluded from Party Mode."
            tags={exclusions.artists}
            inputValue={artistInput}
            onInputChange={setArtistInput}
            onAdd={() => addTag("artists", artistInput, setArtistInput)}
            onRemove={(v) => removeTag("artists", v)}
          />

          <TagInputField
            label="Exclude Genres"
            description="Videos with these genres will be excluded."
            tags={exclusions.genres}
            inputValue={genreInput}
            onInputChange={setGenreInput}
            onAdd={() => addTag("genres", genreInput, setGenreInput)}
            onRemove={(v) => removeTag("genres", v)}
          />

          <TagInputField
            label="Exclude Albums"
            description="Videos from these albums will be excluded."
            tags={exclusions.albums}
            inputValue={albumInput}
            onInputChange={setAlbumInput}
            onAdd={() => addTag("albums", albumInput, setAlbumInput)}
            onRemove={(v) => removeTag("albums", v)}
          />

          <button onClick={resetAll} className="btn-ghost btn-sm text-xs self-start">
            Reset Exclusions
          </button>
        </div>
      </div>
    </>
  );
}

/* ── Tag input helper for Party Mode exclusions ── */

function TagInputField({
  label,
  description,
  tags,
  inputValue,
  onInputChange,
  onAdd,
  onRemove,
}: {
  label: string;
  description: string;
  tags: string[];
  inputValue: string;
  onInputChange: (v: string) => void;
  onAdd: () => void;
  onRemove: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium text-text-primary">{label}</label>
      <p className="text-xs text-text-muted leading-relaxed">{description}</p>
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={inputValue}
          onChange={(e) => onInputChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); onAdd(); } }}
          placeholder="Type and press Enter"
          className="input-field flex-1 py-1.5 text-sm"
        />
        <button onClick={onAdd} className="btn-ghost btn-sm text-xs" disabled={!inputValue.trim()}>
          <Plus size={14} />
        </button>
      </div>
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-1">
          {tags.map((tag) => (
            <span
              key={tag}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-surface-secondary text-xs text-text-secondary"
            >
              {tag}
              <button onClick={() => onRemove(tag)} className="text-text-muted hover:text-red-400 transition-colors">
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Individual setting row with inline edit ── */

function SettingRow({
  setting,
  meta,
  onSave,
  isPending,
}: {
  setting: AppSetting;
  meta?: SettingMeta;
  onSave: (value: string, valueType: string) => void;
  isPending: boolean;
}) {
  const [value, setValue] = useState(setting.value);

  // Sync local state when the setting prop changes (e.g. after refetch)
  const [prevValue, setPrevValue] = useState(setting.value);
  if (setting.value !== prevValue) {
    setValue(setting.value);
    setPrevValue(setting.value);
  }

  const isDirty = value !== setting.value;

  const isBoolean = setting.value_type === "bool" || value === "true" || value === "false";
  const isNumber = setting.value_type === "int" || setting.value_type === "float" || /^\d+(\.\d+)?$/.test(setting.value);

  const label = meta?.label || setting.key.replace(/_/g, " ").replace(/\./g, " ");
  const description = meta?.description;

  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-2">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1">
          <label className="text-sm font-medium text-text-primary">{label}</label>
          {meta?.tooltip && (
            <Tooltip content={meta.tooltip}>
              <span className="text-text-muted hover:text-text-secondary cursor-help text-xs">ⓘ</span>
            </Tooltip>
          )}
        </div>
        {description && (
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">{description}</p>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0 sm:pt-0.5">
        {isBoolean ? (
          <button
            onClick={() => {
              const next = value === "true" ? "false" : "true";
              setValue(next);
              onSave(next, setting.value_type);
            }}
            className={`w-11 h-6 rounded-full transition-colors relative inline-flex items-center ${
              value === "true" ? "bg-accent" : "bg-surface-light"
            }`}
            disabled={isPending}
            aria-label={`Toggle ${label}`}
          >
            <span
              className={`inline-block h-4 w-4 rounded-full bg-white shadow transform transition-transform ${
                value === "true" ? "translate-x-6" : "translate-x-1"
              }`}
            />
          </button>
        ) : meta?.options ? (
          <select
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              onSave(e.target.value, setting.value_type);
            }}
            className="input-field w-48 text-sm"
            disabled={isPending}
          >
            {meta.options.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        ) : (
          <input
            type={isNumber ? "number" : "text"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="input-field w-48 text-sm"
            onKeyDown={(e) => {
              if (e.key === "Enter" && isDirty) onSave(value, setting.value_type);
            }}
          />
        )}
        {!isBoolean && !meta?.options && isDirty && (
          <Tooltip content="Save changes">
            <button
              onClick={() => onSave(value, setting.value_type)}
              disabled={isPending}
              className="btn-primary btn-sm"
            >
              <Save size={14} />
            </button>
          </Tooltip>
        )}
      </div>
    </div>
  );
}
