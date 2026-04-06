import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  Library, Users, CalendarDays, Tags, Disc3, Star, MonitorPlay, ListMusic, ListOrdered, Settings, Plus, CheckCircle2, Film, FlaskConical, Sparkles, ArrowUpCircle, X, Heart,
} from "lucide-react";
import { useStats, useReviewQueue, useUpdateCheck } from "@/hooks/queries";
import { useState } from "react";
import { AddVideoModal } from "@/components/AddVideoModal";
import { GlobalSearch } from "@/components/GlobalSearch";
import { PlayerBar } from "@/components/PlayerBar";
import { AudioManager } from "@/components/AudioManager";
import { Fireworks } from "@/components/Fireworks";
import { Tooltip } from "@/components/Tooltip";
import { usePlaybackStore } from "@/stores/playbackStore";
import { useFireworksStore } from "@/stores/fireworksStore";

const navItems = [
  { to: "/library", icon: Library, label: "Library" },
  { to: "/artists", icon: Users, label: "Artists" },
  { to: "/albums", icon: Disc3, label: "Albums" },
  { to: "/years", icon: CalendarDays, label: "Years" },
  { to: "/genres", icon: Tags, label: "Genres" },
  { to: "/ratings", icon: Star, label: "Ratings" },
  { to: "/quality", icon: MonitorPlay, label: "Quality" },
  { to: "/playlists", icon: ListMusic, label: "Playlists" },
  { to: "/new-videos", icon: Sparkles, label: "New Videos" },
  "separator",
  { to: "/queue", icon: ListOrdered, label: "Queue" },
  { to: "/review", icon: CheckCircle2, label: "Review" },
  { to: "/video-editor", icon: Film, label: "Video Editor" },
  { to: "/scraper-tester", icon: FlaskConical, label: "Scraper Tester" },
  { to: "/settings", icon: Settings, label: "Settings" },
] as const;

/** Inline SVG play-button logo matching favicon */
function PlayarrLogo({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="logo-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#1c2230" />
          <stop offset="100%" stopColor="#0f1117" />
        </linearGradient>
        <linearGradient id="logo-play" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#ff3b3b" />
          <stop offset="100%" stopColor="#e11d2e" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="url(#logo-bg)" />
      <rect width="62" height="62" x="1" y="1" rx="13" fill="none" stroke="#2b3245" strokeWidth="1" />
      <circle cx="32" cy="32" r="22" fill="url(#logo-play)" opacity="0.12" />
      <path d="M25 18 L47 32 L25 46 Z" fill="url(#logo-play)" />
      <path d="M25 18 L47 32 L25 32 Z" fill="white" opacity="0.1" />
    </svg>
  );
}

export function Layout() {
  const { data: stats } = useStats();
  const { data: reviewData } = useReviewQueue({ page_size: 1 });
  const [showImport, setShowImport] = useState(false);
  const reviewCount = reviewData?.total ?? 0;
  const queue = usePlaybackStore((s) => s.queue);
  const fullscreenMode = usePlaybackStore((s) => s.fullscreenMode);
  const navigate = useNavigate();

  const { data: updateInfo, isLoading: updateLoading } = useUpdateCheck();
  const [dismissedUpdate, setDismissedUpdate] = useState(false);
  const showUpdateBanner = !dismissedUpdate && !updateLoading && updateInfo?.update_available;

  const isFullscreen = fullscreenMode !== "off";

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ── Sidebar ── */}
      {!isFullscreen && (
      <aside className="flex w-56 flex-col border-r border-surface-border bg-surface-light">
        {/* Logo */}
        <div
          className="flex h-14 items-center gap-2.5 px-4 border-b border-surface-border cursor-pointer"
          onClick={() => queue.length > 0 && navigate("/now-playing")}
          title={queue.length > 0 ? "Now Playing" : ""}
        >
          <PlayarrLogo size={32} />
          <span className="text-lg font-extrabold tracking-tight bg-gradient-to-r from-accent to-orange bg-clip-text text-transparent">
            Playarr
          </span>
          {queue.length > 0 && (
            <span className="ml-auto badge-red text-[10px] px-1.5">{queue.length}</span>
          )}
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto py-3 px-2" aria-label="Main navigation">
          {navItems.map((item, idx) => {
            if (item === "separator") {
              return <div key={`sep-${idx}`} className="my-2 mx-3 border-t border-surface-border" />;
            }
            const { to, icon: Icon, label } = item;
            return (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-150 ${
                  isActive
                    ? "bg-accent/10 text-accent shadow-[inset_3px_0_0_var(--color-accent)]"
                    : "text-text-secondary hover:bg-surface-lighter hover:text-text-primary"
                }`
              }
            >
              <Icon size={18} />
              <span>{label}</span>
              {label === "Queue" && stats && stats.active_jobs > 0 && (
                <span className="ml-auto badge-red text-[10px] px-1.5">
                  {stats.active_jobs}
                </span>
              )}
              {label === "Review" && reviewCount > 0 && (
                <span className="ml-auto badge-orange text-[10px] px-1.5">
                  {reviewCount}
                </span>
              )}
            </NavLink>
            );
          })}
        </nav>

        {/* Footer stats */}
        <div className="border-t border-surface-border p-3 text-xs text-text-muted space-y-1">
          <div className="flex justify-between">
            <span>Videos</span>
            <span className="text-text-secondary">{stats?.total_videos ?? "—"}</span>
          </div>
          <div className="flex justify-between">
            <span>Genres</span>
            <span className="text-text-secondary">{stats?.total_genres ?? "—"}</span>
          </div>
          <a
            href="https://github.com/sponsors/lambertius"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 pt-2 text-text-muted/60 hover:text-pink-400 transition-colors"
          >
            <Heart className="w-3 h-3" />
            <span>Support the project</span>
          </a>
        </div>
      </aside>
      )}

      {/* ── Main area ── */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar — subtle gradient */}
        {!isFullscreen && (
        <header className="flex h-14 items-center gap-3 border-b border-surface-border px-4"
          style={{ background: "linear-gradient(90deg, var(--color-surface-light) 0%, var(--color-surface) 100%)" }}
        >
          <PlayerBar />
          <div className="flex-shrink-0"><GlobalSearch /></div>
          <Tooltip content="Import a video by YouTube or Vimeo URL">
            <button
              onClick={() => setShowImport(true)}
              className="btn-primary btn-sm flex-shrink-0"
            >
              <Plus size={16} />
              Add Video
            </button>
          </Tooltip>
        </header>
        )}

        {/* Update banner */}
        {showUpdateBanner && (
          <div className="flex items-center gap-2 bg-accent/15 border-b border-accent/30 px-4 py-2 text-sm text-accent">
            <ArrowUpCircle size={16} className="flex-shrink-0" />
            <span>
              <strong>Playarr {updateInfo.latest_version}</strong> is available (you have {updateInfo.current_version}).
            </span>
            {updateInfo.release_url && (
              <a
                href={updateInfo.release_url}
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:text-accent/80 font-medium"
              >
                View release
              </a>
            )}
            <button
              onClick={() => setDismissedUpdate(true)}
              className="ml-auto text-text-muted hover:text-text-primary"
              aria-label="Dismiss"
            >
              <X size={14} />
            </button>
          </div>
        )}

        {/* Page content */}
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>

      {/* Import modal */}
      <AddVideoModal open={showImport} onClose={() => setShowImport(false)} />

      {/* Global audio element */}
      <AudioManager />

      {/* Global fireworks overlay (Party Mode) */}
      <GlobalFireworks />

    </div>
  );
}

function GlobalFireworks() {
  const visible = useFireworksStore((s) => s.visible);
  const duration = useFireworksStore((s) => s.duration);
  const hide = useFireworksStore((s) => s.hide);

  if (!visible) return null;
  return <Fireworks duration={duration} onComplete={hide} />;
}
