import { useEffect, useMemo, useRef, useState } from "react";
import { Play, Sparkles, Tv, Maximize2, Loader2 } from "lucide-react";
import {
  loadExclusions, saveExclusions, savePartyPlaylist, saveEraSettings,
} from "@/hooks/usePartyMode";
import { playlistApi } from "@/lib/api";
import type { PartyModeParams, PlaylistSummary } from "@/types";

export interface PartyStartChoice {
  /** Chosen "party like it's…" year, or undefined for the whole library
   *  (no era bias) when the current year is selected. */
  partyYear?: number;
  /** "theater" = artwork grid + video; "video" = full-screen video only. */
  fullscreenMode: "theater" | "video";
  playlistId: number | null;
  filters: Partial<PartyModeParams>;
}

/**
 * First-access gate shown before a streaming surface (/tv, /cast) starts the
 * party.  Every access is unique — a refresh is a fresh prompt — so this is
 * rendered on every mount and never auto-skipped.
 *
 * Collects the era year and the theatre/fullscreen layout, and shows a
 * read-only summary of the server-side Party Mode exclusions (changed in the
 * Playarr Settings on the server).  Rendered unscaled (above any TV canvas
 * transform) so it stays readable on a television.
 */
export function PartyStartGate({
  onStart,
  surface,
}: {
  onStart: (choice: PartyStartChoice) => void;
  surface: "tv" | "cast";
}) {
  const currentYear = new Date().getFullYear();
  const [year, setYear] = useState(currentYear);
  const [layout, setLayout] = useState<"theater" | "video">("theater");
  const [starting, setStarting] = useState(false);
  const [playlists, setPlaylists] = useState<PlaylistSummary[]>([]);
  const [playlistId, setPlaylistId] = useState<number | null>(null);
  const [artist, setArtist] = useState("");
  const [album, setAlbum] = useState("");
  const [genre, setGenre] = useState("");
  const [excludedVersions, setExcludedVersions] = useState<string[]>([]);
  const [minSongRating, setMinSongRating] = useState<number | null>(null);
  const [minVideoRating, setMinVideoRating] = useState<number | null>(null);
  const [excludeAdult, setExcludeAdult] = useState(true);
  const [saveAsDefault, setSaveAsDefault] = useState(false);
  const startRef = useRef<HTMLButtonElement>(null);

  // Read once on mount — reflects the server-cached partyExclusions preference.
  const exclusions = useMemo(() => loadExclusions(), []);

  // Focus Start so a TV remote's OK/Enter launches with the defaults.
  useEffect(() => {
    startRef.current?.focus();
    void playlistApi.list().then(setPlaylists).catch(() => setPlaylists([]));
  }, []);

  const start = () => {
    if (starting) return;
    // Flip the button to a "starting" state immediately so the press is
    // acknowledged even before the surface swaps in its loading screen.
    setStarting(true);
    const exclusionsForStart = [
      ...exclusions.version_types,
      ...excludedVersions,
      ...(excludeAdult ? ["18+"] : []),
    ].filter((value, index, all) => all.indexOf(value) === index);
    const filters: Partial<PartyModeParams> = {
      party_year: year < currentYear ? year : undefined,
      artist: artist.trim() || undefined,
      album: album.trim() || undefined,
      genre: genre.trim() || undefined,
      exclude_version_types: exclusionsForStart.join(",") || undefined,
      min_song_rating: minSongRating ?? exclusions.min_song_rating ?? undefined,
      min_video_rating: minVideoRating ?? exclusions.min_video_rating ?? undefined,
    };
    if (saveAsDefault) {
      savePartyPlaylist({ playlistId });
      saveEraSettings({ enabled: year < currentYear, year });
      saveExclusions({
        ...exclusions,
        version_types: excludedVersions,
        min_song_rating: minSongRating,
        min_video_rating: minVideoRating,
        exclude_adult: excludeAdult,
      });
    }
    onStart({
      // Current year (or later) = whole library, no era bias.
      partyYear: year < currentYear ? year : undefined,
      fullscreenMode: layout,
      playlistId,
      filters,
    });
  };

  const exclusionLines: string[] = [];
  if (exclusions.version_types.length)
    exclusionLines.push(`Version types: ${exclusions.version_types.join(", ")}`);
  if (exclusions.artists.length)
    exclusionLines.push(`Artists: ${exclusions.artists.join(", ")}`);
  if (exclusions.genres.length)
    exclusionLines.push(`Genres: ${exclusions.genres.join(", ")}`);
  if (exclusions.albums.length)
    exclusionLines.push(`Albums: ${exclusions.albums.join(", ")}`);
  if (exclusions.min_song_rating != null)
    exclusionLines.push(`Min song rating: ${exclusions.min_song_rating}★`);
  if (exclusions.min_video_rating != null)
    exclusionLines.push(`Min video rating: ${exclusions.min_video_rating}★`);
  if (exclusions.exclude_adult) exclusionLines.push("Adult content excluded");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-auto bg-black p-6 text-white">
      <div className="w-full max-w-lg space-y-8">
        <div className="flex items-center gap-3">
          <Sparkles className="h-7 w-7 text-accent" />
          <h1 className="text-2xl font-bold tracking-tight">Start the Party</h1>
          <span className="ml-auto text-xs uppercase tracking-widest text-white/40">
            {surface === "tv" ? "TV mode" : "Cast mode"}
          </span>
        </div>

        <div className="grid gap-4 rounded-lg border border-white/10 bg-white/5 p-4 sm:grid-cols-2">
          <label className="text-sm font-semibold text-white/80 sm:col-span-2">
            Playlist
            <select value={playlistId ?? ""} onChange={e => setPlaylistId(e.target.value ? Number(e.target.value) : null)} className="input-field mt-2 min-h-12 w-full text-base">
              <option value="">All eligible library videos</option>
              {playlists.map(playlist => <option key={playlist.id} value={playlist.id}>{playlist.name} ({playlist.entry_count})</option>)}
            </select>
          </label>
          <label className="text-sm text-white/80">Artist<input value={artist} onChange={e => setArtist(e.target.value)} className="input-field mt-1 min-h-12 w-full" placeholder="Any artist" /></label>
          <label className="text-sm text-white/80">Album<input value={album} onChange={e => setAlbum(e.target.value)} className="input-field mt-1 min-h-12 w-full" placeholder="Any album" /></label>
          <label className="text-sm text-white/80">Genre<input value={genre} onChange={e => setGenre(e.target.value)} className="input-field mt-1 min-h-12 w-full" placeholder="Any genre" /></label>
          <label className="text-sm text-white/80">Minimum song rating
            <select value={minSongRating ?? ""} onChange={e => setMinSongRating(e.target.value ? Number(e.target.value) : null)} className="input-field mt-1 min-h-12 w-full">
              <option value="">No minimum</option>{[1,2,3,4,5].map(value => <option key={value} value={value}>{value}★</option>)}
            </select>
          </label>
          <label className="text-sm text-white/80">Minimum video rating
            <select value={minVideoRating ?? ""} onChange={e => setMinVideoRating(e.target.value ? Number(e.target.value) : null)} className="input-field mt-1 min-h-12 w-full">
              <option value="">No minimum</option>{[1,2,3,4,5].map(value => <option key={value} value={value}>{value}★</option>)}
            </select>
          </label>
          <fieldset className="sm:col-span-2">
            <legend className="mb-2 text-sm font-semibold text-white/80">Exclude versions</legend>
            <div className="flex flex-wrap gap-2">
              {["live", "cover", "alternate", "uncensored"].map(version => (
                <label key={version} className="flex min-h-12 items-center gap-2 rounded border border-white/15 px-3 text-sm capitalize focus-within:ring-2 focus-within:ring-accent">
                  <input type="checkbox" checked={excludedVersions.includes(version)} onChange={e => setExcludedVersions(values => e.target.checked ? [...values, version] : values.filter(value => value !== version))} /> {version}
                </label>
              ))}
              <label className="flex min-h-12 items-center gap-2 rounded border border-white/15 px-3 text-sm focus-within:ring-2 focus-within:ring-accent">
                <input type="checkbox" checked={excludeAdult} onChange={e => setExcludeAdult(e.target.checked)} /> Exclude 18+
              </label>
            </div>
          </fieldset>
          <label className="flex min-h-12 items-center gap-3 text-sm text-white/80 sm:col-span-2">
            <input type="checkbox" checked={saveAsDefault} onChange={e => setSaveAsDefault(e.target.checked)} /> Save these choices as the default
          </label>
        </div>

        {/* Party like it's… */}
        <div className="space-y-3">
          <label className="block text-sm font-semibold text-white/80">
            Party like it's…
          </label>
          <div className="flex items-center gap-3">
            <button
              className="btn-secondary h-12 w-12 text-xl"
              onClick={() => setYear((y) => Math.max(1900, y - 1))}
              aria-label="Earlier year"
            >
              −
            </button>
            <input
              type="number"
              className="input-field flex-1 text-center text-2xl font-bold tabular-nums"
              min={1900}
              max={currentYear}
              value={year}
              onChange={(e) => {
                const n = parseInt(e.target.value, 10);
                if (!Number.isNaN(n)) setYear(Math.min(currentYear, Math.max(1900, n)));
              }}
            />
            <button
              className="btn-secondary h-12 w-12 text-xl"
              onClick={() => setYear((y) => Math.min(currentYear, y + 1))}
              aria-label="Later year"
            >
              +
            </button>
          </div>
          <p className="text-xs text-white/50">
            {year >= currentYear
              ? "Whole library — every era, weighted by what you've played least."
              : `Caps at ${year} and favours videos from around then.`}
          </p>
        </div>

        {/* Theatre / Fullscreen */}
        <div className="space-y-3">
          <span className="block text-sm font-semibold text-white/80">Layout</span>
          <div className="grid grid-cols-2 gap-3">
            <button
              onClick={() => setLayout("theater")}
              className={`flex items-center justify-center gap-2 rounded-lg border px-4 py-3 text-sm font-medium transition-colors ${
                layout === "theater"
                  ? "border-accent bg-accent/15 text-white"
                  : "border-white/15 text-white/60 hover:border-white/30"
              }`}
            >
              <Tv className="h-4 w-4" /> Theatre
            </button>
            <button
              onClick={() => setLayout("video")}
              className={`flex items-center justify-center gap-2 rounded-lg border px-4 py-3 text-sm font-medium transition-colors ${
                layout === "video"
                  ? "border-accent bg-accent/15 text-white"
                  : "border-white/15 text-white/60 hover:border-white/30"
              }`}
            >
              <Maximize2 className="h-4 w-4" /> Fullscreen
            </button>
          </div>
          <p className="text-xs text-white/50">
            {layout === "theater"
              ? "Artwork wall with the video centred."
              : "Video only, filling the screen."}
          </p>
        </div>

        {/* Exclusions summary (read-only) */}
        <div className="space-y-2 rounded-lg border border-white/10 bg-white/5 p-4">
          <span className="block text-xs font-semibold uppercase tracking-wider text-white/50">
            Active exclusions
          </span>
          {exclusionLines.length ? (
            <ul className="space-y-1 text-sm text-white/70">
              {exclusionLines.map((line) => (
                <li key={line} className="truncate">
                  • {line}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-white/50">No exclusions set.</p>
          )}
          <p className="pt-1 text-xs text-white/40">
            To change these, edit Settings → Party Mode in Playarr on the server.
          </p>
        </div>

        <button
          ref={startRef}
          onClick={start}
          disabled={starting}
          className="btn-primary flex w-full items-center justify-center gap-2 py-3 text-base disabled:opacity-100"
        >
          {starting ? (
            <>
              <Loader2 className="h-5 w-5 animate-spin" /> Starting the party…
            </>
          ) : (
            <>
              <Play className="h-5 w-5" /> Start the Party
            </>
          )}
        </button>
      </div>
    </div>
  );
}
