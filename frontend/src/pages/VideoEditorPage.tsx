import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import {
  Scissors, ScanLine, Play, Pause, Square, CheckSquare,
  Loader2, Settings2, MonitorPlay, Film, X, Eye, EyeOff, Ban, ExternalLink,
  Volume2, VolumeX, ZoomIn, ZoomOut, Timer, SkipBack, SkipForward, Link2,
  ChevronUp, ChevronDown, ArrowUpDown, ListX, StepBack, StepForward,
  RotateCcw, AlertTriangle, Archive,
} from "lucide-react";
import { useEditorQueue, useDetectLetterbox, useScanLetterbox, useEditorScanResults, useEditorEncodeStatus, useVideoEditorEncode, useVideoEditorBatchEncode, useSetExcludeFromScan, useRestoreFromArchive } from "@/hooks/queries";
import { playbackApi, jobsApi } from "@/lib/api";
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
const MANUAL_IDS_KEY = "playarr_editor_manual_ids";

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

function loadManualIds(): Set<number> {
  try {
    const raw = localStorage.getItem(MANUAL_IDS_KEY);
    return raw ? new Set(JSON.parse(raw) as number[]) : new Set();
  } catch {
    return new Set();
  }
}

function saveManualIds(ids: Set<number>) {
  localStorage.setItem(MANUAL_IDS_KEY, JSON.stringify([...ids]));
}

// ── Numeric Stepper — larger +/- buttons for number inputs ──
// Chevron buttons auto-repeat on press-and-hold (400ms delay, then every 60ms)
// and Shift+click steps by ×10.
function NumericStepper({ value, onChange, min, max, step = 1, disabled, className = "w-16" }: {
  value: number;
  onChange: (val: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  className?: string;
}) {
  const clamp = useCallback((v: number) => {
    if (min !== undefined) v = Math.max(min, v);
    if (max !== undefined) v = Math.min(max, v);
    return Math.round(v * 1000) / 1000;
  }, [min, max]);

  // Refs so hold-to-repeat always reads the latest value/handlers
  const valueRef = useRef(value);
  const onChangeRef = useRef(onChange);
  const clampRef = useRef(clamp);
  useEffect(() => {
    valueRef.current = value;
    onChangeRef.current = onChange;
    clampRef.current = clamp;
  });

  const delayTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const repeatTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopRepeat = useCallback(() => {
    if (delayTimerRef.current) { clearTimeout(delayTimerRef.current); delayTimerRef.current = null; }
    if (repeatTimerRef.current) { clearInterval(repeatTimerRef.current); repeatTimerRef.current = null; }
  }, []);

  // Clear timers on unmount
  useEffect(() => stopRepeat, [stopRepeat]);

  const stepValue = useCallback((dir: 1 | -1, mult: number) => {
    const next = clampRef.current(valueRef.current + dir * step * mult);
    if (next === valueRef.current) {
      // Hit min/max — stop auto-repeating
      stopRepeat();
      return;
    }
    onChangeRef.current(next);
  }, [step, stopRepeat]);

  const handlePointerDown = useCallback((dir: 1 | -1) => (e: React.PointerEvent) => {
    if (disabled) return;
    const mult = e.shiftKey ? 10 : 1;
    stepValue(dir, mult);
    stopRepeat();
    delayTimerRef.current = setTimeout(() => {
      repeatTimerRef.current = setInterval(() => stepValue(dir, mult), 60);
    }, 400);
    // Stop even if the pointer is released outside the button
    window.addEventListener("pointerup", stopRepeat, { once: true });
  }, [disabled, stepValue, stopRepeat]);

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
          title="Hold to repeat · Shift = ×10"
          disabled={disabled || (max !== undefined && value >= max)}
          className="flex items-center justify-center px-1.5 h-1/2 text-text-muted hover:text-text-primary hover:bg-surface-hover disabled:opacity-30 border-b border-surface-border"
          onPointerDown={handlePointerDown(1)}
          onPointerUp={stopRepeat}
          onPointerLeave={stopRepeat}
          onPointerCancel={stopRepeat}
        >
          <ChevronUp size={12} />
        </button>
        <button
          type="button"
          tabIndex={-1}
          title="Hold to repeat · Shift = ×10"
          disabled={disabled || (min !== undefined && value <= min)}
          className="flex items-center justify-center px-1.5 h-1/2 text-text-muted hover:text-text-primary hover:bg-surface-hover disabled:opacity-30"
          onPointerDown={handlePointerDown(-1)}
          onPointerUp={stopRepeat}
          onPointerLeave={stopRepeat}
          onPointerCancel={stopRepeat}
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
    /** audioPassthrough value before trim forced it off, restored on trim-disable */
    prevAudioPassthrough?: boolean;
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
  const [showScanDialog, setShowScanDialog] = useState(false);

  // Manual item tracking (persisted in localStorage)
  const [manualIds, setManualIds] = useState<Set<number>>(loadManualIds);

  // Tag filter for queue display
  type TagFilter = "all" | "letterboxed" | "manual";
  const [tagFilter, setTagFilter] = useState<TagFilter>("all");

  // Queue display: sorting and pagination
  type SortField = "artist" | "album" | "title" | "created_at" | "editor_order";
  type SortDir = "asc" | "desc";
  const [sortBy, setSortBy] = useState<SortField>("editor_order");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [pageSize, setPageSize] = useState<number>(10);
  const [currentPage, setCurrentPage] = useState(1);

  // Encode job tracking: list of { videoId, jobId } — persisted so it survives page navigation
  const [encodeJobs, setEncodeJobs] = useState<{ videoId: number; jobId: number }[]>(loadEncodeJobs);
  const activeEncodeJob = encodeJobs[0] ?? null;

  // Post-encode summary (shown as dismissible banner after encode completes)
  const [lastEncodeSummary, setLastEncodeSummary] = useState<{ videoId: number; title: string; summary: string } | null>(null);

  // Encode confirmation modal — list of video IDs pending confirmation
  const [encodeConfirmIds, setEncodeConfirmIds] = useState<number[] | null>(null);

  // Restore-original confirmation modal
  const [restoreConfirm, setRestoreConfirm] = useState<{ videoId: number; title: string } | null>(null);

  // Fetch queue items from API
  const { data: queueItems, isLoading: queueLoading } = useEditorQueue(queueIds);
  const detectLetterbox = useDetectLetterbox();
  const scanLetterbox = useScanLetterbox();
  const scanResults = useEditorScanResults(scanJobId);
  const encodeStatus = useEditorEncodeStatus(activeEncodeJob?.jobId ?? null);
  const encodeSingle = useVideoEditorEncode();
  const encodeBatch = useVideoEditorBatchEncode();
  const excludeFromScan = useSetExcludeFromScan();
  const restoreFromArchive = useRestoreFromArchive();

  // Local mutation — cancel a running encode job (no existing hook for this)
  const cancelEncode = useMutation({
    mutationFn: (jobId: number) => jobsApi.cancel(jobId),
  });

  // ── Video playback controls ──────────────────────────────
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const dbDurationRef = useRef<number>(0);
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
    // Force the video element to reload when src changes on the same element
    // (some browsers don't fire loadedmetadata on src attribute change alone)
    if (videoRef.current) {
      videoRef.current.load();
    }
  }, [selectedId]);

  const handleDurationChange = useCallback(() => {
    if (videoRef.current) {
      const dur = videoRef.current.duration;
      if (dur && isFinite(dur) && dur > 0) setDuration(dur);
    }
  }, []);

  const handleVideoRef = useCallback((el: HTMLVideoElement | null) => {
    if (videoRef.current) {
      videoRef.current.removeEventListener("timeupdate", handleTimeUpdate);
      videoRef.current.removeEventListener("loadedmetadata", handleLoadedMetadata);
      videoRef.current.removeEventListener("durationchange", handleDurationChange);
      videoRef.current.removeEventListener("play", handlePlayEvent);
      videoRef.current.removeEventListener("pause", handlePauseEvent);
      videoRef.current.removeEventListener("ended", handlePauseEvent);
    }
    videoRef.current = el;
    if (el) {
      el.addEventListener("timeupdate", handleTimeUpdate);
      el.addEventListener("loadedmetadata", handleLoadedMetadata);
      el.addEventListener("durationchange", handleDurationChange);
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
      const el = videoRef.current;
      const elDur = el.duration;
      setDuration(elDur);

      // For native MP4 with range support, seek to 1/3 of the real duration.
      // For piped streams this silently stays near 0 — acceptable since the
      // poster provides a visual preview and the user can press play.
      const dur = (dbDurationRef.current > 0) ? dbDurationRef.current : elDur;
      if (dur > 0) {
        const target = dur / 3;
        const seekable = el.seekable;
        const seekEnd = seekable.length > 0 ? seekable.end(seekable.length - 1) : 0;
        if (seekEnd >= target) {
          el.currentTime = target;
        }
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

  const formatTime = (s: number, tenths = false) => {
    if (tenths) {
      // Round to whole tenths first so float64 imprecision doesn't format one tenth low.
      const t10 = Math.round(s * 10);
      const m = Math.floor(t10 / 600);
      const whole = Math.floor((t10 % 600) / 10);
      const dec = t10 % 10;
      return `${m}:${String(whole).padStart(2, "0")}.${dec}`;
    }
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${Math.floor(sec).toString().padStart(2, "0")}`;
  };

  // Seek relative to the current playhead (clamped to [0, duration])
  const seekBy = useCallback((deltaSeconds: number) => {
    const v = videoRef.current;
    if (!v) return;
    const maxT = (dbDurationRef.current > 0 ? dbDurationRef.current : v.duration) || 0;
    let target = v.currentTime + deltaSeconds;
    if (maxT > 0) target = Math.min(maxT, target);
    v.currentTime = Math.max(0, target);
    setCurrentTime(v.currentTime);
  }, []);

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

  // Sync manualIds to localStorage
  useEffect(() => { saveManualIds(manualIds); }, [manualIds]);

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

  // Load stored crop / auto-detect letterboxing for queue items.
  useEffect(() => {
    if (!queueItems || queueItems.length === 0) return;
    for (const item of queueItems) {
      const vid = item.video_id;
      // Already have crop settings for this item in the current session.
      if (itemSettings[vid]?.crop) continue;

      // Always re-apply a previously-scanned crop from the backend. itemSettings
      // is in-memory and resets on navigation, so returning to the editor must
      // restore the stored crop from QualitySignature (the source of truth)
      // without a rescan — this runs regardless of autoDetectedRef.
      if (item.letterbox_detected && item.crop_w != null && item.crop_h != null) {
        updateItemSetting(vid, {
          crop: {
            video_id: vid,
            original_w: item.width ?? 0,
            original_h: item.height ?? 0,
            crop_w: item.crop_w,
            crop_h: item.crop_h,
            crop_x: item.crop_x ?? 0,
            crop_y: item.crop_y ?? 0,
            effective_ratio: `${item.crop_w}:${item.crop_h}`,
          },
        });
        continue;
      }

      // No stored crop. Skip the expensive ffmpeg detection if the backend has
      // already scanned this video (scanned with no letterbox found) or we've
      // already auto-detected it in this browser session.
      if (item.letterbox_scanned || autoDetectedRef.current.has(vid)) continue;
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
    setManualIds(prev => { const n = new Set(prev); n.delete(videoId); return n; });
    if (selectedId === videoId) setSelectedId(null);
  }, [selectedId]);

  const clearQueue = useCallback(() => {
    setQueueIds([]);
    setCheckedIds(new Set());
    setSelectedId(null);
    // Note: encodeJobs is intentionally NOT cleared here — running encode jobs
    // must keep being tracked even when the queue is cleared.
    setManualIds(new Set());
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

  // Prefer the DB-stored duration (reliable) over the video element's
  // reported duration (unreliable for remuxed/transcoded streams).
  const effectiveDuration = useMemo(() => {
    const dbDur = selectedItem?.duration_seconds;
    if (dbDur && dbDur > 0) return dbDur;
    return duration;
  }, [selectedItem?.duration_seconds, duration]);

  // Keep ref in sync so handleLoadedMetadata (stable callback) can use DB duration
  dbDurationRef.current = selectedItem?.duration_seconds ?? 0;



  const selectedSettings = selectedId ? getItemSettings(selectedId) : null;

  // ── Frame stepping (±1 frame using item fps, fallback 1/25s) ──
  const frameStep = useCallback((dir: 1 | -1) => {
    const v = videoRef.current;
    if (!v) return;
    if (!v.paused) v.pause();
    const fps = selectedItem?.fps && selectedItem.fps > 0 ? selectedItem.fps : 25;
    seekBy(dir / fps);
  }, [selectedItem?.fps, seekBy]);

  // ── Enable trim on an item, remembering audio passthrough so it can be
  //    restored when trim is disabled again (trim requires audio re-encode) ──
  const setTrimEnabled = useCallback((videoId: number, enabled: boolean, extra: { trimStart?: number; trimEnd?: number } = {}) => {
    const s = getItemSettings(videoId);
    if (enabled) {
      updateItemSetting(videoId, {
        trimEnabled: true,
        ...(s.trimEnabled ? {} : {
          prevAudioPassthrough: s.audioPassthrough,
          audioPassthrough: false,
        }),
        ...extra,
      });
    } else {
      updateItemSetting(videoId, {
        trimEnabled: false,
        audioPassthrough: s.prevAudioPassthrough ?? s.audioPassthrough,
        prevAudioPassthrough: undefined,
        ...extra,
      });
    }
  }, [getItemSettings, updateItemSetting]);

  // ── Set trim points from the current playhead ────────────
  const setTrimInFromPlayhead = useCallback(() => {
    if (!selectedId) return;
    const s = getItemSettings(selectedId);
    let t = Math.max(0, Math.round((videoRef.current?.currentTime ?? currentTime) * 10) / 10);
    // Keep at least 0.1s of output (respect the existing end trim)
    if (effectiveDuration > 0) t = Math.min(t, Math.max(0, effectiveDuration - s.trimEnd - 0.1));
    setTrimEnabled(selectedId, true, { trimStart: Math.round(t * 10) / 10 });
  }, [selectedId, currentTime, effectiveDuration, getItemSettings, setTrimEnabled]);

  const setTrimOutFromPlayhead = useCallback(() => {
    if (!selectedId) return;
    const dur = effectiveDuration;
    if (!dur || dur <= 0) return;
    const s = getItemSettings(selectedId);
    const t = videoRef.current?.currentTime ?? currentTime;
    // trimEnd is seconds removed from the END, not an end timestamp.
    // Keep at least 0.1s of output (respect the existing start trim).
    let trimEnd = Math.max(0, Math.round((dur - t) * 10) / 10);
    trimEnd = Math.min(trimEnd, Math.max(0, dur - s.trimStart - 0.1));
    setTrimEnabled(selectedId, true, { trimEnd: Math.round(trimEnd * 10) / 10 });
  }, [selectedId, currentTime, effectiveDuration, getItemSettings, setTrimEnabled]);

  // ── Keyboard shortcuts (only while an item is selected) ──
  useEffect(() => {
    if (!selectedId) return;
    const onKeyDown = (e: KeyboardEvent) => {
      // Never hijack typing in form fields (trim/crop number inputs etc.)
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      // Don't drive the video while any modal is open (they have no focus trap).
      if (encodeConfirmIds !== null || restoreConfirm !== null || showScanDialog) return;
      // Let Space/Enter activate a focused button/link instead of hijacking play/pause.
      if (t && t.closest('button, a, [role="button"]')) return;
      switch (e.key) {
        case " ":
          e.preventDefault();
          togglePlay();
          break;
        case "ArrowLeft":
          e.preventDefault();
          if (e.shiftKey) frameStep(-1); else seekBy(-1);
          break;
        case "ArrowRight":
          e.preventDefault();
          if (e.shiftKey) frameStep(1); else seekBy(1);
          break;
        case "i":
        case "I":
          setTrimInFromPlayhead();
          break;
        case "o":
        case "O":
          setTrimOutFromPlayhead();
          break;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedId, togglePlay, frameStep, seekBy, setTrimInFromPlayhead, setTrimOutFromPlayhead, encodeConfirmIds, restoreConfirm, showScanDialog]);

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
  const handleScanLibrary = useCallback(async (opts: {
    includeExcluded?: boolean;
    skipCropped?: boolean;
    skipTrimmed?: boolean;
  } = {}) => {
    setIsScanning(true);
    try {
      const result = await scanLetterbox.mutateAsync({
        limit: 2000,
        includeExcluded: opts.includeExcluded,
        skipCropped: opts.skipCropped,
        skipTrimmed: opts.skipTrimmed,
      });
      if (result.status === "scanning" && result.job_id) {
        setScanJobId(result.job_id);
        toast({ type: "info", title: "Letterbox scan started..." });
      } else if (result.results) {
        // Inline results — merge scan results into the existing queue
        // (addToQueue de-dupes; manually-added items are preserved)
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
        toast({ type: "success", title: `Found ${ids.length} letterboxed videos — added to queue` });
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
      // Merge scan results into the existing queue (addToQueue de-dupes;
      // manually-added items are preserved)
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
      toast({ type: "success", title: `Found ${ids.length} letterboxed videos — added to queue` });
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
        setLastEncodeSummary({ videoId: activeEncodeJob.videoId, title: videoTitle, summary });
      }
      removeFromQueue(activeEncodeJob.videoId);
      setEncodeJobs(prev => prev.filter(j => j.jobId !== activeEncodeJob.jobId));
    } else if (status === "failed") {
      toast({ type: "error", title: `Encode failed: ${encodeStatus.data.error ?? "Unknown error"}` });
      setEncodeJobs(prev => prev.filter(j => j.jobId !== activeEncodeJob.jobId));
    } else if (status === "cancelled") {
      toast({ type: "warning", title: `Encode cancelled: ${encodeStatus.data.error ?? "Cancelled by user"}` });
      setEncodeJobs(prev => prev.filter(j => j.jobId !== activeEncodeJob.jobId));
    }
  }, [encodeStatus.data, activeEncodeJob, queueItems, toast, removeFromQueue]);

  // Set of video IDs currently encoding
  const encodingVideoIds = useMemo(() => new Set(encodeJobs.map(j => j.videoId)), [encodeJobs]);

  // Sorted + filtered + paginated queue items
  const sortedQueueItems = useMemo(() => {
    if (!queueItems) return [];
    // Filter to only items still in the queue (handles optimistic removal)
    const queueIdSet = new Set(queueIds);
    let items = queueItems.filter(i => queueIdSet.has(i.video_id));

    // Apply tag filter
    if (tagFilter === "letterboxed") {
      items = items.filter(i => i.letterbox_detected);
    } else if (tagFilter === "manual") {
      items = items.filter(i => manualIds.has(i.video_id));
    }

    if (sortBy === "editor_order") return items; // preserve insertion order
    items.sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case "artist":
          cmp = (a.artist ?? "").localeCompare(b.artist ?? "", undefined, { sensitivity: "base" });
          break;
        case "album":
          cmp = (a.album ?? "").localeCompare(b.album ?? "", undefined, { sensitivity: "base" });
          break;
        case "title":
          cmp = (a.title ?? "").localeCompare(b.title ?? "", undefined, { sensitivity: "base" });
          break;
        case "created_at":
          cmp = (a.created_at ?? "").localeCompare(b.created_at ?? "");
          break;
      }
      return sortDir === "desc" ? -cmp : cmp;
    });
    return items;
  }, [queueItems, queueIds, sortBy, sortDir, tagFilter, manualIds]);

  const totalPages = pageSize === 0 ? 1 : Math.max(1, Math.ceil(sortedQueueItems.length / pageSize));
  const clampedPage = Math.min(currentPage, totalPages);
  const paginatedItems = pageSize === 0
    ? sortedQueueItems
    : sortedQueueItems.slice((clampedPage - 1) * pageSize, clampedPage * pageSize);

  // Reset to page 1 when sort or pageSize changes
  useEffect(() => { setCurrentPage(1); }, [sortBy, sortDir, pageSize]);

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

  // ── Build an encode request from an item's settings ─────
  const buildEncodeRequest = useCallback((videoId: number): EncodeRequest => {
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
    }
    // Audio codec/bitrate apply whenever audio is re-encoded (not only for trim)
    if (!s.audioPassthrough) {
      req.audio_codec = s.audioCodec;
      req.audio_bitrate = s.audioBitrate !== "auto" ? s.audioBitrate : undefined;
    }
    return req;
  }, [getItemSettings]);

  // ── Start encode jobs (called after the confirmation modal) ──
  const startEncode = useCallback(async (videoIds: number[]) => {
    const items = videoIds.map(buildEncodeRequest);
    if (items.length === 0) return;
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
  }, [buildEncodeRequest, encodeSingle, encodeBatch, toast]);

  // ── Encode trigger paths — all open the confirmation modal ──
  const handleApplyChecked = useCallback(() => {
    if (checkedIds.size === 0) {
      toast({ type: "warning", title: "No videos checked" });
      return;
    }
    setEncodeConfirmIds([...checkedIds]);
  }, [checkedIds, toast]);

  const handleEncodeSingle = useCallback((videoId: number) => {
    setEncodeConfirmIds([videoId]);
  }, []);

  // ── Restore original from archive (confirm-gated) ───────
  const handleRestoreConfirm = useCallback(async () => {
    if (!restoreConfirm) return;
    const { videoId, title } = restoreConfirm;
    try {
      await restoreFromArchive.mutateAsync(videoId);
      toast({ type: "success", title: `Original restored: ${title}` });
      setRestoreConfirm(null);
      // Dismiss the post-encode summary banner if it refers to this video
      setLastEncodeSummary(prev => (prev && prev.videoId === videoId ? null : prev));
    } catch {
      toast({ type: "error", title: "Failed to restore original" });
    }
  }, [restoreConfirm, restoreFromArchive, toast]);

  // ── Cancel the active encode job ─────────────────────────
  const handleCancelEncode = useCallback(() => {
    if (!activeEncodeJob) return;
    cancelEncode.mutate(activeEncodeJob.jobId, {
      onSuccess: () => toast({ type: "info", title: "Cancelling encode..." }),
      onError: () => toast({ type: "error", title: "Failed to cancel encode" }),
    });
  }, [activeEncodeJob, cancelEncode, toast]);

  // ── Bulk-apply global defaults to every queued item ──────
  // (per-item settings override the globals once an item has its own entry,
  // so this is the way to push new defaults onto already-queued items)
  const applyGlobalsToAll = useCallback(() => {
    if (queueIds.length === 0) return;
    setItemSettings(prev => {
      const next = { ...prev };
      for (const id of queueIds) {
        const cur = prev[id] ?? getItemSettings(id);
        next[id] = {
          ...cur,
          crf: globalCrf,
          preset: globalPreset,
          // Trim requires audio re-encode — never force passthrough back on
          audioPassthrough: cur.trimEnabled ? false : globalAudioPassthrough,
        };
      }
      return next;
    });
    toast({ type: "success", title: `Defaults applied to ${queueIds.length} queued item${queueIds.length > 1 ? "s" : ""}` });
  }, [queueIds, getItemSettings, globalCrf, globalPreset, globalAudioPassthrough, toast]);

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
              onClick={() => setShowScanDialog(true)}
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

          <Tooltip content="Apply edits to checked videos (asks for confirmation)">
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
            <Tooltip content="Remove checked items from the editor queue (files are not deleted)">
              <button className="btn-secondary btn-sm text-orange-400 whitespace-nowrap" onClick={clearCheckedFromQueue}>
                <X size={14} /> Remove Checked
              </button>
            </Tooltip>
          )}

          {queueIds.length > 0 && (
            <Tooltip content="Clear the editor queue (files are not deleted)">
              <button className="btn-secondary btn-sm text-red-400 whitespace-nowrap" onClick={clearQueue}>
                <ListX size={14} /> Clear Queue
              </button>
            </Tooltip>
          )}
        </div>

        {/* Global Settings Collapsible */}
        {showSettings && (
          <div className="px-3 py-3 border-b border-surface-border bg-surface/50 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <h4 className="text-xs font-medium text-text-muted uppercase tracking-wider">Default Encode Settings</h4>
              <Tooltip content="Write these CRF/preset/audio defaults into every queued item's settings">
                <button
                  className="btn-secondary btn-sm whitespace-nowrap"
                  onClick={applyGlobalsToAll}
                  disabled={queueIds.length === 0}
                >
                  Apply to all queued
                </button>
              </Tooltip>
            </div>
            <p className="text-[10px] text-text-muted">Per-item settings override these defaults.</p>
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

        {/* Sort & Page Size Controls */}
        {queueIds.length > 0 && (
          <div className="flex items-center gap-2 px-3 py-1.5 border-b border-surface-border bg-surface/50 text-[11px]">
            <ArrowUpDown size={12} className="text-text-muted flex-shrink-0" />
            <select
              value={sortBy}
              onChange={e => setSortBy(e.target.value as SortField)}
              className="input-sm text-[11px] py-0.5 px-1 bg-surface"
            >
              <option value="editor_order">Queue Order</option>
              <option value="artist">Artist</option>
              <option value="album">Album</option>
              <option value="title">Title</option>
              <option value="created_at">Date Added (Library)</option>
            </select>
            <button
              className="btn-ghost btn-xs text-text-muted hover:text-text-primary px-1"
              onClick={() => setSortDir(d => d === "asc" ? "desc" : "asc")}
              title={sortDir === "asc" ? "Ascending" : "Descending"}
            >
              {sortDir === "asc" ? "A→Z" : "Z→A"}
            </button>
            <select
              value={tagFilter}
              onChange={e => { setTagFilter(e.target.value as TagFilter); setCurrentPage(1); }}
              className="input-sm text-[11px] py-0.5 px-1 bg-surface"
            >
              <option value="all">All</option>
              <option value="letterboxed">Letterboxed</option>
              <option value="manual">Manual</option>
            </select>
            <div className="flex-1" />
            <span className="text-text-muted">Show</span>
            <select
              value={pageSize}
              onChange={e => setPageSize(Number(e.target.value))}
              className="input-sm text-[11px] py-0.5 px-1 bg-surface w-16"
            >
              <option value={10}>10</option>
              <option value={20}>20</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
              <option value={0}>All</option>
            </select>
          </div>
        )}

        {/* Queue List */}
        <div className="flex-1 overflow-y-auto">
          {queueLoading && !queueItems && queueIds.length > 0 && (
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
                  Encoding 1 of {encodeJobs.length}
                  {(() => {
                    const t = queueItems?.find(i => i.video_id === activeEncodeJob?.videoId)?.title;
                    return t ? `: ${t}` : "";
                  })()}
                </span>
                <Tooltip content="Cancel the active encode job">
                  <button
                    className="btn-ghost btn-xs text-text-muted hover:text-red-400"
                    onClick={handleCancelEncode}
                    disabled={cancelEncode.isPending}
                  >
                    {cancelEncode.isPending ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
                  </button>
                </Tooltip>
              </div>
              {encodeStatus.data?.current_step && (
                <div className="text-[10px] text-text-muted mt-1 truncate">{encodeStatus.data.current_step}</div>
              )}
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
              <button
                className="btn-ghost btn-xs text-orange-400 mt-1.5"
                onClick={() => setRestoreConfirm({ videoId: lastEncodeSummary.videoId, title: lastEncodeSummary.title })}
              >
                <RotateCcw size={12} /> Undo encode (restore original)
              </button>
            </div>
          )}

          {paginatedItems.map(item => (
            <QueueRow
              key={item.video_id}
              item={item}
              checked={checkedIds.has(item.video_id)}
              selected={selectedId === item.video_id}
              settings={getItemSettings(item.video_id)}
              isEncoding={encodingVideoIds.has(item.video_id)}
              isActiveEncode={activeEncodeJob?.videoId === item.video_id}
              encodeProgress={activeEncodeJob?.videoId === item.video_id ? (encodeStatus.data?.progress_percent ?? 0) : undefined}
              isManual={manualIds.has(item.video_id)}
              onToggleCheck={() => toggleCheck(item.video_id)}
              onSelect={() => setSelectedId(item.video_id)}
              onRemove={() => removeFromQueue(item.video_id)}
              onDetectLetterbox={() => handleDetectSingle(item.video_id)}
              onEncode={() => handleEncodeSingle(item.video_id)}
              onExclude={() => handleToggleExcludeFromScan(item.video_id, item.exclude_from_scan)}
              excludePending={excludeFromScan.isPending && excludeFromScan.variables?.videoId === item.video_id}
            />
          ))}

          {/* Pagination controls */}
          {pageSize > 0 && sortedQueueItems.length > pageSize && (
            <div className="flex items-center justify-center gap-2 py-2 border-t border-surface-border text-xs text-text-muted">
              <button
                className="btn-ghost btn-xs"
                disabled={clampedPage <= 1}
                onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
              >
                ‹ Prev
              </button>
              <span className="tabular-nums">
                {clampedPage} / {totalPages}
              </span>
              <button
                className="btn-ghost btn-xs"
                disabled={clampedPage >= totalPages}
                onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
              >
                Next ›
              </button>
              <span className="text-text-muted ml-1">({sortedQueueItems.length} total)</span>
            </div>
          )}
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
                <Tooltip content={showOverlay ? "Hide the crop/DAR edit overlay" : "Show the crop/DAR edit overlay"}>
                  <button
                    className={`btn-secondary btn-sm ${showOverlay ? "!bg-accent/10 !text-accent" : ""}`}
                    onClick={() => setShowOverlay(!showOverlay)}
                  >
                    {showOverlay ? <Eye size={14} /> : <EyeOff size={14} />}
                    Overlay {showOverlay ? "On" : "Off"}
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
              <Tooltip content="Play/Pause (Space)">
                <button onClick={togglePlay} className="btn-ghost btn-xs text-text-primary">
                  {isPlaying ? <Pause size={16} /> : <Play size={16} />}
                </button>
              </Tooltip>
              <Tooltip content="Back 1 frame (Shift+←)">
                <button onClick={() => frameStep(-1)} className="btn-ghost btn-xs text-text-muted hover:text-text-primary">
                  <StepBack size={14} />
                </button>
              </Tooltip>
              <Tooltip content="Forward 1 frame (Shift+→)">
                <button onClick={() => frameStep(1)} className="btn-ghost btn-xs text-text-muted hover:text-text-primary">
                  <StepForward size={14} />
                </button>
              </Tooltip>
              <span className="text-[11px] text-text-muted tabular-nums w-[52px] text-right">{formatTime(currentTime, true)}</span>
              <input
                type="range"
                min={0}
                max={effectiveDuration || 0}
                step={0.1}
                value={currentTime}
                onChange={handleSeek}
                className="flex-1 h-1 accent-accent cursor-pointer"
              />
              <span className="text-[11px] text-text-muted tabular-nums w-[38px]">{formatTime(effectiveDuration)}</span>
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
            <div className="px-4 pb-1 bg-surface text-[10px] text-text-muted text-center">
              Space play · ←/→ seek · Shift+←/→ frame · I/O trim in/out · Ctrl+scroll zoom · drag to pan
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
                    <Tooltip content="Deletes the edited file and restores the archived original">
                      <button
                        className="btn-secondary btn-sm whitespace-nowrap"
                        onClick={() => setRestoreConfirm({ videoId: selectedItem.video_id, title: `${selectedItem.artist} — ${selectedItem.title}` })}
                        disabled={restoreFromArchive.isPending}
                      >
                        {restoreFromArchive.isPending ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                        Restore Original
                      </button>
                    </Tooltip>
                  )}
                  <Tooltip content={selectedItem.exclude_from_scan ? "Re-include in future letterbox scans" : "Exclude from future letterbox scans (false positive)"}>
                    <button
                      className={`btn-secondary btn-sm ${selectedItem.exclude_from_scan ? "text-orange-400" : "text-text-muted"}`}
                      onClick={() => handleToggleExcludeFromScan(selectedItem.video_id, selectedItem.exclude_from_scan)}
                      disabled={excludeFromScan.isPending && excludeFromScan.variables?.videoId === selectedItem.video_id}
                    >
                      {(excludeFromScan.isPending && excludeFromScan.variables?.videoId === selectedItem.video_id) ? <Loader2 size={14} className="animate-spin" /> : <Ban size={14} />}
                      {selectedItem.exclude_from_scan ? "Excluded" : "Exclude"}
                    </button>
                  </Tooltip>
                  <Tooltip content="Encode this video (asks for confirmation)">
                    <button
                      className="btn-primary btn-sm"
                      onClick={() => handleEncodeSingle(selectedItem.video_id)}
                      disabled={encodeSingle.isPending || encodingVideoIds.has(selectedItem.video_id)}
                    >
                      {(encodeSingle.isPending || encodingVideoIds.has(selectedItem.video_id)) ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                      {encodingVideoIds.has(selectedItem.video_id) ? `Encoding ${activeEncodeJob?.videoId === selectedItem.video_id ? `${encodeStatus.data?.progress_percent ?? 0}%` : "..."}` : "Encode"}
                    </button>
                  </Tooltip>
                  <Tooltip content="Remove from queue (file is not deleted)">
                    <button
                      className="btn-secondary btn-sm text-red-400"
                      onClick={() => removeFromQueue(selectedItem.video_id)}
                    >
                      <X size={14} />
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
                      Stretch to ratio (DAR)
                      <select
                        value={selectedSettings?.ratio ?? "original"}
                        onChange={e => handleRatioChange(selectedItem.video_id, e.target.value)}
                        className="input-sm w-auto mt-1 block"
                      >
                        {RATIO_PRESETS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
                      </select>
                      <span className="text-[10px] text-text-muted block">Stretches the picture — no pixels are cropped</span>
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

                    <Tooltip content={selectedSettings?.trimEnabled ? "Trim requires audio re-encoding — passthrough is unavailable while trim is enabled" : "Copy the original audio stream without re-encoding"}>
                      <label className={`flex items-center gap-2 text-xs text-text-secondary pb-1 ${selectedSettings?.trimEnabled ? "opacity-50" : ""}`}>
                        <input
                          type="checkbox"
                          checked={selectedSettings?.audioPassthrough ?? globalAudioPassthrough}
                          disabled={selectedSettings?.trimEnabled}
                          onChange={e => updateItemSetting(selectedItem.video_id, { audioPassthrough: e.target.checked })}
                        />
                        Audio copy
                      </label>
                    </Tooltip>

                    {/* Audio re-encode options — shown whenever audio passthrough is off */}
                    {!(selectedSettings?.audioPassthrough ?? globalAudioPassthrough) && (
                      <>
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
                      </>
                    )}
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
                    {/* Always rendered (visibility toggled) so the button appearing
                        after the first non-zero crop value can't grow the header and
                        shift the stepper arrows out from under the user's cursor. */}
                    <button
                      className={`btn-secondary btn-xs text-red-400 ${
                        selectedSettings?.crop && (selectedSettings.crop.crop_w !== selectedSettings.crop.original_w || selectedSettings.crop.crop_h !== selectedSettings.crop.original_h)
                          ? "" : "invisible"
                      }`}
                      onClick={() => handleClearCrop(selectedItem.video_id)}
                    >
                      <X size={11} /> Clear
                    </button>
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
                        // Enabling trim forces audio re-encode; disabling restores
                        // the previous audio passthrough setting
                        onChange={e => setTrimEnabled(selectedItem.video_id, e.target.checked)}
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
                            {formatTime(selectedSettings?.trimStart ?? 0, true)}
                          </span>
                          <span className="text-text-muted/50">▼</span>
                          <span className={selectedSettings?.trimEnd ? "text-red-400" : ""}>
                            -{formatTime(selectedSettings?.trimEnd ?? 0, true)}
                          </span>
                        </div>
                      </div>

                      <div className="flex flex-wrap items-end gap-3">
                        {/* Trim start */}
                        <div className="flex items-end gap-1.5">
                          <Tooltip content="Cuts this many seconds off the BEGINNING of the video. The playhead jumps to the new start so you can see exactly what's removed.">
                          <label className="text-xs text-text-secondary">
                            Trim off start (s)
                            <NumericStepper
                              min={0} step={0.1}
                              max={selectedItem.duration_seconds ? selectedItem.duration_seconds - (selectedSettings?.trimEnd ?? 0) - 0.1 : undefined}
                              value={selectedSettings?.trimStart ?? 0}
                              onChange={v => {
                                updateItemSetting(selectedItem.video_id, { trimStart: v });
                                // Move the playhead to the new start point so the user
                                // sees the frame they're trimming to.
                                const el = videoRef.current;
                                if (el) { el.currentTime = v; setCurrentTime(v); }
                              }}
                              className="w-20"
                            />
                          </label>
                          </Tooltip>
                          <Tooltip content="Set trim start from the current playhead (I)">
                            <button
                              className="btn-ghost btn-xs text-text-muted hover:text-accent mb-0.5"
                              onClick={setTrimInFromPlayhead}
                            >
                              Set
                            </button>
                          </Tooltip>
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
                          <Tooltip content="Cuts this many seconds off the END of the video. The playhead jumps to the new end point so you can see exactly what's removed.">
                          <label className="text-xs text-text-secondary">
                            Trim off end (s)
                            <NumericStepper
                              min={0} step={0.1}
                              max={selectedItem.duration_seconds ? selectedItem.duration_seconds - (selectedSettings?.trimStart ?? 0) - 0.1 : undefined}
                              value={selectedSettings?.trimEnd ?? 0}
                              onChange={v => {
                                updateItemSetting(selectedItem.video_id, { trimEnd: v });
                                // trimEnd is seconds removed from the END; move the
                                // playhead to the new end point (duration − trimEnd).
                                const el = videoRef.current;
                                const dur = selectedItem.duration_seconds ?? el?.duration ?? 0;
                                if (el && dur) { const t = Math.max(0, dur - v); el.currentTime = t; setCurrentTime(t); }
                              }}
                              className="w-20"
                            />
                          </label>
                          </Tooltip>
                          <Tooltip content="Set trim end from the current playhead — seconds removed from the end (O)">
                            <button
                              className="btn-ghost btn-xs text-text-muted hover:text-accent mb-0.5"
                              onClick={setTrimOutFromPlayhead}
                            >
                              Set
                            </button>
                          </Tooltip>
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
                      </div>

                      <div className="text-[10px] text-text-muted/60 mt-2 flex items-center gap-1">
                        Trim requires audio re-encoding — codec/bitrate options are in Encode Settings
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

      {/* Scan options dialog */}
      {showScanDialog && (
        <ScanDialog
          onScan={(opts) => handleScanLibrary(opts)}
          onClose={() => setShowScanDialog(false)}
        />
      )}

      {/* Encode confirmation dialog — gate for all three encode trigger paths */}
      {encodeConfirmIds && (
        <EncodeConfirmDialog
          rows={encodeConfirmIds.map(id => {
            const item = queueItems?.find(i => i.video_id === id);
            const s = getItemSettings(id);
            const hasCrop = !!(s.crop && (s.crop.crop_w !== s.crop.original_w || s.crop.crop_h !== s.crop.original_h));
            const hasTrim = s.trimEnabled && (s.trimStart > 0 || s.trimEnd > 0);
            const trimParts: string[] = [];
            if (hasTrim && s.trimStart > 0) trimParts.push(`${s.trimStart}s from start`);
            if (hasTrim && s.trimEnd > 0) trimParts.push(`${s.trimEnd}s from end`);
            return {
              videoId: id,
              title: item ? `${item.artist} — ${item.title}` : `Video #${id}`,
              cropText: hasCrop && s.crop
                ? `${s.crop.crop_w}×${s.crop.crop_h} at +${s.crop.crop_x},+${s.crop.crop_y} (from ${s.crop.original_w}×${s.crop.original_h})`
                : null,
              darText: s.targetDar ? `Stretch to ${s.targetDar} (no pixels cropped)` : null,
              trimText: trimParts.length > 0 ? `Remove ${trimParts.join(", ")}` : null,
              crf: s.crf,
              preset: s.preset,
              audioText: s.audioPassthrough
                ? "Copy original (passthrough)"
                : `Re-encode ${s.audioCodec.toUpperCase()}${s.audioBitrate !== "auto" ? ` @ ${s.audioBitrate}` : " (auto bitrate)"}`,
              hasEdits: hasCrop || !!s.targetDar || hasTrim,
            };
          })}
          onCancel={() => setEncodeConfirmIds(null)}
          onConfirm={() => {
            // Re-validate the snapshot: an encode may have completed (dropping its id
            // from the queue) while the modal was open — never re-encode a finished output.
            const ids = (encodeConfirmIds ?? []).filter(id => queueIds.includes(id) && !encodingVideoIds.has(id));
            setEncodeConfirmIds(null);
            if (ids.length) startEncode(ids);
          }}
        />
      )}

      {/* Restore-original confirmation dialog */}
      {restoreConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setRestoreConfirm(null)}>
          <div className="bg-surface-light border border-surface-border rounded-lg shadow-xl w-96 max-w-[90vw] p-5" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-text-primary mb-1">Restore original?</h3>
            <p className="text-xs text-text-secondary mb-1 truncate">{restoreConfirm.title}</p>
            <p className="text-xs text-text-muted mb-4 leading-relaxed">
              Deletes the edited file and restores the archived original.
            </p>
            <div className="flex justify-end gap-2">
              <button className="btn-ghost btn-sm" onClick={() => setRestoreConfirm(null)}>Cancel</button>
              <button
                className="btn-primary btn-sm"
                onClick={handleRestoreConfirm}
                disabled={restoreFromArchive.isPending}
              >
                {restoreFromArchive.isPending && <Loader2 size={14} className="animate-spin" />}
                Restore Original
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Encode Confirmation Dialog ────────────────────────────
function EncodeConfirmDialog({ rows, onCancel, onConfirm }: {
  rows: {
    videoId: number;
    title: string;
    cropText: string | null;
    darText: string | null;
    trimText: string | null;
    crf: number;
    preset: string;
    audioText: string;
    hasEdits: boolean;
  }[];
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onCancel}>
      <div className="bg-surface-light border border-surface-border rounded-lg shadow-xl w-[480px] max-w-[92vw] p-5" onClick={e => e.stopPropagation()}>
        <h3 className="text-sm font-semibold text-text-primary mb-1">
          Encode {rows.length} video{rows.length !== 1 ? "s" : ""}?
        </h3>
        <p className="text-xs text-text-muted mb-3 leading-relaxed">
          This permanently replaces the library file{rows.length !== 1 ? "s" : ""}. The original{rows.length !== 1 ? "s" : ""} will be archived and can be restored later.
        </p>

        <div className="max-h-72 overflow-y-auto space-y-2 pr-1">
          {rows.map(r => (
            <div key={r.videoId} className="rounded border border-surface-border bg-surface/40 px-3 py-2">
              <div className="text-xs font-medium text-text-primary truncate">{r.title}</div>
              <ul className="text-[11px] text-text-secondary mt-1 space-y-0.5">
                {r.cropText && <li className="flex items-center gap-1.5"><Scissors size={10} className="text-accent flex-shrink-0" /> Crop: {r.cropText}</li>}
                {r.darText && <li className="flex items-center gap-1.5"><Film size={10} className="text-blue-400 flex-shrink-0" /> DAR: {r.darText}</li>}
                {r.trimText && <li className="flex items-center gap-1.5"><Timer size={10} className="text-red-400 flex-shrink-0" /> Trim: {r.trimText}</li>}
                <li className="text-text-muted">Video: CRF {r.crf} · {r.preset} preset · Audio: {r.audioText}</li>
                <li className="text-text-muted flex items-center gap-1.5"><Archive size={10} className="flex-shrink-0" /> Original will be archived</li>
              </ul>
              {!r.hasEdits && (
                <div className="flex items-center gap-1.5 text-[11px] text-amber-400 mt-1.5">
                  <AlertTriangle size={11} className="flex-shrink-0" />
                  Re-encode only — quality loss, no changes applied
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="flex justify-end gap-2 mt-4">
          <button className="btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
          <button className="btn-primary btn-sm" onClick={onConfirm}>
            <Play size={14} /> Encode{rows.length > 1 ? ` (${rows.length})` : ""}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Queue Row Component ──────────────────────────────────
function QueueRow({ item, checked, selected, settings, isEncoding, isActiveEncode, encodeProgress, isManual, onToggleCheck, onSelect, onRemove, onDetectLetterbox, onEncode, onExclude, excludePending }: {
  item: EditorQueueItem;
  checked: boolean;
  selected: boolean;
  settings: { ratio: string; crop?: CropPreviewResponse; targetDar?: string; trimEnabled?: boolean; trimStart?: number; trimEnd?: number };
  isEncoding: boolean;
  /** true when this row's encode job is the one actively running (others are queued) */
  isActiveEncode: boolean;
  encodeProgress?: number;
  isManual: boolean;
  onToggleCheck: () => void;
  onSelect: () => void;
  onRemove: () => void;
  onDetectLetterbox: () => void;
  onEncode: () => void;
  onExclude: () => void;
  excludePending: boolean;
}) {
  const hasCrop = settings.crop && (settings.crop.crop_w !== settings.crop.original_w || settings.crop.crop_h !== settings.crop.original_h);
  const hasTrim = !!settings.trimEnabled && ((settings.trimStart ?? 0) > 0 || (settings.trimEnd ?? 0) > 0);

  return (
    <div
      className={`flex flex-col border-b border-surface-border transition-colors cursor-pointer ${
        selected ? "bg-accent/10 border-l-2 border-l-accent" : "hover:bg-surface-lighter"
      }`}
      onClick={onSelect}
    >
      <div className="flex items-center gap-2 px-3 pt-2 pb-1">
        {/* Checkbox */}
        <button
          onClick={e => { e.stopPropagation(); onToggleCheck(); }}
          className="flex-shrink-0 text-text-muted hover:text-text-primary"
        >
          {checked ? <CheckSquare size={16} className="text-accent" /> : <Square size={16} />}
        </button>

        {/* Poster thumbnail */}
        <div className="w-10 h-10 rounded bg-surface-lighter flex-shrink-0 overflow-hidden">
          <img
            src={playbackApi.posterUrl(item.video_id)}
            alt=""
            className="w-full h-full object-cover"
            onError={e => { (e.target as HTMLImageElement).style.display = "none"; }}
          />
        </div>

        {/* Title */}
        <div className="flex-1 min-w-0">
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
          {isManual && (
            <span className="text-blue-400">· Manual</span>
          )}
          {hasCrop && (
            <span className="text-accent">· Crop set</span>
          )}
          {hasTrim && (
            <span className="text-red-400">· Trim</span>
          )}
          {settings.targetDar && (
            <span className="text-blue-400">· DAR {settings.targetDar}</span>
          )}
          {item.has_archive && (
            <span className="text-purple-400">· Original archived</span>
          )}
          {isEncoding && !isActiveEncode && (
            <span className="text-green-400">· Queued</span>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-0.5 flex-shrink-0">
          <Tooltip content="Detect letterboxing">
            <button
              className="btn-ghost btn-xs text-text-muted hover:text-text-primary"
              onClick={e => { e.stopPropagation(); onDetectLetterbox(); }}
            >
              <ScanLine size={13} />
            </button>
          </Tooltip>
          <Tooltip content="Encode this video (asks for confirmation)">
            <button
              className="btn-ghost btn-xs text-text-muted hover:text-accent"
              onClick={e => { e.stopPropagation(); onEncode(); }}
              disabled={isEncoding}
            >
              {isActiveEncode ? <Loader2 size={13} className="animate-spin text-green-400" /> : <Play size={13} />}
            </button>
          </Tooltip>
          <Tooltip content={item.exclude_from_scan ? "Re-include in future scans" : "Exclude from future scans"}>
            <button
              className={`btn-ghost btn-xs ${item.exclude_from_scan ? "text-orange-400" : "text-text-muted hover:text-orange-400"}`}
              onClick={e => { e.stopPropagation(); onExclude(); }}
              disabled={excludePending}
            >
              {excludePending ? <Loader2 size={13} className="animate-spin" /> : <Ban size={13} />}
            </button>
          </Tooltip>
          <Tooltip content="Remove from queue (file is not deleted)">
            <button
              className="btn-ghost btn-xs text-text-muted hover:text-red-400"
              onClick={e => { e.stopPropagation(); onRemove(); }}
            >
              <X size={13} />
            </button>
          </Tooltip>
        </div>
      </div>

      {/* Encode progress bar — only the actively running job has real progress */}
      {isActiveEncode && (
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

// ── Scan Dialog ──────────────────────────────────────────
function ScanDialog({ onScan, onClose }: {
  onScan: (opts: { includeExcluded?: boolean; skipCropped?: boolean; skipTrimmed?: boolean }) => void;
  onClose: () => void;
}) {
  const [includeExcluded, setIncludeExcluded] = useState(false);
  const [includeCropped, setIncludeCropped] = useState(false);
  const [includeTrimmed, setIncludeTrimmed] = useState(false);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div className="bg-surface-light border border-surface-border rounded-lg shadow-xl w-72 p-5" onClick={e => e.stopPropagation()}>
        <h3 className="text-sm font-semibold text-text-primary mb-1">Letterbox Scan</h3>
        <p className="text-xs text-text-muted mb-1 leading-relaxed">
          Scan for videos with black bars. Excluded, previously cropped, and trimmed videos are skipped by default.
        </p>
        <p className="text-xs text-text-muted mb-4 leading-relaxed">
          Scan results are added to the current queue — items already in the queue are kept.
        </p>

        <div className="space-y-1.5">
          <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer">
            <input
              type="checkbox"
              checked={includeExcluded}
              onChange={() => setIncludeExcluded(!includeExcluded)}
              className="accent-accent w-3.5 h-3.5"
            />
            Include excluded videos
          </label>
          <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer">
            <input
              type="checkbox"
              checked={includeCropped}
              onChange={() => setIncludeCropped(!includeCropped)}
              className="accent-accent w-3.5 h-3.5"
            />
            Include previously cropped
          </label>
          <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer">
            <input
              type="checkbox"
              checked={includeTrimmed}
              onChange={() => setIncludeTrimmed(!includeTrimmed)}
              className="accent-accent w-3.5 h-3.5"
            />
            Include previously trimmed
          </label>
        </div>

        <div className="flex justify-end gap-2 mt-4">
          <button className="btn-ghost btn-sm" onClick={onClose}>Cancel</button>
          <button
            className="btn-primary btn-sm"
            onClick={() => {
              onScan({
                includeExcluded,
                skipCropped: !includeCropped,
                skipTrimmed: !includeTrimmed,
              });
              onClose();
            }}
          >
            <ScanLine size={14} /> Scan
          </button>
        </div>
      </div>
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
  // Mark as manually added
  const manuals = loadManualIds();
  for (const id of newIds) manuals.add(id);
  saveManualIds(manuals);
}
