import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import {
  FileText,
  Download,
  RefreshCw,
  Loader2,
  Server,
  Briefcase,
  ArrowDown,
  Scissors,
  Search,
  X,
  ExternalLink,
  Link2,
  FolderInput,
  RotateCw,
  FlaskConical,
} from "lucide-react";
import { useLogFiles, useLogContent } from "@/hooks/queries";
import { useToast } from "@/components/Toast";
import { settingsApi, jobsApi } from "@/lib/api";
import { Tooltip } from "@/components/Tooltip";
import type { LogFileEntry } from "@/types";
import { cn } from "@/lib/utils";


/* ── helpers ── */

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* ── log line colouring ── */

function classForLine(line: string): string {
  if (/\bERROR\b/i.test(line)) return "text-red-400";
  if (/\bWARNING\b/i.test(line)) return "text-amber-400";
  if (/\bDEBUG\b/i.test(line)) return "text-text-muted/70";
  return "text-text-secondary";
}

/* ── Category definitions ── */

type LogCategory = "system" | "url_import" | "library_import" | "rescan" | "scraper_tester";

const JOB_TYPE_TO_CATEGORY: Record<string, LogCategory> = {
  library_scan: "system",
  duplicate_scan: "system",
  library_export: "system",
  normalize: "system",
  batch_normalize: "system",
  video_editor_scan: "system",
  video_editor_encode: "system",
  import_url: "url_import",
  playlist_import: "url_import",
  redownload: "url_import",
  library_import: "library_import",
  library_import_video: "library_import",
  rescan: "rescan",
  batch_rescan: "rescan",
  batch_resolve: "rescan",
  metadata_scrape: "rescan",
  metadata_refresh: "rescan",
  batch_metadata_refresh: "rescan",
};

interface CategoryConfig {
  label: string;
  icon: React.ReactNode;
}

const CATEGORIES: Record<LogCategory, CategoryConfig> = {
  system:           { label: "System",           icon: <Server size={14} /> },
  url_import:       { label: "URL Import",       icon: <Link2 size={14} /> },
  library_import:   { label: "Library Import",   icon: <FolderInput size={14} /> },
  rescan:           { label: "Rescan",           icon: <RotateCw size={14} /> },
  scraper_tester:   { label: "Scraper Tester",   icon: <FlaskConical size={14} /> },
};

const CATEGORY_ORDER: LogCategory[] = ["system", "url_import", "library_import", "rescan", "scraper_tester"];

function categorizeFile(f: LogFileEntry): LogCategory {
  if (f.category === "app") return "system";
  if (f.category === "scraper_test") return "scraper_tester";
  if (f.category === "job" && f.job_type) {
    return JOB_TYPE_TO_CATEGORY[f.job_type] || "system";
  }
  return "system";
}

/* ── Component ── */

export function LogViewer() {
  const { toast } = useToast();
  const { data: files, isLoading: filesLoading, refetch: refetchFiles } = useLogFiles();

  const [activeTab, setActiveTab] = useState<LogCategory>("system");
  const [selectedFile, setSelectedFile] = useState<string>("");
  const [tailLines, setTailLines] = useState<number>(500);
  const [searchTerm, setSearchTerm] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const logRef = useRef<HTMLPreElement>(null);

  const {
    data: logData,
    isLoading: logLoading,
    refetch: refetchLog,
    isFetching,
  } = useLogContent(selectedFile, tailLines);

  // Group files by category
  const filesByCategory = useMemo(() => {
    const map: Record<LogCategory, LogFileEntry[]> = {
      system: [], url_import: [], library_import: [], rescan: [], scraper_tester: [],
    };
    for (const f of files ?? []) {
      map[categorizeFile(f)].push(f);
    }
    return map;
  }, [files]);

  // Count per category
  const categoryCounts = useMemo(() => {
    const counts: Record<LogCategory, number> = {
      system: 0, url_import: 0, library_import: 0, rescan: 0, scraper_tester: 0,
    };
    for (const cat of CATEGORY_ORDER) counts[cat] = filesByCategory[cat].length;
    return counts;
  }, [filesByCategory]);

  const tabFiles = filesByCategory[activeTab];

  // Auto-select first file in category on tab change
  useEffect(() => {
    if (tabFiles.length > 0) {
      if (activeTab === "system") {
        const main = tabFiles.find((f) => f.filename === "playarr.log");
        setSelectedFile(main?.filename ?? tabFiles[0].filename);
      } else {
        setSelectedFile(tabFiles[0].filename);
      }
    } else {
      setSelectedFile("");
    }
  }, [activeTab, tabFiles]);

  // Auto-select playarr.log on first load
  useEffect(() => {
    if (files && files.length > 0 && !selectedFile) {
      const main = files.find((f) => f.filename === "playarr.log");
      setSelectedFile(main?.filename ?? files[0].filename);
    }
  }, [files, selectedFile]);

  // Auto-scroll to bottom when content changes
  useEffect(() => {
    if (autoScroll && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logData?.log_text, autoScroll]);

  /* ── Download helpers ── */

  const downloadText = useCallback(
    (text: string, filename: string) => {
      const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    },
    [],
  );

  const handleDownloadFull = () => {
    if (!logData?.log_text) return;
    const baseName = selectedFile.replace(/\//g, "_").replace(/\.[^.]+$/, "");
    downloadText(logData.log_text, `${baseName}.txt`);
    toast({ type: "success", title: "Log downloaded" });
  };

  const handleDownloadSelection = () => {
    const selection = window.getSelection()?.toString();
    if (!selection) {
      toast({ type: "info", title: "Select text in the log first" });
      return;
    }
    const baseName = selectedFile.replace(/\//g, "_").replace(/\.[^.]+$/, "");
    downloadText(selection, `${baseName}_excerpt.txt`);
    toast({ type: "success", title: "Selection saved" });
  };

  const selectedEntry = (files ?? []).find((f) => f.filename === selectedFile);

  const lines = logData?.log_text?.split("\n") ?? [];
  const filteredLines = searchTerm
    ? lines.filter((l: string) => l.toLowerCase().includes(searchTerm.toLowerCase()))
    : lines;

  return (
    <div className="space-y-3">
      {/* ── Category tabs ── */}
      <div className="flex items-center gap-1 overflow-x-auto border-b border-surface-border pb-px">
        {CATEGORY_ORDER.map((cat) => {
          const cfg = CATEGORIES[cat];
          const count = categoryCounts[cat];
          const isActive = activeTab === cat;
          return (
            <button
              key={cat}
              onClick={() => setActiveTab(cat)}
              className={cn(
                "flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-t-lg border-b-2 whitespace-nowrap transition-colors",
                isActive
                  ? "border-accent text-accent bg-accent/5"
                  : "border-transparent text-text-muted hover:text-text-secondary hover:bg-surface-hover/50",
              )}
            >
              {cfg.icon}
              {cfg.label}
              {count > 0 && (
                <span className={cn(
                  "text-[10px] px-1.5 py-0.5 rounded-full font-mono",
                  isActive ? "bg-accent/15 text-accent" : "bg-surface-hover text-text-muted",
                )}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* ── File list + log content ── */}
      <div className="flex gap-3">
        {/* Left: File list panel */}
        <div className="w-56 flex-shrink-0 rounded-lg border border-surface-border bg-surface-light overflow-hidden">
          <div className="px-3 py-2 border-b border-surface-border bg-surface-lighter/30">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">
              {CATEGORIES[activeTab].label} Logs
            </span>
          </div>
          <div className="overflow-y-auto max-h-[60vh]">
            {filesLoading ? (
              <div className="flex items-center justify-center py-8 text-text-muted text-xs">
                <Loader2 size={14} className="animate-spin mr-2" /> Loading...
              </div>
            ) : tabFiles.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-text-muted text-xs">
                <FileText size={20} className="mb-1.5 opacity-40" />
                No logs
              </div>
            ) : (
              tabFiles.map((f) => (
                <button
                  key={f.filename}
                  onClick={() => setSelectedFile(f.filename)}
                  className={cn(
                    "w-full text-left px-3 py-2 text-xs border-b border-surface-border/50 transition-colors",
                    selectedFile === f.filename
                      ? "bg-accent/10 text-accent"
                      : "hover:bg-surface-hover text-text-secondary",
                  )}
                >
                  <div className="flex items-center gap-1.5">
                    {f.category === "app"
                      ? <Server size={11} className="flex-shrink-0 opacity-60" />
                      : f.category === "scraper_test"
                        ? <FlaskConical size={11} className="flex-shrink-0 opacity-60" />
                        : <Briefcase size={11} className="flex-shrink-0 opacity-60" />}
                    <span className="truncate font-medium">{f.label}</span>
                  </div>
                  <div className="flex items-center gap-2 mt-0.5 ml-4">
                    <span className="text-[10px] text-text-muted">{formatDate(f.modified)}</span>
                    <span className="text-[10px] text-text-muted">{formatBytes(f.size_bytes)}</span>
                  </div>
                </button>
              ))
            )}
          </div>
        </div>

        {/* Right: Log content */}
        <div className="flex-1 min-w-0 space-y-2">
          {/* Toolbar */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5">
              <label className="text-xs text-text-muted whitespace-nowrap">Lines:</label>
              <select
                value={tailLines}
                onChange={(e) => setTailLines(Number(e.target.value))}
                className="px-2 py-1.5 text-xs rounded-lg bg-surface-light border border-surface-border text-text-primary"
              >
                <option value={100}>100</option>
                <option value={500}>500</option>
                <option value={1000}>1,000</option>
                <option value={5000}>5,000</option>
                <option value={0}>All</option>
              </select>
            </div>

            <div className="flex items-center gap-1">
              <Tooltip content="Search log">
                <button onClick={() => setSearchOpen(!searchOpen)} className="btn-ghost btn-sm">
                  <Search size={14} />
                </button>
              </Tooltip>
              <Tooltip content="Refresh">
                <button
                  onClick={() => { refetchFiles(); refetchLog(); }}
                  disabled={isFetching}
                  className="btn-ghost btn-sm"
                >
                  <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
                </button>
              </Tooltip>
              <Tooltip content={autoScroll ? "Auto-scroll ON" : "Auto-scroll OFF"}>
                <button
                  onClick={() => setAutoScroll(!autoScroll)}
                  className={`btn-ghost btn-sm ${autoScroll ? "text-accent" : ""}`}
                >
                  <ArrowDown size={14} />
                </button>
              </Tooltip>
              <Tooltip content="Open log folder">
                <button
                  onClick={async () => {
                    try {
                      const { path } = await jobsApi.logDirectory();
                      await settingsApi.openDirectory(path);
                    } catch {
                      toast({ type: "error", title: "Could not open log folder" });
                    }
                  }}
                  className="btn-ghost btn-sm"
                >
                  <ExternalLink size={14} />
                </button>
              </Tooltip>
            </div>

            <div className="flex items-center gap-1 ml-auto">
              <Tooltip content="Save selected text as .txt">
                <button onClick={handleDownloadSelection} className="btn-secondary btn-sm text-xs">
                  <Scissors size={13} className="mr-1" />
                  Save Selection
                </button>
              </Tooltip>
              <Tooltip content="Download full log as .txt">
                <button
                  onClick={handleDownloadFull}
                  disabled={!logData?.log_text}
                  className="btn-secondary btn-sm text-xs"
                >
                  <Download size={13} className="mr-1" />
                  Download
                </button>
              </Tooltip>
            </div>
          </div>

          {/* Search bar */}
          {searchOpen && (
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
                <input
                  type="text"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  placeholder="Filter log lines..."
                  autoFocus
                  className="input-field w-full pl-9 pr-8 py-1.5 text-sm"
                />
                {searchTerm && (
                  <button
                    onClick={() => setSearchTerm("")}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary"
                  >
                    <X size={14} />
                  </button>
                )}
              </div>
              <span className="text-xs text-text-muted whitespace-nowrap">
                {searchTerm
                  ? `${filteredLines.length} of ${lines.length} lines`
                  : `${lines.length} lines`}
              </span>
            </div>
          )}

          {/* Log content panel */}
          <div className="relative rounded-lg border border-surface-border bg-[#0a0c12] overflow-hidden">
            {selectedEntry && (
              <div className="flex items-center justify-between px-3 py-1.5 bg-surface-light/50 border-b border-surface-border text-[11px] text-text-muted">
                <span className="flex items-center gap-1.5">
                  <FileText size={12} />
                  {selectedEntry.filename}
                  {logData && <span className="ml-2">({logData.total_lines.toLocaleString()} total lines)</span>}
                </span>
                <span>{formatBytes(selectedEntry.size_bytes)} · {formatDate(selectedEntry.modified)}</span>
              </div>
            )}

            {logLoading ? (
              <div className="flex items-center justify-center py-16 text-text-muted">
                <Loader2 size={20} className="animate-spin mr-2" />
                Loading log...
              </div>
            ) : !logData?.log_text ? (
              <div className="flex flex-col items-center justify-center py-16 text-text-muted">
                <FileText size={32} className="mb-2 opacity-40" />
                <p className="text-sm">{tabFiles.length === 0 ? "No logs in this category" : "Select a log file to view"}</p>
              </div>
            ) : (
              <pre
                ref={logRef}
                className="overflow-auto max-h-[60vh] p-3 text-xs leading-5 font-mono select-text whitespace-pre-wrap break-all"
              >
                {filteredLines.map((line: string, i: number) => (
                  <div key={i} className={`${classForLine(line)} hover:bg-white/[0.03]`}>
                    {searchTerm ? highlightMatch(line, searchTerm) : line}
                  </div>
                ))}
              </pre>
            )}

            {logData?.log_text && !autoScroll && (
              <button
                onClick={() => {
                  if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
                }}
                className="absolute bottom-3 right-3 p-2 rounded-full bg-surface-lighter border border-surface-border shadow-lg hover:bg-surface-hover transition-colors"
                title="Scroll to bottom"
              >
                <ArrowDown size={14} className="text-text-secondary" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Search highlight helper ── */

function highlightMatch(line: string, term: string): React.ReactNode {
  if (!term) return line;
  const idx = line.toLowerCase().indexOf(term.toLowerCase());
  if (idx === -1) return line;
  return (
    <>
      {line.slice(0, idx)}
      <mark className="bg-amber-500/30 text-amber-200 rounded-sm px-0.5">{line.slice(idx, idx + term.length)}</mark>
      {line.slice(idx + term.length)}
    </>
  );
}
