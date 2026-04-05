import axios from "axios";
import type {
  PaginatedResponse, VideoItemSummary, VideoItemDetail, VideoItemUpdate,
  JobSummary, JobLog, JobTelemetry, TelemetrySnapshot, MetadataSnapshot,
  AppSetting, SettingUpdate,
  NormalizationRecord, ArtistBucket, YearBucket, GenreBucket, AlbumBucket, RatingBucket,
  GenreBlacklistItem,
  ImportRequest, NormalizeRequest, BatchRescanRequest, BatchActionResponse,
  BatchDeleteRequest, BatchDeleteResponse,
  LibraryParams, JobsParams, AppStats, FacetFilterParams,
  MatchResult, NormalizationResult, ReviewListResponse, ReviewParams,
  PinRequest, ApplyRequest, BatchResolveRequest, BatchResolveResponse,
  ManualSearchResponse, ExportKodiRequest, ExportKodiResponse,
  SearchEntityType, SourceInfo,
  OrphanDetectResponse, OrphanCleanRequest, OrphanCleanResponse,
  LibraryHealthResponse,
  AIEnrichRequest, AIComparisonResponse, AIMetadataResultOut,
  AIApplyFieldsRequest, AIUndoRequest, AITestConnectionRequest, AITestConnectionResponse,
  FingerprintResult, ModelCatalog, RoutingPreview,
  SceneAnalysisRequest, SceneAnalysisOut,
  AIThumbnailOut, AISettingsOut, AISettingsUpdate,
  ModelAvailabilityOut,
  LibraryImportScanRequest, LibraryImportScanResponse,
  LibraryImportStartRequest, LibraryImportStartResponse,
  ExistingDetailsRequest, ExistingDetailsResponse,
  RegexPreviewRequest, RegexPreviewResponse,
  PlaylistOut, PlaylistSummary, PlaylistEntry,
  PartyModeParams, PartyModeResponse,
  LogFileEntry, LogReadResponse,
} from "@/types";

const api = axios.create({ baseURL: "/api" });

export interface FormatResolution {
  height: number;
  width: number | null;
  label: string;
  format_id: string;
  ext: string;
  vcodec: string;
  tbr: number;
}

// ─── Library ──────────────────────────────────────────────
export const libraryApi = {
  list: (params: LibraryParams) =>
    api.get<PaginatedResponse<VideoItemSummary>>("/library/", { params }).then(r => r.data),

  get: (id: number) =>
    api.get<VideoItemDetail>(`/library/${id}`).then(r => r.data),

  update: (id: number, data: VideoItemUpdate) =>
    api.put<VideoItemDetail>(`/library/${id}`, data).then(r => r.data),

  delete: (id: number) =>
    api.delete(`/library/${id}`).then(r => r.data),

  artists: (params?: FacetFilterParams) =>
    api.get<ArtistBucket[]>("/library/artists", { params }).then(r => r.data),

  years: (params?: FacetFilterParams) =>
    api.get<YearBucket[]>("/library/years", { params }).then(r => r.data),

  genres: (params?: FacetFilterParams) =>
    api.get<GenreBucket[]>("/library/genres", { params }).then(r => r.data),

  albums: (params?: FacetFilterParams) =>
    api.get<AlbumBucket[]>("/library/albums", { params }).then(r => r.data),

  songRatings: (params?: FacetFilterParams) =>
    api.get<RatingBucket[]>("/library/song-ratings", { params }).then(r => r.data),

  videoRatings: (params?: FacetFilterParams) =>
    api.get<RatingBucket[]>("/library/video-ratings", { params }).then(r => r.data),

  snapshots: (videoId: number) =>
    api.get<MetadataSnapshot[]>(`/library/${videoId}/snapshots`).then(r => r.data),

  nav: (videoId: number) =>
    api.get<{ prev_id: number | null; next_id: number | null; random_id: number | null }>(`/library/${videoId}/nav`).then(r => r.data),

  undoRescan: (videoId: number) =>
    api.post<VideoItemDetail>(`/library/${videoId}/undo-rescan`).then(r => r.data),

  scrape: (videoId: number, opts: { aiAutoAnalyse?: boolean; aiOnly?: boolean; scrapeWikipedia?: boolean; wikipediaUrl?: string; scrapeMusicbrainz?: boolean; musicbrainzUrl?: string; scrapeTmvdb?: boolean; isCover?: boolean; isLive?: boolean; isAlternate?: boolean; isUncensored?: boolean; alternateVersionLabel?: string; findSourceVideo?: boolean; normalizeAudio?: boolean } = {}) =>
    api.post<{ job_id: number; message: string }>(`/library/${videoId}/scrape`, {
      ai_auto_analyse: opts.aiAutoAnalyse || false,
      ai_only: opts.aiOnly || false,
      scrape_wikipedia: opts.scrapeWikipedia || false,
      ...(opts.wikipediaUrl ? { wikipedia_url: opts.wikipediaUrl } : {}),
      scrape_musicbrainz: opts.scrapeMusicbrainz || false,
      ...(opts.musicbrainzUrl ? { musicbrainz_url: opts.musicbrainzUrl } : {}),
      scrape_tmvdb: opts.scrapeTmvdb || false,
      is_cover: opts.isCover || false,
      is_live: opts.isLive || false,
      is_alternate: opts.isAlternate || false,
      is_uncensored: opts.isUncensored || false,
      ...(opts.alternateVersionLabel ? { alternate_version_label: opts.alternateVersionLabel } : {}),
      find_source_video: opts.findSourceVideo || false,
      normalize_audio: opts.normalizeAudio || false,
    }).then(r => r.data),

  orphans: () =>
    api.get<OrphanDetectResponse>("/library/orphans").then(r => r.data),

  cleanOrphans: (data: OrphanCleanRequest) =>
    api.post<OrphanCleanResponse>("/library/orphans/clean", data).then(r => r.data),

  health: () =>
    api.get<LibraryHealthResponse>("/library/health").then(r => r.data),

  cleanStale: (videoIds: number[]) =>
    api.post<{ results: { id: number; status: string; reason?: string }[]; removed: number }>("/library/clean-stale", { video_ids: videoIds }).then(r => r.data),

  cleanRedundant: (filePaths: string[]) =>
    api.post<{ results: { file: string; status: string; reason?: string }[]; deleted: number }>("/library/clean-redundant", { file_paths: filePaths }).then(r => r.data),

  rename: (videoId: number) =>
    api.post<VideoItemDetail>(`/library/${videoId}/rename`).then(r => r.data),

  openFolder: (videoId: number) =>
    api.post<{ ok: boolean; folder: string }>(`/library/${videoId}/open-folder`).then(r => r.data),

  bulkRenamePreview: () =>
    api.post<{
      total: number;
      needs_rename: number;
      already_correct: number;
      items: { video_id: number; artist: string; title: string; current_path: string; expected_path: string; needs_rename: boolean }[];
    }>("/library/bulk-rename/preview").then(r => r.data),

  bulkRenameExecute: () =>
    api.post<{ renamed: number; failed: number; errors: string[] }>("/library/bulk-rename/execute", {}, { timeout: 300_000 }).then(r => r.data),

  batchDelete: (data: BatchDeleteRequest) =>
    api.post<BatchDeleteResponse>("/library/batch-delete", data).then(r => r.data),

  createSource: (videoId: number, data: { provider: string; source_video_id: string; original_url: string; canonical_url: string; source_type?: string }) =>
    api.post<SourceInfo>(`/library/${videoId}/sources`, data).then(r => r.data),

  updateSource: (videoId: number, sourceId: number, data: { provider?: string; source_video_id?: string; original_url?: string; canonical_url?: string; source_type?: string }) =>
    api.put<SourceInfo>(`/library/${videoId}/sources/${sourceId}`, data).then(r => r.data),

  deleteSource: (videoId: number, sourceId: number) =>
    api.delete(`/library/${videoId}/sources/${sourceId}`).then(r => r.data),

  partyMode: (params: PartyModeParams) =>
    api.get<PartyModeResponse>("/library/party-mode", { params }).then(r => r.data),
};

// ─── Jobs ─────────────────────────────────────────────────
export const jobsApi = {
  import: (data: ImportRequest) =>
    api.post<JobSummary>("/jobs/import", data).then(r => r.data),

  rescan: (videoId: number, fromDisk?: boolean) =>
    api.post<JobSummary>(`/jobs/rescan/${videoId}`, null, {
      params: fromDisk ? { from_disk: true } : undefined,
    }).then(r => r.data),

  redownload: (videoId: number, formatSpec?: string) =>
    api.post<JobSummary>(`/jobs/redownload/${videoId}`, null, {
      params: formatSpec ? { format_spec: formatSpec } : undefined,
    }).then(r => r.data),

  formats: (videoId: number) =>
    api.get<{ resolutions: FormatResolution[]; url: string }>(`/jobs/formats/${videoId}`).then(r => r.data),

  rescanBatch: (data: BatchRescanRequest) =>
    api.post<BatchActionResponse>("/jobs/rescan-batch", data).then(r => r.data),

  normalize: (data: NormalizeRequest) =>
    api.post<BatchActionResponse>("/jobs/normalize", data).then(r => r.data),

  libraryScan: (importNew = true) =>
    api.post<JobSummary>("/jobs/library-scan", { import_new: importNew }).then(r => r.data),

  libraryDuplicateScan: () =>
    api.post<JobSummary>("/jobs/library-duplicate-scan").then(r => r.data),

  libraryExport: (mode: string) =>
    api.post<JobSummary>("/jobs/library-export", { mode }).then(r => r.data),

  list: (params?: JobsParams) =>
    api.get<JobSummary[]>("/jobs/", { params }).then(r => r.data),

  get: (id: number) =>
    api.get<JobSummary>(`/jobs/${id}`).then(r => r.data),

  log: (id: number) =>
    api.get<JobLog>(`/jobs/${id}/log`).then(r => r.data),

  retry: (id: number) =>
    api.post<JobSummary>(`/jobs/${id}/retry`).then(r => r.data),

  cancel: (id: number) =>
    api.post<JobSummary>(`/jobs/${id}/cancel`).then(r => r.data),

  clearHistory: (params?: { status?: string; job_type?: string }) =>
    api.delete<{ deleted: number }>("/jobs/history", { params }).then(r => r.data),

  batchDelete: (ids: number[]) =>
    api.post<{ deleted: number[]; skipped: number[]; count: number }>("/jobs/batch/delete", ids).then(r => r.data),

  telemetry: () =>
    api.get<TelemetrySnapshot>("/jobs/telemetry").then(r => r.data),

  jobTelemetry: (id: number) =>
    api.get<JobTelemetry & { active: boolean }>(`/jobs/${id}/telemetry`).then(r => r.data),

  // ── Log viewer ──
  logFiles: () =>
    api.get<LogFileEntry[]>("/jobs/logs/files").then(r => r.data),

  logDirectory: () =>
    api.get<{ path: string }>("/jobs/logs/directory").then(r => r.data),

  readLog: (params: { file: string; tail?: number; offset?: number; limit?: number }) =>
    api.get<LogReadResponse>("/jobs/logs/read", { params }).then(r => r.data),
};

// ─── Playback URLs ────────────────────────────────────────
export const playbackApi = {
  streamUrl: (videoId: number) => `/api/playback/stream/${videoId}`,
  previewUrl: (videoId: number) => `/api/playback/preview/${videoId}`,
  posterUrl: (videoId: number) => `/api/playback/poster/${videoId}`,
  artworkUrl: (videoId: number, assetType: string) => `/api/playback/artwork/${videoId}/${assetType}`,

  recordHistory: (videoId: number, durationWatched: number) =>
    api.post(`/playback/history/${videoId}`, null, { params: { duration_watched: durationWatched } }).then(r => r.data),

  uploadArtwork: (videoId: number, assetType: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.put(`/playback/artwork/${videoId}/${assetType}`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then(r => r.data);
  },

  deleteArtwork: (videoId: number, assetType: string) =>
    api.delete(`/playback/artwork/${videoId}/${assetType}`).then(r => r.data),

  artworkIds: () =>
    api.get<{ videoId: number; type: string }[]>("/playback/artwork-ids").then(r => r.data),
};

// ─── Settings ─────────────────────────────────────────────
export const settingsApi = {
  list: () =>
    api.get<AppSetting[]>("/settings/").then(r => r.data),

  update: (data: SettingUpdate) =>
    api.put<AppSetting>("/settings/", data).then(r => r.data),

  updateSourceDirs: (dirs: string[]) =>
    api.put<{ saved: boolean; added_dirs: string[]; removed_dirs: string[]; cleaned_count: number }>(
      "/settings/source-directories", { dirs },
    ).then(r => r.data),

  normalizationHistory: (videoId?: number) =>
    api.get<NormalizationRecord[]>(videoId ? `/settings/normalization-history/${videoId}` : `/settings/normalization-history`).then(r => r.data),

  browseDirectories: () =>
    api.get<{ path: string }>("/settings/browse-directories", { timeout: 120_000 }).then(r => r.data),

  namingPreview: (naming_pattern: string, folder_structure: string) =>
    api.post<{ examples: { artist: string; title: string; version_type: string; path: string }[] }>(
      "/settings/naming-preview", { naming_pattern, folder_structure },
    ).then(r => r.data),

  genreBlacklist: () =>
    api.get<GenreBlacklistItem[]>("/settings/genre-blacklist").then(r => r.data),

  updateGenreBlacklist: (genre_ids: number[], blacklisted: boolean) =>
    api.put<{ updated: number }>("/settings/genre-blacklist", { genre_ids, blacklisted }).then(r => r.data),

  createGenre: (name: string) =>
    api.post<GenreBlacklistItem>("/settings/genre-blacklist", { name }).then(r => r.data),

  restart: () =>
    api.post<{ status: string }>("/settings/restart").then(r => r.data),

  getStartupStatus: () =>
    api.get<{ registered: boolean; command: string | null }>("/settings/startup").then(r => r.data),

  configureStartup: () =>
    api.post<{ status: string; startup_enabled: boolean; delay: number }>("/settings/startup").then(r => r.data),

  defaults: () =>
    api.get<{ library_dir: string }>("/settings/defaults").then(r => r.data),

  openDirectory: (path: string) =>
    api.post<{ ok: boolean; path: string }>("/settings/open-directory", { path }).then(r => r.data),
};

// ─── Stats ────────────────────────────────────────────────
export const statsApi = {
  get: () => api.get<AppStats>("/stats").then(r => r.data),
  health: () => api.get<{ status: string }>("/health").then(r => r.data),
  version: () => api.get<{ app_version: string; db_version: string; version_mismatch: boolean }>("/version").then(r => r.data),
  updateCheck: () => api.get<{ update_available: boolean; current_version?: string; latest_version?: string; release_url?: string; release_name?: string }>("/update-check").then(r => r.data),
};

// ─── Resolve / Matching ──────────────────────────────────
export const resolveApi = {
  trigger: (videoId: number, force = false) =>
    api.post<MatchResult>(`/resolve/${videoId}`, null, { params: { force } }).then(r => r.data),

  get: (videoId: number) =>
    api.get<MatchResult>(`/resolve/${videoId}`).then(r => r.data),

  normalization: (videoId: number) =>
    api.get<NormalizationResult>(`/resolve/${videoId}/normalization`).then(r => r.data),

  pin: (videoId: number, data: PinRequest) =>
    api.post(`/resolve/${videoId}/pin`, data).then(r => r.data),

  unpin: (videoId: number) =>
    api.post(`/resolve/${videoId}/unpin`).then(r => r.data),

  apply: (videoId: number, data: ApplyRequest) =>
    api.post(`/resolve/${videoId}/apply`, data).then(r => r.data),

  undo: (videoId: number) =>
    api.post(`/resolve/${videoId}/undo`).then(r => r.data),

  batch: (data: BatchResolveRequest) =>
    api.post<BatchResolveResponse>("/resolve/batch", data).then(r => r.data),
};

// ─── Review Queue ────────────────────────────────────────
export const reviewApi = {
  list: (params?: ReviewParams) =>
    api.get<ReviewListResponse>("/review", { params }).then(r => r.data),
  approve: (videoId: number) =>
    api.post<{ status: string; video_id: number }>(`/review/${videoId}/approve`).then(r => r.data),
  dismiss: (videoId: number) =>
    api.post<{ status: string; video_id: number }>(`/review/${videoId}/dismiss`).then(r => r.data),
  setVersion: (videoId: number, versionType: string, approve = true) =>
    api.post<{ status: string; video_id: number; version_type: string }>(
      `/review/${videoId}/set-version`, null, { params: { version_type: versionType, approve } },
    ).then(r => r.data),
  batchApprove: (videoIds: number[]) =>
    api.post<{ status: string; count: number }>("/review/batch/approve", videoIds).then(r => r.data),
  batchDismiss: (videoIds: number[]) =>
    api.post<{ status: string; count: number }>("/review/batch/dismiss", videoIds).then(r => r.data),
  scanRenames: () =>
    api.post<{ status: string; flagged: number }>("/review/scan-renames").then(r => r.data),
  applyRename: (videoId: number) =>
    api.post<{ status: string; video_id: number }>(`/review/${videoId}/apply-rename`).then(r => r.data),
  batchApplyRename: (videoIds: number[]) =>
    api.post<{ status: string; renamed: number; failed: number; errors: string[] }>("/review/batch/apply-rename", videoIds).then(r => r.data),
  batchDelete: (videoIds: number[]) =>
    api.post<{ deleted: number[]; errors: number[]; count: number }>("/review/batch/delete", videoIds).then(r => r.data),
  batchScrape: (videoIds: number[], options?: { scrape_wikipedia?: boolean; scrape_musicbrainz?: boolean; ai_auto?: boolean; ai_only?: boolean; normalize?: boolean }) =>
    api.post<{ job_id: number; message: string }>("/review/batch/scrape", { video_ids: videoIds, ...options }).then(r => r.data),
};

// ─── Search (MusicBrainz Manual) ─────────────────────────
export const searchApi = {
  search: (entityType: SearchEntityType, q: string, artist?: string, limit = 10) =>
    api.get<ManualSearchResponse>(`/search/${entityType}`, {
      params: { q, artist, limit },
    }).then(r => r.data),
};

// ─── Export ──────────────────────────────────────────────
export const exportApi = {
  kodi: (data?: ExportKodiRequest) =>
    api.post<ExportKodiResponse>("/export/kodi", data ?? {}).then(r => r.data),
};

// ─── AI Metadata Enrichment ─────────────────────────────
export const aiApi = {
  enrich: (videoId: number, data?: AIEnrichRequest) =>
    api.post<AIMetadataResultOut>(`/ai/${videoId}/enrich`, data ?? {}).then(r => r.data),

  comparison: (videoId: number) =>
    api.get<AIComparisonResponse>(`/ai/${videoId}/comparison`).then(r => r.data),

  applyFields: (videoId: number, data: AIApplyFieldsRequest) =>
    api.post(`/ai/${videoId}/apply`, data).then(r => r.data),

  undo: (videoId: number, data: AIUndoRequest) =>
    api.post(`/ai/${videoId}/undo`, data).then(r => r.data),

  fingerprint: (videoId: number) =>
    api.post<FingerprintResult>(`/ai/${videoId}/fingerprint`).then(r => r.data),

  results: (videoId: number) =>
    api.get<AIMetadataResultOut[]>(`/ai/${videoId}/results`).then(r => r.data),

  scenes: (videoId: number, data?: SceneAnalysisRequest) =>
    api.post<SceneAnalysisOut>(`/ai/${videoId}/scenes`, data ?? {}).then(r => r.data),

  getScenes: (videoId: number) =>
    api.get<SceneAnalysisOut>(`/ai/${videoId}/scenes`).then(r => r.data),

  thumbnails: (videoId: number) =>
    api.get<AIThumbnailOut[]>(`/ai/${videoId}/thumbnails`).then(r => r.data),

  selectThumbnail: (videoId: number, thumbnailId: number, applyToPoster?: boolean) =>
    api.post(`/ai/${videoId}/thumbnail`, { thumbnail_id: thumbnailId, apply_to_poster: applyToPoster ?? false }).then(r => r.data),

  settings: () =>
    api.get<AISettingsOut>("/ai/settings").then(r => r.data),

  updateSettings: (data: AISettingsUpdate) =>
    api.put<AISettingsOut>("/ai/settings", data).then(r => r.data),

  testConnection: (data: AITestConnectionRequest) =>
    api.post<AITestConnectionResponse>("/ai/test", data).then(r => r.data),

  models: (provider: string, forceRefresh = false) =>
    api.get<ModelCatalog>("/ai/models", { params: { provider, force_refresh: forceRefresh } }).then(r => r.data),

  testModelAvailability: (force = false) =>
    api.post<ModelAvailabilityOut>("/ai/models/test-availability", null, { params: { force } }).then(r => r.data),

  routingPreview: () =>
    api.get<RoutingPreview>("/ai/routing-preview").then(r => r.data),

  batchEnrich: (videoIds?: number[], provider?: string, autoApply = false, force = false) =>
    api.post("/ai/batch/enrich", null, { params: { video_ids: videoIds, provider, auto_apply: autoApply, force } }).then(r => r.data),

  batchScenes: (videoIds?: number[], force = false) =>
    api.post("/ai/batch/scenes", null, { params: { video_ids: videoIds, force } }).then(r => r.data),

  thumbnailUrl: (videoId: number, thumbnailId: number) =>
    `/api/ai/${videoId}/thumbnails/${thumbnailId}/image`,

  prompts: () =>
    api.get<import("@/types").AIPromptSettingsOut>("/ai/prompts").then(r => r.data),

  updatePrompts: (data: import("@/types").AIPromptSettingsUpdate) =>
    api.put<import("@/types").AIPromptSettingsOut>("/ai/prompts", data).then(r => r.data),

  dismissScrape: (videoId: number) =>
    api.post(`/ai/${videoId}/dismiss-scrape`).then(r => r.data),
};

// ─── Library Import ──────────────────────────────────────
export const libraryImportApi = {
  scan: (data: LibraryImportScanRequest) =>
    api.post<LibraryImportScanResponse>("/library-import/scan", data).then(r => r.data),

  previewRegex: (data: RegexPreviewRequest) =>
    api.post<RegexPreviewResponse>("/library-import/preview-regex", data).then(r => r.data),

  start: (data: LibraryImportStartRequest) =>
    api.post<LibraryImportStartResponse>("/library-import/start", data).then(r => r.data),

  existingDetails: (data: ExistingDetailsRequest) =>
    api.post<ExistingDetailsResponse>("/library-import/existing-details", data).then(r => r.data),
};

// ─── Playlists ───────────────────────────────────────────
export const playlistApi = {
  list: () =>
    api.get<PlaylistSummary[]>("/playlists/").then(r => r.data),

  get: (id: number) =>
    api.get<PlaylistOut>(`/playlists/${id}`).then(r => r.data),

  create: (name: string, description?: string) =>
    api.post<PlaylistOut>("/playlists/", { name, description }).then(r => r.data),

  update: (id: number, data: { name?: string; description?: string }) =>
    api.put<PlaylistOut>(`/playlists/${id}`, data).then(r => r.data),

  delete: (id: number) =>
    api.delete(`/playlists/${id}`).then(r => r.data),

  addEntry: (playlistId: number, videoId: number) =>
    api.post<PlaylistEntry>(`/playlists/${playlistId}/entries`, { video_id: videoId }).then(r => r.data),

  addEntries: (playlistId: number, videoIds: number[]) =>
    api.post<PlaylistEntry[]>(`/playlists/${playlistId}/entries/batch`, { video_ids: videoIds }).then(r => r.data),

  removeEntry: (playlistId: number, entryId: number) =>
    api.delete(`/playlists/${playlistId}/entries/${entryId}`).then(r => r.data),

  reorder: (playlistId: number, entryIds: number[]) =>
    api.put<PlaylistOut>(`/playlists/${playlistId}/reorder`, { entry_ids: entryIds }).then(r => r.data),
};

// ─── Video Editor ─────────────────────────────────────────
import type {
  EditorQueueItem, CropPreviewRequest, CropPreviewResponse,
  EncodeRequest, LetterboxDetectResult, LetterboxScanItem,
} from "@/types";
import type { ScraperTestRequest, ScraperTestResult, ScraperTestProgress, ImportTestRequest, DirectoryScanResult } from "@/types";

export const videoEditorApi = {
  getQueueItems: (videoIds: number[]) =>
    api.get<EditorQueueItem[]>("/video-editor/queue", {
      params: { video_ids: videoIds.join(",") },
    }).then(r => r.data),

  detectLetterbox: (videoId: number) =>
    api.post<LetterboxDetectResult>("/video-editor/detect-letterbox", null, {
      params: { video_id: videoId },
    }).then(r => r.data),

  scanLetterbox: (limit: number = 200) =>
    api.post<{ status: string; job_id?: number; results?: LetterboxScanItem[]; total_scanned?: number }>(
      "/video-editor/scan-letterbox", { limit },
    ).then(r => r.data),

  getScanResults: (jobId: number) =>
    api.get<{ status: string; progress_percent: number; current_step: string; results: LetterboxScanItem[]; error?: string }>(
      `/video-editor/scan-results/${jobId}`,
    ).then(r => r.data),

  cropPreview: (req: CropPreviewRequest) =>
    api.post<CropPreviewResponse>("/video-editor/crop-preview", req).then(r => r.data),

  encode: (req: EncodeRequest) =>
    api.post<{ job_id: number; message: string }>("/video-editor/encode", req).then(r => r.data),

  batchEncode: (items: EncodeRequest[]) =>
    api.post<{ job_ids: number[]; message: string }>("/video-editor/batch-encode", { items }).then(r => r.data),

  getEncodeStatus: (jobId: number) =>
    api.get<{ status: string; progress_percent: number; current_step: string; error?: string; video_id?: number; summary?: string }>(
      `/video-editor/encode-status/${jobId}`,
    ).then(r => r.data),

  restoreFromArchive: (videoId: number) =>
    api.post<{ message: string; archive_path: string }>("/video-editor/restore-from-archive", null, {
      params: { video_id: videoId },
    }).then(r => r.data),

  setExcludeFromScan: (videoId: number, exclude: boolean) =>
    api.post<{ video_id: number; exclude_from_scan: boolean }>("/video-editor/exclude-from-scan", {
      video_id: videoId, exclude,
    }).then(r => r.data),
};

// ─── Scraper Test ─────────────────────────────────────────
export const scraperTestApi = {
  run: (data: ScraperTestRequest) =>
    api.post<ScraperTestResult>("/scraper-test/run", data).then(r => r.data),

  /**
   * SSE streaming scraper test via EventSource (GET).
   * Returns a close() handle to abort the stream.
   */
  runStream: (
    data: ScraperTestRequest,
    onProgress: (p: ScraperTestProgress) => void,
    onResult: (r: ScraperTestResult) => void,
    onError: (msg: string) => void,
  ): { close: () => void } => {
    const params = new URLSearchParams();
    params.set("url", data.url);
    if (data.artist_override) params.set("artist_override", data.artist_override);
    if (data.title_override) params.set("title_override", data.title_override);
    if (data.scrape_wikipedia) params.set("scrape_wikipedia", "true");
    if (data.scrape_musicbrainz) params.set("scrape_musicbrainz", "true");
    if (data.wikipedia_url) params.set("wikipedia_url", data.wikipedia_url);
    if (data.musicbrainz_url) params.set("musicbrainz_url", data.musicbrainz_url);
    if (data.ai_auto) params.set("ai_auto", "true");
    if (data.ai_only) params.set("ai_only", "true");

    const es = new EventSource(`/api/scraper-test/run-stream?${params.toString()}`);

    es.addEventListener("progress", (e) => {
      try { onProgress(JSON.parse((e as MessageEvent).data)); } catch { /* skip */ }
    });

    es.addEventListener("result", (e) => {
      try { onResult(JSON.parse((e as MessageEvent).data)); } catch { /* skip */ }
      es.close();
    });

    es.addEventListener("fail", (e) => {
      try { onError(JSON.parse((e as MessageEvent).data).detail || "Unknown error"); } catch { onError("Unknown error"); }
      es.close();
    });

    es.addEventListener("done", () => {
      es.close();
    });

    es.onerror = () => {
      // Connection-level error — EventSource would auto-reconnect, prevent that
      es.close();
      onError("Connection to server lost");
    };

    return { close: () => es.close() };
  },

  scanDirectory: (directory: string) =>
    api.get<DirectoryScanResult>("/scraper-test/scan-directory", { params: { directory } }).then(r => r.data),

  /**
   * SSE streaming import-mode scraper test via EventSource (GET).
   * Returns a close() handle to abort the stream.
   */
  runImportStream: (
    data: ImportTestRequest,
    onProgress: (p: ScraperTestProgress) => void,
    onResult: (r: ScraperTestResult) => void,
    onError: (msg: string) => void,
  ): { close: () => void } => {
    const params = new URLSearchParams();
    params.set("directory", data.directory);
    if (data.file_name) params.set("file_name", data.file_name);
    if (data.artist_override) params.set("artist_override", data.artist_override);
    if (data.title_override) params.set("title_override", data.title_override);
    if (data.scrape_wikipedia) params.set("scrape_wikipedia", "true");
    if (data.scrape_musicbrainz) params.set("scrape_musicbrainz", "true");
    if (data.wikipedia_url) params.set("wikipedia_url", data.wikipedia_url);
    if (data.musicbrainz_url) params.set("musicbrainz_url", data.musicbrainz_url);
    if (data.ai_auto) params.set("ai_auto", "true");
    if (data.ai_only) params.set("ai_only", "true");

    const es = new EventSource(`/api/scraper-test/run-import-stream?${params.toString()}`);

    es.addEventListener("progress", (e) => {
      try { onProgress(JSON.parse((e as MessageEvent).data)); } catch { /* skip */ }
    });

    es.addEventListener("result", (e) => {
      try { onResult(JSON.parse((e as MessageEvent).data)); } catch { /* skip */ }
      es.close();
    });

    es.addEventListener("fail", (e) => {
      try { onError(JSON.parse((e as MessageEvent).data).detail || "Unknown error"); } catch { onError("Unknown error"); }
      es.close();
    });

    es.addEventListener("done", () => {
      es.close();
    });

    es.onerror = () => {
      es.close();
      onError("Connection to server lost");
    };

    return { close: () => es.close() };
  },
};

// ─── New Videos / Discovery ──────────────────────────────
export const newVideosApi = {
  feed: () =>
    api.get<import("@/types").NewVideosFeed>("/new-videos/").then(r => r.data),

  refresh: (categories?: string[], force = false) =>
    api.post<{ status: string; refreshed: Record<string, number> }>("/new-videos/refresh", {
      categories: categories ?? null,
      force,
    }).then(r => r.data),

  cart: () =>
    api.get<import("@/types").CartResponse>("/new-videos/cart").then(r => r.data),

  cartAdd: (suggested_video_id: number) =>
    api.post<{ status: string; id: number }>("/new-videos/cart/add", { suggested_video_id }).then(r => r.data),

  cartRemove: (suggested_video_id: number) =>
    api.post<{ status: string }>("/new-videos/cart/remove", { suggested_video_id }).then(r => r.data),

  cartClear: () =>
    api.post<{ status: string; removed: number }>("/new-videos/cart/clear").then(r => r.data),

  cartImportAll: (options?: { normalize?: boolean; scrape?: boolean; scrape_musicbrainz?: boolean; ai_auto_analyse?: boolean; ai_auto_fallback?: boolean }) =>
    api.post<{ status: string; job_count: number; jobs: { job_id: number; url: string; title: string }[] }>("/new-videos/cart/import-all", options ?? {}).then(r => r.data),

  addVideo: (suggested_video_id: number) =>
    api.post<{ status: string; job_id: number }>("/new-videos/add", { suggested_video_id }).then(r => r.data),

  dismiss: (suggested_video_id: number, dismissal_type: "temporary" | "permanent" = "temporary", reason?: string) =>
    api.post<{ status: string; type: string }>("/new-videos/dismiss", {
      suggested_video_id, dismissal_type, reason,
    }).then(r => r.data),

  undismiss: (suggested_video_id: number) =>
    api.post<{ status: string }>("/new-videos/undismiss", { suggested_video_id }).then(r => r.data),

  feedback: (data: { suggested_video_id?: number; feedback_type: string; provider?: string; provider_video_id?: string; artist?: string; category?: string; context?: Record<string, unknown> }) =>
    api.post<{ status: string }>("/new-videos/feedback", data).then(r => r.data),

  settings: () =>
    api.get<import("@/types").NewVideosSettings>("/new-videos/settings").then(r => r.data),

  updateSettings: (updates: { key: string; value: string }[]) =>
    api.post<{ status: string; saved: string[] }>("/new-videos/settings", updates).then(r => r.data),
};
