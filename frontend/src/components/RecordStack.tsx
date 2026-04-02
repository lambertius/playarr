import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import ReactDOM from "react-dom";
import { Play, MoreVertical, ListPlus, ListEnd, ListStart } from "lucide-react";
import { playbackApi } from "@/lib/api";
import { usePlaybackStore, type PlaybackTrack } from "@/stores/playbackStore";
import { PlaylistPicker } from "@/components/PlaylistPicker";

interface RecordStackProps {
  /** Video IDs whose posters form the stack */
  videoIds: number[];
  /** Label text displayed below the stack */
  label: string;
  /** Sub-label (e.g. "5 videos") */
  subLabel?: string;
  /** Called when the stack is clicked */
  onClick?: () => void;
  /** Optional cover image URL shown as the top (first) image in the stack */
  coverImageUrl?: string;
  /** Whether this stack is selected */
  selected?: boolean;
  /** Called when the checkbox is toggled */
  onSelect?: (selected: boolean) => void;
  /** Called when a context menu action is chosen. Return the action string and videoIds. */
  onContextAction?: (action: string, videoIds: number[]) => void;
}

/**
 * A stacked-record display.  The cover image (artist / album art) sits
 * on top.  On hover the cover dissolves away to reveal each track poster
 * sequentially (track 1 → track 2 → … → cover again).
 */
export function RecordStack({
  videoIds,
  label,
  subLabel,
  onClick,
  coverImageUrl,
  selected,
  onSelect,
  onContextAction,
}: RecordStackProps) {
  // Build an ordered image list: cover first (index 0), then each track poster.
  // "cover" slot uses coverImageUrl; track slots use posterUrl(videoId).
  type Slot = { key: string; src: string; isCover: boolean };
  const [coverFailed, setCoverFailed] = useState(false);

  // Context menu state
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const [playlistPickerOpen, setPlaylistPickerOpen] = useState(false);

  const playStore = usePlaybackStore;

  const toTracks = useCallback(
    (): PlaybackTrack[] =>
      videoIds.map((vid) => ({
        videoId: vid,
        artist: label,
        title: "",
        hasPoster: true,
      })),
    [videoIds, label],
  );

  const handleMenuAction = useCallback(
    (action: string) => {
      setMenuOpen(false);
      switch (action) {
        case "play_now": {
          const tracks = toTracks();
          if (tracks.length > 0) {
            const state = playStore.getState();
            state.play(tracks[0]);
            for (let i = 1; i < tracks.length; i++) state.addToQueue(tracks[i]);
          }
          return;
        }
        case "play_next": {
          const tracks = toTracks();
          tracks.forEach((t) => playStore.getState().playNext(t));
          return;
        }
        case "add_to_queue": {
          const tracks = toTracks();
          tracks.forEach((t) => playStore.getState().addToQueue(t));
          return;
        }
        case "add_to_playlist":
          setPlaylistPickerOpen(true);
          return;
      }
      // Delegate everything else to the parent
      onContextAction?.(action, videoIds);
    },
    [onContextAction, videoIds, toTracks, playStore],
  );

  const slots: Slot[] = useMemo(() => {
    const out: Slot[] = [];
    if (coverImageUrl && !coverFailed) {
      out.push({ key: "cover", src: coverImageUrl, isCover: true });
    }
    for (const vid of videoIds) {
      out.push({ key: `v-${vid}`, src: playbackApi.posterUrl(vid), isCover: false });
    }
    return out;
  }, [videoIds, coverImageUrl, coverFailed]);

  // Which slot index is currently on top (0 = cover when present)
  const [topIndex, setTopIndex] = useState(0);
  const [fading, setFading] = useState(false);
  const hoverRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cycleRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimers = useCallback(() => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    if (cycleRef.current) { clearTimeout(cycleRef.current); cycleRef.current = null; }
  }, []);

  // Reset to cover when mouse leaves
  const resetToCover = useCallback(() => {
    setTopIndex(0);
    setFading(false);
  }, []);

  const advanceRecord = useCallback(() => {
    if (!hoverRef.current || slots.length <= 1) return;
    setFading(true);
    cycleRef.current = setTimeout(() => {
      setTopIndex((prev) => (prev + 1) % slots.length);
      setFading(false);
      if (hoverRef.current) {
        cycleRef.current = setTimeout(() => advanceRecord(), 500);
      }
    }, 600);
  }, [slots.length]);

  const handleMouseEnter = useCallback(() => {
    if (slots.length <= 1) return;
    hoverRef.current = true;
    timerRef.current = setTimeout(() => advanceRecord(), 500);
  }, [advanceRecord, slots.length]);

  const handleMouseLeave = useCallback(() => {
    hoverRef.current = false;
    clearTimers();
    resetToCover();
  }, [clearTimers, resetToCover]);

  useEffect(() => () => clearTimers(), [clearTimers]);

  // Build visible stack layers (up to 4 behind the top)
  const stackDepth = Math.min(slots.length, 4);
  const layers = useMemo(() => {
    const result: { slot: Slot; rotation: number; offsetX: number; offsetY: number }[] = [];
    for (let i = 0; i < stackDepth; i++) {
      const idx = (topIndex + i) % slots.length;
      const seed = idx * 13 + i * 7;
      const rotation = i === 0 ? 0 : ((seed % 17) - 8) * 1.8;
      const offsetX = i === 0 ? 0 : ((seed % 11) - 5) * 1.35;
      const offsetY = i === 0 ? 0 : ((seed % 7) - 3) * 0.9;
      result.push({ slot: slots[idx], rotation, offsetX, offsetY });
    }
    return result.reverse(); // bottom → top render order
  }, [slots, topIndex, stackDepth]);

  const trackCount = videoIds.length;
  const isSingle = slots.length <= 1;

  return (
    <div className={`group relative flex flex-col items-center gap-2 text-center w-full ${
      selected ? "ring-2 ring-accent rounded-xl" : ""
    }`}>
      {/* Selection checkbox */}
      {onSelect && (
        <div
          className="absolute top-1 left-1 z-20"
          onClick={(e) => e.stopPropagation()}
        >
          <input
            type="checkbox"
            checked={selected ?? false}
            onChange={(e) => onSelect(e.target.checked)}
            className="h-4 w-4 rounded border-surface-border bg-surface-lighter text-accent focus:ring-accent cursor-pointer accent-[var(--color-accent)]"
          />
        </div>
      )}

      {/* Context menu trigger */}
      {onContextAction && (
        <div className="absolute top-1 right-1 z-20">
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (!menuOpen) {
                const rect = e.currentTarget.getBoundingClientRect();
                setMenuPos({ top: rect.bottom + 4, left: rect.right });
              }
              setMenuOpen(!menuOpen);
            }}
            className="flex h-6 w-6 items-center justify-center rounded bg-black/50 text-white opacity-0 group-hover:opacity-100 transition-opacity"
            aria-label="Stack actions"
          >
            <MoreVertical size={13} />
          </button>

          {menuOpen && menuPos && ReactDOM.createPortal(
            <div className="fixed inset-0 z-50">
              <div className="absolute inset-0" onClick={() => setMenuOpen(false)} />
              <div
                className="absolute z-10 w-48 rounded-lg border border-surface-border bg-surface-light/95 backdrop-blur-md py-1 shadow-xl"
                style={{ top: menuPos.top, left: menuPos.left - 192, maxHeight: `calc(100vh - ${menuPos.top + 8}px)`, overflowY: 'auto' }}
              >
                {[
                  { action: "play_now", label: "Play Now", icon: Play },
                  { action: "play_next", label: "Play Next", icon: ListStart },
                  { action: "add_to_queue", label: "Add to Queue", icon: ListEnd },
                  { action: "add_to_playlist", label: "Add to Playlist…", icon: ListPlus },
                ].map(({ action, label: lbl, icon: Icon }) => (
                  <button
                    key={action}
                    onClick={() => handleMenuAction(action)}
                    className="w-full px-3 py-1.5 text-left text-sm text-text-secondary hover:bg-surface-lighter hover:text-text-primary flex items-center gap-2"
                  >
                    <Icon size={13} />
                    {lbl}
                  </button>
                ))}
                <div className="my-1 border-t border-surface-border" />
                {["Edit Metadata", "Rescan", "Normalise", "Redownload", "Undo Rescan"].map(
                  (action) => (
                    <button
                      key={action}
                      onClick={() => handleMenuAction(action.toLowerCase().replace(/ /g, "_"))}
                      className="w-full px-3 py-1.5 text-left text-sm text-text-secondary hover:bg-surface-lighter hover:text-text-primary"
                    >
                      {action}
                    </button>
                  )
                )}
                <div className="my-1 border-t border-surface-border" />
                <button
                  onClick={() => handleMenuAction("delete")}
                  className="w-full px-3 py-1.5 text-left text-sm text-danger hover:bg-danger/10"
                >
                  Delete
                </button>
              </div>
            </div>,
            document.body,
          )}
        </div>
      )}

      <button
        onClick={onClick}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        className="flex flex-col items-center gap-2 text-center focus:outline-none w-full"
      >
        {/* Stack container */}
        <div className="relative w-full aspect-square">
          {layers.map((layer, i) => {
            const isTop = i === layers.length - 1;
            return (
              <div
                key={layer.slot.key}
                className="absolute inset-0 rounded-lg overflow-hidden shadow-md transition-all duration-300"
                style={{
                  transform: `rotate(${layer.rotation}deg) translate(${layer.offsetX}px, ${layer.offsetY}px)`,
                  zIndex: i,
                  opacity: isTop && fading ? 0 : 1,
                  transition: isTop
                    ? "opacity 600ms ease-in-out, transform 300ms ease"
                    : "transform 300ms ease",
                }}
              >
                <img
                  src={layer.slot.src}
                  alt=""
                  className="w-full h-full object-cover"
                  loading="lazy"
                  {...(layer.slot.isCover
                    ? { onError: () => setCoverFailed(true) }
                    : {})}
                />
                {!isTop && (
                  <div className="absolute inset-0 bg-black/20" />
                )}
              </div>
            );
          })}

          {/* Stack depth indicator */}
          {!isSingle && (
            <div className="absolute -top-1.5 -right-1.5 z-10 flex h-6 min-w-6 items-center justify-center rounded-full bg-accent text-white text-[10px] font-bold px-1.5 shadow-lg">
              {trackCount}
            </div>
          )}
        </div>

        {/* Label area */}
        <div className="w-full px-1">
          <p className="text-sm font-medium text-text-primary truncate group-hover:text-accent transition-colors">
            {label}
          </p>
          {subLabel && (
            <p className="text-xs text-text-muted">{subLabel}</p>
          )}
        </div>
      </button>

      {/* Playlist picker popup */}
      <PlaylistPicker
        open={playlistPickerOpen}
        videoIds={videoIds}
        onClose={() => setPlaylistPickerOpen(false)}
      />
    </div>
  );
}
