/* ── Playarr TypeScript types ── */

// ─── Enums ────────────────────────────────────────────────
export type SourceProvider = "youtube" | "vimeo" | "wikipedia" | "imdb" | "musicbrainz" | "other";

export type JobStatus =
  | "queued"
  | "downloading"
  | "downloaded"
  | "remuxing"
  | "analyzing"
  | "normalizing"
  | "tagging"
  | "writing_nfo"
  | "asset_fetch"
  | "finalizing"
  | "complete"
  | "failed"
  | "cancelled"
  | "skipped";

export type ViewMode = "grid" | "list";

// ─── Library ──────────────────────────────────────────────
export interface VideoItemSummary {
  id: number;
  artist: string;
  title: string;
  album?: string | null;
  album_entity_id?: number | null;
  year?: number | null;
  resolution_label?: string | null;
  has_poster: boolean;
  version_type?: string;
  review_status?: string;
  enrichment_status?: string;
  import_method?: string | null;
  duration_seconds?: number | null;
  playarr_video_id?: string | null;
  playarr_track_id?: string | null;
  created_at: string;
}

export interface SourceInfo {
  id: number;
  provider: SourceProvider;
  source_video_id: string;
  original_url: string;
  canonical_url: string;
  source_type?: string;
}

export interface QualitySignature {
  width?: number | null;
  height?: number | null;
  fps?: number | null;
  video_codec?: string | null;
  video_bitrate?: number | null;
  hdr: boolean;
  audio_codec?: string | null;
  audio_bitrate?: number | null;
  audio_sample_rate?: number | null;
  audio_channels?: number | null;
  container?: string | null;
  duration_seconds?: number | null;
  loudness_lufs?: number | null;
}

export interface GenreInfo {
  id: number;
  name: string;
}

export interface MediaAsset {
  id: number;
  asset_type: string;
  file_path: string;
  source_url?: string | null;
  provenance?: string | null;
  status?: string | null; // valid|invalid|missing|pending
  file_hash?: string | null;
  crop_position?: string | null; // CSS object-position e.g. "50% 30%"
}

export interface VideoItemDetail {
  id: number;
  artist: string;
  title: string;
  album?: string | null;
  album_entity_id?: number | null;
  year?: number | null;
  plot?: string | null;
  mb_artist_id?: string | null;
  mb_recording_id?: string | null;
  mb_release_id?: string | null;
  mb_release_group_id?: string | null;
  mb_track_id?: string | null;
  artist_ids?: { name: string; mb_artist_id?: string }[] | null;
  playarr_video_id?: string | null;
  playarr_track_id?: string | null;
  folder_path?: string | null;
  file_path?: string | null;
  file_size_bytes?: number | null;
  resolution_label?: string | null;
  song_rating?: number | null;
  video_rating?: number | null;
  song_rating_set?: boolean;
  video_rating_set?: boolean;
  locked_fields?: string[] | null;
  version_type?: string;
  alternate_version_label?: string | null;
  original_artist?: string | null;
  original_title?: string | null;
  related_versions?: { id: number; version_type: string; label?: string }[] | null;
  parent_video_id?: number | null;
  canonical_confidence?: number | null;
  canonical_provenance?: string | null;
  review_status?: string;
  review_reason?: string | null;
  processing_state?: ProcessingState | null;
  canonical_track_id?: number | null;
  canonical_track?: CanonicalTrack | null;
  has_archive?: boolean;
  exclude_from_editor_scan?: boolean;
  field_provenance?: Record<string, string> | null;
  created_at: string;
  updated_at: string;
  sources: SourceInfo[];
  quality_signature?: QualitySignature | null;
  genres: GenreInfo[];
  media_assets: MediaAsset[];
}

export interface VideoItemUpdate {
  artist?: string;
  title?: string;
  album?: string | null;
  year?: number | null;
  plot?: string | null;
  genres?: string[];
  locked_fields?: string[];
  version_type?: string;
  alternate_version_label?: string | null;
  original_artist?: string | null;
  original_title?: string | null;
  review_status?: string;
  song_rating?: number | null;
  video_rating?: number | null;
  song_rating_set?: boolean;
  video_rating_set?: boolean;
  mb_artist_id?: string | null;
  mb_recording_id?: string | null;
  mb_release_id?: string | null;
  mb_release_group_id?: string | null;
  mb_track_id?: string | null;
  artist_ids?: { name: string; mb_artist_id?: string }[] | null;
  playarr_video_id?: string | null;
  playarr_track_id?: string | null;
}

// ─── Jobs ─────────────────────────────────────────────────
export interface JobSummary {
  id: number;
  video_id?: number | null;
  celery_task_id?: string | null;
  job_type: string;
  status: JobStatus;
  display_name?: string | null;
  action_label?: string | null;
  input_url?: string | null;
  progress_percent: number;
  current_step?: string | null;
  error_message?: string | null;
  retry_count: number;
  pipeline_steps?: PipelineStep[] | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
}

export interface PipelineStep {
  step: string;
  status: "success" | "failed" | "skipped";
  type?: "ai_error";
  code?: string;
}

export interface JobLog {
  id: number;
  log_text?: string | null;
}

// ─── Telemetry (real-time, ephemeral) ─────────────────────

export interface DownloadMetrics {
  speed_bytes: number;
  avg_speed_30s: number;
  downloaded_bytes: number;
  total_bytes: number;
  eta_seconds: number;
  fragments_done: number;
  fragments_total: number;
  percent: number;
  selected_format: string;
  last_progress_at: number;
  consecutive_stall_seconds: number;
}

export interface ProcessMetrics {
  step_name: string;
  speed_factor: number;
  fps: number;
  progress_pct: number;
  elapsed_seconds: number;
}

export interface HealthInfo {
  stall_flags: string[];
  risk_score: number;
  recommended_action: string;
}

export interface AttemptRecord {
  attempt_num: number;
  started_at: number;
  ended_at?: number | null;
  strategy: string;
  reason: string;
  outcome: string;
  error: string;
  format_spec: string;
}

export interface JobTelemetry {
  job_id: number;
  download: DownloadMetrics;
  process: ProcessMetrics;
  health: HealthInfo;
  attempts: AttemptRecord[];
  active?: boolean;
}

export type TelemetrySnapshot = Record<string, JobTelemetry>;

// ─── Log Viewer ───────────────────────────────────────────
export interface LogFileEntry {
  filename: string;
  category: "app" | "job" | "scraper_test";
  size_bytes: number;
  modified: string;
  label: string;
  job_id?: string;
  job_type?: string;
}

export interface LogReadResponse {
  file: string;
  log_text: string;
  total_lines: number;
  returned_lines: number;
  offset: number;
}

// ─── Metadata Snapshots ───────────────────────────────────
export interface MetadataSnapshot {
  id: number;
  video_id: number;
  snapshot_data: Record<string, unknown>;
  reason: string;
  created_at: string;
}

// ─── Settings ─────────────────────────────────────────────
export interface AppSetting {
  key: string;
  value: string;
  value_type: string;
}

export interface SettingUpdate {
  key: string;
  value: string;
  value_type?: string;
}

// ─── Orphan Cleanup ───────────────────────────────────────
export interface OrphanFolder {
  folder_path: string;
  folder_name: string;
  size_bytes: number;
  file_count: number;
  has_video: boolean;
  files?: string[];
}

export interface OrphanDetectResponse {
  orphans: OrphanFolder[];
}

export interface OrphanCleanRequest {
  folder_paths: string[];
  mode: "delete" | "archive";
  force_permanent?: boolean;
}

export interface OrphanCleanResult {
  folder: string;
  status: "deleted" | "archived" | "skipped" | "error" | "network_confirm_required";
  reason?: string;
  destination?: string;
}

export interface OrphanCleanResponse {
  results: OrphanCleanResult[];
}

export interface StaleItem {
  id: number;
  artist: string;
  title: string;
  file_path: string;
  folder_path: string;
}

export interface RedundantFile {
  file_name: string;
  file_path: string;
  reason: string;
  size_bytes: number;
}

export interface RedundantItem {
  video_id: number;
  artist: string;
  title: string;
  folder_path: string;
  video_stem: string;
  files: RedundantFile[];
  total_size_bytes: number;
}

export interface LibraryHealthResponse {
  stale_count: number;
  stale_items: StaleItem[];
  unmanaged_count: number;
  unmanaged_items: StaleItem[];
  orphan_count: number;
  orphan_folders: OrphanFolder[];
  redundant_count: number;
  redundant_items: RedundantItem[];
  stale_archive_count: number;
  stale_archives: StaleArchive[];
}

export interface StaleArchive {
  folder: string;
  folder_name: string;
  artist: string;
  title: string;
  archived_at: string;
  size_bytes: number;
}

// ─── Normalization ────────────────────────────────────────
export interface NormalizationRecord {
  id: number;
  video_id: number;
  target_lufs: number;
  measured_lufs_before?: number | null;
  measured_lufs_after?: number | null;
  gain_applied_db?: number | null;
  created_at: string;
}

export interface ArchiveItem {
  path: string;
  folder: string;
  reason: string;
  artist: string;
  title: string;
  video_id: number | null;
  archived_at: string;
  file_size_bytes: number;
}

export interface QualityBucket {
  quality: string;
  count: number;
  video_ids: number[];
}

// ─── Facet Buckets ────────────────────────────────────────
export interface FacetBucket {
  [key: string]: string | number | null;
}
export interface ArtistBucket {
  artist: string;
  count: number;
  video_ids: number[];
}
export interface YearBucket {
  year: number | null;
  count: number;
  video_ids: number[];
}
export interface GenreBucket {
  genre: string;
  count: number;
  video_ids: number[];
}

// ─── Metadata Manager ────────────────────────────────────
export interface ArtistConflict {
  mb_artist_id: string;
  names: { name: string; video_count: number }[];
  total_videos: number;
}

export interface MbidStats {
  total_videos: number;
  with_artist_id: number;
  with_recording_id: number;
  with_release_id: number;
  with_release_group_id: number;
  with_track_id: number;
  with_any_mbid: number;
  with_complete: number;
  artist_conflicts: number;
  with_playarr_video_id: number;
  with_playarr_track_id: number;
}
export interface GenreBlacklistItem {
  id: number;
  name: string;
  blacklisted: boolean;
  video_count: number;
  master_genre_id: number | null;
  alias_count: number;
}
export interface GenreConflict {
  master_genre: string;
  master_genre_id: number;
  aliases: { id: number; name: string; video_count: number }[];
  total_videos: number;
  blacklisted: boolean;
}
export interface GenreSearchResult {
  id: number;
  name: string;
  video_count: number;
  already_consolidated: boolean;
}
export interface GenreSuggestion {
  master_name: string;
  master_id: number;
  aliases: { id: number; name: string; video_count: number }[];
}

// ─── Artwork Manager types ──────────────────────────────

export interface ArtworkVideoStats {
  total: number;
  with_poster: number;
  poster_from_source: number;
  poster_from_thumb: number;
  with_thumbnail: number;
  with_artist_thumb: number;
  with_album_thumb: number;
}

export interface ArtworkEntityStats {
  total: number;
  with_art: number;
  with_source: number;
  missing_with_source: number;
  missing_no_source: number;
}

export interface ArtworkStats {
  videos: ArtworkVideoStats;
  artists: ArtworkEntityStats;
  albums: ArtworkEntityStats;
}

export interface ArtworkChildVideo {
  id: number;
  title: string;
  artist: string | null;
}

export interface ArtworkEntityRow {
  id: number;
  name: string;
  entity_type: "artist" | "album" | "poster";
  has_art: boolean;
  art_path: string | null;
  has_source: boolean;
  source_providers: string[];
  video_count: number;
  category: "filled" | "missing" | "unavailable";
  provenance: string | null;
  children: ArtworkChildVideo[];
  created_at: string | null;
  mb_id: string | null;
  parent_artist_name: string | null;
  crop_position?: string | null;
}

export interface ArtworkEntitiesResponse {
  items: ArtworkEntityRow[];
  total: number;
  page: number;
  per_page: number;
}

export interface ArtworkRepairResult {
  status: string;
  repaired: number;
  already_ok: number;
  still_missing: number;
  total: number;
}

export interface EntitySourceRow {
  id: number | null;
  provider: string;
  source_type: string;
  url: string;
  provenance: string | null;
}

export interface EntitySourcesResponse {
  entity_type: string;
  entity_id: number;
  mb_id: string | null;
  sources: EntitySourceRow[];
}

export interface AlbumBucket {
  album: string | null;
  album_entity_id: number | null;
  artist: string | null;
  count: number;
  video_ids: number[];
}
export interface RatingBucket {
  rating: number;
  count: number;
  video_ids: number[];
}

/** Common filter params for browse/facet endpoints. */
export interface FacetFilterParams {
  version_type?: string;
  artist?: string;
  year_from?: number;
  year_to?: number;
  song_rating?: number;
  video_rating?: number;
  genre?: string;
  quality?: string;
  search?: string;
}

export interface PartyModeExclusions {
  version_types: string[];
  artists: string[];
  genres: string[];
  albums: string[];
  min_song_rating: number | null;
  min_video_rating: number | null;
}

export interface PartyModeParams {
  search?: string;
  artist?: string;
  album?: string;
  genre?: string;
  year?: number;
  year_from?: number;
  year_to?: number;
  version_type?: string;
  enrichment?: string;
  song_rating?: number;
  video_rating?: number;
  exclude_version_types?: string;
  exclude_artists?: string;
  exclude_genres?: string;
  exclude_albums?: string;
  min_song_rating?: number;
  min_video_rating?: number;
}

export interface PartyModeResponse {
  tracks: { videoId: number; artist: string; title: string; hasPoster: boolean; playCount: number; duration?: number | null }[];
  total: number;
}

// ─── Pagination ───────────────────────────────────────────
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

// ─── API Params ───────────────────────────────────────────
export interface LibraryParams {
  page?: number;
  page_size?: number;
  search?: string;
  artist?: string;
  album?: string;
  album_entity_id?: number;
  genre?: string;
  year?: number;
  year_from?: number;
  year_to?: number;
  version_type?: string;
  enrichment?: string;
  import_method?: string;
  song_rating?: number;
  video_rating?: number;
  quality?: string;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export interface ImportRequest {
  url: string;
  artist?: string;
  title?: string;
  normalize?: boolean;
  scrape?: boolean;
  scrape_musicbrainz?: boolean;
  scrape_tmvdb?: boolean;
  is_cover?: boolean;
  is_live?: boolean;
  is_alternate?: boolean;
  is_uncensored?: boolean;
  alternate_version_label?: string;
  ai_auto_analyse?: boolean;
  ai_auto_fallback?: boolean;
}

export interface NormalizeRequest {
  target_lufs?: number;
  video_ids?: number[];
}

export interface BatchRescanRequest {
  video_ids?: number[];
  scrape_wikipedia?: boolean;
  scrape_musicbrainz?: boolean;
  scrape_tmvdb?: boolean;
  ai_auto?: boolean;
  ai_only?: boolean;
  hint_cover?: boolean;
  hint_live?: boolean;
  hint_alternate?: boolean;
  normalize?: boolean;
  find_source_video?: boolean;
  from_disk?: boolean;
  scene_analysis?: boolean;
}

export interface JobsParams {
  status?: JobStatus;
  job_type?: string;
  limit?: number;
  offset?: number;
}

// ─── Stats ────────────────────────────────────────────────
export interface AppStats {
  total_videos: number;
  total_genres: number;
  active_jobs: number;
  failed_jobs: number;
}

// ─── Batch ────────────────────────────────────────────────
export interface BatchActionResponse {
  job_id: number;
  message: string;
  locked_skipped?: number;
}

export interface BatchDeleteRequest {
  video_ids: number[];
}

export interface BatchDeleteResponse {
  deleted: number[];
  errors: number[];
  count: number;
}

// ─── Matching / Resolve ──────────────────────────────────
export type MatchStatus =
  | "matched_high"
  | "matched_medium"
  | "needs_review"
  | "unmatched";

export interface ScoreBreakdown {
  features: Record<string, number>;
  weighted_contributions: Record<string, number>;
  category_scores: Record<string, number>;
  overall_score: number;
  status: string;
}

export interface MatchCandidate {
  entity_type: string;
  mbid: string | null;
  canonical_name: string;
  provider: string;
  score: number;
  breakdown: Record<string, unknown> | null;
  is_selected: boolean;
}

export interface MatchResult {
  video_id: number;
  resolved_artist: string;
  artist_mbid: string | null;
  resolved_recording: string;
  recording_mbid: string | null;
  resolved_release: string | null;
  release_mbid: string | null;
  confidence_overall: number;
  confidence_breakdown: Record<string, unknown> | null;
  status: MatchStatus;
  candidate_list: MatchCandidate[];
  normalization_notes: Record<string, unknown> | null;
  changed: boolean;
  is_user_pinned: boolean;
}

export interface NormalizationResult {
  raw_artist: string;
  raw_title: string;
  raw_album: string | null;
  artist_display: string;
  artist_key: string;
  primary_artist: string;
  featured_artists: string[] | null;
  title_display: string;
  title_key: string;
  title_base: string;
  qualifiers: string[] | null;
  album_display: string | null;
  album_key: string | null;
  normalization_notes: Record<string, unknown> | null;
}

// ─── Review Queue ────────────────────────────────────────
export type ReviewStatus = "none" | "needs_human_review" | "needs_ai_review" | "reviewed";

export interface DuplicateVideoSummary {
  video_id: number;
  artist: string;
  title: string;
  version_type: string;
  thumbnail_url: string | null;
  resolution_label: string | null;
  file_size_bytes: number | null;
  duration_seconds: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  video_bitrate: number | null;
  audio_bitrate: number | null;
  fps: number | null;
  hdr: boolean;
  container: string | null;
  import_method: string | null;
  quality_score: number;
}

export interface ReviewItem {
  video_id: number;
  artist: string;
  title: string;
  filename: string | null;
  thumbnail_url: string | null;
  review_status: ReviewStatus;
  review_category: string | null;
  resolved_artist: string;
  resolved_recording: string;
  confidence_overall: number;
  status: MatchStatus;
  is_user_pinned: boolean;
  top_candidate: MatchCandidate | null;
  candidate_count: number;
  version_type?: string;
  review_reason?: string | null;
  updated_at: string | null;
  resolution_label?: string | null;
  file_size_bytes?: number | null;
  import_method?: string | null;
  related_versions?: { id: number; version_type: string; label?: string }[] | null;
  // Quality fields
  duration_seconds?: number | null;
  video_codec?: string | null;
  audio_codec?: string | null;
  video_bitrate?: number | null;
  audio_bitrate?: number | null;
  fps?: number | null;
  hdr?: boolean;
  container?: string | null;
  quality_score?: number;
  // Duplicate comparison
  duplicate_of?: DuplicateVideoSummary | null;
  dup_group_key?: string | null;
  // Rename info
  expected_path?: string | null;
}

export interface ReviewListResponse {
  items: ReviewItem[];
  total: number;
  page: number;
  page_size: number;
  category_counts: Record<string, number>;
}

export interface ReviewParams {
  status?: ReviewStatus | null;
  category?: string | null;
  q?: string;
  sort?: "updated_desc" | "title_asc" | "status_asc";
  page?: number;
  page_size?: number;
}

// ─── Manual Search ───────────────────────────────────────
export type SearchEntityType = "artist" | "recording" | "release";

export interface ManualSearchResult {
  mbid: string;
  name: string;
  disambiguation: string | null;
  score: number;
  extra: Record<string, unknown> | null;
}

export interface ManualSearchResponse {
  query: string;
  entity_type: SearchEntityType;
  results: ManualSearchResult[];
}

// ─── Export ──────────────────────────────────────────────
export interface ExportKodiRequest {
  video_ids?: number[] | null;
  overwrite_existing?: boolean;
}

export interface ExportKodiResponse {
  exported: number;
  skipped: number;
  errors: number;
  message: string;
}

// ─── Resolve Requests ────────────────────────────────────
export interface PinRequest {
  candidate_id: number;
}

export interface ApplyRequest {
  candidate_id: number;
}

export interface BatchResolveRequest {
  video_ids?: number[] | null;
  filter?: "missing" | "low_confidence" | "needs_review" | "all" | null;
  force?: boolean;
}

export interface BatchResolveResponse {
  job_id: number;
  message: string;
  video_count: number;
}

// ─── AI Metadata Enrichment ──────────────────────────────

export const ALL_ENRICHABLE_FIELDS = [
  "artist", "title", "album", "year", "genres", "plot",
  "director", "studio", "actors", "tags",
] as const;
export type EnrichableField = (typeof ALL_ENRICHABLE_FIELDS)[number];

export interface AIEnrichRequest {
  provider?: string | null;
  auto_apply?: boolean;
  force?: boolean;
  fields?: string[] | null;
  run_fingerprint?: boolean;
  skip_mismatch_check?: boolean;
  review_description_only?: boolean;
}

export interface AIFieldComparison {
  field: string;
  scraped_value: unknown;
  ai_value: unknown;
  ai_confidence: number;
  changed: boolean;
  accepted: boolean;
  locked: boolean;
}

export interface MismatchSignal {
  name: string;
  score: number;
  weight: number;
  details?: string | null;
}

export interface MismatchReport {
  overall_score: number;
  is_suspicious: boolean;
  signals: MismatchSignal[];
  ai_identity?: AIIdentityVerification | null;
  ai_mismatch?: AIMismatchInfo | null;
}

export interface AIIdentityVerification {
  candidate_artist?: string | null;
  candidate_title?: string | null;
  evidence?: {
    filename_match?: boolean;
    url_match?: boolean;
    metadata_consistent?: boolean;
    known_song?: boolean;
  } | null;
}

export interface AIMismatchInfo {
  is_mismatch?: boolean;
  severity?: "none" | "low" | "medium" | "high";
  reasons?: string[];
}

export interface FingerprintMatch {
  artist?: string | null;
  title?: string | null;
  album?: string | null;
  year?: number | null;
  mb_recording_id?: string | null;
  confidence: number;
}

export interface FingerprintResult {
  fpcalc_available: boolean;
  match_count: number;
  best_match?: FingerprintMatch | null;
  matches: FingerprintMatch[];
  error?: string | null;
}

export interface ArtworkUpdate {
  asset_type: string;
  proposed_asset_id?: number | null;
  proposed_source_url?: string | null;
  current_asset_id?: number | null;
  current_source_url?: string | null;
  provenance?: string | null;
  width?: number | null;
  height?: number | null;
  unchanged?: boolean;
}

export interface SourceUpdate {
  provider: string;
  source_type?: string | null;
  original_url: string;
  provenance?: string | null;
  pending?: boolean;
}

export interface AIComparisonResponse {
  video_id: number;
  scraped: Record<string, unknown>;
  ai: Record<string, unknown> | null;
  ai_result_id: number | null;
  provider?: string | null;
  model?: string | null;
  overall_confidence?: number | null;
  status?: string | null;
  created_at?: string | null;
  fields: AIFieldComparison[];
  mismatch_report?: MismatchReport | Record<string, unknown> | null;
  fingerprint_result?: FingerprintResult | null;
  change_summary?: string | null;
  verification_status?: boolean | null;
  artwork_updates?: ArtworkUpdate[];
  source_updates?: SourceUpdate[];
}

export interface AIMetadataResultOut {
  id: number;
  video_id: number;
  provider: string;
  model_name?: string | null;
  model_task?: string | null;
  status: string;
  ai_artist?: string | null;
  ai_title?: string | null;
  ai_album?: string | null;
  ai_year?: number | null;
  ai_plot?: string | null;
  ai_genres?: string[] | null;
  ai_director?: string | null;
  ai_studio?: string | null;
  ai_actors?: Array<{ name: string; role?: string }> | null;
  ai_tags?: string[] | null;
  confidence_score: number;
  field_scores?: Record<string, number> | null;
  accepted_fields?: string[] | null;
  verification_status?: boolean | null;
  requested_fields?: string[] | null;
  mismatch_score?: number | null;
  mismatch_signals?: Record<string, unknown> | null;
  fingerprint_result?: Record<string, unknown> | null;
  change_summary?: string | null;
  tokens_used?: number | null;
  error_message?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
  dismissed_at?: string | null;
}

export interface AIApplyFieldsRequest {
  ai_result_id: number;
  fields: string[];
  rename_files?: boolean;
}

export interface AIUndoRequest {
  ai_result_id: number;
}

export interface AITestConnectionRequest {
  provider: string;
  api_key?: string | null;
  model?: string | null;
  base_url?: string | null;
}

export interface AITestConnectionResponse {
  success: boolean;
  provider: string;
  model_name?: string | null;
  message: string;
  tokens_used?: number | null;
  response_time_ms?: number | null;
}

// ─── Model Catalog ───────────────────────────────────────

export interface ModelInfo {
  id: string;
  label: string;
  tier: "fast" | "standard" | "high";
  capabilities: string[];
  recommended_for: string[];
}

export interface ModelCatalog {
  provider: string;
  models: ModelInfo[];
  defaults: {
    manual_default: string;
    auto_tiers: Record<string, string>;
  };
  updated_at: string;
}

export interface RoutingPreviewEntry {
  task: string;
  model_id: string;
  model_label: string;
  reason: string;
}

export interface RoutingPreview {
  provider: string;
  mode: string;
  entries: RoutingPreviewEntry[];
}

export interface ModelAvailabilityEntry {
  model_id: string;
  available: boolean;
  error: string;
  response_time_ms: number;
}

export interface ModelAvailabilityOut {
  provider: string;
  results: ModelAvailabilityEntry[];
  cached: boolean;
  tested_at: string;
}

export interface AIThumbnailOut {
  id: number;
  video_id: number;
  timestamp_sec: number;
  file_path: string;
  score_sharpness: number;
  score_contrast: number;
  score_color_variance: number;
  score_composition: number;
  score_overall: number;
  is_selected: boolean;
  provenance: string;
}

export interface SceneAnalysisOut {
  id: number;
  video_id: number;
  status: string;
  total_scenes: number;
  duration_seconds?: number | null;
  scenes?: Array<{ start: number; end: number; index: number }> | null;
  thumbnails: AIThumbnailOut[];
  error_message?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
}

export interface SceneAnalysisRequest {
  threshold?: number;
  max_thumbnails?: number;
  force?: boolean;
}

export interface AISettingsOut {
  provider: string;
  openai_api_key_set: boolean;
  gemini_api_key_set: boolean;
  claude_api_key_set: boolean;
  local_llm_base_url: string;
  local_llm_model: string;
  auto_enrich_on_import: boolean;
  auto_scene_analysis: boolean;
  auto_apply_threshold: number;
  model_selection_mode: string;
  model_default?: string | null;
  model_fallback?: string | null;
  model_metadata?: string | null;
  model_verification?: string | null;
  model_scene?: string | null;
  auto_tier_preference: string;
  enrichable_fields?: string[] | null;
  rename_on_metadata_update: boolean;
  scene_analysis_mode: string;
  acoustid_api_key_set: boolean;
}

export interface AISettingsUpdate {
  provider?: string;
  openai_api_key?: string;
  gemini_api_key?: string;
  claude_api_key?: string;
  local_llm_base_url?: string;
  local_llm_model?: string;
  auto_enrich_on_import?: boolean;
  auto_scene_analysis?: boolean;
  auto_apply_threshold?: number;
  model_selection_mode?: string;
  model_default?: string;
  model_fallback?: string;
  model_metadata?: string;
  model_verification?: string;
  model_scene?: string;
  auto_tier_preference?: string;
  enrichable_fields?: string[];
  rename_on_metadata_update?: boolean;
  scene_analysis_mode?: string;
  acoustid_api_key?: string;
}

// ─── AI Prompt Settings ─────────────────────────────────

export interface AIPromptSettingsOut {
  system_prompt: string;
  enrichment_prompt: string;
  review_prompt: string;
  is_default_system: boolean;
  is_default_enrichment: boolean;
  is_default_review: boolean;
}

export interface AIPromptSettingsUpdate {
  system_prompt?: string;
  enrichment_prompt?: string;
  review_prompt?: string;
}

// ─── Canonical Track Identity ────────────────────────────

export interface ProcessingStateEntry {
  completed: boolean;
  timestamp?: string | null;
  method?: string | null;
  version?: string | null;
}

export interface ProcessingState {
  imported: ProcessingStateEntry;
  downloaded: ProcessingStateEntry;
  metadata_resolved: ProcessingStateEntry;
  metadata_scraped: ProcessingStateEntry;
  metadata_ai_analyzed: ProcessingStateEntry;
  canonical_linked: ProcessingStateEntry;
  description_generated: ProcessingStateEntry;
  filename_checked: ProcessingStateEntry;
  file_organized: ProcessingStateEntry;
  nfo_exported: ProcessingStateEntry;
  xml_exported: ProcessingStateEntry;
  thumbnail_selected: ProcessingStateEntry;
  ai_enriched: ProcessingStateEntry;
  scenes_analyzed: ProcessingStateEntry;
  audio_normalized: ProcessingStateEntry;
  track_identified: ProcessingStateEntry;
  artwork_fetched: ProcessingStateEntry;
  [key: string]: ProcessingStateEntry;
}

export interface ArtistEntity {
  id: number;
  name: string;
  mb_artist_id?: string | null;
  origin?: string | null;
  artist_image?: string | null;
}

export interface CanonicalTrack {
  id: number;
  artist_id?: number | null;
  artist_name?: string | null;
  album_name?: string | null;
  title: string;
  year?: number | null;
  mb_recording_id?: string | null;
  mb_release_id?: string | null;
  mb_release_group_id?: string | null;
  mb_artist_id?: string | null;
  mb_track_id?: string | null;
  acoustid_id?: string | null;
  artwork_album?: string | null;
  artwork_single?: string | null;
  canonical_verified: boolean;
  metadata_source?: string | null;
  ai_verified: boolean;
  ai_verified_at?: string | null;
  is_cover: boolean;
  original_artist?: string | null;
  original_title?: string | null;
  video_count: number;
  linked_videos: { id: number; artist: string; title: string; resolution_label?: string | null; version_type?: string }[];
  genres: { id: number; name: string }[];
}

// ─── Library Import ──────────────────────────────────────

export interface LibraryImportScannedItem {
  file_path: string;
  folder_path: string;
  folder_name: string;
  filename: string;
  file_size_bytes: number;
  artist?: string | null;
  title?: string | null;
  album?: string | null;
  year?: number | null;
  genres: string[];
  plot?: string | null;
  resolution?: string | null;
  source_url?: string | null;
  duration_seconds?: number | null;
  has_nfo: boolean;
  has_poster: boolean;
  has_thumb: boolean;
  metadata_source: string;
  already_exists: boolean;
  existing_video_id?: number | null;
}

export interface LibraryImportScanResponse {
  total_found: number;
  items: LibraryImportScannedItem[];
  already_in_library: number;
  new_items: number;
  scan_is_library: boolean;
}

export interface LibraryImportScanRequest {
  directory: string;
  recursive: boolean;
  custom_regex?: string | null;
}

export interface LibraryImportOptions {
  mode: "simple" | "advanced";
  file_handling: "copy" | "move" | "copy_to" | "move_to" | "in_place";
  custom_destination?: string | null;
  normalize_audio: boolean;
  find_source_video: boolean;
  source_match_duration: boolean;
  source_match_min_confidence: number;
  review_mode: "basic" | "advanced" | "skip";
  critical_fields: string[];
  confidence_threshold: number;
  custom_regex?: string | null;
  scrape_wikipedia: boolean;
  scrape_musicbrainz: boolean;
  scrape_tmvdb: boolean;
  ai_auto_analyse: boolean;
  ai_auto_fallback: boolean;
}

export interface LibraryImportStartRequest {
  directory: string;
  items: string[];
  options: LibraryImportOptions;
  duplicate_actions?: Record<string, DuplicateAction>;
}

export interface DuplicateAction {
  action: "skip" | "overwrite" | "keep_both" | "review_later";
  version_type?: string | null;
}

export interface ExistingVideoDetail {
  id: number;
  artist?: string | null;
  title?: string | null;
  album?: string | null;
  year?: number | null;
  resolution_label?: string | null;
  version_type?: string | null;
  file_path?: string | null;
  file_size_bytes?: number | null;
  has_poster: boolean;
  has_thumb: boolean;
  song_rating?: number | null;
  video_rating?: number | null;
  created_at?: string | null;
}

export interface ExistingDetailsRequest {
  video_ids: number[];
}

export interface ExistingDetailsResponse {
  videos: Record<number, ExistingVideoDetail>;
}

export interface LibraryImportStartResponse {
  job_id: number;
  total_items: number;
  message: string;
}

export interface RegexPreviewRequest {
  pattern: string;
  filenames: string[];
}

export interface RegexPreviewResult {
  filename: string;
  matched: boolean;
  artist?: string | null;
  title?: string | null;
  year?: number | null;
  resolution?: string | null;
}

export interface RegexPreviewResponse {
  results: RegexPreviewResult[];
  match_count: number;
  total: number;
}

// ─── Playlists ───────────────────────────────────────────

export interface PlaylistEntry {
  id: number;
  video_id: number;
  position: number;
  artist: string;
  title: string;
  has_poster: boolean;
  duration_seconds?: number | null;
}

export interface PlaylistOut {
  id: number;
  name: string;
  description?: string | null;
  entry_count: number;
  created_at: string;
  updated_at: string;
  entries: PlaylistEntry[];
}

export interface PlaylistSummary {
  id: number;
  name: string;
  description?: string | null;
  entry_count: number;
  created_at: string;
  updated_at: string;
}

// ─── Video Editor ────────────────────────────────────────

export interface EditorQueueItem {
  video_id: number;
  artist: string;
  title: string;
  album?: string | null;
  file_path?: string | null;
  resolution_label?: string | null;
  width?: number | null;
  height?: number | null;
  duration_seconds?: number | null;
  video_codec?: string | null;
  video_bitrate?: number | null;
  fps?: number | null;
  audio_codec?: string | null;
  audio_bitrate?: number | null;
  audio_channels?: number | null;
  letterbox_detected: boolean;
  crop_w?: number | null;
  crop_h?: number | null;
  crop_x?: number | null;
  crop_y?: number | null;
  bar_top: number;
  bar_bottom: number;
  bar_left: number;
  bar_right: number;
  has_archive: boolean;
  exclude_from_scan: boolean;
  created_at?: string | null;
}

export interface CropPreviewRequest {
  video_id: number;
  ratio?: string;
  custom_ratio_w?: number;
  custom_ratio_h?: number;
  crop_w?: number;
  crop_h?: number;
  crop_x?: number;
  crop_y?: number;
}

export interface CropPreviewResponse {
  video_id: number;
  original_w: number;
  original_h: number;
  crop_w: number;
  crop_h: number;
  crop_x: number;
  crop_y: number;
  effective_ratio: string;
}

export interface EncodeRequest {
  video_id: number;
  crop_w?: number;
  crop_h?: number;
  crop_x?: number;
  crop_y?: number;
  target_dar?: string;
  crf: number;
  preset: string;
  audio_passthrough: boolean;
  trim_start?: number;
  trim_end?: number;
  audio_codec?: string;
  audio_bitrate?: string;
}

export interface LetterboxDetectResult {
  video_id: number;
  detected: boolean;
  original_w?: number;
  original_h?: number;
  crop_w?: number;
  crop_h?: number;
  crop_x?: number;
  crop_y?: number;
  bar_top: number;
  bar_bottom: number;
  bar_left: number;
  bar_right: number;
}

export interface LetterboxScanItem {
  video_id: number;
  artist: string;
  title: string;
  file_path?: string;
  original_w: number;
  original_h: number;
  crop_w: number;
  crop_h: number;
  crop_x: number;
  crop_y: number;
  bar_top: number;
  bar_bottom: number;
  bar_left: number;
  bar_right: number;
}

// ─── Scraper Test ─────────────────────────────────────────
export interface ScraperTestRequest {
  url: string;
  artist_override?: string;
  title_override?: string;
  scrape_wikipedia: boolean;
  scrape_musicbrainz: boolean;
  wikipedia_url?: string;
  musicbrainz_url?: string;
  ai_auto: boolean;
  ai_only: boolean;
}

export interface ImportTestRequest {
  directory: string;
  file_name?: string;
  artist_override?: string;
  title_override?: string;
  scrape_wikipedia: boolean;
  scrape_musicbrainz: boolean;
  wikipedia_url?: string;
  musicbrainz_url?: string;
  ai_auto: boolean;
  ai_only: boolean;
}

export interface DirectoryScanResult {
  directory: string;
  video_files: string[];
  nfo_files: string[];
  has_multiple: boolean;
}

export interface ProvenanceField {
  value: any;
  source: string;
}

export interface ArtworkCandidate {
  url: string;
  source: string;
  art_type: string; // "artist", "album", "poster", "fanart"
  applied: boolean;
}

export interface BeforeAfterField {
  field: string;
  before: any;
  after: any;
  source: string;
}

export interface ScraperTestResult {
  url: string;
  canonical_url: string;
  provider: string;
  video_id: string;
  ytdlp_title?: string;
  ytdlp_uploader?: string;
  ytdlp_channel?: string;
  ytdlp_artist?: string;
  ytdlp_track?: string;
  ytdlp_album?: string;
  ytdlp_duration?: number;
  ytdlp_upload_date?: string;
  ytdlp_thumbnail?: string;
  ytdlp_description?: string;
  ytdlp_tags: string[];
  parsed_artist: string;
  parsed_title: string;
  artist: ProvenanceField;
  title: ProvenanceField;
  album: ProvenanceField;
  year: ProvenanceField;
  genres: ProvenanceField;
  plot: ProvenanceField;
  image_url: ProvenanceField;
  mb_artist_id: ProvenanceField;
  mb_recording_id: ProvenanceField;
  mb_release_id: ProvenanceField;
  mb_release_group_id: ProvenanceField;
  imdb_url: ProvenanceField;
  source_urls: Record<string, string>;
  artwork_candidates: ArtworkCandidate[];
  ai_changes: BeforeAfterField[];
  scraper_sources_used: string[];
  pipeline_log: string[];
  pipeline_failures: { code: string; description: string }[];
  mode: string;
  ai_source_resolution?: Record<string, any> | null;
  ai_final_review?: Record<string, any> | null;
  // Import-mode fields
  import_directory?: string | null;
  import_file?: string | null;
  import_identity_source?: string | null;
  import_nfo_found?: boolean | null;
  import_youtube_match?: Record<string, any> | null;
  import_quality?: Record<string, any> | null;
  output_file?: string | null;
}

export interface ScraperTestProgress {
  step: number;
  total: number;
  label: string;
  status: "running" | "complete";
  sub_label?: string;
  elapsed_ms: number;
}

// ─── New Videos / Discovery ───────────────────────────────
export type NewVideoCategory = "new" | "popular" | "rising" | "by_artist" | "taste" | "famous";

export interface SuggestedVideoItem {
  id: number;
  provider: string;
  provider_video_id: string;
  url: string;
  title: string;
  artist: string | null;
  album: string | null;
  channel: string | null;
  thumbnail_url: string | null;
  duration_seconds: number | null;
  release_date: string | null;
  view_count: number | null;
  category: NewVideoCategory;
  source_type: string | null;
  trust_score: number;
  popularity_score: number;
  trend_score: number;
  recommendation_score: number;
  reasons: string[];
  trust_reasons: string[];
  in_cart: boolean;
}

export interface NewVideosCategoryData {
  videos: SuggestedVideoItem[];
  generated_at: string | null;
  expires_at: string | null;
}

export interface NewVideosFeed {
  categories: Record<NewVideoCategory, NewVideosCategoryData>;
  cart_count: number;
}

export interface CartItem {
  id: number;
  suggested_video_id: number;
  url: string;
  title: string | null;
  artist: string | null;
  provider: string | null;
  provider_video_id: string | null;
  added_at: string | null;
}

export interface CartResponse {
  items: CartItem[];
  count: number;
}

export interface NewVideosSettings {
  nv_enabled: boolean;
  nv_videos_per_category: number;
  nv_refresh_interval_minutes: number;
  nv_auto_refresh_on_startup: boolean;
  nv_include_temp_dismissed_after_refresh: boolean;
  nv_enable_ai_ranking: boolean;
  nv_enable_trusted_source_filtering: boolean;
  nv_min_trust_threshold: number;
  nv_allow_unofficial_fallback: boolean;
  nv_preferred_providers: string;
  nv_min_owned_for_artist_rec: number;
  nv_max_recs_per_artist: number;
  nv_use_ratings: boolean;
  nv_use_genre_similarity: boolean;
  nv_use_artist_similarity: boolean;
  nv_persist_cart: boolean;
  nv_auto_clear_cart: boolean;
  nv_famous_count: number;
  nv_popular_count: number;
  nv_rising_count: number;
  nv_new_count: number;
}
