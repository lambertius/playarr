import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { libraryApi, jobsApi, settingsApi, statsApi, resolveApi, reviewApi, searchApi, exportApi, aiApi, libraryImportApi, playlistApi, videoEditorApi, scraperTestApi, newVideosApi, metadataManagerApi } from "@/lib/api";
import type {
  LibraryParams, JobsParams, VideoItemUpdate, FacetFilterParams,
  ImportRequest, NormalizeRequest, BatchRescanRequest, SettingUpdate,
  ReviewParams, PinRequest, ApplyRequest, BatchResolveRequest,
  ExportKodiRequest, SearchEntityType,
  OrphanCleanRequest,
  AIEnrichRequest, AIApplyFieldsRequest, AIUndoRequest,
  AITestConnectionRequest, SceneAnalysisRequest, AISettingsUpdate,
  AIPromptSettingsUpdate,
  LibraryImportScanRequest, LibraryImportStartRequest, RegexPreviewRequest,
  ExistingDetailsRequest,
  CropPreviewRequest, EncodeRequest,
  ScraperTestRequest,
} from "@/types";

// ─── Query Keys ───────────────────────────────────────────
export const qk = {
  library: (params: LibraryParams) => ["library", params] as const,
  video: (id: number) => ["video", id] as const,
  snapshots: (id: number) => ["snapshots", id] as const,
  artists: (params?: FacetFilterParams) => ["artists", params] as const,
  years: (params?: FacetFilterParams) => ["years", params] as const,
  genres: (params?: FacetFilterParams) => ["genres", params] as const,
  albums: (params?: FacetFilterParams) => ["albums", params] as const,
  songRatings: (params?: FacetFilterParams) => ["songRatings", params] as const,
  videoRatings: (params?: FacetFilterParams) => ["videoRatings", params] as const,
  qualityBuckets: (params?: FacetFilterParams) => ["qualityBuckets", params] as const,
  jobs: (params?: JobsParams) => ["jobs", params] as const,
  job: (id: number) => ["job", id] as const,
  jobLog: (id: number) => ["jobLog", id] as const,
  settings: ["settings"] as const,
  stats: ["stats"] as const,
  normHistory: (id?: number) => ["normHistory", id ?? "all"] as const,
  // Matching / review keys
  resolveResult: (videoId: number) => ["resolve", videoId] as const,
  normalization: (videoId: number) => ["normalization", videoId] as const,
  reviewQueue: (params?: ReviewParams) => ["reviewQueue", params] as const,
  manualSearch: (type: SearchEntityType, q: string, artist?: string) =>
    ["manualSearch", type, q, artist] as const,
  playlists: ["playlists"] as const,
  playlist: (id: number) => ["playlist", id] as const,
  editorQueue: (ids: number[]) => ["editorQueue", ids] as const,
  editorScanResults: (jobId: number) => ["editorScanResults", jobId] as const,
  editorEncodeStatus: (jobId: number) => ["editorEncodeStatus", jobId] as const,
  genreBlacklist: ["genreBlacklist"] as const,
  logFiles: ["logFiles"] as const,
  logContent: (file: string) => ["logContent", file] as const,
};

// ─── Library Queries ──────────────────────────────────────
export function useLibrary(params: LibraryParams) {
  return useQuery({
    queryKey: qk.library(params),
    queryFn: () => libraryApi.list(params),
    placeholderData: (prev) => prev,
  });
}

export function useVideo(id: number) {
  return useQuery({
    queryKey: qk.video(id),
    queryFn: () => libraryApi.get(id),
    enabled: id > 0,
    // Poll faster when a job is actively processing, slower when idle.
    refetchInterval: (query) => {
      const ps = query.state.data?.processing_state;
      if (!ps) return 15_000;
      // Check if any step is in a non-final state (pending tasks)
      const hasActive = Object.values(ps).some(
        (entry) => typeof entry === "object" && entry !== null && "status" in entry && entry.status === "pending"
      );
      return hasActive ? 5_000 : 30_000;
    },
  });
}

export function useSnapshots(videoId: number) {
  return useQuery({
    queryKey: qk.snapshots(videoId),
    queryFn: () => libraryApi.snapshots(videoId),
    enabled: videoId > 0,
  });
}

export function useVideoNav(videoId: number, sort?: { sort_by?: string; sort_dir?: string }) {
  return useQuery({
    queryKey: ["videoNav", videoId, sort?.sort_by, sort?.sort_dir] as const,
    queryFn: () => libraryApi.nav(videoId, sort),
    enabled: videoId > 0,
  });
}

export function useArtists(params?: FacetFilterParams) {
  return useQuery({ queryKey: qk.artists(params), queryFn: () => libraryApi.artists(params) });
}
export function useYears(params?: FacetFilterParams) {
  return useQuery({ queryKey: qk.years(params), queryFn: () => libraryApi.years(params) });
}
export function useGenres(params?: FacetFilterParams) {
  return useQuery({ queryKey: qk.genres(params), queryFn: () => libraryApi.genres(params) });
}
export function useAlbums(params?: FacetFilterParams) {
  return useQuery({ queryKey: qk.albums(params), queryFn: () => libraryApi.albums(params) });
}
export function useSongRatings(params?: FacetFilterParams) {
  return useQuery({ queryKey: qk.songRatings(params), queryFn: () => libraryApi.songRatings(params) });
}
export function useVideoRatings(params?: FacetFilterParams) {
  return useQuery({ queryKey: qk.videoRatings(params), queryFn: () => libraryApi.videoRatings(params) });
}
export function useQualityBuckets(params?: FacetFilterParams) {
  return useQuery({ queryKey: qk.qualityBuckets(params), queryFn: () => libraryApi.qualityBuckets(params) });
}

// ─── Facets ───────────────────────────────────────────────
export function useStats() {
  return useQuery({ queryKey: qk.stats, queryFn: statsApi.get, refetchInterval: 15_000 });
}

export function useUpdateCheck() {
  return useQuery({
    queryKey: ["update-check"],
    queryFn: statsApi.updateCheck,
    staleTime: 1000 * 60 * 60,     // recheck at most once per hour
    refetchOnWindowFocus: false,
    retry: false,
  });
}

// ─── Library Mutations ────────────────────────────────────
export function useUpdateVideo(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: VideoItemUpdate) => libraryApi.update(id, data),
    onSuccess: (updated) => {
      qc.setQueryData(qk.video(id), updated);
      // Only invalidate the library list (not facets like artists/albums/genres)
      // to avoid cascade refetches across all facet pages.
      qc.invalidateQueries({ queryKey: ["library"], exact: false, refetchType: "none" });
    },
  });
}

export function useDeleteVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => libraryApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["library"] }),
  });
}

export function useDeleteBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoIds: number[]) => libraryApi.batchDelete({ video_ids: videoIds }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["library"] }),
  });
}

export function useCreateSource(videoId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { provider: string; source_video_id: string; original_url: string; canonical_url: string; source_type?: string }) =>
      libraryApi.createSource(videoId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.video(videoId) });
    },
  });
}

export function useUpdateSource(videoId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sourceId, data }: { sourceId: number; data: { provider?: string; source_video_id?: string; original_url?: string; canonical_url?: string; source_type?: string } }) =>
      libraryApi.updateSource(videoId, sourceId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.video(videoId) });
    },
  });
}

export function useDeleteSource(videoId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: number) => libraryApi.deleteSource(videoId, sourceId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.video(videoId) });
    },
  });
}

// ── Canonical Track hooks ──

export function useCanonicalScan(videoId: number) {
  return useQuery({
    queryKey: [...qk.video(videoId), "canonical-scan"],
    queryFn: () => libraryApi.canonicalScan(videoId),
    enabled: false, // Manual trigger only
  });
}

export function useCanonicalLink(videoId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (trackId: number) => libraryApi.canonicalLink(videoId, trackId),
    onSuccess: (updated) => {
      qc.setQueryData(qk.video(videoId), updated);
      qc.invalidateQueries({ queryKey: ["library"], exact: false, refetchType: "none" });
    },
  });
}

export function useCanonicalUnlink(videoId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => libraryApi.canonicalUnlink(videoId),
    onSuccess: (updated) => {
      qc.setQueryData(qk.video(videoId), updated);
      qc.invalidateQueries({ queryKey: ["library"], exact: false, refetchType: "none" });
    },
  });
}

export function useCanonicalCreate(videoId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { title: string; artist_name?: string; album_name?: string; year?: number; is_cover?: boolean; original_artist?: string; original_title?: string; genres?: string[] }) =>
      libraryApi.canonicalCreate(videoId, data),
    onSuccess: (updated) => {
      qc.setQueryData(qk.video(videoId), updated);
      qc.invalidateQueries({ queryKey: ["library"], exact: false, refetchType: "none" });
    },
  });
}

export function useCanonicalEdit(videoId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { title?: string; artist_name?: string; album_name?: string; year?: number; is_cover?: boolean; original_artist?: string; original_title?: string; genres?: string[] }) =>
      libraryApi.canonicalEdit(videoId, data),
    onSuccess: (updated) => {
      qc.setQueryData(qk.video(videoId), updated);
      qc.invalidateQueries({ queryKey: ["library"], exact: false, refetchType: "none" });
    },
  });
}

export function useSetParentVideo(videoId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (parentVideoId: number | null) => libraryApi.setParentVideo(videoId, parentVideoId),
    onSuccess: (updated) => {
      qc.setQueryData(qk.video(videoId), updated);
      qc.invalidateQueries({ queryKey: ["library"], exact: false, refetchType: "none" });
    },
  });
}

export function useScanCanonicalIssues() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => libraryApi.scanCanonicalIssues(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review"] });
    },
  });
}

export function useUndoRescan(videoId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => libraryApi.undoRescan(videoId),
    onSuccess: (updated) => {
      qc.setQueryData(qk.video(videoId), updated);
      qc.invalidateQueries({ queryKey: ["library"], exact: false, refetchType: "none" });
    },
  });
}

export function useRenameToExpected() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoId: number) => libraryApi.rename(videoId),
    onSuccess: (updated, videoId) => {
      qc.setQueryData(qk.video(videoId), updated);
      qc.invalidateQueries({ queryKey: ["library"], exact: false, refetchType: "none" });
    },
  });
}

export function useScrapeMetadata() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, ...opts }: { videoId: number; aiAutoAnalyse?: boolean; aiOnly?: boolean; scrapeWikipedia?: boolean; wikipediaUrl?: string; scrapeMusicbrainz?: boolean; musicbrainzUrl?: string; scrapeTmvdb?: boolean; isCover?: boolean; isLive?: boolean; isAlternate?: boolean; isUncensored?: boolean; alternateVersionLabel?: string; findSourceVideo?: boolean; normalizeAudio?: boolean }) =>
      libraryApi.scrape(videoId, opts),
    onSuccess: (_data, { videoId }) => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      // Video data refresh is handled by the polling mechanism in ActionsPanel
      // — no more hacky setTimeout delay.
      qc.invalidateQueries({ queryKey: qk.video(videoId) });
    },
  });
}

// Keep old name for backwards compat
export const useScrapeWikipedia = useScrapeMetadata;

// ─── Orphan Cleanup ───────────────────────────────────────
export function useOrphans(enabled = false) {
  return useQuery({
    queryKey: ["orphans"] as const,
    queryFn: () => libraryApi.orphans(),
    enabled,
  });
}

export function useCleanOrphans() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: OrphanCleanRequest) => libraryApi.cleanOrphans(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["orphans"] });
      qc.invalidateQueries({ queryKey: ["library"] });
      qc.invalidateQueries({ queryKey: ["library-health"] });
    },
  });
}

// ─── Library Health ───────────────────────────────────────
export function useLibraryHealth(enabled = false) {
  return useQuery({
    queryKey: ["library-health"] as const,
    queryFn: () => libraryApi.health(),
    enabled,
  });
}

export function useCleanStale() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoIds: number[]) => libraryApi.cleanStale(videoIds),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["library-health"] });
      qc.invalidateQueries({ queryKey: ["library"] });
    },
  });
}

export function useCleanRedundant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (filePaths: string[]) => libraryApi.cleanRedundant(filePaths),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["library-health"] });
      qc.invalidateQueries({ queryKey: ["library"] });
    },
  });
}

export function useCleanStaleArchives() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (folders: string[]) => libraryApi.cleanStaleArchives(folders),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["library-health"] });
      qc.invalidateQueries({ queryKey: ["archive-items"] });
    },
  });
}

// ─── Log Viewer ───────────────────────────────────────────
export function useLogFiles() {
  return useQuery({
    queryKey: qk.logFiles,
    queryFn: () => jobsApi.logFiles(),
  });
}

export function useLogContent(file: string, tail?: number) {
  return useQuery({
    queryKey: qk.logContent(file),
    queryFn: () => jobsApi.readLog({ file, tail }),
    enabled: !!file,
  });
}

// ─── Job Mutations ────────────────────────────────────────
export function useImportVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ImportRequest) => jobsApi.import(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useRescan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, fromDisk }: { videoId: number; fromDisk?: boolean }) =>
      jobsApi.rescan(videoId, fromDisk),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useRedownload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, formatSpec }: { videoId: number; formatSpec?: string }) =>
      jobsApi.redownload(videoId, formatSpec),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useRescanBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: BatchRescanRequest) => jobsApi.rescanBatch(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useNormalize() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: NormalizeRequest) => jobsApi.normalize(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useLibraryScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (importNew?: boolean) => jobsApi.libraryScan(importNew),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useLibraryDuplicateScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rescanAll: boolean = false) => jobsApi.libraryDuplicateScan(rescanAll),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useLibraryExport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mode: string) => jobsApi.libraryExport(mode),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useRetryJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => jobsApi.retry(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => jobsApi.cancel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useClearHistory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params?: { status?: string; job_type?: string }) => jobsApi.clearHistory(params),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useBatchDeleteJobs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: number[]) => jobsApi.batchDelete(ids),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

// ─── Jobs Queries ─────────────────────────────────────────
export function useJobs(params?: JobsParams) {
  return useQuery({
    queryKey: qk.jobs(params),
    queryFn: () => jobsApi.list(params),
    // Poll fast when active jobs exist, slow down when all idle
    refetchInterval: (query) => {
      const jobs = query.state.data;
      if (!jobs || jobs.length === 0) return 10_000;
      const hasActive = jobs.some(
        (j) => j.status === "queued" || j.status === "downloading" || j.status === "downloaded"
            || j.status === "remuxing" || j.status === "analyzing" || j.status === "normalizing"
            || j.status === "tagging" || j.status === "writing_nfo" || j.status === "asset_fetch"
      );
      return hasActive ? 3_000 : 15_000;
    },
  });
}

export function useJobLog(id: number | null, isFinished = false) {
  return useQuery({
    queryKey: qk.jobLog(id!),
    queryFn: () => jobsApi.log(id!),
    enabled: id != null,
    refetchInterval: id != null && !isFinished ? 3_000 : false,
  });
}

// ─── Settings ─────────────────────────────────────────────
export function useSettings() {
  return useQuery({ queryKey: qk.settings, queryFn: settingsApi.list });
}

export function useUpdateSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: SettingUpdate) => settingsApi.update(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.settings }),
  });
}

export function useGenreBlacklist() {
  return useQuery({ queryKey: qk.genreBlacklist, queryFn: settingsApi.genreBlacklist });
}

export function useUpdateGenreBlacklist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { genre_ids: number[]; blacklisted: boolean }) =>
      settingsApi.updateGenreBlacklist(data.genre_ids, data.blacklisted),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.genreBlacklist });
      qc.invalidateQueries({ queryKey: ["genres"] });
    },
  });
}

export function useCreateGenre() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => settingsApi.createGenre(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.genreBlacklist });
      qc.invalidateQueries({ queryKey: ["genres"] });
    },
  });
}

export function useNormalizationHistory(videoId?: number) {
  return useQuery({
    queryKey: qk.normHistory(videoId),
    queryFn: () => settingsApi.normalizationHistory(videoId),
  });
}

// ─── Resolve / Matching Queries ──────────────────────────
export function useResolveResult(videoId: number) {
  return useQuery({
    queryKey: qk.resolveResult(videoId),
    queryFn: () => resolveApi.get(videoId),
    enabled: videoId > 0,
    retry: false,
  });
}

export function useNormalizationDetail(videoId: number) {
  return useQuery({
    queryKey: qk.normalization(videoId),
    queryFn: () => resolveApi.normalization(videoId),
    enabled: videoId > 0,
    retry: false,
  });
}

// ─── Review Queue ────────────────────────────────────────
export function useReviewQueue(params?: ReviewParams) {
  return useQuery({
    queryKey: qk.reviewQueue(params),
    queryFn: () => reviewApi.list(params),
    placeholderData: (prev) => prev,
  });
}

export function useApproveReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoId: number) => reviewApi.approve(videoId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reviewQueue"] }); },
  });
}

export function useDismissReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoId: number) => reviewApi.dismiss(videoId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reviewQueue"] }); },
  });
}

export function useSetReviewVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, versionType, approve }: { videoId: number; versionType: string; approve?: boolean }) =>
      reviewApi.setVersion(videoId, versionType, approve),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reviewQueue"] }); },
  });
}

export function useBatchApproveReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoIds: number[]) => reviewApi.batchApprove(videoIds),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reviewQueue"] }); },
  });
}

export function useBatchDismissReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoIds: number[]) => reviewApi.batchDismiss(videoIds),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reviewQueue"] }); },
  });
}

export function useScanRenames() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rescanAll: boolean = false) => reviewApi.scanRenames(rescanAll),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reviewQueue"] }); },
  });
}

export function useScanEnrichment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rescanAll: boolean = false) => reviewApi.scanEnrichment(rescanAll),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reviewQueue"] }); },
  });
}

export function useApplyRename() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoId: number) => reviewApi.applyRename(videoId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reviewQueue"] }); },
  });
}

export function useBatchApplyRename() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoIds: number[]) => reviewApi.batchApplyRename(videoIds),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reviewQueue"] }); },
  });
}

export function useBatchDeleteReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoIds: number[]) => reviewApi.batchDelete(videoIds),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reviewQueue"] });
      qc.invalidateQueries({ queryKey: ["library"] });
    },
  });
}

export function useBatchScrapeReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoIds, options }: { videoIds: number[]; options?: { scrape_wikipedia?: boolean; scrape_musicbrainz?: boolean; ai_auto?: boolean; ai_only?: boolean; normalize?: boolean } }) => reviewApi.batchScrape(videoIds, options),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reviewQueue"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

// ─── Manual Search ───────────────────────────────────────
export function useManualSearch(entityType: SearchEntityType, q: string, artist?: string) {
  return useQuery({
    queryKey: qk.manualSearch(entityType, q, artist),
    queryFn: () => searchApi.search(entityType, q, artist),
    enabled: q.length >= 2,
    staleTime: 60_000,
  });
}

// ─── Resolve Mutations ──────────────────────────────────
export function useTriggerResolve() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, force }: { videoId: number; force?: boolean }) =>
      resolveApi.trigger(videoId, force),
    onSuccess: (data) => {
      qc.setQueryData(qk.resolveResult(data.video_id), data);
      qc.invalidateQueries({ queryKey: ["reviewQueue"] });
    },
  });
}

export function usePinCandidate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, data }: { videoId: number; data: PinRequest }) =>
      resolveApi.pin(videoId, data),
    onSuccess: (_res, { videoId }) => {
      qc.invalidateQueries({ queryKey: qk.resolveResult(videoId) });
      qc.invalidateQueries({ queryKey: ["reviewQueue"] });
    },
  });
}

export function useApplyCandidate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, data }: { videoId: number; data: ApplyRequest }) =>
      resolveApi.apply(videoId, data),
    onSuccess: (_res, { videoId }) => {
      qc.invalidateQueries({ queryKey: qk.resolveResult(videoId) });
      qc.invalidateQueries({ queryKey: ["reviewQueue"] });
    },
  });
}

export function useUnpinMatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoId: number) => resolveApi.unpin(videoId),
    onSuccess: (_res, videoId) => {
      qc.invalidateQueries({ queryKey: qk.resolveResult(videoId) });
      qc.invalidateQueries({ queryKey: ["reviewQueue"] });
    },
  });
}

export function useUndoResolve() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoId: number) => resolveApi.undo(videoId),
    onSuccess: (_res, videoId) => {
      qc.invalidateQueries({ queryKey: qk.resolveResult(videoId) });
      qc.invalidateQueries({ queryKey: ["reviewQueue"] });
    },
  });
}

export function useBatchResolve() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: BatchResolveRequest) => resolveApi.batch(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reviewQueue"] });
      qc.invalidateQueries({ queryKey: ["resolve"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

// ─── Export ──────────────────────────────────────────────
export function useExportKodi() {
  return useMutation({
    mutationFn: (data?: ExportKodiRequest) => exportApi.kodi(data),
  });
}

// ─── AI Metadata Queries ─────────────────────────────────
export function useAIComparison(videoId: number) {
  return useQuery({
    queryKey: ["ai", "comparison", videoId] as const,
    queryFn: () => aiApi.comparison(videoId),
    enabled: videoId > 0,
    retry: false,
  });
}

export function useAIResults(videoId: number) {
  return useQuery({
    queryKey: ["ai", "results", videoId] as const,
    queryFn: () => aiApi.results(videoId),
    enabled: videoId > 0,
  });
}

export function useAIScenes(videoId: number) {
  return useQuery({
    queryKey: ["ai", "scenes", videoId] as const,
    queryFn: () => aiApi.getScenes(videoId),
    enabled: videoId > 0,
    retry: false,
  });
}

export function useAIThumbnails(videoId: number) {
  return useQuery({
    queryKey: ["ai", "thumbnails", videoId] as const,
    queryFn: () => aiApi.thumbnails(videoId),
    enabled: videoId > 0,
  });
}

export function useAISettings() {
  return useQuery({
    queryKey: ["ai", "settings"] as const,
    queryFn: aiApi.settings,
  });
}

export function useModelCatalog(provider: string) {
  return useQuery({
    queryKey: ["ai", "models", provider] as const,
    queryFn: () => aiApi.models(provider),
    enabled: !!provider && provider !== "none",
    staleTime: 300_000, // 5 min cache in frontend
  });
}

export function useRoutingPreview() {
  return useQuery({
    queryKey: ["ai", "routing-preview"] as const,
    queryFn: aiApi.routingPreview,
    staleTime: 10_000,
  });
}

// ─── AI Mutations ────────────────────────────────────────
export function useAIEnrich() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, data }: { videoId: number; data?: AIEnrichRequest }) =>
      aiApi.enrich(videoId, data),
    onSuccess: (_res, { videoId }) => {
      qc.invalidateQueries({ queryKey: ["ai", "comparison", videoId] });
      qc.invalidateQueries({ queryKey: ["ai", "results", videoId] });
      qc.invalidateQueries({ queryKey: qk.video(videoId) });
    },
  });
}

export function useAIApplyFields() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ videoId, data }: { videoId: number; data: AIApplyFieldsRequest }) => {
      const res = await aiApi.applyFields(videoId, data);
      // Await critical refetches so the UI updates before the mutation resolves
      await Promise.all([
        qc.refetchQueries({ queryKey: ["ai", "comparison", videoId] }),
        qc.refetchQueries({ queryKey: ["ai", "results", videoId] }),
        qc.refetchQueries({ queryKey: qk.video(videoId) }),
      ]);
      // Mark library list as stale without immediately refetching all facets
      qc.invalidateQueries({ queryKey: ["library"], exact: false, refetchType: "none" });
      return res;
    },
  });
}

export function useAIRunScenes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, data }: { videoId: number; data?: SceneAnalysisRequest }) =>
      aiApi.scenes(videoId, data),
    onSuccess: (_res, { videoId }) => {
      qc.invalidateQueries({ queryKey: ["ai", "scenes", videoId] });
      qc.invalidateQueries({ queryKey: ["ai", "thumbnails", videoId] });
      qc.invalidateQueries({ queryKey: qk.video(videoId) });
    },
  });
}

export function useAISelectThumbnail() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, thumbnailId, applyToPoster }: { videoId: number; thumbnailId: number; applyToPoster?: boolean }) =>
      aiApi.selectThumbnail(videoId, thumbnailId, applyToPoster),
    onSuccess: (_res, { videoId }) => {
      qc.invalidateQueries({ queryKey: ["ai", "thumbnails", videoId] });
      qc.invalidateQueries({ queryKey: qk.video(videoId) });
    },
  });
}

// ─── Library Import ────────────────────────────────────────
export function useScanLibraryImport() {
  return useMutation({
    mutationFn: (data: LibraryImportScanRequest) => libraryImportApi.scan(data),
  });
}

export function usePreviewRegex() {
  return useMutation({
    mutationFn: (data: RegexPreviewRequest) => libraryImportApi.previewRegex(data),
  });
}

export function useStartLibraryImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: LibraryImportStartRequest) => libraryImportApi.start(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useExistingDetails() {
  return useMutation({
    mutationFn: (data: ExistingDetailsRequest) => libraryImportApi.existingDetails(data),
  });
}

export function useAIUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: AISettingsUpdate) => aiApi.updateSettings(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ai", "settings"] }),
  });
}

export function useAIPrompts() {
  return useQuery({
    queryKey: ["ai", "prompts"] as const,
    queryFn: aiApi.prompts,
  });
}

export function useAIUpdatePrompts() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: AIPromptSettingsUpdate) => aiApi.updatePrompts(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ai", "prompts"] }),
  });
}

export function useAITestConnection() {
  return useMutation({
    mutationFn: (data: AITestConnectionRequest) => aiApi.testConnection(data),
  });
}

export function useTestModelAvailability() {
  return useMutation({
    mutationFn: (force?: boolean) => aiApi.testModelAvailability(force ?? false),
  });
}

export function useAIUndo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ videoId, data }: { videoId: number; data: AIUndoRequest }) => {
      const res = await aiApi.undo(videoId, data);
      // Await critical refetches so the UI updates before the mutation resolves
      await Promise.all([
        qc.refetchQueries({ queryKey: ["ai", "comparison", videoId] }),
        qc.refetchQueries({ queryKey: ["ai", "results", videoId] }),
        qc.refetchQueries({ queryKey: qk.video(videoId) }),
      ]);
      qc.invalidateQueries({ queryKey: ["library"], exact: false, refetchType: "none" });
      return res;
    },
  });
}

export function useAIFingerprint() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoId: number) => aiApi.fingerprint(videoId),
    onSuccess: (_res, videoId) => {
      qc.invalidateQueries({ queryKey: ["ai", "comparison", videoId] });
      qc.invalidateQueries({ queryKey: qk.video(videoId) });
    },
  });
}

export function useAIDismissScrape() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoId: number) => aiApi.dismissScrape(videoId),
    onSuccess: (_res, videoId) => {
      qc.invalidateQueries({ queryKey: ["ai", "results", videoId] });
    },
  });
}
// ─── Playlist Queries ─────────────────────────────────────

export function usePlaylists() {
  return useQuery({
    queryKey: qk.playlists,
    queryFn: () => playlistApi.list(),
  });
}

export function usePlaylist(id: number) {
  return useQuery({
    queryKey: qk.playlist(id),
    queryFn: () => playlistApi.get(id),
    enabled: id > 0,
  });
}

export function useCreatePlaylist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, description }: { name: string; description?: string }) =>
      playlistApi.create(name, description),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.playlists }),
  });
}

export function useDeletePlaylist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => playlistApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.playlists }),
  });
}

export function useAddToPlaylist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ playlistId, videoId }: { playlistId: number; videoId: number }) =>
      playlistApi.addEntry(playlistId, videoId),
    onSuccess: (_res, { playlistId }) => {
      qc.invalidateQueries({ queryKey: qk.playlist(playlistId) });
      qc.invalidateQueries({ queryKey: qk.playlists });
    },
  });
}

export function useAddEntriesToPlaylist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ playlistId, videoIds }: { playlistId: number; videoIds: number[] }) =>
      playlistApi.addEntries(playlistId, videoIds),
    onSuccess: (_res, { playlistId }) => {
      qc.invalidateQueries({ queryKey: qk.playlist(playlistId) });
      qc.invalidateQueries({ queryKey: qk.playlists });
    },
  });
}

export function useRemoveFromPlaylist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ playlistId, entryId }: { playlistId: number; entryId: number }) =>
      playlistApi.removeEntry(playlistId, entryId),
    onSuccess: (_res, { playlistId }) => {
      qc.invalidateQueries({ queryKey: qk.playlist(playlistId) });
      qc.invalidateQueries({ queryKey: qk.playlists });
    },
  });
}

// ─── Video Editor Hooks ───────────────────────────────────
export function useEditorQueue(videoIds: number[]) {
  return useQuery({
    queryKey: qk.editorQueue(videoIds),
    queryFn: () => videoEditorApi.getQueueItems(videoIds),
    enabled: videoIds.length > 0,
  });
}

export function useDetectLetterbox() {
  return useMutation({
    mutationFn: (videoId: number) => videoEditorApi.detectLetterbox(videoId),
  });
}

export function useScanLetterbox() {
  return useMutation({
    mutationFn: ({ limit, includeExcluded }: { limit: number; includeExcluded?: boolean }) =>
      videoEditorApi.scanLetterbox(limit, includeExcluded),
  });
}

export function useEditorScanResults(jobId: number | null) {
  return useQuery({
    queryKey: qk.editorScanResults(jobId ?? 0),
    queryFn: () => videoEditorApi.getScanResults(jobId!),
    enabled: jobId != null && jobId > 0,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "complete" || status === "failed" ? false : 2000;
    },
  });
}

export function useEditorEncodeStatus(jobId: number | null) {
  return useQuery({
    queryKey: qk.editorEncodeStatus(jobId ?? 0),
    queryFn: () => videoEditorApi.getEncodeStatus(jobId!),
    enabled: jobId != null && jobId > 0,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "complete" || status === "failed" ? false : 2000;
    },
  });
}

export function useCropPreview() {
  return useMutation({
    mutationFn: (req: CropPreviewRequest) => videoEditorApi.cropPreview(req),
  });
}

export function useVideoEditorEncode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: EncodeRequest) => videoEditorApi.encode(req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useVideoEditorBatchEncode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (items: EncodeRequest[]) => videoEditorApi.batchEncode(items),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useRestoreFromArchive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (videoId: number) => videoEditorApi.restoreFromArchive(videoId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["editorQueue"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useSetExcludeFromScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoId, exclude }: { videoId: number; exclude: boolean }) =>
      videoEditorApi.setExcludeFromScan(videoId, exclude),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["editorQueue"] });
    },
  });
}

// ─── Archive Queries ──────────────────────────────────────
export function useArchiveItems() {
  return useQuery({
    queryKey: ["archiveItems"],
    queryFn: () => settingsApi.archiveItems(),
  });
}

export function useArchiveRestore() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (folder: string) => settingsApi.archiveRestore(folder),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["archiveItems"] });
      qc.invalidateQueries({ queryKey: ["library"] });
      qc.invalidateQueries({ queryKey: ["editorQueue"] });
    },
  });
}

export function useArchiveDelete() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (folders: string[]) => settingsApi.archiveDelete(folders),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["archiveItems"] });
    },
  });
}

export function useArchiveClear() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => settingsApi.archiveClear(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["archiveItems"] });
    },
  });
}

// ─── Scraper Test ─────────────────────────────────────────
export function useScraperTest() {
  return useMutation({
    mutationFn: (data: ScraperTestRequest) => scraperTestApi.run(data),
  });
}

// ─── New Videos ── Discovery / Recommendations ───────────
export function useNewVideosFeed() {
  return useQuery({
    queryKey: ["newVideosFeed"],
    queryFn: () => newVideosApi.feed(),
    staleTime: 60_000,
  });
}

export function useNewVideosCart() {
  return useQuery({
    queryKey: ["newVideosCart"],
    queryFn: () => newVideosApi.cart(),
    staleTime: 10_000,
  });
}

export function useNewVideosSettings() {
  return useQuery({
    queryKey: ["newVideosSettings"],
    queryFn: () => newVideosApi.settings(),
  });
}

export function useRefreshNewVideos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ categories, force }: { categories?: string[]; force?: boolean } = {}) =>
      newVideosApi.refresh(categories, force),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["newVideosFeed"] });
    },
  });
}

export function useNewVideosAddToCart() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (suggested_video_id: number) => newVideosApi.cartAdd(suggested_video_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["newVideosFeed"] });
      qc.invalidateQueries({ queryKey: ["newVideosCart"] });
    },
  });
}

export function useNewVideosRemoveFromCart() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (suggested_video_id: number) => newVideosApi.cartRemove(suggested_video_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["newVideosFeed"] });
      qc.invalidateQueries({ queryKey: ["newVideosCart"] });
    },
  });
}

export function useNewVideosClearCart() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => newVideosApi.cartClear(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["newVideosFeed"] });
      qc.invalidateQueries({ queryKey: ["newVideosCart"] });
    },
  });
}

export function useNewVideosImportCart() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (options?: { normalize?: boolean; scrape?: boolean; scrape_musicbrainz?: boolean; ai_auto_analyse?: boolean; ai_auto_fallback?: boolean }) => newVideosApi.cartImportAll(options),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["newVideosFeed"] });
      qc.invalidateQueries({ queryKey: ["newVideosCart"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useNewVideosAddVideo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (suggested_video_id: number) => newVideosApi.addVideo(suggested_video_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["newVideosFeed"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useNewVideosDismiss() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, type, reason }: { id: number; type: "temporary" | "permanent"; reason?: string }) =>
      newVideosApi.dismiss(id, type, reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["newVideosFeed"] });
    },
  });
}

export function useNewVideosUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (updates: { key: string; value: string }[]) => newVideosApi.updateSettings(updates),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["newVideosSettings"] });
    },
  });
}

export function useNewVideosFeedback() {
  return useMutation({
    mutationFn: (data: { suggested_video_id?: number; feedback_type: string; provider?: string; provider_video_id?: string; artist?: string; category?: string }) =>
      newVideosApi.feedback(data),
  });
}

// ─── Metadata Manager ────────────────────────────────────

export function useMbidStats() {
  return useQuery({ queryKey: ["mbidStats"], queryFn: metadataManagerApi.mbidStats });
}

export function useArtistConflicts() {
  return useQuery({ queryKey: ["artistConflicts"], queryFn: metadataManagerApi.artistConflicts });
}

export function useConsolidateArtist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { mb_artist_id: string; canonical_name: string }) =>
      metadataManagerApi.consolidateArtist(data.mb_artist_id, data.canonical_name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["artistConflicts"] });
      qc.invalidateQueries({ queryKey: ["mbidStats"] });
      qc.invalidateQueries({ queryKey: ["library"] });
      qc.invalidateQueries({ queryKey: ["artists"] });
    },
  });
}