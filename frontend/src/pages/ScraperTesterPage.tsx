import { useState, useMemo, useRef, useCallback, type FormEvent } from "react";
import ReactDOM from "react-dom";
import {
  FlaskConical, Link as LinkIcon, User, Music, Loader2, Info,
  CheckCircle2, AlertTriangle, ChevronDown, ChevronUp, ExternalLink,
  Database, Sparkles, Tag, Clock, Image, ArrowRight,
  Disc3, Search, Globe, XCircle, Zap, Timer, FolderOpen, File, Download,
  MessageSquarePlus,
} from "lucide-react";
import { scraperTestApi } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { Tooltip } from "@/components/Tooltip";
import type { ProvenanceField, ScraperTestResult, ScraperTestProgress, DirectoryScanResult } from "@/types";

/* ─────────────────────── Provenance palette ─────────────────────── */
const PROV: Record<string, { bg: string; text: string; border: string; label: string }> = {
  "yt-dlp":               { bg: "bg-blue-500/20",    text: "text-blue-400",    border: "border-blue-500/30",    label: "yt-dlp" },
  "nfo":                  { bg: "bg-sky-500/20",     text: "text-sky-400",     border: "border-sky-500/30",     label: "NFO" },
  "filename":             { bg: "bg-cyan-500/20",    text: "text-cyan-400",    border: "border-cyan-500/30",    label: "Filename" },
  "fallback":             { bg: "bg-zinc-500/20",    text: "text-zinc-400",    border: "border-zinc-500/30",    label: "Fallback" },
  "library":              { bg: "bg-indigo-500/20",  text: "text-indigo-400",  border: "border-indigo-500/30",  label: "Library" },
  "parsed":               { bg: "bg-cyan-500/20",     text: "text-cyan-400",    border: "border-cyan-500/30",    label: "Parsed" },
  "override":             { bg: "bg-purple-500/20",   text: "text-purple-400",  border: "border-purple-500/30",  label: "Override" },
  "musicbrainz":          { bg: "bg-orange-500/20",   text: "text-orange-400",  border: "border-orange-500/30",  label: "MusicBrainz" },
  "musicbrainz_coverart": { bg: "bg-orange-500/20",   text: "text-orange-400",  border: "border-orange-500/30",  label: "MB Cover Art" },
  "wikipedia":            { bg: "bg-emerald-500/20",  text: "text-emerald-400", border: "border-emerald-500/30", label: "Wikipedia" },
  "wikipedia_artist":     { bg: "bg-emerald-500/20",  text: "text-emerald-400", border: "border-emerald-500/30", label: "Artist (Wikipedia)" },
  "wikipedia_album":      { bg: "bg-emerald-500/20",  text: "text-emerald-400", border: "border-emerald-500/30", label: "Album (Wikipedia)" },
  "ai":                   { bg: "bg-violet-500/20",   text: "text-violet-400",  border: "border-violet-500/30",  label: "AI" },
  "ai_review":            { bg: "bg-pink-500/20",     text: "text-pink-400",    border: "border-pink-500/30",    label: "AI Review" },
  "imdb":                 { bg: "bg-yellow-500/20",   text: "text-yellow-400",  border: "border-yellow-500/30",  label: "IMDB" },
  "artist_scraper":       { bg: "bg-violet-500/20",   text: "text-violet-400",  border: "border-violet-500/30",  label: "Artist Scraper" },
  "album_scraper":        { bg: "bg-teal-500/20",     text: "text-teal-400",    border: "border-teal-500/30",    label: "Album (CAA)" },
  "album_scraper_wiki":   { bg: "bg-emerald-500/20",  text: "text-emerald-400", border: "border-emerald-500/30", label: "Album (Wikipedia)" },
  "coverartarchive":      { bg: "bg-orange-500/20",   text: "text-orange-400",  border: "border-orange-500/30",  label: "Cover Art Archive" },
  "musicbrainz_artist":   { bg: "bg-orange-500/20",   text: "text-orange-400",  border: "border-orange-500/30",  label: "MB Artist" },
  "musicbrainz_release":  { bg: "bg-orange-500/20",   text: "text-orange-400",  border: "border-orange-500/30",  label: "MB Release" },
  "musicbrainz_release_group": { bg: "bg-orange-500/20", text: "text-orange-400", border: "border-orange-500/30", label: "MB Release Group" },
  "none":                 { bg: "bg-zinc-500/20",     text: "text-zinc-500",    border: "border-zinc-500/30",    label: "Not Found" },
  "unknown":              { bg: "bg-zinc-500/20",     text: "text-zinc-400",    border: "border-zinc-500/30",    label: "Unknown" },
};
function prov(source: string) { return PROV[source] ?? PROV["none"]; }

/* ─── Source URL entity-type descriptors (for Source URLs tile) ─── */
const SOURCE_URL_META: Record<string, { label: string; group: string }> = {
  video:                      { label: "YouTube / Video",         group: "video" },
  imdb:                       { label: "IMDB",                    group: "video" },
  musicbrainz:                { label: "MusicBrainz Recording",   group: "song" },
  musicbrainz_release:        { label: "MusicBrainz Release",     group: "song" },
  musicbrainz_release_group:  { label: "MusicBrainz Release Group", group: "song" },
  coverartarchive:            { label: "Cover Art Archive",       group: "song" },
  wikipedia:                  { label: "Wikipedia",               group: "song" },
  musicbrainz_album_release:  { label: "MB Album Release",        group: "album" },
  musicbrainz_album:          { label: "MB Album Release Group",  group: "album" },
  wikipedia_album:            { label: "Wikipedia Album",         group: "album" },
  musicbrainz_artist:         { label: "MusicBrainz Artist",      group: "artist" },
  wikipedia_artist:           { label: "Wikipedia Artist",        group: "artist" },
};
function sourceUrlMeta(key: string) { return SOURCE_URL_META[key] ?? { label: key, group: "other" }; }

const SOURCE_GROUPS: { key: string; heading: string; color: string }[] = [
  { key: "video",  heading: "Video",  color: "text-blue-400" },
  { key: "song",   heading: "Song / Single",  color: "text-amber-400" },
  { key: "album",  heading: "Album",  color: "text-cyan-400" },
  { key: "artist", heading: "Artist", color: "text-violet-400" },
  { key: "other",  heading: "Other",  color: "text-text-muted" },
];

function Badge({ source, className = "" }: { source: string; className?: string }) {
  const s = prov(source);
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${s.bg} ${s.text} ${className}`}>
      {s.label}
    </span>
  );
}

/* ─────────────────────── Toggle ─────────────────────── */
function Toggle({ label, description, checked, onChange }: { label: string; description?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center justify-between gap-3 text-sm text-text-secondary cursor-pointer">
      <div>
        <span className="font-medium">{label}</span>
        {description && <p className="text-[11px] text-text-muted mt-0.5">{description}</p>}
      </div>
      <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={`relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors duration-200 ${checked ? "bg-accent" : "bg-surface-lighter"}`}>
        <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-200 mt-0.5 ${checked ? "translate-x-4 ml-0.5" : "translate-x-0.5"}`} />
      </button>
    </label>
  );
}

/* ─────────────────────── Collapsible ─────────────────────── */
function Collapsible({ title, count, children, defaultOpen = false, variant = "default" }: {
  title: string; count?: number; children: React.ReactNode; defaultOpen?: boolean;
  variant?: "default" | "violet" | "amber";
}) {
  const [open, setOpen] = useState(defaultOpen);
  const border = variant === "violet" ? "border-violet-500/30" : variant === "amber" ? "border-amber-500/30" : "border-surface-border";
  const bg = variant === "violet" ? "bg-violet-500/5" : variant === "amber" ? "bg-amber-500/5" : "bg-surface-light";
  const hoverBg = variant === "violet" ? "hover:bg-violet-500/10" : variant === "amber" ? "hover:bg-amber-500/10" : "hover:bg-surface-lighter/50";
  const textColor = variant === "violet" ? "text-violet-400" : variant === "amber" ? "text-amber-400" : "text-text-primary";
  const divider = variant === "violet" ? "border-violet-500/20" : variant === "amber" ? "border-amber-500/20" : "border-surface-border";
  return (
    <div className={`rounded-xl border overflow-hidden ${border} ${bg}`}>
      <button onClick={() => setOpen(!open)} className={`w-full flex items-center justify-between px-4 py-3 transition-colors ${hoverBg} ${textColor}`}>
        <h3 className="text-sm font-semibold flex items-center gap-2">
          {variant === "violet" && <Sparkles size={14} />}
          {variant === "amber" && <AlertTriangle size={14} />}
          {title}{count != null && <span className="text-text-muted font-normal">({count})</span>}
        </h3>
        {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
      </button>
      {open && <div className={`border-t ${divider}`}>{children}</div>}
    </div>
  );
}

/* ─────────────────────── Format helpers ─────────────────────── */
function fmtDuration(s?: number | null) {
  if (!s) return null;
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}
function fmtVal(v: any): string {
  if (v == null || v === "") return "";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

/* ═══════════════════════════════════════════════════════════════
   PIPELINE FLOW VISUALIZER
   Parses the ScraperTestResult into linear steps showing data
   flowing through yt-dlp → parsing → scrapers → AI → final result
   ═══════════════════════════════════════════════════════════════ */

type StepStatus = "success" | "fail" | "skip" | "info" | "warn";

interface FlowStep {
  id: string;
  label: string;
  source: string;         // provenance key for coloring
  status: StepStatus;
  icon: React.ReactNode;
  /** Data that was INPUT to this step */
  dataIn?: { label: string; value: string }[];
  /** Data that was OUTPUT from this step */
  dataOut?: { label: string; value: string }[];
  /** Notable decisions / rejections */
  notes?: string[];
  /** Collapsible prompt/response text blocks */
  expandable?: { label: string; content: string; variant?: "prompt" | "response" }[];
}

const STATUS_STYLES: Record<StepStatus, { ring: string; bg: string; icon: string }> = {
  success: { ring: "ring-emerald-500/40", bg: "bg-emerald-500/10", icon: "text-emerald-400" },
  fail:    { ring: "ring-red-500/40",     bg: "bg-red-500/10",     icon: "text-red-400" },
  skip:    { ring: "ring-zinc-500/30",    bg: "bg-zinc-500/10",    icon: "text-zinc-500" },
  info:    { ring: "ring-blue-500/40",    bg: "bg-blue-500/10",    icon: "text-blue-400" },
  warn:    { ring: "ring-amber-500/40",   bg: "bg-amber-500/10",   icon: "text-amber-400" },
};

function buildFlowSteps(r: ScraperTestResult): FlowStep[] {
  const steps: FlowStep[] = [];
  const logs = r.pipeline_log;
  const fv = (v: any) => fmtVal(v) || "(none)";

  // ── Step 1: yt-dlp extraction ──
  steps.push({
    id: "ytdlp",
    label: "yt-dlp Metadata Extraction",
    source: "yt-dlp",
    status: "success",
    icon: <Database size={16} />,
    dataOut: [
      { label: "Title", value: r.ytdlp_title || "(none)" },
      { label: "Artist", value: r.ytdlp_artist || "(none)" },
      { label: "Track", value: r.ytdlp_track || "(none)" },
      { label: "Channel", value: r.ytdlp_channel || r.ytdlp_uploader || "(none)" },
      ...(r.ytdlp_album ? [{ label: "Album", value: r.ytdlp_album }] : []),
      ...(r.ytdlp_duration ? [{ label: "Duration", value: fmtDuration(r.ytdlp_duration) || "" }] : []),
    ],
  });

  // ── Step 2: Title parsing → Artist / Title determination ──
  const parseNotes: string[] = [];
  if (r.parsed_artist && r.parsed_title) {
    parseNotes.push(`Parsed "${r.parsed_artist}" and "${r.parsed_title}" from video title`);
  }
  // Check if override was used
  if (r.artist.source === "override") parseNotes.push(`Artist override applied`);
  if (r.title.source === "override") parseNotes.push(`Title override applied`);

  // Did we strip artist prefix?
  const stripLog = logs.find(l => l.includes("Stripped artist prefix"));
  if (stripLog) parseNotes.push(stripLog.replace("[scraper-test] ", ""));

  steps.push({
    id: "parse",
    label: "Artist & Title Resolution",
    source: "parsed",
    status: "success",
    icon: <Zap size={16} />,
    dataIn: [
      { label: "Raw Title", value: r.ytdlp_title || "(none)" },
    ],
    dataOut: [
      { label: "Artist", value: fv(r.artist.value) },
      { label: "Title", value: fv(r.title.value) },
    ],
    notes: parseNotes.length ? parseNotes : undefined,
  });

  // ── Step 3: AI Source Resolution (if applicable) ──
  const aiSrcStarted = logs.some(l => l.includes("stage:ai_source_resolution:started") || l.includes("Running AI source resolution"));
  const aiSrcNoProvider = logs.some(l => l.includes("stage:ai_source_resolution:disabled:no_provider"));
  const aiSrcDisabled = logs.some(l => l.includes("stage:ai_source_resolution:disabled"));
  const aiSrcFailed = logs.some(l => l.includes("stage:ai_source_resolution:skipped_or_failed") || l.includes("stage:ai_source_resolution:failed:")) || !!r.ai_source_resolution?.error;

  if (aiSrcStarted || (!aiSrcDisabled && r.ai_source_resolution)) {
    const aiSrcNotes: string[] = [];
    const aiSrcOut: { label: string; value: string }[] = [];

    if (r.ai_source_resolution) {
      const identity = r.ai_source_resolution.identity;
      const sources = r.ai_source_resolution.sources;
      const conf = r.ai_source_resolution.confidence;
      if (identity) {
        if (identity.artist) aiSrcOut.push({ label: "AI Artist", value: identity.artist });
        if (identity.title) aiSrcOut.push({ label: "AI Title", value: identity.title });
        if (identity.album) aiSrcOut.push({ label: "AI Album", value: identity.album });
      }
      if (conf) {
        aiSrcNotes.push(`Identity confidence: ${(conf.identity * 100).toFixed(0)}%`);
        aiSrcNotes.push(`Sources confidence: ${(conf.sources * 100).toFixed(0)}%`);
      }
      if (sources) {
        if (sources.wikipedia_url) aiSrcOut.push({ label: "Wikipedia URL", value: sources.wikipedia_url });
        if (sources.musicbrainz_recording_id) aiSrcOut.push({ label: "MB Recording ID", value: sources.musicbrainz_recording_id });
        if (sources.imdb_url) aiSrcOut.push({ label: "IMDB URL", value: sources.imdb_url });
      }
    }

    // Add failure reason from pipeline failures
    if (aiSrcFailed) {
      const srcFailure = r.pipeline_failures?.find((f: { code: string }) => f.code === "AI_SOURCE_FAILED" || f.code === "AI_NO_PROVIDER");
      if (srcFailure) aiSrcNotes.push(srcFailure.description);
      // Also capture the log message about failure
      const failLog = logs.find(l => l.includes("AI source resolution:") && (l.includes("failed") || l.includes("no result") || l.includes("SKIPPED")));
      if (failLog && !srcFailure) aiSrcNotes.push(failLog.replace(/.*AI source resolution:\s*/, ""));
    }

    // Build expandable prompt/response blocks
    const aiSrcExpandable: FlowStep["expandable"] = [];
    if (r.ai_source_resolution) {
      if (r.ai_source_resolution.prompt_used) aiSrcExpandable.push({ label: `Prompt Sent${r.ai_source_resolution.model_name ? ` (${r.ai_source_resolution.model_name})` : ""}`, content: r.ai_source_resolution.prompt_used, variant: "prompt" });
      if (r.ai_source_resolution.raw_response) aiSrcExpandable.push({ label: r.ai_source_resolution.error ? "Error Response" : "AI Response", content: r.ai_source_resolution.raw_response, variant: "response" });
    }

    steps.push({
      id: "ai_source",
      label: "AI Source Resolution",
      source: "ai",
      status: aiSrcFailed ? "fail" : "success",
      icon: <Sparkles size={16} />,
      dataIn: [
        { label: "Artist", value: fv(r.artist.value) },
        { label: "Title", value: fv(r.title.value) },
      ],
      dataOut: aiSrcOut.length ? aiSrcOut : [{ label: "Result", value: "No suggestions" }],
      notes: aiSrcNotes.length ? aiSrcNotes : undefined,
      expandable: aiSrcExpandable.length ? aiSrcExpandable : undefined,
    });
  } else if (aiSrcDisabled) {
    steps.push({
      id: "ai_source",
      label: "AI Source Resolution",
      source: "ai",
      status: aiSrcNoProvider ? "fail" : "skip",
      icon: <Sparkles size={16} />,
      notes: [aiSrcNoProvider
        ? "No AI provider configured — set one in Settings → AI Provider"
        : "Skipped — AI not enabled for this mode"],
    });
  }

  // ── Step 4: MusicBrainz ──
  const mbLogs = logs.filter(l =>
    l.includes("MusicBrainz:") || l.includes("musicbrainz:")
  );
  const mbSkipped = mbLogs.some(l => l.includes("skipped (disabled)"));
  const mbResolved = r.scraper_sources_used.some(s => s.startsWith("musicbrainz:"));
  const mbMethod = r.scraper_sources_used.find(s => s.startsWith("musicbrainz:"));

  if (mbSkipped) {
    steps.push({
      id: "musicbrainz",
      label: "MusicBrainz Lookup",
      source: "musicbrainz",
      status: "skip",
      icon: <Database size={16} />,
      notes: ["Skipped — MusicBrainz disabled"],
    });
  } else {
    const mbOut: { label: string; value: string }[] = [];
    const mbNotes: string[] = [];

    if (mbMethod) mbNotes.push(`Method: ${mbMethod === "musicbrainz:ai_id" ? "AI-provided recording ID" : "Search-based lookup"}`);

    if (mbResolved) {
      if (r.album.source === "musicbrainz" && r.album.value) mbOut.push({ label: "Album", value: fv(r.album.value) });
      if (r.year.source === "musicbrainz" && r.year.value) mbOut.push({ label: "Year", value: fv(r.year.value) });
      if (r.genres.source === "musicbrainz" && r.genres.value) mbOut.push({ label: "Genres", value: fv(r.genres.value) });
      if (r.mb_recording_id.value) mbOut.push({ label: "Recording ID", value: fv(r.mb_recording_id.value) });
      if (r.mb_release_id.value) mbOut.push({ label: "Release ID", value: fv(r.mb_release_id.value) });
    }

    // Extract rejection/decision notes from logs
    for (const l of mbLogs) {
      if (l.includes("rejected") || l.includes("doesn't match") || l.includes("discarded") || l.includes("failed")) {
        const cleaned = l.replace(/^\s*Scraper:\s*/, "").replace(/^scraper:/, "");
        mbNotes.push(cleaned);
      }
      if (l.includes("parent album")) {
        const cleaned = l.replace(/^\s*Scraper:\s*/, "").replace(/^scraper:/, "");
        mbNotes.push(cleaned);
      }
    }

    steps.push({
      id: "musicbrainz",
      label: "MusicBrainz Lookup",
      source: "musicbrainz",
      status: mbResolved ? "success" : "fail",
      icon: <Database size={16} />,
      dataIn: [
        { label: "Artist", value: fv(r.artist.value) },
        { label: "Title", value: fv(r.title.value) },
      ],
      dataOut: mbOut.length ? mbOut : [{ label: "Result", value: "No match found" }],
      notes: mbNotes.length ? mbNotes : undefined,
    });
  }

  // ── Step 5: Wikipedia ──
  const wikiLogs = logs.filter(l =>
    l.includes("Wikipedia:") || l.includes("wikipedia:")
  );
  const wikiResolved = r.scraper_sources_used.some(s => s.startsWith("wikipedia:"));
  const wikiMethod = r.scraper_sources_used.find(s => s.startsWith("wikipedia:"));
  // Check if Wikipedia was even attempted (not in No Scraping / AI Only modes)
  const wikiAttempted = logs.some(l =>
    l.includes("Wikipedia: using AI-provided") ||
    l.includes("Wikipedia: search") ||
    l.includes("Wikipedia: no confident")
  );

  if (!wikiAttempted && !wikiResolved) {
    steps.push({
      id: "wikipedia",
      label: "Wikipedia Scrape",
      source: "wikipedia",
      status: "skip",
      icon: <Globe size={16} />,
      notes: ["Skipped — Wikipedia disabled"],
    });
  } else {
    const wikiOut: { label: string; value: string }[] = [];
    const wikiNotes: string[] = [];

    if (wikiMethod) wikiNotes.push(`Method: ${wikiMethod === "wikipedia:ai_url" ? "AI-provided URL" : "Search-based"}`);

    if (wikiResolved) {
      if (r.plot.source === "wikipedia" && r.plot.value) {
        const plotLen = String(r.plot.value).length;
        wikiOut.push({ label: "Plot", value: `${plotLen} chars retrieved` });
      }
      if (r.genres.source === "wikipedia" && r.genres.value) wikiOut.push({ label: "Genres", value: fv(r.genres.value) });
      if (r.image_url.source === "wikipedia" && r.image_url.value) wikiOut.push({ label: "Image", value: "Cover art found" });
      const wikiUrl = r.source_urls?.wikipedia;
      if (wikiUrl) wikiOut.push({ label: "URL", value: wikiUrl });
    }

    // Extract rejections
    for (const l of wikiLogs) {
      if (l.includes("mismatch") || l.includes("no confident") || l.includes("failed") || l.includes("no usable data")) {
        const cleaned = l.replace(/^\s*Scraper:\s*/, "").replace(/^scraper:/, "");
        wikiNotes.push(cleaned);
      }
    }

    steps.push({
      id: "wikipedia",
      label: "Wikipedia Scrape",
      source: "wikipedia",
      status: wikiResolved ? "success" : "fail",
      icon: <Globe size={16} />,
      dataIn: [
        { label: "Artist", value: fv(r.artist.value) },
        { label: "Title", value: fv(r.title.value) },
      ],
      dataOut: wikiOut.length ? wikiOut : [{ label: "Result", value: "No article found" }],
      notes: wikiNotes.length ? wikiNotes : undefined,
    });
  }

  // ── Step 6: IMDB ──
  const imdbResolved = r.scraper_sources_used.some(s => s.startsWith("imdb:"));
  const imdbAttempted = logs.some(l => l.includes("IMDB:"));

  if (imdbAttempted || imdbResolved) {
    const imdbOut: { label: string; value: string }[] = [];
    if (r.imdb_url.value) imdbOut.push({ label: "IMDB URL", value: fv(r.imdb_url.value) });

    steps.push({
      id: "imdb",
      label: "IMDB Lookup",
      source: "imdb",
      status: imdbResolved ? "success" : "fail",
      icon: <Search size={16} />,
      dataIn: [
        { label: "Artist", value: fv(r.artist.value) },
        { label: "Title", value: fv(r.title.value) },
      ],
      dataOut: imdbOut.length ? imdbOut : [{ label: "Result", value: "No match" }],
    });
  }

  // ── Step 7: AI Final Review (if applicable) ──
  const aiReviewStarted = logs.some(l => l.includes("stage:ai_final_review:started") || l.includes("Running AI final review"));
  const aiReviewDisabled = logs.some(l => l.includes("stage:ai_final_review:disabled"));
  const aiReviewFailed = logs.some(l => l.includes("stage:ai_final_review:skipped_or_failed") || l.includes("stage:ai_final_review:failed:"));

  if (aiReviewStarted || r.ai_final_review) {
    const aiRevNotes: string[] = [];
    const aiRevOut: { label: string; value: string }[] = [];
    const changes = r.ai_changes ?? [];

    if (r.ai_final_review) {
      const conf = r.ai_final_review.confidence;
      if (conf != null) aiRevNotes.push(`Overall confidence: ${(conf * 100).toFixed(0)}%`);
    }

    if (changes.length > 0) {
      for (const ch of changes) {
        const bv = fmtVal(ch.before) || "(empty)";
        const av = fmtVal(ch.after) || "(empty)";
        aiRevOut.push({ label: ch.field, value: `"${bv}" → "${av}"` });
      }
    } else {
      aiRevOut.push({ label: "Changes", value: "None — metadata validated as correct" });
    }

    // Artwork decision
    if (r.ai_final_review?.artwork_approved === false) {
      aiRevNotes.push("Artwork rejected by AI review");
    }

    // Capture rejections from logs
    for (const l of logs) {
      if (l.includes("AI Final Review:") && (l.includes("skipping") || l.includes("discarded") || l.includes("rejected") || l.includes("sanitized"))) {
        aiRevNotes.push(l.replace("AI Final Review: ", ""));
      }
    }

    // Add failure reason from pipeline failures
    if (aiReviewFailed) {
      const revFailure = r.pipeline_failures?.find((f: { code: string }) => f.code === "AI_REVIEW_FAILED" || f.code === "AI_NO_PROVIDER");
      if (revFailure) aiRevNotes.push(revFailure.description);
      const failLog = logs.find(l => l.includes("AI final review:") && (l.includes("failed") || l.includes("no result") || l.includes("SKIPPED")));
      if (failLog && !revFailure) aiRevNotes.push(failLog.replace(/.*AI final review:\s*/, ""));
    }

    // Build expandable prompt/response blocks
    const aiRevExpandable: FlowStep["expandable"] = [];
    if (r.ai_final_review) {
      if (r.ai_final_review.prompt_used) aiRevExpandable.push({ label: `Prompt Sent${r.ai_final_review.model_name ? ` (${r.ai_final_review.model_name})` : ""}`, content: r.ai_final_review.prompt_used, variant: "prompt" });
      if (r.ai_final_review.raw_response) aiRevExpandable.push({ label: r.ai_final_review.error ? "Error Response" : "AI Response", content: r.ai_final_review.raw_response, variant: "response" });
    }

    steps.push({
      id: "ai_review",
      label: "AI Final Review",
      source: "ai_review",
      status: aiReviewFailed ? "fail" : changes.length > 0 ? "warn" : "success",
      icon: <Sparkles size={16} />,
      dataIn: [
        { label: "All scraped metadata", value: "Artist, title, album, year, genres, plot" },
      ],
      dataOut: aiRevOut,
      notes: aiRevNotes.length ? aiRevNotes : undefined,
      expandable: aiRevExpandable.length ? aiRevExpandable : undefined,
    });
  } else if (aiReviewDisabled) {
    const aiRevNoProvider = logs.some(l => l.includes("stage:ai_final_review:disabled:no_provider"));
    steps.push({
      id: "ai_review",
      label: "AI Final Review",
      source: "ai_review",
      status: aiRevNoProvider ? "fail" : "skip",
      icon: <Sparkles size={16} />,
      notes: [aiRevNoProvider
        ? "No AI provider configured — set one in Settings → AI Provider"
        : "Skipped — AI not enabled"],
    });
  }

  // ── Step 8: Post-review cleanup (only if there were cascading changes) ──
  const cleanupLogs = logs.filter(l =>
    l.includes("cleared") || l.includes("Re-synced") || l.includes("re-resolved") || l.includes("sanitized")
  );
  if (cleanupLogs.length > 0) {
    steps.push({
      id: "cleanup",
      label: "Post-Review Cleanup",
      source: "ai_review",
      status: "warn",
      icon: <AlertTriangle size={16} />,
      notes: cleanupLogs.map(l => l.replace("[scraper-test] ", "")),
    });
  }

  // ── Step 9: Final Result ──
  const finalOut: { label: string; value: string }[] = [
    { label: "Artist", value: fv(r.artist.value) },
    { label: "Title", value: fv(r.title.value) },
  ];
  if (r.album.value) finalOut.push({ label: "Album", value: fv(r.album.value) });
  if (r.year.value) finalOut.push({ label: "Year", value: fv(r.year.value) });
  if (r.genres.value) finalOut.push({ label: "Genres", value: fv(r.genres.value) });
  if (r.plot.value) finalOut.push({ label: "Plot", value: `${String(r.plot.value).length} chars` });
  if (r.image_url.value) finalOut.push({ label: "Artwork", value: "Found" });
  if (r.mb_recording_id.value) finalOut.push({ label: "MB Recording", value: fv(r.mb_recording_id.value) });

  steps.push({
    id: "final",
    label: "Final Result",
    source: "none",
    status: "success",
    icon: <CheckCircle2 size={16} />,
    dataOut: finalOut,
  });

  return steps;
}

function PipelineFlowVisualizer({ r }: { r: ScraperTestResult }) {
  const steps = useMemo(() => buildFlowSteps(r), [r]);

  return (
    <div className="card">
      <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-4 flex items-center gap-2">
        <Zap size={13} /> Pipeline Flow
      </h3>
      <div className="relative">
        {steps.map((step, i) => (
          <PipelineStepRow key={step.id} step={step} isLast={i === steps.length - 1} nextSkip={steps[i + 1]?.status === "skip"} />
        ))}
      </div>
    </div>
  );
}

/** Single step row — pulled out so each can hold its own expand/collapse state */
function PipelineStepRow({ step, isLast, nextSkip }: { step: FlowStep; isLast: boolean; nextSkip: boolean }) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const st = STATUS_STYLES[step.status];
  const pr = prov(step.source);
  const isFinal = step.id === "final";

  const toggleExpand = (idx: number) => setExpanded(prev => ({ ...prev, [idx]: !prev[idx] }));

  return (
    <div>
      {/* Step node */}
      <div className={`relative flex gap-3 ${isFinal ? "mt-2" : ""}`}>
        {/* Left: icon circle + connecting line */}
        <div className="flex flex-col items-center flex-shrink-0">
          <div className={`w-8 h-8 rounded-full ring-2 ${st.ring} ${st.bg} flex items-center justify-center ${st.icon}`}>
            {step.status === "skip" ? <XCircle size={14} /> : step.icon}
          </div>
          {!isLast && (
            <div className="w-px flex-1 min-h-[16px] bg-surface-border/50" />
          )}
        </div>

        {/* Right: content */}
        <div className={`flex-1 min-w-0 pb-4 ${isLast ? "" : ""}`}>
          {/* Header row */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-sm font-semibold ${step.status === "skip" ? "text-text-muted" : "text-text-primary"}`}>
              {step.label}
            </span>
            <span className={`inline-flex px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wider ${pr.bg} ${pr.text}`}>
              {pr.label}
            </span>
            {step.status === "success" && <CheckCircle2 size={12} className="text-emerald-400" />}
            {step.status === "fail" && <XCircle size={12} className="text-red-400" />}
            {step.status === "warn" && <AlertTriangle size={12} className="text-amber-400" />}
          </div>

          {/* Data flow cards */}
          {step.status !== "skip" && (step.dataIn || step.dataOut) && (
            <div className="mt-2 flex flex-wrap gap-2 items-start">
              {step.dataIn && (
                <div className="rounded-md border border-surface-border/40 bg-surface px-2.5 py-1.5 text-[11px]">
                  <p className="text-[9px] font-semibold text-text-muted uppercase tracking-wider mb-0.5">In</p>
                  {step.dataIn.map((d, j) => (
                    <div key={j} className="flex gap-1.5 py-0.5">
                      <span className="text-text-muted">{d.label}:</span>
                      <span className="text-text-secondary font-medium truncate max-w-[200px]">{d.value}</span>
                    </div>
                  ))}
                </div>
              )}
              {step.dataIn && step.dataOut && (
                <ArrowRight size={14} className="text-text-muted/40 mt-3 flex-shrink-0" />
              )}
              {step.dataOut && (
                <div className={`rounded-md border px-2.5 py-1.5 text-[11px] ${
                  isFinal
                    ? "border-accent/30 bg-accent/5"
                    : step.status === "fail"
                      ? "border-red-500/20 bg-red-500/5"
                      : "border-emerald-500/20 bg-emerald-500/5"
                }`}>
                  <p className={`text-[9px] font-semibold uppercase tracking-wider mb-0.5 ${
                    isFinal ? "text-accent" : step.status === "fail" ? "text-red-400" : "text-emerald-400"
                  }`}>
                    {isFinal ? "Result" : "Out"}
                  </p>
                  {step.dataOut.map((d, j) => (
                    <div key={j} className="flex gap-1.5 py-0.5">
                      <span className="text-text-muted">{d.label}:</span>
                      <span className={`font-medium truncate max-w-[280px] ${
                        step.status === "fail" ? "text-red-400" : "text-text-primary"
                      }`}>{d.value}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Notes */}
          {step.notes && step.notes.length > 0 && (
            <div className="mt-1.5 space-y-0.5">
              {step.notes.map((note, j) => (
                <p key={j} className="text-[11px] text-text-muted italic flex items-start gap-1">
                  <span className="text-text-muted/40 mt-0.5">›</span> {note}
                </p>
              ))}
            </div>
          )}

          {/* Expandable prompt/response tiles */}
          {step.expandable && step.expandable.length > 0 && (
            <div className="mt-2 space-y-1.5">
              {step.expandable.map((block, idx) => {
                const isOpen = !!expanded[idx];
                const isPrompt = block.variant === "prompt";
                const borderColor = isPrompt ? "border-violet-500/30" : "border-emerald-500/30";
                const bgColor = isPrompt ? "bg-violet-500/5" : "bg-emerald-500/5";
                const hoverBgColor = isPrompt ? "hover:bg-violet-500/10" : "hover:bg-emerald-500/10";
                const textColor = isPrompt ? "text-violet-400" : "text-emerald-400";
                return (
                  <div key={idx} className={`rounded-lg border overflow-hidden ${borderColor} ${bgColor}`}>
                    <button
                      onClick={() => toggleExpand(idx)}
                      className={`w-full flex items-center justify-between px-3 py-2 transition-colors ${hoverBgColor}`}
                    >
                      <span className={`text-[11px] font-semibold flex items-center gap-1.5 ${textColor}`}>
                        {isPrompt ? <ArrowRight size={11} /> : <Sparkles size={11} />}
                        {block.label}
                      </span>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-text-muted">{block.content.length.toLocaleString()} chars</span>
                        {isOpen ? <ChevronUp size={13} className="text-text-muted" /> : <ChevronDown size={13} className="text-text-muted" />}
                      </div>
                    </button>
                    {isOpen && (
                      <div className={`border-t ${borderColor} px-3 py-2`}>
                        <pre className="text-[11px] text-text-secondary whitespace-pre-wrap break-words font-mono leading-relaxed max-h-[400px] overflow-y-auto">
                          {block.content}
                        </pre>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Arrow between steps */}
      {!isLast && step.status !== "skip" && !nextSkip && (
        <div className="ml-[15px] flex items-center h-0">
          {/* The connecting line is already part of the icon column */}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   MAIN PAGE
   ═══════════════════════════════════════════════════════════════ */
export function ScraperTesterPage() {
  const [testMode, setTestMode] = useState<"url" | "import">("url");

  // URL mode state
  const [url, setUrl] = useState("");

  // Import mode state
  const [directory, setDirectory] = useState("");
  const [scanResult, setScanResult] = useState<DirectoryScanResult | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [isScanning, setIsScanning] = useState(false);

  // Shared state
  const [artistOverride, setArtistOverride] = useState("");
  const [titleOverride, setTitleOverride] = useState("");
  const [scrapeWiki, setScrapeWiki] = useState(true);
  const [scrapeMusicbrainz, setScrapeMusicbrainz] = useState(true);
  const [wikiUrl, setWikiUrl] = useState("");
  const [mbUrl, setMbUrl] = useState("");
  const [aiAuto, setAiAuto] = useState(false);
  const [aiOnly, setAiOnly] = useState(false);

  const [isPending, setIsPending] = useState(false);
  const [result, setResult] = useState<ScraperTestResult | null>(null);
  const [steps, setSteps] = useState<ScraperTestProgress[]>([]);
  const streamRef = useRef<{ close: () => void } | null>(null);
  const { toast } = useToast();

  // Download dialog state
  const [downloadDialogOpen, setDownloadDialogOpen] = useState(false);
  const [downloadStep, setDownloadStep] = useState<"warning" | "feedback">("warning");
  const [feedbackText, setFeedbackText] = useState("");

  const urlInfo = useMemo(() => {
    const t = url.trim();
    if (!t) return null;
    const isYT = /^https?:\/\/(www\.)?(youtube\.com|youtu\.be)\//i.test(t);
    const isVimeo = /^https?:\/\/(www\.)?vimeo\.com\//i.test(t);
    const isPL = isYT && (/[?&]list=/i.test(t) || /\/playlist\?/i.test(t));
    if (isPL) return { provider: "YouTube", isPlaylist: true, valid: false } as const;
    if (isYT) return { provider: "YouTube", isPlaylist: false, valid: true } as const;
    if (isVimeo) return { provider: "Vimeo", isPlaylist: false, valid: true } as const;
    if (/^https?:\/\//i.test(t)) return { provider: "Unknown", isPlaylist: false, valid: false } as const;
    return null;
  }, [url]);

  // Whether the form can be submitted
  const canSubmit = useMemo(() => {
    if (isPending) return false;
    if (testMode === "url") return !!(url.trim() && urlInfo?.valid);
    // Import mode: need a directory with either 1 video or a selected file
    if (!scanResult || scanResult.video_files.length === 0) return false;
    if (scanResult.has_multiple && !selectedFile) return false;
    return true;
  }, [testMode, url, urlInfo, isPending, scanResult, selectedFile]);

  const handleScanDirectory = useCallback(async () => {
    const dir = directory.trim();
    if (!dir || isScanning) return;
    setIsScanning(true);
    setScanResult(null);
    setSelectedFile(null);
    try {
      const result = await scraperTestApi.scanDirectory(dir);
      setScanResult(result);
      // Auto-select if only one video
      if (result.video_files.length === 1) {
        setSelectedFile(result.video_files[0]);
      }
    } catch (err: any) {
      toast({ type: "error", title: "Directory scan failed", description: err?.response?.data?.detail || err?.message || "Unknown error" });
    } finally {
      setIsScanning(false);
    }
  }, [directory, isScanning, toast]);

  const onProgress = useCallback((p: ScraperTestProgress) => {
    setSteps((prev) => {
      const existing = prev.findIndex((s) => s.step === p.step);
      if (existing >= 0) {
        const next = [...prev];
        next[existing] = p;
        return next;
      }
      return [...prev, p];
    });
  }, []);

  const onResult = useCallback((r: ScraperTestResult) => {
    setResult(r);
    setIsPending(false);
  }, []);

  const onError = useCallback((msg: string) => {
    toast({ type: "error", title: "Scraper test failed", description: msg });
    setIsPending(false);
  }, [toast]);

  const handleSubmit = useCallback((e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;

    // Reset state
    setIsPending(true);
    setResult(null);
    setSteps([]);
    streamRef.current?.close();

    if (testMode === "url") {
      streamRef.current = scraperTestApi.runStream(
        {
          url: url.trim(),
          artist_override: artistOverride.trim() || undefined,
          title_override: titleOverride.trim() || undefined,
          scrape_wikipedia: scrapeWiki,
          scrape_musicbrainz: scrapeMusicbrainz,
          wikipedia_url: scrapeWiki && wikiUrl.trim() ? wikiUrl.trim() : undefined,
          musicbrainz_url: scrapeMusicbrainz && mbUrl.trim() ? mbUrl.trim() : undefined,
          ai_auto: aiAuto,
          ai_only: aiOnly,
        },
        onProgress, onResult, onError,
      );
    } else {
      const chosenFile = scanResult?.has_multiple ? selectedFile ?? undefined : undefined;
      streamRef.current = scraperTestApi.runImportStream(
        {
          directory: directory.trim(),
          file_name: chosenFile,
          artist_override: artistOverride.trim() || undefined,
          title_override: titleOverride.trim() || undefined,
          scrape_wikipedia: scrapeWiki,
          scrape_musicbrainz: scrapeMusicbrainz,
          wikipedia_url: scrapeWiki && wikiUrl.trim() ? wikiUrl.trim() : undefined,
          musicbrainz_url: scrapeMusicbrainz && mbUrl.trim() ? mbUrl.trim() : undefined,
          ai_auto: aiAuto,
          ai_only: aiOnly,
        },
        onProgress, onResult, onError,
      );
    }
  }, [testMode, canSubmit, url, directory, scanResult, selectedFile, artistOverride, titleOverride, scrapeWiki, scrapeMusicbrainz, wikiUrl, mbUrl, aiAuto, aiOnly, onProgress, onResult, onError]);

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 md:p-6">
      {/* ── Page Header ── */}
      <div className="flex items-center gap-3">
        <FlaskConical size={24} className="text-accent" />
        <div>
          <h1 className="text-xl font-bold text-text-primary">Scraper Tester</h1>
          <p className="text-sm text-text-muted">Test scraping without downloading — see exactly what data the pipeline would produce.</p>
        </div>
      </div>

      {/* ── Input Form ── */}
      <form onSubmit={handleSubmit} className="space-y-4 rounded-xl border border-surface-border bg-surface-light p-5">
        {/* ── Mode Toggle ── */}
        <div className="flex gap-1 rounded-lg bg-surface p-1">
          <button type="button"
            className={`flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              testMode === "url"
                ? "bg-accent/20 text-accent border border-accent/30"
                : "text-text-muted hover:text-text-secondary"
            }`}
            onClick={() => setTestMode("url")}
          >
            <LinkIcon size={14} /> URL Mode
          </button>
          <button type="button"
            className={`flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              testMode === "import"
                ? "bg-accent/20 text-accent border border-accent/30"
                : "text-text-muted hover:text-text-secondary"
            }`}
            onClick={() => setTestMode("import")}
          >
            <FolderOpen size={14} /> Import Mode
          </button>
        </div>

        {/* ── URL Input (URL mode) ── */}
        {testMode === "url" && (
          <div>
            <label htmlFor="test-url" className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-text-secondary">
              <LinkIcon size={14} /> Video URL
            </label>
            <input id="test-url" type="url" value={url} onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.youtube.com/watch?v=..." className="input-field" required={testMode === "url"} autoFocus />
            <div className="mt-1 flex items-center gap-2 min-h-[18px]">
              {urlInfo ? (
                urlInfo.isPlaylist ? <span className="flex items-center gap-1 text-xs text-amber-400"><AlertTriangle size={12} /> Playlists not supported</span>
                  : urlInfo.valid ? <span className="flex items-center gap-1 text-xs text-emerald-400"><CheckCircle2 size={12} /> {urlInfo.provider}</span>
                    : <span className="text-xs text-amber-400">Unsupported URL</span>
              ) : <span className="text-xs text-text-muted">YouTube or Vimeo URL</span>}
            </div>
          </div>
        )}

        {/* ── Directory Input (Import mode) ── */}
        {testMode === "import" && (
          <div className="space-y-3">
            <div>
              <label htmlFor="test-dir" className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-text-secondary">
                <FolderOpen size={14} /> Video Directory
              </label>
              <div className="flex gap-2">
                <input id="test-dir" type="text" value={directory} onChange={(e) => { setDirectory(e.target.value); setScanResult(null); setSelectedFile(null); }}
                  placeholder="D:\MusicVideos\Artist - Title [1080p]" className="input-field flex-1"
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleScanDirectory(); } }}
                />
                <button type="button" onClick={handleScanDirectory} disabled={!directory.trim() || isScanning}
                  className="btn-secondary flex items-center gap-1.5 px-4 whitespace-nowrap">
                  {isScanning ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
                  Scan
                </button>
              </div>
              <p className="mt-1 text-xs text-text-muted">Enter a directory path containing a music video file</p>
            </div>

            {/* Scan Results */}
            {scanResult && (
              <div className="rounded-lg border border-surface-border/50 bg-surface p-3 space-y-2">
                {scanResult.video_files.length === 0 ? (
                  <p className="flex items-center gap-1.5 text-xs text-amber-400"><AlertTriangle size={12} /> No video files found in this directory</p>
                ) : scanResult.video_files.length === 1 ? (
                  <div className="flex items-center gap-2">
                    <CheckCircle2 size={14} className="text-emerald-400 flex-shrink-0" />
                    <div>
                      <p className="text-sm text-text-primary font-medium flex items-center gap-1.5"><File size={12} /> {scanResult.video_files[0]}</p>
                      {scanResult.nfo_files.length > 0 && (
                        <p className="text-xs text-text-muted mt-0.5">NFO found: {scanResult.nfo_files.join(", ")}</p>
                      )}
                    </div>
                  </div>
                ) : (
                  <div>
                    <p className="text-xs font-medium text-text-muted uppercase tracking-wide mb-2">
                      {scanResult.video_files.length} video files found — select one:
                    </p>
                    <div className="space-y-1">
                      {scanResult.video_files.map((f) => (
                        <label key={f} className={`flex items-center gap-2 rounded-md px-3 py-2 cursor-pointer transition-colors ${
                          selectedFile === f ? "bg-accent/10 border border-accent/30" : "hover:bg-surface-lighter border border-transparent"
                        }`}>
                          <input type="radio" name="video-file" value={f} checked={selectedFile === f}
                            onChange={() => setSelectedFile(f)} className="accent-accent" />
                          <File size={13} className="text-text-muted flex-shrink-0" />
                          <span className="text-sm text-text-primary">{f}</span>
                        </label>
                      ))}
                    </div>
                    {scanResult.nfo_files.length > 0 && (
                      <p className="text-xs text-text-muted mt-2">NFO files: {scanResult.nfo_files.join(", ")}</p>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label htmlFor="test-artist" className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-text-secondary"><User size={14} /> Artist Override</label>
            <input id="test-artist" type="text" value={artistOverride} onChange={(e) => setArtistOverride(e.target.value)} placeholder="Auto-detect" className="input-field" />
          </div>
          <div>
            <label htmlFor="test-title" className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-text-secondary"><Music size={14} /> Title Override</label>
            <input id="test-title" type="text" value={titleOverride} onChange={(e) => setTitleOverride(e.target.value)} placeholder="Auto-detect" className="input-field" />
          </div>
        </div>

        <div className="space-y-2 rounded-lg border border-surface-border/50 p-3">
          <div className="flex items-center gap-1.5">
            <p className="text-xs font-medium text-text-muted uppercase tracking-wide">Scraping Mode</p>
            <Tooltip content="Wikipedia + MusicBrainz can be enabled together. AI modes are exclusive.">
              <span><Info size={12} className="text-text-muted" /></span>
            </Tooltip>
          </div>
          <div>
            <Toggle label="Wikipedia" description="Plot, genre, and background from Wikipedia" checked={scrapeWiki} onChange={(v) => { setScrapeWiki(v); if (v) { setAiAuto(false); setAiOnly(false); } }} />
            {scrapeWiki && (
              <div className="mt-1.5 ml-5">
                <label className="text-[11px] text-text-muted mb-1 block">Wikipedia URL (optional — auto-search if blank)</label>
                <input type="url" value={wikiUrl} onChange={(e) => setWikiUrl(e.target.value)} placeholder="https://en.wikipedia.org/wiki/..." className="input-field w-full text-xs" />
              </div>
            )}
          </div>
          <div>
            <Toggle label="MusicBrainz" description="Album, year, genre tags, cover art" checked={scrapeMusicbrainz} onChange={(v) => { setScrapeMusicbrainz(v); if (v) { setAiAuto(false); setAiOnly(false); } }} />
            {scrapeMusicbrainz && (
              <div className="mt-1.5 ml-5">
                <label className="text-[11px] text-text-muted mb-1 block">MusicBrainz recording URL (optional — auto-search if blank)</label>
                <input type="url" value={mbUrl} onChange={(e) => setMbUrl(e.target.value)} placeholder="https://musicbrainz.org/recording/..." className="input-field w-full text-xs" />
              </div>
            )}
          </div>
          <Toggle label="AI Auto" description="Full AI-guided enrichment after scraping" checked={aiAuto} onChange={(v) => { setAiAuto(v); if (v) { setScrapeWiki(false); setScrapeMusicbrainz(false); setAiOnly(false); } }} />
          <Toggle label="AI Only" description="Skip all external scrapers — AI generates everything" checked={aiOnly} onChange={(v) => { setAiOnly(v); if (v) { setScrapeWiki(false); setScrapeMusicbrainz(false); setAiAuto(false); } }} />
        </div>

        <button type="submit" disabled={!canSubmit} className="btn-primary w-full">
          {isPending
            ? <><Loader2 size={16} className="animate-spin" /> Running scraper test...</>
            : testMode === "url"
              ? <><FlaskConical size={16} /> Run Scraper Test</>
              : <><FlaskConical size={16} /> Run Import Test</>}
        </button>
      </form>

      {/* ═══════════════ PROGRESS BAR ═══════════════ */}
      {(isPending || (steps.length > 0 && !result)) && (
        <StepProgressBar steps={steps} />
      )}

      {/* ═══════════════ RESULTS ═══════════════ */}
      {result && (
        <>
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <CompletedStepsBar steps={steps} />
            </div>
            {result.output_file && (
              <button
                onClick={() => { setDownloadDialogOpen(true); setDownloadStep("warning"); setFeedbackText(""); }}
                className="btn-primary gap-2 shrink-0"
              >
                <Download size={16} /> Download Log
              </button>
            )}
          </div>
          <ResultsView r={result} />
        </>
      )}

      {/* ═══════════════ DOWNLOAD LOG DIALOG ═══════════════ */}
      {downloadDialogOpen && result?.output_file && ReactDOM.createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={() => setDownloadDialogOpen(false)} />
          <div
            className="relative z-10 w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-xl border border-surface-border bg-surface-light p-6 shadow-[0_0_40px_rgba(225,29,46,0.1)]"
            style={{ background: "linear-gradient(135deg, rgba(21,25,35,0.97) 0%, rgba(28,34,48,0.92) 100%)" }}
            role="dialog"
            aria-modal="true"
          >
            <button onClick={() => setDownloadDialogOpen(false)} className="absolute top-4 right-4 text-text-muted hover:text-text-primary" aria-label="Close">
              <XCircle size={18} />
            </button>

            {downloadStep === "warning" ? (
              <>
                <div className="flex items-center gap-2 mb-3">
                  <Download size={20} className="text-accent" />
                  <h2 className="text-lg font-semibold text-text-primary">Download Scraper Log</h2>
                </div>
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 mb-5">
                  <p className="text-sm text-text-secondary leading-relaxed">
                    If you wish to submit this log for <span className="text-amber-400 font-medium">bug testing</span> or <span className="text-amber-400 font-medium">improving scraper functionality</span>, you should also describe what the correct results should be. Detailed feedback helps pinpoint exactly what went wrong.
                  </p>
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={() => setDownloadStep("feedback")}
                    className="btn-primary flex-1 gap-2"
                  >
                    <MessageSquarePlus size={16} /> Add Details
                  </button>
                  <button
                    onClick={() => {
                      const a = document.createElement("a");
                      a.href = scraperTestApi.downloadLogUrl(result.output_file!);
                      a.download = "";
                      document.body.appendChild(a);
                      a.click();
                      document.body.removeChild(a);
                      setDownloadDialogOpen(false);
                    }}
                    className="btn-ghost flex-1 gap-2"
                  >
                    <Download size={16} /> Download Without Details
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="flex items-center gap-2 mb-3">
                  <MessageSquarePlus size={20} className="text-accent" />
                  <h2 className="text-lg font-semibold text-text-primary">Describe Expected Results</h2>
                </div>

                <div className="rounded-lg border border-surface-border bg-surface/50 p-4 mb-4">
                  <p className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-2">Example of a useful report</p>
                  <div className="space-y-1.5 text-xs text-text-secondary leading-relaxed">
                    <p>• <span className="text-emerald-400">Incorrect MusicBrainz album linked.</span> The scraper matched "Greatest Hits" but the correct release is <span className="text-accent">https://musicbrainz.org/release/...</span></p>
                    <p>• <span className="text-emerald-400">Correct source found, but wrong artwork selected.</span> The album art is from a different release — should be the 2008 reissue cover.</p>
                    <p>• <span className="text-emerald-400">Wikipedia article not found.</span> No result was returned, but this track has an article at <span className="text-accent">https://en.wikipedia.org/wiki/...</span></p>
                    <p>• <span className="text-emerald-400">Artist name incorrect.</span> Should be "The Lonely Island" not "Lonely Island".</p>
                    <p>• <span className="text-emerald-400">Year is wrong.</span> Shows 2010 but the single was released in 2009.</p>
                  </div>
                  <p className="text-[11px] text-text-muted mt-3 italic">
                    More complete descriptions with links to correct sources will improve the scraper more effectively.
                  </p>
                </div>

                <textarea
                  value={feedbackText}
                  onChange={(e) => setFeedbackText(e.target.value)}
                  placeholder="Describe what's wrong and what the correct results should be..."
                  rows={5}
                  className="w-full rounded-lg border border-surface-border bg-surface-lighter/50 px-3 py-2 text-sm text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-accent/50 resize-y mb-4"
                />

                <div className="flex gap-3">
                  <button
                    onClick={async () => {
                      if (result.output_file && feedbackText.trim()) {
                        try {
                          await scraperTestApi.saveComments(result.output_file, { "User Feedback": feedbackText.trim() });
                        } catch { /* ignore */ }
                      }
                      const a = document.createElement("a");
                      a.href = scraperTestApi.downloadLogUrl(result.output_file!);
                      a.download = "";
                      document.body.appendChild(a);
                      a.click();
                      document.body.removeChild(a);
                      setDownloadDialogOpen(false);
                      setDownloadStep("warning");
                      setFeedbackText("");
                    }}
                    disabled={!feedbackText.trim()}
                    className="btn-primary flex-1 gap-2 disabled:opacity-40"
                  >
                    <Download size={16} /> Download with Feedback
                  </button>
                  <button
                    onClick={() => setDownloadStep("warning")}
                    className="btn-ghost px-4"
                  >
                    Back
                  </button>
                </div>
              </>
            )}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   STEP PROGRESS BAR — shown during streaming
   ═══════════════════════════════════════════════════════════════ */
function fmtMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const STEP_ICONS: Record<number, React.ReactNode> = {
  1: <LinkIcon size={13} />,
  2: <Loader2 size={13} />,
  3: <Zap size={13} />,
  4: <Database size={13} />,
  5: <User size={13} />,
  6: <Disc3 size={13} />,
  7: <Image size={13} />,
  8: <Sparkles size={13} />,
  9: <CheckCircle2 size={13} />,
};

function StepProgressBar({ steps }: { steps: ScraperTestProgress[] }) {
  const currentStep = steps.length > 0 ? steps[steps.length - 1] : null;
  const total = currentStep?.total ?? 9;
  const completedCount = steps.filter(s => s.status === "complete").length;
  const pct = Math.round((completedCount / total) * 100);

  return (
    <div className="rounded-xl border border-accent/30 bg-surface-light p-4 space-y-3 animate-slide-up">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Loader2 size={16} className="animate-spin text-accent" />
          <span className="text-sm font-semibold text-text-primary">Running Scraper Pipeline</span>
        </div>
        <span className="text-xs text-text-muted font-mono">{completedCount}/{total} steps</span>
      </div>

      {/* Overall progress bar */}
      <div className="h-1.5 rounded-full bg-surface overflow-hidden">
        <div
          className="h-full rounded-full bg-accent transition-all duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Step list */}
      <div className="grid grid-cols-1 gap-1">
        {Array.from({ length: total }, (_, i) => {
          const stepNum = i + 1;
          const step = steps.find(s => s.step === stepNum);
          const isActive = step?.status === "running";
          const isDone = step?.status === "complete";
          const icon = STEP_ICONS[stepNum] ?? <CheckCircle2 size={13} />;
          const label = step?.label ?? `Step ${stepNum}`;
          const subLabel = step?.sub_label;

          return (
            <div
              key={stepNum}
              className={`flex items-center gap-2.5 px-3 py-1.5 rounded-lg transition-all duration-300 ${
                isActive ? "bg-accent/10 border border-accent/30" :
                isDone ? "bg-surface" :
                "opacity-40"
              }`}
            >
              {/* Status icon */}
              <div className={`flex-shrink-0 ${
                isDone ? "text-emerald-400" :
                isActive ? "text-accent" :
                "text-text-muted"
              }`}>
                {isActive ? <Loader2 size={13} className="animate-spin" /> :
                 isDone ? <CheckCircle2 size={13} /> :
                 icon}
              </div>

              {/* Label */}
              <span className={`text-xs flex-1 ${
                isActive ? "text-text-primary font-medium" :
                isDone ? "text-text-secondary" :
                "text-text-muted"
              }`}>
                {label}
                {subLabel && isActive && (
                  <span className="text-accent/70 ml-1.5">— {subLabel}</span>
                )}
              </span>

              {/* Elapsed time */}
              {isDone && step?.elapsed_ms != null && (
                <span className="text-[11px] font-mono text-text-muted flex items-center gap-1">
                  <Timer size={10} />
                  {fmtMs(step.elapsed_ms)}
                </span>
              )}
              {isActive && (
                <span className="text-[11px] text-accent animate-pulse">running</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CompletedStepsBar({ steps }: { steps: ScraperTestProgress[] }) {
  const completedSteps = steps.filter(s => s.status === "complete");
  if (completedSteps.length === 0) return null;
  const totalMs = completedSteps.reduce((sum, s) => sum + (s.elapsed_ms ?? 0), 0);

  return (
    <div className="rounded-xl border border-surface-border bg-surface-light p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-text-muted uppercase tracking-wide flex items-center gap-1.5">
          <Timer size={12} /> Pipeline Timing
        </span>
        <span className="text-xs font-mono text-text-muted">Total: {fmtMs(totalMs)}</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {completedSteps.map((s) => (
          <div key={s.step} className="flex items-center gap-1 px-2 py-1 rounded-md bg-surface text-xs">
            <CheckCircle2 size={10} className="text-emerald-400 flex-shrink-0" />
            <span className="text-text-secondary">{s.label}</span>
            <span className="font-mono text-text-muted">{fmtMs(s.elapsed_ms)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   RESULTS VIEW — mirrors VideoDetailPage layout
   ═══════════════════════════════════════════════════════════════ */
function ResultsView({ r }: { r: ScraperTestResult }) {
  const artCandidates = r.artwork_candidates ?? [];
  const aiChanges = r.ai_changes ?? [];
  const srcUrls = r.source_urls ?? {};
  const appliedArt = artCandidates.find((c) => c.applied);
  const posterUrl = appliedArt?.url ?? r.image_url?.value ?? r.ytdlp_thumbnail;

  return (
    <div className="space-y-6 animate-slide-up">
      {/* ────────────── HEADER ────────────── */}
      <header>
        <div className="flex items-center gap-2 flex-wrap">
          <h2 className="text-xl md:text-2xl font-bold text-text-primary break-words">
            {r.artist.value && <span className="text-accent font-semibold">{fmtVal(r.artist.value)}</span>}
            {r.artist.value && r.title.value && <span className="text-text-muted mx-2">–</span>}
            <span>{fmtVal(r.title.value) || "Unknown Title"}</span>
          </h2>
          <Badge source={r.artist.source} />
        </div>
        <div className="flex items-center gap-2 mt-1 flex-wrap">
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-accent/20 text-accent">{r.mode}</span>
          {r.year.value && <span className="text-xs text-text-muted">{r.year.value}</span>}
          {r.album.value && (
            <span className="text-xs text-text-muted">
              <span className="text-text-muted/50 mx-1">·</span>{fmtVal(r.album.value)}
              <Badge source={r.album.source} className="ml-1" />
            </span>
          )}
          {r.ytdlp_duration && (
            <span className="text-xs text-text-muted">
              <span className="text-text-muted/50 mx-1">·</span>{fmtDuration(r.ytdlp_duration)}
            </span>
          )}
          {r.scraper_sources_used.length > 0 && (
            <div className="flex items-center gap-1 ml-2">
              {r.scraper_sources_used.map((s, i) => (
                <span key={i} className="inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium bg-surface-lighter text-text-secondary">{s}</span>
              ))}
            </div>
          )}
        </div>
      </header>

      {/* ────────────── IMPORT INFO (import mode only) ────────────── */}
      {r.import_directory && (
        <div className="card space-y-2">
          <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wide flex items-center gap-1.5">
            <FolderOpen size={12} /> Import Source
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-sm">
            <div className="flex items-center gap-2">
              <span className="text-text-muted w-24 flex-shrink-0">Directory</span>
              <span className="text-text-primary font-mono text-xs break-all">{r.import_directory}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-text-muted w-24 flex-shrink-0">File</span>
              <span className="text-text-primary font-mono text-xs">{r.import_file}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-text-muted w-24 flex-shrink-0">Identity from</span>
              <Badge source={r.import_identity_source ?? "none"} />
            </div>
            {r.import_nfo_found != null && (
              <div className="flex items-center gap-2">
                <span className="text-text-muted w-24 flex-shrink-0">NFO found</span>
                <span className={`text-xs ${r.import_nfo_found ? "text-emerald-400" : "text-text-muted"}`}>
                  {r.import_nfo_found ? "Yes" : "No"}
                </span>
              </div>
            )}
            {r.import_youtube_match && (
              <div className="flex items-center gap-2 sm:col-span-2">
                <span className="text-text-muted w-24 flex-shrink-0">YouTube match</span>
                <a href={r.import_youtube_match.url} target="_blank" rel="noopener noreferrer"
                  className="text-xs text-accent hover:underline flex items-center gap-1 break-all">
                  {r.import_youtube_match.url}
                  <ExternalLink size={9} className="flex-shrink-0" />
                </a>
                <span className="text-xs text-text-muted font-mono">
                  score={Number(r.import_youtube_match.score).toFixed(2)}
                </span>
              </div>
            )}
            {r.import_quality && (
              <div className="flex items-center gap-2 sm:col-span-2">
                <span className="text-text-muted w-24 flex-shrink-0">Quality</span>
                <span className="text-xs text-text-secondary">
                  {r.import_quality.width}x{r.import_quality.height}
                  {r.import_quality.video_codec && <> · {r.import_quality.video_codec}</>}
                  {r.import_quality.duration_seconds && <> · {fmtDuration(r.import_quality.duration_seconds)}</>}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ────────────── PIPELINE FLOW VISUALIZER ────────────── */}
      <PipelineFlowVisualizer r={r} />

      {/* ────────────── ROW 1: Poster + Metadata ────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Poster Art */}
        <div className="lg:col-span-2">
          <div className="card overflow-hidden p-0">
            <div className="relative aspect-video bg-black flex items-center justify-center">
              {posterUrl ? (
                <img src={posterUrl} alt="Poster" className="w-full h-full object-contain" />
              ) : (
                <div className="text-text-muted flex flex-col items-center gap-2">
                  <Image size={40} />
                  <span className="text-sm">No artwork found</span>
                </div>
              )}
              {r.image_url.value && (
                <div className="absolute bottom-2 right-2">
                  <Badge source={r.image_url.source} />
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Core Library Fields */}
        <div className="lg:col-span-1">
          <div className="card h-full space-y-0">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wide">Library Metadata</h3>
            </div>
            <MetaRow icon={<User size={13} />} label="Artist" field={r.artist} />
            <MetaRow icon={<Music size={13} />} label="Title" field={r.title} />
            <MetaRow icon={<Disc3 size={13} />} label="Album" field={r.album} />
            <MetaRow icon={<Clock size={13} />} label="Year" field={r.year} />
            <MetaRow icon={<Tag size={13} />} label="Genres" field={r.genres} />
            <MetaRow icon={<Database size={13} />} label="MB Artist" field={r.mb_artist_id} mono />
            <MetaRow icon={<Database size={13} />} label="MB Recording" field={r.mb_recording_id} mono />
            <MetaRow icon={<Database size={13} />} label="MB Release" field={r.mb_release_id} mono />
            <MetaRow icon={<ExternalLink size={13} />} label="IMDB" field={r.imdb_url} mono />
          </div>
        </div>
      </div>

      {/* ────────────── ROW 2: Description + Source URLs ────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Description / Plot */}
        <div className="lg:col-span-2">
          <div className="card h-full">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wide">Description</h3>
              <Badge source={r.plot.source} />
            </div>
            {r.plot.value ? (
              <p className="text-sm text-text-primary leading-relaxed whitespace-pre-line break-words">{fmtVal(r.plot.value)}</p>
            ) : (
              <p className="text-sm text-text-muted italic">No description found from any source</p>
            )}
          </div>
        </div>

        {/* Source URLs */}
        <div className="lg:col-span-1">
          <div className="card h-full">
            <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-3">Source URLs</h3>
            {Object.keys(srcUrls).length > 0 ? (() => {
              // Build art-source lookup from artwork candidates
              const artSourceKeys = new Set<string>();
              for (const c of artCandidates) {
                if (c.applied) {
                  // Map artwork source names to source_url keys
                  if (c.source === "wikipedia") artSourceKeys.add("wikipedia");
                  if (c.source === "wikipedia_album") artSourceKeys.add("wikipedia_album");
                  if (c.source === "wikipedia_artist") artSourceKeys.add("wikipedia_artist");
                  if (c.source === "artist_scraper" && c.art_type === "artist") artSourceKeys.add("wikipedia_artist");
                  if (c.source === "album_scraper" || c.source === "album_scraper_wiki") artSourceKeys.add("wikipedia_album");
                  if (c.source === "musicbrainz_coverart" || c.source === "coverartarchive") artSourceKeys.add("coverartarchive");
                  if (c.source === "album_scraper" && c.url?.includes("coverartarchive")) artSourceKeys.add("coverartarchive");
                }
              }

              // Group URLs by section
              const grouped: Record<string, [string, string][]> = {};
              for (const [key, url] of Object.entries(srcUrls)) {
                if (!url) continue;
                const group = sourceUrlMeta(key).group;
                (grouped[group] ??= []).push([key, url]);
              }

              return (
                <div className="space-y-0">
                  {SOURCE_GROUPS.filter(g => grouped[g.key]?.length).map((g, gi) => (
                    <div key={g.key}>
                      {gi > 0 && <hr className="border-surface-border my-2" />}
                      <h4 className={`text-[10px] font-semibold uppercase tracking-wider mb-1.5 ${g.color}`}>{g.heading}</h4>
                      <div className="space-y-1.5 mb-1">
                        {grouped[g.key].map(([key, url]) => (
                          <div key={key} className="flex items-start gap-1.5">
                            <span className={`inline-flex items-center gap-1 flex-shrink-0 mt-px px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${prov(key).bg} ${prov(key).text}`}>
                              {sourceUrlMeta(key).label}
                              {artSourceKeys.has(key) && <Image size={8} className="opacity-70" />}
                            </span>
                            <a href={url} target="_blank" rel="noopener noreferrer"
                              className="text-[11px] text-accent hover:underline break-all flex items-center gap-1 min-w-0">
                              <span className="break-all">{url}</span>
                              <ExternalLink size={8} className="flex-shrink-0 opacity-50" />
                            </a>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              );
            })() : (
              <p className="text-xs text-text-muted italic">No external sources discovered</p>
            )}
          </div>
        </div>
      </div>

      {/* ────────────── ROW 3: Artwork Candidates (grouped by type) ────────────── */}
      {artCandidates.length > 0 && (() => {
        const ART_TYPE_CONFIG: Record<string, { label: string; color: string }> = {
          artist:  { label: "Artist Art",  color: "text-violet-400" },
          album:   { label: "Album Art",   color: "text-cyan-400" },
          poster:  { label: "Poster / Video Art", color: "text-amber-400" },
          fanart:  { label: "Fanart",      color: "text-emerald-400" },
        };
        // Group candidates by art_type
        const groups: Record<string, typeof artCandidates> = {};
        for (const c of artCandidates) {
          const t = c.art_type || "poster";
          (groups[t] ??= []).push(c);
        }
        const orderedTypes = ["artist", "album", "poster", "fanart"].filter(t => groups[t]);

        return (
          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wide flex items-center gap-2">
                <Image size={13} /> Artwork Candidates
              </h3>
              <span className="text-xs text-text-muted">{artCandidates.length} found</span>
            </div>
            <div className="space-y-4">
              {orderedTypes.map(artType => {
                const cfg = ART_TYPE_CONFIG[artType] ?? { label: artType, color: "text-text-muted" };
                const items = groups[artType];
                return (
                  <div key={artType}>
                    <h4 className={`text-xs font-semibold uppercase tracking-wide mb-2 ${cfg.color}`}>{cfg.label}</h4>
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                      {items.map((cand, i) => {
                        const s = prov(cand.source);
                        return (
                          <div key={i} className={`relative rounded-lg border overflow-hidden ${cand.applied ? "border-emerald-500 ring-2 ring-emerald-500/30" : "border-surface-border"}`}>
                            <div className="aspect-square bg-surface flex items-center justify-center overflow-hidden">
                              <img src={cand.url} alt={`${cand.source} ${artType} artwork`} className="w-full h-full object-cover"
                                onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; (e.target as HTMLImageElement).parentElement!.innerHTML = '<span class="text-text-muted text-xs p-2 text-center">Failed to load</span>'; }} />
                            </div>
                            <div className="px-2 py-1.5 space-y-1">
                              <div className="flex items-center justify-between gap-1">
                                <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${s.bg} ${s.text}`}>{s.label}</span>
                                {cand.applied && (
                                  <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/20 text-emerald-400">
                                    <CheckCircle2 size={9} /> Chosen
                                  </span>
                                )}
                              </div>
                              <a href={cand.url} target="_blank" rel="noopener noreferrer" className="block text-[10px] text-text-muted hover:text-accent truncate">{cand.url}</a>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}

      {/* ────────────── AI Changes (Before / After) ────────────── */}
      {aiChanges.length > 0 && (
        <div className="card border-pink-500/20">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-semibold text-pink-400 uppercase tracking-wide flex items-center gap-2">
              <Sparkles size={13} /> AI Changes — Before &amp; After
            </h3>
            <span className="text-xs text-text-muted">{aiChanges.length} fields modified</span>
          </div>
          <div className="space-y-3">
            {aiChanges.map((ch, i) => {
              const bv = fmtVal(ch.before) || "(empty)";
              const av = fmtVal(ch.after) || "(empty)";
              const long = ch.field === "plot";
              return (
                <div key={i} className="rounded-lg border border-pink-500/15 bg-surface overflow-hidden">
                  <div className="px-3 py-1.5 border-b border-pink-500/10 flex items-center gap-2 bg-pink-500/5">
                    <span className="text-xs font-semibold text-pink-400 uppercase">{ch.field}</span>
                    <Badge source={ch.source} />
                  </div>
                  <div className={`grid ${long ? "grid-cols-1" : "grid-cols-[1fr_auto_1fr]"} gap-2 p-3 items-start`}>
                    <div className="rounded bg-red-500/5 border border-red-500/15 p-2">
                      <p className="text-[10px] font-semibold text-red-400 uppercase mb-1">Before</p>
                      <p className={`text-xs text-text-secondary break-words ${long ? "max-h-28 overflow-y-auto whitespace-pre-wrap" : ""}`}>{bv}</p>
                    </div>
                    {!long && <div className="flex items-center pt-3"><ArrowRight size={14} className="text-pink-400" /></div>}
                    <div className="rounded bg-emerald-500/5 border border-emerald-500/15 p-2">
                      <p className="text-[10px] font-semibold text-emerald-400 uppercase mb-1">After</p>
                      <p className={`text-xs text-text-secondary break-words ${long ? "max-h-28 overflow-y-auto whitespace-pre-wrap" : ""}`}>{av}</p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ────────────── Pipeline Warnings ────────────── */}
      {r.pipeline_failures.length > 0 && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 overflow-hidden px-4 py-3">
          <h3 className="text-sm font-semibold text-amber-400 flex items-center gap-2 mb-2"><AlertTriangle size={14} /> Pipeline Warnings</h3>
          <div className="space-y-1">
            {r.pipeline_failures.map((f, i) => (
              <div key={i} className="flex items-start gap-2 py-1">
                <span className="inline-flex px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold bg-amber-500/20 text-amber-400 flex-shrink-0">{f.code}</span>
                <span className="text-sm text-text-secondary">{f.description}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ────────────── Collapsible: yt-dlp Raw ────────────── */}
      <Collapsible title="yt-dlp Raw Metadata">
        <div className="px-4 py-3 space-y-1 text-sm">
          <RawRow label="Title" value={r.ytdlp_title} />
          <RawRow label="Uploader" value={r.ytdlp_uploader} />
          <RawRow label="Channel" value={r.ytdlp_channel} />
          <RawRow label="Artist" value={r.ytdlp_artist} />
          <RawRow label="Track" value={r.ytdlp_track} />
          <RawRow label="Album" value={r.ytdlp_album} />
          <RawRow label="Upload Date" value={r.ytdlp_upload_date} />
          <RawRow label="Duration" value={fmtDuration(r.ytdlp_duration)} />
          <RawRow label="Thumbnail" value={r.ytdlp_thumbnail} />
          <RawRow label="Tags" value={r.ytdlp_tags?.length ? r.ytdlp_tags.join(", ") : undefined} truncate />
          <RawRow label="Description" value={r.ytdlp_description} truncate />
          <div className="border-t border-surface-border/30 mt-2 pt-2" />
          <RawRow label="Parsed Artist" value={r.parsed_artist} highlight />
          <RawRow label="Parsed Title" value={r.parsed_title} highlight />
        </div>
      </Collapsible>

      {/* ────────────── Collapsible: Pipeline Log ────────────── */}
      <Collapsible title="Full Trace Log" count={r.pipeline_log.length} defaultOpen>
        <div className="px-4 py-3 max-h-[32rem] overflow-y-auto space-y-0">
          {r.pipeline_log.map((entry, i) => (
            <LogLine key={i} index={i + 1} entry={entry} />
          ))}
        </div>
      </Collapsible>

      {/* ────────────── Collapsible: AI Details ────────────── */}
      {r.ai_source_resolution && (
        <Collapsible title="AI Source Resolution" variant="violet">
          <pre className="px-4 py-3 text-xs text-text-secondary font-mono whitespace-pre-wrap max-h-64 overflow-y-auto">
            {JSON.stringify(r.ai_source_resolution, null, 2)}
          </pre>
        </Collapsible>
      )}
      {r.ai_final_review && (
        <Collapsible title="AI Final Review" variant="violet">
          <pre className="px-4 py-3 text-xs text-text-secondary font-mono whitespace-pre-wrap max-h-64 overflow-y-auto">
            {JSON.stringify(r.ai_final_review, null, 2)}
          </pre>
        </Collapsible>
      )}
    </div>
  );
}

/* ─────────────────────── LogLine ─────────────────────── */
function LogLine({ index, entry }: { index: number; entry: string }) {
  // Color-code by prefix/content
  let color = "text-text-muted";
  let bg = "";
  if (entry.startsWith("[scraper-test]")) {
    if (entry.includes("── Pipeline complete ──")) {
      color = "text-emerald-400";
      bg = "bg-emerald-500/5";
    } else {
      color = "text-cyan-400";
    }
  } else if (entry.startsWith("stage:")) {
    color = "text-blue-400 font-semibold";
    bg = "bg-blue-500/5";
  } else if (entry.startsWith("scraper:")) {
    color = "text-orange-400";
  } else if (entry.startsWith("ai_review_change:") || entry.startsWith("AI Final Review")) {
    color = "text-pink-400";
  } else if (entry.includes("cleared") || entry.includes("discarded") || entry.includes("rejected")) {
    color = "text-amber-400";
  } else if (entry.includes("failed") || entry.includes("error")) {
    color = "text-red-400";
  } else if (entry.startsWith("Running ") || entry.startsWith("MusicBrainz:") || entry.startsWith("Wikipedia:") || entry.startsWith("IMDB:")) {
    color = "text-text-secondary";
  }

  return (
    <p className={`text-xs font-mono py-0.5 px-1 rounded ${bg} ${color}`}>
      <span className="text-text-muted/50 select-none mr-1.5">{String(index).padStart(3, " ")}</span>
      {entry}
    </p>
  );
}

/* ─────────────────────── MetaRow ─────────────────────── */
function MetaRow({ icon, label, field, mono }: {
  icon: React.ReactNode; label: string; field: ProvenanceField; mono?: boolean;
}) {
  const val = fmtVal(field.value);
  const found = val !== "";
  return (
    <div className="py-2 border-b border-surface-border/30 last:border-0">
      <div className="flex items-start gap-2">
        <span className="text-text-muted mt-0.5 flex-shrink-0">{icon}</span>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] text-text-muted leading-none mb-0.5">{label}</p>
          <p className={`text-sm break-words ${found ? (mono ? "font-mono text-xs text-text-primary" : "text-text-primary font-medium") : "text-text-muted italic"}`}>
            {found ? val : "Not found"}
          </p>
        </div>
        <Badge source={field.source} className="flex-shrink-0 mt-1" />
      </div>
    </div>
  );
}

/* ─────────────────────── RawRow ─────────────────────── */
function RawRow({ label, value, truncate, highlight }: { label: string; value?: string | null; truncate?: boolean; highlight?: boolean }) {
  return (
    <div className="flex items-start gap-3 py-1 border-b border-surface-border/20 last:border-0">
      <span className="text-text-muted w-28 flex-shrink-0 text-xs">{label}</span>
      <span className={`text-xs break-words ${value ? (highlight ? "text-accent font-medium" : "text-text-primary") : "text-text-muted italic"} ${truncate ? "line-clamp-3" : ""}`}>
        {value || "—"}
      </span>
    </div>
  );
}
