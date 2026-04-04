import { useState, useRef, useEffect, useCallback } from "react";
import {
  FileText,
  Download,
  RefreshCw,
  ChevronDown,
  Loader2,
  Server,
  Briefcase,
  ArrowDown,
  Scissors,
  Search,
  X,
} from "lucide-react";
import { useLogFiles, useLogContent } from "@/hooks/queries";
import { useToast } from "@/components/Toast";


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

function categoryIcon(cat: string) {
  return cat === "job" ? <Briefcase size={14} /> : <Server size={14} />;
}

/* ── log line colouring ── */

function classForLine(line: string): string {
  if (/\bERROR\b/i.test(line)) return "text-red-400";
  if (/\bWARNING\b/i.test(line)) return "text-amber-400";
  if (/\bDEBUG\b/i.test(line)) return "text-text-muted/70";
  return "text-text-secondary";
}

/* ── Component ── */

export function LogViewer() {
  const { toast } = useToast();
  const { data: files, isLoading: filesLoading, refetch: refetchFiles } = useLogFiles();

  const [selectedFile, setSelectedFile] = useState<string>("");
  const [tailLines, setTailLines] = useState<number>(500);
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const logRef = useRef<HTMLPreElement>(null);
  const selectorRef = useRef<HTMLDivElement>(null);

  const {
    data: logData,
    isLoading: logLoading,
    refetch: refetchLog,
    isFetching,
  } = useLogContent(selectedFile, tailLines);

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

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (selectorRef.current && !selectorRef.current.contains(e.target as Node)) {
        setSelectorOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

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

  /* ── Render helpers ── */

  // Group files by category
  const appFiles = (files ?? []).filter((f) => f.category === "app");
  const jobFiles = (files ?? []).filter((f) => f.category === "job");
  const selectedEntry = files?.find((f) => f.filename === selectedFile);

  // Filter / highlight lines
  const lines = logData?.log_text?.split("\n") ?? [];
  const filteredLines = searchTerm
    ? lines.filter((l: string) => l.toLowerCase().includes(searchTerm.toLowerCase()))
    : lines;

  return (
    <div className="space-y-4">
      {/* ── Toolbar ── */}
      <div className="flex flex-wrap items-center gap-2">
        {/* File selector dropdown */}
        <div className="relative flex-1 min-w-[200px] max-w-xs" ref={selectorRef}>
          <button
            onClick={() => setSelectorOpen(!selectorOpen)}
            className="w-full flex items-center justify-between gap-2 px-3 py-2 text-sm rounded-lg bg-surface-light border border-surface-border hover:border-accent/50 transition-colors"
          >
            <span className="flex items-center gap-2 truncate">
              {selectedEntry ? (
                <>
                  {categoryIcon(selectedEntry.category)}
                  <span className="truncate">{selectedEntry.label}</span>
                </>
              ) : (
                <span className="text-text-muted">Select log file…</span>
              )}
            </span>
            <ChevronDown size={14} className={`text-text-muted transition-transform ${selectorOpen ? "rotate-180" : ""}`} />
          </button>

          {selectorOpen && (
            <div className="absolute z-20 top-full mt-1 w-full max-h-72 overflow-y-auto rounded-lg border border-surface-border bg-surface-light shadow-xl">
              {/* App logs */}
              {appFiles.length > 0 && (
                <div className="px-2 pt-2 pb-1">
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-text-muted px-2 mb-1">
                    Application Logs
                  </div>
                  {appFiles.map((f) => (
                    <button
                      key={f.filename}
                      onClick={() => { setSelectedFile(f.filename); setSelectorOpen(false); }}
                      className={`w-full text-left px-2 py-1.5 rounded text-sm flex items-center justify-between gap-2 ${
                        selectedFile === f.filename
                          ? "bg-accent/15 text-accent"
                          : "hover:bg-surface-hover text-text-secondary"
                      }`}
                    >
                      <span className="flex items-center gap-2 truncate">
                        <Server size={13} />
                        {f.label}
                      </span>
                      <span className="text-[10px] text-text-muted whitespace-nowrap">
                        {formatBytes(f.size_bytes)}
                      </span>
                    </button>
                  ))}
                </div>
              )}

              {/* Job logs */}
              {jobFiles.length > 0 && (
                <div className="px-2 pt-1 pb-2 border-t border-surface-border">
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-text-muted px-2 mb-1 mt-1">
                    Job Logs ({jobFiles.length})
                  </div>
                  {jobFiles.slice(0, 50).map((f) => (
                    <button
                      key={f.filename}
                      onClick={() => { setSelectedFile(f.filename); setSelectorOpen(false); }}
                      className={`w-full text-left px-2 py-1.5 rounded text-sm flex items-center justify-between gap-2 ${
                        selectedFile === f.filename
                          ? "bg-accent/15 text-accent"
                          : "hover:bg-surface-hover text-text-secondary"
                      }`}
                    >
                      <span className="flex items-center gap-2 truncate">
                        <Briefcase size={13} />
                        {f.label}
                      </span>
                      <span className="text-[10px] text-text-muted whitespace-nowrap">
                        {formatDate(f.modified)} · {formatBytes(f.size_bytes)}
                      </span>
                    </button>
                  ))}
                </div>
              )}

              {filesLoading && (
                <div className="flex items-center justify-center py-4 text-text-muted text-sm">
                  <Loader2 size={14} className="animate-spin mr-2" /> Loading…
                </div>
              )}
            </div>
          )}
        </div>

        {/* Lines control */}
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

        {/* Action buttons */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => setSearchOpen(!searchOpen)}
            className="btn-ghost btn-sm"
            title="Search log"
          >
            <Search size={14} />
          </button>
          <button
            onClick={() => { refetchFiles(); refetchLog(); }}
            disabled={isFetching}
            className="btn-ghost btn-sm"
            title="Refresh"
          >
            <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
          </button>
          <button
            onClick={() => setAutoScroll(!autoScroll)}
            className={`btn-ghost btn-sm ${autoScroll ? "text-accent" : ""}`}
            title={autoScroll ? "Auto-scroll ON" : "Auto-scroll OFF"}
          >
            <ArrowDown size={14} />
          </button>
        </div>

        {/* Download */}
        <div className="flex items-center gap-1 ml-auto">
          <button
            onClick={handleDownloadSelection}
            className="btn-secondary btn-sm text-xs"
            title="Save selected text as .txt"
          >
            <Scissors size={13} className="mr-1" />
            Save Selection
          </button>
          <button
            onClick={handleDownloadFull}
            disabled={!logData?.log_text}
            className="btn-secondary btn-sm text-xs"
            title="Download full log as .txt"
          >
            <Download size={13} className="mr-1" />
            Download
          </button>
        </div>
      </div>

      {/* ── Search bar ── */}
      {searchOpen && (
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Filter log lines…"
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

      {/* ── Log content ── */}
      <div className="relative rounded-lg border border-surface-border bg-[#0a0c12] overflow-hidden">
        {/* File info header */}
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

        {/* Log body */}
        {logLoading ? (
          <div className="flex items-center justify-center py-16 text-text-muted">
            <Loader2 size={20} className="animate-spin mr-2" />
            Loading log…
          </div>
        ) : !logData?.log_text ? (
          <div className="flex flex-col items-center justify-center py-16 text-text-muted">
            <FileText size={32} className="mb-2 opacity-40" />
            <p className="text-sm">Select a log file to view</p>
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

        {/* Scroll-to-bottom FAB */}
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

      {/* ── Hint ── */}
      <p className="text-[11px] text-text-muted">
        Tip: Highlight text in the log, then click <strong>Save Selection</strong> to export just that portion as a .txt file for sharing.
      </p>
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
