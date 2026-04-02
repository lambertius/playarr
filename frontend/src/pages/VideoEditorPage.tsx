import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  Scissors, ScanLine, Play, Pause, Trash2, Square, CheckSquare,
  Loader2, Settings2, MonitorPlay, Film, X, Eye, EyeOff, ArchiveRestore, Ban, ExternalLink,
  Volume2, VolumeX, ZoomIn, ZoomOut, Timer, SkipBack, SkipForward, Link2,
  ChevronUp, ChevronDown,
} from "lucide-react";
import { useEditorQueue, useDetectLetterbox, useScanLetterbox, useEditorScanResults, useEditorEncodeStatus, useVideoEditorEncode, useVideoEditorBatchEncode, useRestoreFromArchive, useSetExcludeFromScan } from "@/hooks/queries";
import { playbackApi } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { Tooltip } from "@/components/Tooltip";
import type { EditorQueueItem, EncodeRequest, CropPreviewResponse, LetterboxScanItem } from "@/types";

// ── Aspect ratio presets ──────────────────────────────────
const RATIO_PRESETS = [
  { label: "Original", value: "original" },
  { label: "16:9", value: "16:9" },
  { label: "4:3", value: "4:3" },
  { label: "21:9", value: "21:9" },
  { label: "1:1", value: "1:1" },
  { label: "2.35:1", value: "2.35:1" },
  { label: "1.85:1", value: "1.85:1" },
  { label: "Custom", value: "custom" },
];

// ── x264 presets ──────────────────────────────────────────
const X264_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"];

// ── Local storage keys ───────────────────────────────────
const QUEUE_KEY = "playarr_video_editor_queue";
const ENCODE_JOBS_KEY = "playarr_editor_encode_jobs";

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b);
}

function loadQueueIds(): number[] {
  try {
    const raw = localStorage.getItem(QUEUE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveQueueIds(ids: number[]) {
  localStorage.setItem(QUEUE_KEY, JSON.stringify(ids));
}

function loadEncodeJobs(): { videoId: number; jobId: number }[] {
  try {
    const raw = localStorage.getItem(ENCODE_JOBS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveEncodeJobs(jobs: { videoId: number; jobId: number }[]) {
  localStorage.setItem(ENCODE_JOBS_KEY, JSON.stringify(jobs));
}

// ── Numeric Stepper — larger +/- buttons for number inputs ──
function NumericStepper({ value, onChange, min, max, step = 1, disabled, className = "w-16" }: {
  value: number;
  onChange: (val: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  className?: string;
}) {
  const clamp = (v: number) => {
    if (min !== undefined) v = Math.max(min, v);
    if (max !== undefined) v = Math.min(max, v);
    return Math.round(v * 1000) / 1000;
  };
  return (
    <div className={`flex items-stretch mt-1 ${className}`}>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(clamp(Number(e.target.value)))}
        disabled={disabled}
        className="input-sm flex-1 min-w-0 rounded-r-none border-r-0"
      />
      <div className="flex flex-col border border-surface-border border-l-0 rounded-r bg-surface-lighter">
        <button
          type="button"
          tabIndex={-1}
          disabled={disabled || (max !== undefined && value >= max)}
          className="flex items-center justify-center px-1.5 h-1/2 text-text-muted hover:text-text-primary hover:bg-surface-hover disabled:opacity-30 border-b border-surface-border"
          onClick={() => onChange(clamp(value + step))}
        >
          <ChevronUp size={12} />
        </button>
        <button
          type="button"
          tabIndex={-1}
          disabled={disabled || (min !== undefined && value <= min)}
          className="flex items-center justify-center px-1.5 h-1/2 text-text-muted hover:text-text-primary hover:bg-surface-hover disabled:opacity-30"
          onClick={() => onChange(clamp(value - step))}
        >
          <ChevronDown size={12} />
        </button>
      </div>
    </div>
  );
}

// ── Main Page Component ──────────────────────────────────
export function VideoEditorPage() {
  const { toast } = useToast();
  const navigate = useNavigate();

  // Queue state (persisted in localStorage)
  const [queueIds, setQueueIds] = useState<number[]>(loadQueueIds);
  const [checkedIds, setCheckedIds] = useState<Set<number>>(new Set());
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // Encode settings per-item overrides (keyed by video_id)
  const [itemSettings, setItemSettings] = useState<Record<number, {
    ratio: string;
    customRatioW: number;
    customRatioH: number;
    crf: number;
    preset: string;
    audioPassthrough: boolean;
    crop?: CropPreviewResponse;
    targetDar?: string;
    trimEnabled: boolean;
    trimStart: number;
    trimEnd: number;
    audioCodec: string;
    audioBitrate: string;
    cropLinkLR: boolean;
    cropLinkTB: boolean;
  }>>({});

  // Global defaults
  const [globalCrf, setGlobalCrf] = useState(18);
  const [globalPreset, setGlobalPreset] = useState("medium");
  const [globalAudioPassthrough, setGlobalAudioPassthrough] = useState(true);
  const [globalRatio, setGlobalRatio] = useState("original");

  // Scan state
  const [scanJobId, setScanJobId] = useState<number | null>(null);
  const [isScanning, setIsScanning] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showOverlay, setShowOverlay] = useState(true);

  // Encode job tracking: list of { videoId, jobId } — persisted so it survives page navigation
  const [encodeJobs, setEncodeJobs] = useState<{ videoId: number; jobId: number }[]>(loadEncodeJobs);
  const activeEncodeJob = encodeJobs[0] ?? null;

  // Post-encode summary (shown as dismissible banner after encode completes)
  const [lastEncodeSummary, setLastEncodeSummary] = useState<{ title: string; summary: string } | null>(null);

  // Fetch queue items from API
  const { data: queueItems, isLoading: queueLoading, refetch: refetchQueue } = useEditorQueue(queueIds);
  const detectLetterbox = useDetectLetterbox();
  const scanLetterbox = useScanLetterbox();
  const scanResults = useEditorScanResults(scanJobId);
  const encodeStatus = useEditorEncodeStatus(activeEncodeJob?.jobId ?? null);
  const encodeSingle = useVideoEditorEncode();
  const encodeBatch = useVideoEditorBatchEncode();
  const restoreArchive = useRestoreFromArchive();
  const excludeFromScan = useSetExcludeFromScan();

  // ── Video playback controls ──────────────────────────────
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);

  // Reset playback state when selected item changes
  useEffect(() => {
    setIsPlaying(false);
    setCurrentTime(0);
    setDuration(0);
  }, [selectedId]);

  const handleVideoRef = useCallback((el: HTMLVideoElement | null) => {
    if (videoRef.current) {
      videoRef.current.removeEventListener("timeupdate", handleTimeUpdate);
      videoRef.current.removeEventListener("loadedmetadata", handleLoadedMetadata);
      videoRef.current.removeEventListener("play", handlePlayEvent);
      videoRef.current.removeEventListener("pause", handlePauseEvent);
      videoRef.current.removeEventListener("ended", handlePauseEvent);
    }
    videoRef.current = el;
    if (el) {
      el.addEventListener("timeupdate", handleTimeUpdate);
      el.addEventListener("loadedmetadata", handleLoadedMetadata);
      el.addEventListener("play", handlePlayEvent);
      el.addEventListener("pause", handlePauseEvent);
      el.addEventListener("ended", handlePauseEvent);
      el.volume = volume;
      el.muted = isMuted;
    }
  }, []);

  const handleTimeUpdate = useCallback(() => {
    if (videoRef.current) setCurrentTime(videoRef.current.currentTime);
  }, []);
  const handleLoadedMetadata = useCallback(() => {
    if (videoRef.current) {
      const dur = videoRef.current.duration;
      setDuration(dur);
      if (dur > 0) {
        videoRef.current.currentTime = dur / 3;
      }
    }
  }, []);
  const handlePlayEvent = useCallback(() => setIsPlaying(true), []);
  const handlePauseEvent = useCallback(() => setIsPlaying(false), []);

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play(); else v.pause();
  }, []);

  const handleSeek = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = Number(e.target.value);
    setCurrentTime(v.currentTime);
  }, []);

  const toggleMute = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    v.muted = !v.muted;
    setIsMuted(v.muted);
  }, []);

  const handleVolumeChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const v = videoRef.current;
    if (!v) return;
    const val = Number(e.target.value);
    v.volume = val;
    setVolume(val);
    if (val > 0 && v.muted) { v.muted = false; setIsMuted(false); }
  }, []);

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  // ── Zoom controls ──────────────────────────────────────
  const [zoom, setZoom] = useState(1);
  const previewContainerRef = useRef<HTMLDivElement>(null);

  // Reset zoom when switching videos
  useEffect(() => { setZoom(1); }, [selectedId]);

  const handleZoomIn = useCallback(() => setZoom(z => Math.min(z + 0.25, 4)), []);
  const handleZoomOut = useCallback(() => setZoom(z => Math.max(z - 0.25, 0.5)), []);
  const handleZoomReset = useCallback(() => setZoom(1), []);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    setZoom(z => {
      const delta = e.deltaY > 0 ? -0.15 : 0.15;
      return Math.min(4, Math.max(0.5, z + delta));
    });
  }, []);

  // ── Drag-to-pan when zoomed in ─────────────────────────
  const isDragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0, scrollLeft: 0, scrollTop: 0 });

  const handlePanStart = useCallback((e: React.MouseEvent) => {
    if (zoom <= 1 || !previewContainerRef.current) return;
    isDragging.current = true;
    dragStart.current = {
      x: e.clientX,
      y: e.clientY,
      scrollLeft: previewContainerRef.current.scrollLeft,
      scrollTop: previewContainerRef.current.scrollTop,
    };
    e.preventDefault();
  }, [zoom]);

  const handlePanMove = useCallback((e: React.MouseEvent) => {
    if (!isDragging.current || !previewContainerRef.current) return;
    previewContainerRef.current.scrollLeft = dragStart.current.scrollLeft - (e.clientX - dragStart.current.x);
    previewContainerRef.current.scrollTop = dragStart.current.scrollTop - (e.clientY - dragStart.current.y);
  }, []);

  const handlePanEnd = useCallback(() => {
    isDragging.current = false;
  }, []);

  // Sync queueIds to localStorage
  useEffect(() => { saveQueueIds(queueIds); }, [queueIds]);

  // Sync encodeJobs to localStorage
  useEffect(() => { saveEncodeJobs(encodeJobs); }, [encodeJobs]);

  // Track which items we've already triggered auto-detection for (persists across hot reloads)
  const AUTO_DETECTED_KEY = "playarr_editor_auto_detected";
  const autoDetectedRef = useRef<Set<number>>(
    (() => {
      try {
        const stored = localStorage.getItem(AUTO_DETECTED_KEY);
        return stored ? new Set(JSON.parse(stored) as number[]) : new Set<number>();
      } catch { return new Set<number>(); }
    })()
  );
  const markAutoDetected = useCallback((vid: number) => {
    autoDetectedRef.current.add(vid);
    try { localStorage.setItem(AUTO_DETECTED_KEY, JSON.stringify([...autoDetectedRef.current])); } catch {}
  }, []);

  // Auto-detect letterboxing for new queue items without existing crop settings
  useEffect(() => {
    if (!queueItems || queueItems.length === 0) return;
    for (const item of queueItems) {
      const vid = item.video_id;
      if (itemSettings[vid]?.crop || autoDetectedRef.current.has(vid)) continue;
      markAutoDetected(vid);
      detectLetterbox.mutateAsync(vid).then(result => {
        if (result.detected) {
          updateItemSetting(vid, {
            crop: {
              video_id: vid,
              original_w: result.original_w!,
              original_h: result.original_h!,
              crop_w: result.crop_w!,
              crop_h: result.crop_h!,
              crop_x: result.crop_x!,
              crop_y: result.crop_y!,
              effective_ratio: `${result.crop_w}:${result.crop_h}`,
            },
          });
        }
      }).catch(() => {});
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queueItems]);

  // ── Queue management ────────────────────────────────────
  const addToQueue = useCallback((videoIds: number[]) => {
    setQueueIds(prev => {
      const newIds = videoIds.filter(id => !prev.includes(id));
      return [...prev, ...newIds];
    });
  }, []);

  const removeFromQueue = useCallback((videoId: number) => {
    setQueueIds(prev => prev.filter(id => id !== videoId));
    setCheckedIds(prev => { const n = new Set(prev); n.delete(videoId); return n; });
    if (selectedId === videoId) setSelectedId(null);
  }, [selectedId]);

  const clearQueue = useCallback(() => {
    setQueueIds([]);
    setCheckedIds(new Set());
    setSelectedId(null);
    setEncodeJobs([]);
    autoDetectedRef.current.clear();
    try { localStorage.removeItem(AUTO_DETECTED_KEY); } catch {}
  }, []);

  const clearCheckedFromQueue = useCallback(() => {
    setQueueIds(prev => prev.filter(id => !checkedIds.has(id)));
    if (selectedId && checkedIds.has(selectedId)) setSelectedId(null);
    setCheckedIds(new Set());
  }, [checkedIds, selectedId]);

  // ── Toggle check ────────────────────────────────────────
  const toggleCheck = useCallback((videoId: number) => {
    setCheckedIds(prev => {
      const n = new Set(prev);
      if (n.has(videoId)) n.delete(videoId); else n.add(videoId);
      return n;
    });
  }, []);

  const toggleAllChecked = useCallback(() => {
    if (checkedIds.size === queueIds.length) {
      setCheckedIds(new Set());
    } else {
      setCheckedIds(new Set(queueIds));
    }
  }, [checkedIds.size, queueIds]);

  // ── Get settings for a specific item ────────────────────
  const getItemSettings = useCallback((videoId: number) => {
    return itemSettings[videoId] ?? {
      ratio: globalRatio,
      customRatioW: 16,
      customRatioH: 9,
      crf: globalCrf,
      preset: globalPreset,
      audioPassthrough: globalAudioPassthrough,
      trimEnabled: false,
      trimStart: 0,
      trimEnd: 0,
      audioCodec: "aac",
      audioBitrate: "auto",
      cropLinkLR: false,
      cropLinkTB: false,
    };
  }, [itemSettings, globalRatio, globalCrf, globalPreset, globalAudioPassthrough]);

  const updateItemSetting = useCallback((videoId: number, updates: Partial<typeof itemSettings[number]>) => {
    setItemSettings(prev => ({
      ...prev,
      [videoId]: { ...getItemSettings(videoId), ...updates },
    }));
  }, [getItemSettings]);

  // ── Selected item data ──────────────────────────────────
  const selectedItem = useMemo(
    () => queueItems?.find(i => i.video_id === selectedId) ?? null,
    [queueItems, selectedId],
  );

  const selectedSettings = selectedId ? getItemSettings(selectedId) : null;

  // ── Manual crop override from edge pixel inputs ──────────
  const handleCropOverride = useCallback((videoId: number, edge: "left" | "right" | "top" | "bottom", value: number) => {
    const item = queueItems?.find(i => i.video_id === videoId);
    if (!item) return;
    const origW = item.width ?? 1920;
    const origH = item.height ?? 1080;
    const settings = getItemSettings(videoId);
    const current = settings.crop;
    // Derive current edge values from existing crop (or defaults of 0)
    let left = current ? current.crop_x : 0;
    let top = current ? current.crop_y : 0;
    let right = current ? (current.original_w - current.crop_x - current.crop_w) : 0;
    let bottom = current ? (current.original_h - current.crop_y - current.crop_h) : 0;

    if (edge === "left") { left = Math.max(0, value); if (settings.cropLinkLR) right = left; }
    if (edge === "right") { right = Math.max(0, value); if (settings.cropLinkLR) left = right; }
    if (edge === "top") { top = Math.max(0, value); if (settings.cropLinkTB) bottom = top; }
    if (edge === "bottom") { bottom = Math.max(0, value); if (settings.cropLinkTB) top = bottom; }

    const crop_x = left;
    const crop_y = top;
    const crop_w = Math.max(2, origW - left - right);
    const crop_h = Math.max(2, origH - top - bottom);

    const g = gcd(crop_w, crop_h);
    updateItemSetting(videoId, {
      crop: {
        video_id: videoId,
        original_w: origW,
        original_h: origH,
        crop_w, crop_h, crop_x, crop_y,
        effective_ratio: `${crop_w / g}:${crop_h / g}`,
      },
    });
  }, [queueItems, getItemSettings, updateItemSetting]);

  // ── Clear crop override ─────────────────────────────────
  const handleClearCrop = useCallback((videoId: number) => {
    updateItemSetting(videoId, { crop: undefined });
  }, [updateItemSetting]);

  // ── Ratio change handler — sets DAR (display aspect ratio), not crop ──
  const handleRatioChange = useCallback((videoId: number, ratio: string, customW?: number, customH?: number) => {
    const darValue = ratio === "original" ? undefined
      : ratio === "custom" ? `${customW ?? 16}/${customH ?? 9}`
      : ratio;
    updateItemSetting(videoId, {
      ratio,
      customRatioW: customW ?? 16,
      customRatioH: customH ?? 9,
      targetDar: darValue,
    });
  }, [updateItemSetting]);

  // ── Letterbox scan ──────────────────────────────────────
  const handleScanLibrary = useCallback(async () => {
    setIsScanning(true);
    try {
      const result = await scanLetterbox.mutateAsync(200);
      if (result.status === "scanning" && result.job_id) {
        setScanJobId(result.job_id);
        toast({ type: "info", title: "Letterbox scan started..." });
      } else if (result.results) {
        // Inline results
        const ids = result.results.map(r => r.video_id);
        addToQueue(ids);
        // Store letterbox crop info
        for (const r of result.results) {
          updateItemSetting(r.video_id, {
            crop: {
              video_id: r.video_id,
              original_w: r.original_w,
              original_h: r.original_h,
              crop_w: r.crop_w,
              crop_h: r.crop_h,
              crop_x: r.crop_x,
              crop_y: r.crop_y,
              effective_ratio: `${r.crop_w}:${r.crop_h}`,
            },
          });
        }
        toast({ type: "success", title: `Found ${ids.length} videos with letterboxing` });
        setIsScanning(false);
      }
    } catch {
      toast({ type: "error", title: "Letterbox scan failed" });
      setIsScanning(false);
    }
  }, [scanLetterbox, addToQueue, updateItemSetting, toast]);

  // Watch scan job results
  useEffect(() => {
    if (scanResults.data?.status === "complete" && scanResults.data.results.length > 0) {
      const ids = scanResults.data.results.map((r: LetterboxScanItem) => r.video_id);
      addToQueue(ids);
      for (const r of scanResults.data.results) {
        updateItemSetting(r.video_id, {
          crop: {
            video_id: r.video_id,
            original_w: r.original_w,
            original_h: r.original_h,
            crop_w: r.crop_w,
            crop_h: r.crop_h,
            crop_x: r.crop_x,
            crop_y: r.crop_y,
            effective_ratio: `${r.crop_w}:${r.crop_h}`,
          },
        });
      }
      toast({ type: "success", title: `Found ${ids.length} videos with letterboxing` });
      setScanJobId(null);
      setIsScanning(false);
    } else if (scanResults.data?.status === "failed") {
      toast({ type: "error", title: `Scan failed: ${scanResults.data.error}` });
      setScanJobId(null);
      setIsScanning(false);
    }
  }, [scanResults.data, addToQueue, updateItemSetting, toast]);

  // Watch encode job status
  useEffect(() => {
    if (!activeEncodeJob || !encodeStatus.data) return;
    const { status } = encodeStatus.data;
    if (status === "complete") {
      const videoTitle = queueItems?.find(i => i.video_id === activeEncodeJob.videoId)?.title ?? "Video";
      const summary = encodeStatus.data.summary;
      toast({ type: "success", title: `Encode complete: ${videoTitle}`, description: summary ? summary.split("\n").slice(0, 3).join(" · ") : undefined });
      if (summary) {
        setLastEncodeSummary({ title: videoTitle, summary });
      }
      removeFromQueue(activeEncodeJob.videoId);
      setEncodeJobs(prev => prev.filter(j => j.jobId !== activeEncodeJob.jobId));
      refetchQueue();
    } else if (status === "failed") {
      toast({ type: "error", title: `Encode failed: ${encodeStatus.data.error ?? "Unknown error"}` });
      setEncodeJobs(prev => prev.filter(j => j.jobId !== activeEncodeJob.jobId));
    }
  }, [encodeStatus.data, activeEncodeJob, queueItems, toast, refetchQueue, removeFromQueue]);

  // Set of video IDs currently encoding
  const encodingVideoIds = useMemo(() => new Set(encodeJobs.map(j => j.videoId)), [encodeJobs]);

  // ── Detect letterbox on single item ─────────────────────
  const handleDetectSingle = useCallback(async (videoId: number) => {
    try {
      const result = await detectLetterbox.mutateAsync(videoId);
      if (result.detected) {
        updateItemSetting(videoId, {
          crop: {
            video_id: videoId,
            original_w: result.original_w!,
            original_h: result.original_h!,
            crop_w: result.crop_w!,
            crop_h: result.crop_h!,
            crop_x: result.crop_x!,
            crop_y: result.crop_y!,
            effective_ratio: `${result.crop_w}:${result.crop_h}`,
          },
        });
        toast({ type: "success", title: "Letterboxing detected — crop set" });
      } else {
        toast({ type: "info", title: "No letterboxing detected" });
      }
    } catch {
      toast({ type: "error", title: "Letterbox detection failed" });
    }
  }, [detectLetterbox, updateItemSetting, toast]);

  // ── Detect letterbox on checked items ───────────────────
  const [batchDetecting, setBatchDetecting] = useState(false);
  const handleDetectChecked = useCallback(async () => {
    if (checkedIds.size === 0) return;
    setBatchDetecting(true);
    let detected = 0;
    let failed = 0;
    for (const videoId of checkedIds) {
      try {
        const result = await detectLetterbox.mutateAsync(videoId);
        if (result.detected) {
          detected++;
          updateItemSetting(videoId, {
            crop: {
              video_id: videoId,
              original_w: result.original_w!,
              original_h: result.original_h!,
              crop_w: result.crop_w!,
              crop_h: result.crop_h!,
              crop_x: result.crop_x!,
              crop_y: result.crop_y!,
              effective_ratio: `${result.crop_w}:${result.crop_h}`,
            },
          });
        }
      } catch {
        failed++;
      }
    }
    setBatchDetecting(false);
    if (detected > 0) {
      toast({ type: "success", title: `Letterboxing detected on ${detected} of ${checkedIds.size} video${checkedIds.size > 1 ? "s" : ""}` });
    } else if (failed > 0) {
      toast({ type: "error", title: `Detection failed for ${failed} video${failed > 1 ? "s" : ""}` });
    } else {
      toast({ type: "info", title: `No letterboxing found in ${checkedIds.size} checked video${checkedIds.size > 1 ? "s" : ""}` });
    }
  }, [checkedIds, detectLetterbox, updateItemSetting, toast]);

  // ── Apply edits (encode) ────────────────────────────────
  const handleApplyChecked = useCallback(async () => {
    const items: EncodeRequest[] = [];
    for (const videoId of checkedIds) {
      const s = getItemSettings(videoId);
      const req: EncodeRequest = {
        video_id: videoId,
        crf: s.crf,
        preset: s.preset,
        audio_passthrough: s.audioPassthrough,
      };
      if (s.crop && (s.crop.crop_w !== s.crop.original_w || s.crop.crop_h !== s.crop.original_h)) {
        req.crop_w = s.crop.crop_w;
        req.crop_h = s.crop.crop_h;
        req.crop_x = s.crop.crop_x;
        req.crop_y = s.crop.crop_y;
      }
      if (s.targetDar) {
        req.target_dar = s.targetDar;
      }
      if (s.trimEnabled && (s.trimStart > 0 || s.trimEnd > 0)) {
        req.trim_start = s.trimStart > 0 ? s.trimStart : undefined;
        req.trim_end = s.trimEnd > 0 ? s.trimEnd : undefined;
        req.audio_codec = s.audioCodec !== "aac" ? s.audioCodec : undefined;
        req.audio_bitrate = s.audioBitrate !== "auto" ? s.audioBitrate : undefined;
      }
      items.push(req);
    }

    if (items.length === 0) {
      toast({ type: "warning", title: "No videos checked" });
      return;
    }

    try {
      if (items.length === 1) {
        const result = await encodeSingle.mutateAsync(items[0]);
        setEncodeJobs(prev => [...prev, { videoId: items[0].video_id, jobId: result.job_id }]);
        toast({ type: "info", title: "Encode job started" });
      } else {
        const result = await encodeBatch.mutateAsync(items);
        const newJobs = result.job_ids.map((jid: number, i: number) => ({ videoId: items[i].video_id, jobId: jid }));
        setEncodeJobs(prev => [...prev, ...newJobs]);
        toast({ type: "info", title: `${items.length} encode jobs started` });
      }
    } catch {
      toast({ type: "error", title: "Failed to start encode" });
    }
  }, [checkedIds, getItemSettings, encodeSingle, encodeBatch, toast]);

  // ── Encode single item ──────────────────────────────────
  const handleEncodeSingle = useCallback(async (videoId: number) => {
    const s = getItemSettings(videoId);
    const req: EncodeRequest = {
      video_id: videoId,
      crf: s.crf,
      preset: s.preset,
      audio_passthrough: s.audioPassthrough,
    };
    if (s.crop && (s.crop.crop_w !== s.crop.original_w || s.crop.crop_h !== s.crop.original_h)) {
      req.crop_w = s.crop.crop_w;
      req.crop_h = s.crop.crop_h;
      req.crop_x = s.crop.crop_x;
      req.crop_y = s.crop.crop_y;
    }
    if (s.targetDar) {
      req.target_dar = s.targetDar;
    }
    if (s.trimEnabled && (s.trimStart > 0 || s.trimEnd > 0)) {
      req.trim_start = s.trimStart > 0 ? s.trimStart : undefined;
      req.trim_end = s.trimEnd > 0 ? s.trimEnd : undefined;
      req.audio_codec = s.audioCodec !== "aac" ? s.audioCodec : undefined;
      req.audio_bitrate = s.audioBitrate !== "auto" ? s.audioBitrate : undefined;
    }
    try {
      const result = await encodeSingle.mutateAsync(req);
      setEncodeJobs(prev => [...prev, { videoId: videoId, jobId: result.job_id }]);
      toast({ type: "info", title: "Encode job started" });
    } catch {
      toast({ type: "error", title: "Failed to start encode" });
    }
  }, [getItemSettings, encodeSingle, toast]);

  const handleRestoreFromArchive = useCallback(async (videoId: number) => {
    try {
      await restoreArchive.mutateAsync(videoId);
      toast({ type: "success", title: "Original restored from archive" });
      refetchQueue();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      toast({ type: "error", title: detail || "Failed to restore from archive" });
    }
  }, [restoreArchive, toast, refetchQueue]);

  const handleToggleExcludeFromScan = useCallback(async (videoId: number, currentlyExcluded: boolean) => {
    const newExclude = !currentlyExcluded;
    try {
      await excludeFromScan.mutateAsync({ videoId, exclude: newExclude });
      if (newExclude) {
        removeFromQueue(videoId);
        toast({ type: "info", title: "Excluded from future scans and removed from queue" });
      } else {
        toast({ type: "info", title: "Re-included in future scans" });
      }
    } catch {
      toast({ type: "error", title: "Failed to update scan exclusion" });
    }
  }, [excludeFromScan, removeFromQueue, toast]);

  // ── Render ──────────────────────────────────────────────
  return (
    <div className="flex h-full">
      {/* ═══ Left: Queue Panel ═══ */}
      <div className="w-[420px] flex-shrink-0 border-r border-surface-border flex flex-col bg-surface-light">
        {/* Header */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-surface-border">
          <Film size={18} className="text-accent" />
          <h2 className="text-sm font-semibold text-text-primary">Video Editor</h2>
          <span className="text-xs text-text-muted ml-auto">
            {queueIds.length} item{queueIds.length !== 1 ? "s" : ""}
          </span>
        </div>

        {/* Toolbar */}
        <div className="flex flex-wrap items-center gap-1.5 px-3 py-2 border-b border-surface-border bg-surface">
          <Tooltip content="Scan library for letterboxed videos">
            <button
              className="btn-secondary btn-sm"
              onClick={handleScanLibrary}
              disabled={isScanning}
            >
              {isScanning ? <Loader2 size={14} className="animate-spin" /> : <ScanLine size={14} />}
              Scan
            </button>
          </Tooltip>

          {checkedIds.size > 0 && (
            <Tooltip content="Detect letterboxing on checked videos">
              <button
                className="btn-secondary btn-sm whitespace-nowrap"
                onClick={handleDetectChecked}
                disabled={batchDetecting}
              >
                {batchDetecting ? <Loader2 size={14} className="animate-spin" /> : <ScanLine size={14} />}
                Detect ({checkedIds.size})
              </button>
            </Tooltip>
          )}

          <Tooltip content={checkedIds.size === queueIds.length ? "Uncheck all" : "Check all"}>
            <button className="btn-secondary btn-sm" onClick={toggleAllChecked}>
              {checkedIds.size === queueIds.length && queueIds.length > 0 ? <CheckSquare size={14} /> : <Square size={14} />}
            </button>
          </Tooltip>

          <Tooltip content="Global encode settings">
            <button
              className={`btn-secondary btn-sm ${showSettings ? "!bg-accent/10 !text-accent" : ""}`}
              onClick={() => setShowSettings(!showSettings)}
            >
              <Settings2 size={14} />
            </button>
          </Tooltip>

          <div className="flex-1 min-w-[8px]" />

          <Tooltip content="Apply edits to checked videos">
            <button
              className="btn-primary btn-sm whitespace-nowrap"
              onClick={handleApplyChecked}
              disabled={checkedIds.size === 0 || encodeBatch.isPending || encodeSingle.isPending}
            >
              {(encodeBatch.isPending || encodeSingle.isPending) ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
              Apply ({checkedIds.size})
            </button>
          </Tooltip>

          {checkedIds.size > 0 && (
            <Tooltip content="Remove checked from queue">
              <button className="btn-secondary btn-sm text-orange-400 whitespace-nowrap" onClick={clearCheckedFromQueue}>
                <Trash2 size={14} /> Checked
              </button>
            </Tooltip>
          )}

          {queueIds.length > 0 && (
            <Tooltip content="Clear entire queue">
              <button className="btn-secondary btn-sm text-red-400 whitespace-nowrap" onClick={clearQueue}>
                <Trash2 size={14} /> All
              </button>
            </Tooltip>
          )}
        </div>

        {/* Global Settings Collapsible */}
        {showSettings && (
          <div className="px-3 py-3 border-b border-surface-border bg-surface/50 space-y-3">
            <h4 className="text-xs font-medium text-text-muted uppercase tracking-wider">Default Encode Settings</h4>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-xs text-text-secondary">
                CRF (Quality)
                <input
                  type="number"
                  min={0} max={51}
                  value={globalCrf}
                  onChange={e => setGlobalCrf(Number(e.target.value))}
                  className="input-sm w-full mt-1"
                />
                <span className="text-[10px] text-text-muted">Lower = better (18 = visually lossless)</span>
              </label>
              <label className="text-xs text-text-secondary">
                Preset
                <select
                  value={globalPreset}
                  onChange={e => setGlobalPreset(e.target.value)}
                  className="input-sm w-full mt-1"
                >
                  {X264_PRESETS.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </label>
            </div>
            <label className="flex items-center gap-2 text-xs text-text-secondary">
              <input
                type="checkbox"
                checked={globalAudioPassthrough}
                onChange={e => setGlobalAudioPassthrough(e.target.checked)}
              />
              Audio passthrough (copy original audio)
            </label>
            <label className="text-xs text-text-secondary">
              Default Ratio
              <select
                value={globalRatio}
                onChange={e => setGlobalRatio(e.target.value)}
                className="input-sm w-full mt-1"
              >
                {RATIO_PRESETS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
              </select>
            </label>
          </div>
        )}

        {/* Queue List */}
        <div className="flex-1 overflow-y-auto">
          {queueLoading && queueIds.length > 0 && (
            <div className="flex items-center justify-center py-8 text-text-muted">
              <Loader2 size={20} className="animate-spin mr-2" /> Loading queue...
            </div>
          )}

          {queueIds.length === 0 && !isScanning && (
            <div className="flex flex-col items-center justify-center py-12 text-text-muted text-sm">
              <Film size={40} className="mb-3 opacity-40" />
              <p>No videos in editor queue</p>
              <p className="text-xs mt-1">Use "Scan" to find letterboxed videos or</p>
              <p className="text-xs">"Send to Video Editor" from any video detail page</p>
            </div>
          )}

          {isScanning && scanJobId && scanResults.data && (
            <div className="px-4 py-3 bg-accent/5 border-b border-surface-border">
              <div className="flex items-center gap-2 text-xs text-accent">
                <Loader2 size={14} className="animate-spin" />
                <span>{scanResults.data.current_step || "Scanning..."}</span>
              </div>
              <div className="h-1 mt-2 bg-surface rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent transition-all"
                  style={{ width: `${scanResults.data.progress_percent ?? 0}%` }}
                />
              </div>
            </div>
          )}

          {encodeJobs.length > 0 && (
            <div className="px-4 py-3 bg-green-500/5 border-b border-surface-border">
              <div className="flex items-center gap-2 text-xs text-green-400">
                <Loader2 size={14} className="animate-spin" />
                <span className="flex-1 truncate">
                  {encodeStatus.data?.current_step || "Encoding..."}
                </span>
                <span className="text-text-muted">{encodeJobs.length} job{encodeJobs.length > 1 ? "s" : ""}</span>
              </div>
              <div className="h-1 mt-2 bg-surface rounded-full overflow-hidden">
                <div
                  className="h-full bg-green-500 transition-all"
                  style={{ width: `${encodeStatus.data?.progress_percent ?? 0}%` }}
                />
              </div>
            </div>
          )}

          {lastEncodeSummary && (
            <div className="px-4 py-3 bg-green-500/5 border-b border-surface-border">
              <div className="flex items-center justify-between gap-2 mb-1">
                <span className="text-xs font-medium text-green-400">Encode Summary: {lastEncodeSummary.title}</span>
                <button onClick={() => setLastEncodeSummary(null)} className="text-text-muted hover:text-text-primary">
                  <X size={12} />
                </button>
              </div>
              <pre className="text-[11px] text-text-secondary leading-relaxed whitespace-pre-wrap">{lastEncodeSummary.summary}</pre>
            </div>
          )}

          {queueItems?.map(item => (
            <QueueRow
              key={item.video_id}
              item={item}
              checked={checkedIds.has(item.video_id)}
              selected={selectedId === item.video_id}
              settings={getItemSettings(item.video_id)}
              isEncoding={encodingVideoIds.has(item.video_id)}
              encodeProgress={activeEncodeJob?.videoId === item.video_id ? (encodeStatus.data?.progress_percent ?? 0) : undefined}
              onToggleCheck={() => toggleCheck(item.video_id)}
              onSelect={() => setSelectedId(item.video_id === selectedId ? null : item.video_id)}
              onRemove={() => removeFromQueue(item.video_id)}
              onDetectLetterbox={() => handleDetectSingle(item.video_id)}
              onEncode={() => handleEncodeSingle(item.video_id)}
              onExclude={() => handleToggleExcludeFromScan(item.video_id, item.exclude_from_scan)}
              excludePending={excludeFromScan.isPending}
            />
          ))}
        </div>
      </div>

      {/* ═══ Right: Preview & Edit Panel ═══ */}
      <div className="flex-1 flex flex-col overflow-hidden bg-surface">
        {!selectedItem ? (
          <div className="flex-1 flex items-center justify-center text-text-muted">
            <div className="text-center">
              <MonitorPlay size={48} className="mx-auto mb-3 opacity-30" />
              <p className="text-sm">Select a video from the queue to preview</p>
            </div>
          </div>
        ) : (
          <>
            {/* Preview Area */}
            <div className="flex-1 flex flex-col items-center justify-center p-4 min-h-0 bg-zinc-500">
              <div className="flex items-center gap-2 mb-2">
                <Tooltip content={showOverlay ? "Hide crop overlay" : "Show crop overlay"}>
                  <button
                    className={`btn-secondary btn-sm ${showOverlay ? "!bg-accent/10 !text-accent" : ""}`}
                    onClick={() => setShowOverlay(!showOverlay)}
                  >
                    {showOverlay ? <Eye size={14} /> : <EyeOff size={14} />}
                    Preview {showOverlay ? "On" : "Off"}
                  </button>
                </Tooltip>
                <div className="flex items-center gap-1 ml-2">
                  <Tooltip content="Zoom out">
                    <button className="btn-secondary btn-sm" onClick={handleZoomOut} disabled={zoom <= 0.5}>
                      <ZoomOut size={14} />
                    </button>
                  </Tooltip>
                  <Tooltip content="Reset zoom (Ctrl+scroll to zoom)">
                    <button className="btn-secondary btn-sm tabular-nums min-w-[52px]" onClick={handleZoomReset}>
                      {Math.round(zoom * 100)}%
                    </button>
                  </Tooltip>
                  <Tooltip content="Zoom in">
                    <button className="btn-secondary btn-sm" onClick={handleZoomIn} disabled={zoom >= 4}>
                      <ZoomIn size={14} />
                    </button>
                  </Tooltip>
                </div>
              </div>
              <div
                ref={previewContainerRef}
                className={`overflow-auto flex-1 min-h-0 w-full editor-preview-scroll${zoom > 1 ? " select-none" : ""}`}
                onWheel={handleWheel}
                onMouseDown={handlePanStart}
                onMouseMove={handlePanMove}
                onMouseUp={handlePanEnd}
                onMouseLeave={handlePanEnd}
                style={{ cursor: zoom > 1 ? "grab" : undefined }}
              >
                <div style={zoom > 1 ? {
                  width: `${zoom * 100}%`,
                  height: `${zoom * 100}%`,
                  position: "relative" as const,
                } : {
                  display: "flex",
                  justifyContent: "center",
                  alignItems: "center",
                  width: "100%",
                  height: "100%",
                }}>
                  <div style={zoom > 1 ? {
                    position: "absolute" as const,
                    top: 0,
                    left: 0,
                    width: `${100 / zoom}%`,
                    height: `${100 / zoom}%`,
                    transform: `scale(${zoom})`,
                    transformOrigin: "top left",
                  } : {
                    transform: zoom < 1 ? `scale(${zoom})` : undefined,
                  }}>
                    <VideoPreview
                      videoId={selectedItem.video_id}
                      originalW={selectedItem.width ?? 1920}
                      originalH={selectedItem.height ?? 1080}
                      crop={selectedSettings?.crop ?? null}
                      targetDar={selectedSettings?.targetDar}
                      showOverlay={showOverlay}
                      onVideoRef={handleVideoRef}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* ── Playback Controls Bar ── */}
            <div className="flex items-center gap-2 px-4 py-1.5 bg-surface border-t border-surface-border">
              <button onClick={togglePlay} className="btn-ghost btn-xs text-text-primary">
                {isPlaying ? <Pause size={16} /> : <Play size={16} />}
              </button>
              <span className="text-[11px] text-text-muted tabular-nums w-[38px] text-right">{formatTime(currentTime)}</span>
              <input
                type="range"
                min={0}
                max={duration || 0}
                step={0.1}
                value={currentTime}
                onChange={handleSeek}
                className="flex-1 h-1 accent-accent cursor-pointer"
              />
              <span className="text-[11px] text-text-muted tabular-nums w-[38px]">{formatTime(duration)}</span>
              <button onClick={toggleMute} className="btn-ghost btn-xs text-text-muted">
                {isMuted || volume === 0 ? <VolumeX size={14} /> : <Volume2 size={14} />}
              </button>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={isMuted ? 0 : volume}
                onChange={handleVolumeChange}
                className="w-16 h-1 accent-accent cursor-pointer"
              />
            </div>

            {/* Edit Controls */}
            <div className="border-t border-surface-border bg-surface-light">
              {/* ── Title Bar ── */}
              <div className="flex items-center gap-3 px-4 py-2.5 border-b border-surface-border">
                <Tooltip content="Open video detail page">
                <h3
                  className="text-sm font-semibold text-accent truncate flex-1 cursor-pointer hover:underline flex items-center gap-1.5"
                  onClick={() => navigate(`/video/${selectedItem.video_id}`)}
                >
                  {selectedItem.artist} — {selectedItem.title}
                  <ExternalLink size={12} className="flex-shrink-0 opacity-60" />
                </h3>
                </Tooltip>
                <span className="text-[11px] text-text-muted tabular-nums">
                  {selectedItem.width}×{selectedItem.height}
                  {selectedItem.video_codec ? ` · ${selectedItem.video_codec}` : ""}
                  {selectedItem.fps ? ` · ${selectedItem.fps}fps` : ""}
                </span>
                <div className="flex items-center gap-1.5 ml-2 pl-2 border-l border-surface-border">
                  {selectedItem.has_archive && (
                    <Tooltip content="Delete encoded video and restore original from archive">
                      <button
                        className="btn-secondary btn-sm text-amber-400"
                        onClick={() => handleRestoreFromArchive(selectedItem.video_id)}
                        disabled={restoreArchive.isPending}
                      >
                        {restoreArchive.isPending ? <Loader2 size={14} className="animate-spin" /> : <ArchiveRestore size={14} />}
                        Restore
                      </button>
                    </Tooltip>
                  )}
                  <Tooltip content={selectedItem.exclude_from_scan ? "Re-include in future letterbox scans" : "Exclude from future letterbox scans (false positive)"}>
                    <button
                      className={`btn-secondary btn-sm ${selectedItem.exclude_from_scan ? "text-orange-400" : "text-text-muted"}`}
                      onClick={() => handleToggleExcludeFromScan(selectedItem.video_id, selectedItem.exclude_from_scan)}
                      disabled={excludeFromScan.isPending}
                    >
                      {excludeFromScan.isPending ? <Loader2 size={14} className="animate-spin" /> : <Ban size={14} />}
                      {selectedItem.exclude_from_scan ? "Excluded" : "Exclude"}
                    </button>
                  </Tooltip>
                  <Tooltip content="Encode this video">
                    <button
                      className="btn-primary btn-sm"
                      onClick={() => handleEncodeSingle(selectedItem.video_id)}
                      disabled={encodeSingle.isPending || encodingVideoIds.has(selectedItem.video_id)}
                    >
                      {(encodeSingle.isPending || encodingVideoIds.has(selectedItem.video_id)) ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                      {encodingVideoIds.has(selectedItem.video_id) ? `Encoding ${activeEncodeJob?.videoId === selectedItem.video_id ? `${encodeStatus.data?.progress_percent ?? 0}%` : "..."}` : "Encode"}
                    </button>
                  </Tooltip>
                  <Tooltip content="Remove from queue">
                    <button
                      className="btn-secondary btn-sm text-red-400"
                      onClick={() => removeFromQueue(selectedItem.video_id)}
                    >
                      <Trash2 size={14} />
                    </button>
                  </Tooltip>
                </div>
              </div>

              {/* ── Settings Panels ── */}
              <div className="flex gap-3 px-4 py-3">
                {/* Encode Settings Group */}
                <div className="flex-1 rounded border border-surface-border bg-surface/40 p-3">
                  <h4 className="text-[10px] font-semibold uppercase tracking-wider text-text-muted mb-2 flex items-center gap-1.5">
                    <Settings2 size={11} /> Encode Settings
                  </h4>
                  <div className="flex flex-wrap items-end gap-3">
                    <label className="text-xs text-text-secondary">
                      Aspect Ratio
                      <select
                        value={selectedSettings?.ratio ?? "original"}
                        onChange={e => handleRatioChange(selectedItem.video_id, e.target.value)}
                        className="input-sm w-auto mt-1 block"
                      >
                        {RATIO_PRESETS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
                      </select>
                    </label>

                    {selectedSettings?.ratio === "custom" && (
                      <>
                        <label className="text-xs text-text-secondary">
                          W
                          <input
                            type="number" min={1}
                            value={selectedSettings?.customRatioW ?? 16}
                            onChange={e => handleRatioChange(selectedItem.video_id, "custom", Number(e.target.value), selectedSettings?.customRatioH)}
                            className="input-sm w-16 mt-1 block"
                          />
                        </label>
                        <label className="text-xs text-text-secondary">
                          H
                          <input
                            type="number" min={1}
                            value={selectedSettings?.customRatioH ?? 9}
                            onChange={e => handleRatioChange(selectedItem.video_id, "custom", selectedSettings?.customRatioW, Number(e.target.value))}
                            className="input-sm w-16 mt-1 block"
                          />
                        </label>
                      </>
                    )}

                    <label className="text-xs text-text-secondary">
                      CRF
                      <NumericStepper
                        min={0} max={51}
                        value={selectedSettings?.crf ?? globalCrf}
                        onChange={v => updateItemSetting(selectedItem.video_id, { crf: v })}
                      />
                    </label>

                    <label className="text-xs text-text-secondary">
                      Preset
                      <select
                        value={selectedSettings?.preset ?? globalPreset}
                        onChange={e => updateItemSetting(selectedItem.video_id, { preset: e.target.value })}
                        className="input-sm w-auto mt-1 block"
                      >
                        {X264_PRESETS.map(p => <option key={p} value={p}>{p}</option>)}
                      </select>
                    </label>

                    <label className="flex items-center gap-2 text-xs text-text-secondary pb-1">
                      <input
                        type="checkbox"
                        checked={selectedSettings?.audioPassthrough ?? globalAudioPassthrough}
                        onChange={e => updateItemSetting(selectedItem.video_id, { audioPassthrough: e.target.checked })}
                      />
                      Audio copy
                    </label>
                  </div>
                  {selectedSettings?.targetDar && (
                    <div className="text-[11px] text-blue-400 flex items-center gap-2 mt-2 pt-2 border-t border-surface-border">
                      <Film size={11} />
                      DAR: {selectedSettings.targetDar}
                    </div>
                  )}
                </div>

                {/* Crop Controls Group */}
                <div className="flex-1 rounded border border-surface-border bg-surface/40 p-3">
                  <div className="flex items-center justify-between mb-2">
                    <h4 className="text-[10px] font-semibold uppercase tracking-wider text-text-muted flex items-center gap-1.5">
                      <Scissors size={11} /> Crop
                    </h4>
                    {selectedSettings?.crop && (selectedSettings.crop.crop_w !== selectedSettings.crop.original_w || selectedSettings.crop.crop_h !== selectedSettings.crop.original_h) && (
                      <button
                        className="btn-secondary btn-xs text-red-400"
                        onClick={() => handleClearCrop(selectedItem.video_id)}
                      >
                        <X size={11} /> Clear
                      </button>
                    )}
                  </div>
                  <div className="flex flex-wrap items-end gap-3">
                    <label className="text-xs text-text-secondary">
                      Left
                      <NumericStepper
                        min={0}
                        value={selectedSettings?.crop ? selectedSettings.crop.crop_x : 0}
                        onChange={v => handleCropOverride(selectedItem.video_id, "left", v)}
                      />
                    </label>
                    <Tooltip content={selectedSettings?.cropLinkLR ? "Unlink Left/Right" : "Link Left/Right (same value)"}>
                      <button
                        className={`btn-ghost btn-xs mb-1 ${selectedSettings?.cropLinkLR ? "text-accent" : "text-text-muted"}`}
                        onClick={() => {
                          const linking = !(selectedSettings?.cropLinkLR ?? false);
                          updateItemSetting(selectedItem.video_id, { cropLinkLR: linking });
                          if (linking && selectedSettings?.crop) {
                            handleCropOverride(selectedItem.video_id, "right", selectedSettings.crop.crop_x);
                          }
                        }}
                      >
                        <Link2 size={13} />
                      </button>
                    </Tooltip>
                    <label className={`text-xs text-text-secondary ${selectedSettings?.cropLinkLR ? "opacity-50" : ""}`}>
                      Right
                      <NumericStepper
                        min={0}
                        value={selectedSettings?.crop ? (selectedSettings.crop.original_w - selectedSettings.crop.crop_x - selectedSettings.crop.crop_w) : 0}
                        onChange={v => handleCropOverride(selectedItem.video_id, "right", v)}
                        disabled={selectedSettings?.cropLinkLR}
                      />
                    </label>
                    <label className="text-xs text-text-secondary">
                      Top
                      <NumericStepper
                        min={0}
                        value={selectedSettings?.crop ? selectedSettings.crop.crop_y : 0}
                        onChange={v => handleCropOverride(selectedItem.video_id, "top", v)}
                      />
                    </label>
                    <Tooltip content={selectedSettings?.cropLinkTB ? "Unlink Top/Bottom" : "Link Top/Bottom (same value)"}>
                      <button
                        className={`btn-ghost btn-xs mb-1 ${selectedSettings?.cropLinkTB ? "text-accent" : "text-text-muted"}`}
                        onClick={() => {
                          const linking = !(selectedSettings?.cropLinkTB ?? false);
                          updateItemSetting(selectedItem.video_id, { cropLinkTB: linking });
                          if (linking && selectedSettings?.crop) {
                            handleCropOverride(selectedItem.video_id, "bottom", selectedSettings.crop.crop_y);
                          }
                        }}
                      >
                        <Link2 size={13} />
                      </button>
                    </Tooltip>
                    <label className={`text-xs text-text-secondary ${selectedSettings?.cropLinkTB ? "opacity-50" : ""}`}>
                      Bottom
                      <NumericStepper
                        min={0}
                        value={selectedSettings?.crop ? (selectedSettings.crop.original_h - selectedSettings.crop.crop_y - selectedSettings.crop.crop_h) : 0}
                        onChange={v => handleCropOverride(selectedItem.video_id, "bottom", v)}
                        disabled={selectedSettings?.cropLinkTB}
                      />
                    </label>
                  </div>
                  {selectedSettings?.crop && (selectedSettings.crop.crop_w !== selectedSettings.crop.original_w || selectedSettings.crop.crop_h !== selectedSettings.crop.original_h) && (
                    <div className="text-[11px] text-accent flex items-center gap-2 mt-2 pt-2 border-t border-surface-border">
                      <Scissors size={11} />
                      {selectedSettings.crop.crop_w}×{selectedSettings.crop.crop_h}+{selectedSettings.crop.crop_x}+{selectedSettings.crop.crop_y}
                      <span className="text-text-muted">({selectedSettings.crop.effective_ratio})</span>
                    </div>
                  )}
                </div>
              </div>

              {/* ── Trim Controls ── */}
              <div className="px-4 pb-3">
                <div className="rounded border border-surface-border bg-surface/40 p-3">
                  <div className="flex items-center gap-3 mb-2">
                    <label className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-text-muted cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selectedSettings?.trimEnabled ?? false}
                        onChange={e => updateItemSetting(selectedItem.video_id, {
                          trimEnabled: e.target.checked,
                          // Force audio re-encode when trim is enabled
                          ...(e.target.checked ? { audioPassthrough: false } : {}),
                        })}
                      />
                      <Timer size={11} /> Trim
                    </label>
                    {selectedSettings?.trimEnabled && selectedItem.duration_seconds && (
                      <span className="text-[10px] text-text-muted ml-auto tabular-nums">
                        Output: {formatTime(
                          (selectedItem.duration_seconds ?? 0) - (selectedSettings?.trimStart ?? 0) - (selectedSettings?.trimEnd ?? 0)
                        )}
                        <span className="text-text-muted/50"> / {formatTime(selectedItem.duration_seconds)}</span>
                      </span>
                    )}
                  </div>

                  {selectedSettings?.trimEnabled && (
                    <>
                      {/* Trim timeline bar */}
                      <div className="relative h-6 bg-surface-lighter rounded overflow-hidden mb-3">
                        {/* Trimmed-away regions (darker) */}
                        {selectedItem.duration_seconds && selectedItem.duration_seconds > 0 && (
                          <>
                            <div
                              className="absolute inset-y-0 left-0 bg-red-500/20 border-r border-red-500/40"
                              style={{ width: `${((selectedSettings?.trimStart ?? 0) / selectedItem.duration_seconds) * 100}%` }}
                            />
                            <div
                              className="absolute inset-y-0 right-0 bg-red-500/20 border-l border-red-500/40"
                              style={{ width: `${((selectedSettings?.trimEnd ?? 0) / selectedItem.duration_seconds) * 100}%` }}
                            />
                          </>
                        )}
                        {/* Labels */}
                        <div className="absolute inset-0 flex items-center justify-between px-2 text-[9px] tabular-nums text-text-muted pointer-events-none">
                          <span className={selectedSettings?.trimStart ? "text-red-400" : ""}>
                            {formatTime(selectedSettings?.trimStart ?? 0)}
                          </span>
                          <span className="text-text-muted/50">▼</span>
                          <span className={selectedSettings?.trimEnd ? "text-red-400" : ""}>
                            -{formatTime(selectedSettings?.trimEnd ?? 0)}
                          </span>
                        </div>
                      </div>

                      <div className="flex flex-wrap items-end gap-3">
                        {/* Trim start */}
                        <div className="flex items-end gap-1.5">
                          <label className="text-xs text-text-secondary">
                            Start trim (s)
                            <NumericStepper
                              min={0} step={0.1}
                              max={selectedItem.duration_seconds ? selectedItem.duration_seconds - (selectedSettings?.trimEnd ?? 0) - 0.1 : undefined}
                              value={selectedSettings?.trimStart ?? 0}
                              onChange={v => updateItemSetting(selectedItem.video_id, { trimStart: v })}
                              className="w-20"
                            />
                          </label>
                          <Tooltip content="Seek to trim start point">
                            <button
                              className="btn-ghost btn-xs text-text-muted hover:text-accent mb-0.5"
                              onClick={() => {
                                if (videoRef.current) {
                                  videoRef.current.currentTime = selectedSettings?.trimStart ?? 0;
                                }
                              }}
                            >
                              <SkipBack size={13} />
                            </button>
                          </Tooltip>
                        </div>

                        {/* Trim end */}
                        <div className="flex items-end gap-1.5">
                          <label className="text-xs text-text-secondary">
                            End trim (s)
                            <NumericStepper
                              min={0} step={0.1}
                              max={selectedItem.duration_seconds ? selectedItem.duration_seconds - (selectedSettings?.trimStart ?? 0) - 0.1 : undefined}
                              value={selectedSettings?.trimEnd ?? 0}
                              onChange={v => updateItemSetting(selectedItem.video_id, { trimEnd: v })}
                              className="w-20"
                            />
                          </label>
                          <Tooltip content="Seek to trim end point">
                            <button
                              className="btn-ghost btn-xs text-text-muted hover:text-accent mb-0.5"
                              onClick={() => {
                                if (videoRef.current && selectedItem.duration_seconds) {
                                  videoRef.current.currentTime = selectedItem.duration_seconds - (selectedSettings?.trimEnd ?? 0);
                                }
                              }}
                            >
                              <SkipForward size={13} />
                            </button>
                          </Tooltip>
                        </div>

                        <div className="border-l border-surface-border pl-3 flex items-end gap-3">
                          {/* Audio codec */}
                          <Tooltip content="AAC: universally compatible. Opus: better quality at low bitrates. FLAC: lossless (larger files).">
                            <label className="text-xs text-text-secondary">
                              Audio codec
                              <select
                                value={selectedSettings?.audioCodec ?? "aac"}
                                onChange={e => updateItemSetting(selectedItem.video_id, { audioCodec: e.target.value })}
                                className="input-sm w-auto mt-1 block"
                              >
                                <option value="aac">AAC</option>
                                <option value="opus">Opus</option>
                                <option value="flac">FLAC (lossless)</option>
                              </select>
                            </label>
                          </Tooltip>

                          {/* Audio bitrate (not for FLAC) */}
                          {(selectedSettings?.audioCodec ?? "aac") !== "flac" && (
                            <Tooltip content="Auto matches the source bitrate. Higher values preserve more audio quality but increase file size.">
                              <label className="text-xs text-text-secondary">
                                Bitrate
                                <select
                                  value={selectedSettings?.audioBitrate ?? "auto"}
                                  onChange={e => updateItemSetting(selectedItem.video_id, { audioBitrate: e.target.value })}
                                  className="input-sm w-auto mt-1 block"
                                >
                                  <option value="auto">Auto (match source)</option>
                                  <option value="128k">128k</option>
                                  <option value="192k">192k</option>
                                  <option value="256k">256k</option>
                                  <option value="320k">320k</option>
                                </select>
                              </label>
                            </Tooltip>
                          )}
                        </div>
                      </div>

                      <div className="text-[10px] text-text-muted/60 mt-2 flex items-center gap-1">
                        Trim requires audio re-encoding
                        {selectedItem.audio_codec && (
                        <span>
                           · Source: {selectedItem.audio_codec}
                          {selectedItem.audio_bitrate ? ` ${Math.round(selectedItem.audio_bitrate / 1000)}k` : ""}
                          {selectedItem.audio_channels ? ` ${selectedItem.audio_channels}ch` : ""}
                        </span>
                        )}
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Queue Row Component ──────────────────────────────────
function QueueRow({ item, checked, selected, settings, isEncoding, encodeProgress, onToggleCheck, onSelect, onRemove, onDetectLetterbox, onEncode, onExclude, excludePending }: {
  item: EditorQueueItem;
  checked: boolean;
  selected: boolean;
  settings: { ratio: string; crop?: CropPreviewResponse; targetDar?: string };
  isEncoding: boolean;
  encodeProgress?: number;
  onToggleCheck: () => void;
  onSelect: () => void;
  onRemove: () => void;
  onDetectLetterbox: () => void;
  onEncode: () => void;
  onExclude: () => void;
  excludePending: boolean;
}) {
  const hasCrop = settings.crop && (settings.crop.crop_w !== settings.crop.original_w || settings.crop.crop_h !== settings.crop.original_h);

  return (
    <div
      className={`flex flex-col border-b border-surface-border transition-colors cursor-pointer ${
        selected ? "bg-accent/10 border-l-2 border-l-accent" : "hover:bg-surface-lighter"
      }`}
    >
      <div className="flex items-center gap-2 px-3 pt-2 pb-1">
        {/* Checkbox */}
        <button onClick={onToggleCheck} className="flex-shrink-0 text-text-muted hover:text-text-primary">
          {checked ? <CheckSquare size={16} className="text-accent" /> : <Square size={16} />}
        </button>

        {/* Poster thumbnail */}
        <div
          className="w-10 h-10 rounded bg-surface-lighter flex-shrink-0 overflow-hidden"
          onClick={onSelect}
        >
          <img
            src={playbackApi.posterUrl(item.video_id)}
            alt=""
            className="w-full h-full object-cover"
            onError={e => { (e.target as HTMLImageElement).style.display = "none"; }}
          />
        </div>

        {/* Title */}
        <div className="flex-1 min-w-0" onClick={onSelect}>
          <div className="text-sm font-medium text-text-primary truncate">
            {item.artist} — {item.title}
          </div>
        </div>
      </div>

      {/* Bottom row: metadata + actions */}
      <div className="flex items-center gap-1.5 px-3 pb-2 pl-[4.25rem]">
        {/* Metadata badges */}
        <div className="flex items-center gap-1.5 text-[10px] text-text-muted flex-1 min-w-0">
          <span>{item.resolution_label ?? `${item.width}x${item.height}`}</span>
          {item.video_codec && <span>· {item.video_codec}</span>}
          {item.letterbox_detected && (
            <span className="text-orange-400">· Letterboxed</span>
          )}
          {hasCrop && (
            <span className="text-accent">· Crop set</span>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-0.5 flex-shrink-0">
          <Tooltip content="Detect letterboxing">
            <button className="btn-ghost btn-xs text-text-muted hover:text-text-primary" onClick={onDetectLetterbox}>
              <ScanLine size={13} />
            </button>
          </Tooltip>
          <Tooltip content="Encode this video">
            <button className="btn-ghost btn-xs text-text-muted hover:text-accent" onClick={onEncode} disabled={isEncoding}>
              {isEncoding ? <Loader2 size={13} className="animate-spin text-green-400" /> : <Play size={13} />}
            </button>
          </Tooltip>
          <Tooltip content={item.exclude_from_scan ? "Re-include in future scans" : "Exclude from future scans"}>
            <button
              className={`btn-ghost btn-xs ${item.exclude_from_scan ? "text-orange-400" : "text-text-muted hover:text-orange-400"}`}
              onClick={onExclude}
              disabled={excludePending}
            >
              {excludePending ? <Loader2 size={13} className="animate-spin" /> : <Ban size={13} />}
            </button>
          </Tooltip>
          <Tooltip content="Remove from queue">
            <button className="btn-ghost btn-xs text-text-muted hover:text-red-400" onClick={onRemove}>
              <X size={13} />
            </button>
          </Tooltip>
        </div>
      </div>

      {/* Encode progress bar */}
      {isEncoding && (
        <div className="h-1 bg-surface-lighter">
          <div
            className="h-full bg-green-500 transition-all duration-500"
            style={{ width: `${encodeProgress ?? 0}%` }}
          />
        </div>
      )}
    </div>
  );
}

// ── Video Preview Component ──────────────────────────────
function parseDar(dar: string): number | null {
  // "16:9" | "4:3" | "21:9" | "1:1" | "2.35:1" | "1.85:1" | "16/9"
  const sep = dar.includes("/") ? "/" : ":";
  const parts = dar.split(sep).map(Number);
  if (parts.length === 2 && parts[0] > 0 && parts[1] > 0) return parts[0] / parts[1];
  return null;
}

function VideoPreview({ videoId, originalW, originalH, crop, targetDar, showOverlay, onVideoRef }: {
  videoId: number;
  originalW: number;
  originalH: number;
  crop: CropPreviewResponse | null;
  targetDar?: string;
  showOverlay: boolean;
  onVideoRef?: (el: HTMLVideoElement | null) => void;
}) {
  const originalRatio = originalW / originalH;
  const darRatio = targetDar ? parseDar(targetDar) : null;
  const hasDar = darRatio !== null && Math.abs(darRatio - originalRatio) > 0.01;

  // When DAR is active, use the target aspect ratio for the container
  const displayRatio = hasDar ? darRatio! : originalRatio;
  const containerStyle = { aspectRatio: `${displayRatio}` };

  const hasCrop = crop && (crop.crop_w !== originalW || crop.crop_h !== originalH);

  // Calculate overlay positions as percentages
  const topPct = crop ? (crop.crop_y / originalH) * 100 : 0;
  const bottomPct = crop ? ((originalH - crop.crop_y - crop.crop_h) / originalH) * 100 : 0;
  const leftPct = crop ? (crop.crop_x / originalW) * 100 : 0;
  const rightPct = crop ? ((originalW - crop.crop_x - crop.crop_w) / originalW) * 100 : 0;

  return (
    <div className="relative max-w-full max-h-full" style={containerStyle}>
      <video
        ref={onVideoRef}
        src={playbackApi.streamUrl(videoId)}
        className={`w-full h-full rounded-lg ${
          hasDar && showOverlay ? "object-fill" : "object-contain"
        }`}
        playsInline
        preload="metadata"
        poster={playbackApi.posterUrl(videoId)}
      />

      {/* DAR label */}
      {hasDar && showOverlay && (
        <div className="absolute top-2 left-2 bg-black/70 text-blue-400 text-[10px] px-1.5 py-0.5 rounded pointer-events-none flex items-center gap-1">
          <Film size={10} /> DAR: {targetDar}
        </div>
      )}

      {/* Crop overlay — dark regions showing what will be removed */}
      {hasCrop && showOverlay && (
        <>
          {/* Top bar */}
          {topPct > 0 && (
            <div
              className="absolute top-0 left-0 right-0 bg-black/60 pointer-events-none border-b border-red-500/50"
              style={{ height: `${topPct}%` }}
            />
          )}
          {/* Bottom bar */}
          {bottomPct > 0 && (
            <div
              className="absolute bottom-0 left-0 right-0 bg-black/60 pointer-events-none border-t border-red-500/50"
              style={{ height: `${bottomPct}%` }}
            />
          )}
          {/* Left bar */}
          {leftPct > 0 && (
            <div
              className="absolute left-0 bg-black/60 pointer-events-none border-r border-red-500/50"
              style={{ top: `${topPct}%`, bottom: `${bottomPct}%`, width: `${leftPct}%` }}
            />
          )}
          {/* Right bar */}
          {rightPct > 0 && (
            <div
              className="absolute right-0 bg-black/60 pointer-events-none border-l border-red-500/50"
              style={{ top: `${topPct}%`, bottom: `${bottomPct}%`, width: `${rightPct}%` }}
            />
          )}
          {/* Crop label */}
          <div className="absolute top-2 right-2 bg-black/70 text-red-400 text-[10px] px-1.5 py-0.5 rounded pointer-events-none">
            Crop: {crop!.crop_w}x{crop!.crop_h}
          </div>
        </>
      )}
    </div>
  );
}

// ── Export utility for use from VideoDetailPage ───────────
export function addToVideoEditorQueue(videoIds: number[]) {
  const current = loadQueueIds();
  const newIds = videoIds.filter(id => !current.includes(id));
  saveQueueIds([...current, ...newIds]);
}
