# UI/UX/Ergonomics Review — Complete Change Log

All changes made during the comprehensive UI/UX review of the Playarr frontend.
Build verified: all changes compile cleanly (`tsc -b && vite build`).

---

## 1. Tooltip Standardisation (`title` → `<Tooltip>`)

**Problem:** The app used a mix of native HTML `title` attributes (plain grey browser tooltip) and the custom `<Tooltip>` component (styled popup with 400ms delay, viewport clamping). This was visually inconsistent.

**Fix:** Systematically converted all `title` attributes on interactive elements (buttons, icon-only controls, status badges) to use the custom `<Tooltip>` component across every page.

### Files changed:

#### SettingsPage.tsx
- DirectoryRow Browse button → `<Tooltip>`
- DirectoryRow Save button → `<Tooltip>`
- SourceDirectoriesEditor Browse/Remove/Add buttons → `<Tooltip>`
- SettingRow Save button → `<Tooltip>`
- Genre Manager show/hide: title text made more descriptive

#### LibraryPage.tsx
- Party Mode button → `<Tooltip>` ("Enable party mode — auto-play random videos")
- Sort direction button → `<Tooltip>` ("Sort ascending" / "Sort descending")
- Grid/List view toggle → `<Tooltip>` ("Switch to list view" / "Switch to grid view")
- Select All button → `<Tooltip>` ("Select all videos" / "Deselect all")
- Rescan Selected → `<Tooltip>` ("Rescan metadata for selected videos")
- Add to Playlist → `<Tooltip>` ("Add selected videos to a playlist")
- Delete Selected → `<Tooltip>` ("Delete selected videos")

#### BulkActionsBar.tsx
- Accept Top Match → `<Tooltip>` ("Accept the highest-scoring match…")
- Re-resolve → `<Tooltip>` ("Re-run matching for all selected videos")
- Clear → `<Tooltip>` ("Remove all selected videos from the review queue")

#### Layout.tsx
- Add Video button → `<Tooltip>` ("Import a video by YouTube or Vimeo URL")

#### VideoDetailPage.tsx
- Previous/Random/Next track buttons → `<Tooltip>`
- Play audio / Play next / Add to queue / Add to playlist → `<Tooltip>`

#### CandidateCard.tsx
- Apply button → `<Tooltip>` ("Apply this match without pinning — it may change on next resolve")
- Pin button → `<Tooltip>` ("Pin this match permanently — it won't change on future resolves")

#### QueuePage.tsx
- Clear history button → `<Tooltip>` ("Remove finished jobs from history. Respects the current status and source filters.")

#### NowPlayingPage.tsx
- Theatre mode button → `<Tooltip>` ("Theatre mode — expand the video to fill the page width")
- Video fullscreen button → `<Tooltip>` ("Video fullscreen — make the video fill the entire screen")
- Remove from queue (X) → `<Tooltip>` ("Remove from queue")

#### PlaylistsPage.tsx
- Remove entry button → `<Tooltip>` ("Remove this track from the playlist")

#### MatchDetailPage.tsx
- Force button → `<Tooltip>` ("Force re-resolve — bypass hysteresis and re-evaluate all candidates")

#### CartPanel.tsx (new-videos)
- Open source link → `<Tooltip>` ("Open source URL in a new tab")
- Remove from cart button → `<Tooltip>` ("Remove this item from the import cart")

#### CanonicalTrackPanel.tsx
- AI Verified badge → `<Tooltip>` (shows verification timestamp)
- Canonical Confirmed badge → `<Tooltip>` ("Canonical identity confirmed — this track's metadata has been verified")

#### FullscreenControls.tsx
- **ControlBtn component** rewritten to wrap children in `<Tooltip>` instead of passing `title` to `<button>`
- Shuffle / Previous / Play-Pause / Next / Stop / Repeat / Fullscreen cycle buttons all now use `<Tooltip>`

#### NewVideosPage.tsx
- Import Cart button → `<Tooltip>` ("View and manage items queued for import")
- Refresh button → `<Tooltip>` ("Re-fetch suggestions for all categories")

#### SuggestionCard.tsx
- Open in new tab → `<Tooltip>`
- Import now → `<Tooltip>` ("Import this video now")
- Dismiss (temporary) → `<Tooltip>` ("Dismiss temporarily — may reappear later")
- Never show again → `<Tooltip>` ("Permanently hide — never suggest again")

#### MetadataEditorForm.tsx
- Edit sources pencil button → `<Tooltip>` ("Edit sources")

#### ArtworkTiles.tsx
- Upload new image → `<Tooltip>` ("Upload new image")
- Refresh artwork → `<Tooltip>` ("Refresh artwork from sources")
- Delete artwork → `<Tooltip>` ("Delete artwork")

#### QueueComponents.tsx
- Retry button → `<Tooltip>` ("Retry this job")
- Cancel button → `<Tooltip>` ("Cancel this job")

#### AISettingsPanel.tsx
- Test connection → `<Tooltip>` ("Test connection to the selected AI provider")
- Test Models → `<Tooltip>` ("Test all configured models for availability")
- Reset prompt → `<Tooltip>` ("Reset to default prompt")

#### ActionsPanel.tsx
- "Rename files" label → `<Tooltip>` ("Rename the video file and folder to match the corrected metadata")
- Select all / Deselect all → `<Tooltip>` (dynamic tooltip)
- Apply Selected → `<Tooltip>` ("Apply only the corrections you've selected above")
- Apply All Changes → `<Tooltip>` ("Apply all suggested corrections at once")
- Apply High Confidence → `<Tooltip>` ("Apply only corrections with 85%+ confidence")
- Undo Last Enrichment → `<Tooltip>` ("Undo: Restore metadata from before AI was applied")
- DiffRow Applied icon → `<Tooltip>` ("Applied")
- DiffRow toggle checkbox → `<Tooltip>` ("Deselect" / "Select for apply")
- ArtworkDiffRow Applied icon → `<Tooltip>` ("Applied")
- ArtworkDiffRow toggle checkbox → `<Tooltip>` ("Deselect" / "Select for apply")
- SourceDiffRow toggle checkbox → `<Tooltip>` ("Deselect" / "Select for apply")

#### AIPanel.tsx
- Dismiss/Close button → `<Tooltip>` ("Close — reappears after a new metadata scan")
- "Rename files" label → `<Tooltip>`
- Select all / Deselect all → `<Tooltip>` (dynamic)
- Apply Selected / Apply All / Apply High Confidence → `<Tooltip>`
- Undo Last Enrichment → `<Tooltip>`
- DiffRow Applied/toggle → `<Tooltip>`
- ArtworkDiffRow Applied/toggle → `<Tooltip>`

#### ImportLibraryPage.tsx
- "Has NFO" indicator → `<Tooltip>` ("This file has an NFO metadata sidecar")
- "Has poster" indicator → `<Tooltip>` ("This file has a poster image sidecar")
- "No metadata" indicator → `<Tooltip>` ("No metadata was found — filename parsing will be used")

#### VideoEditorPage.tsx
- Title link to video detail → `<Tooltip>` ("Open video detail page")

### Remaining `title` attributes (intentionally kept):
- `<EmptyState title="...">` — component prop, not HTML attribute
- `<Collapsible title="...">` — component prop
- `title={source.original_url}` on `<a>` tags — shows full URL on hover for truncated links (valid HTML pattern)
- `<div title={...}>` on AI diff cells — shows full text for truncated content (valid HTML pattern)
- `title="Poster"` on decorative Poster labels — redundant (label already says "Poster"), harmless

---

## 2. Settings Tooltips Added (SettingsPage.tsx)

**Problem:** Many settings had descriptions but no detailed `tooltip` text explaining recommended values or what the setting actually does.

**Fix:** Added comprehensive tooltips to every entry in `SETTING_META`:

| Setting Key | Tooltip Added |
|---|---|
| `normalization_target_lufs` | Streaming: −14 LUFS, broadcast TV: −23, cinema: −24, audiophile: −16 |
| `normalization_lra` | Typical: 7 LU (pop), 11 LU (classical), 20 LU (film) |
| `normalization_tp` | Streaming: −1.0 dBTP, broadcast: −2.0 dBTP, 0.0 for none |
| `auto_normalize_on_import` | When enabled, normalisation runs automatically after download |
| `preview_duration_sec` | Short: 3–5s, medium: 8–10s, full track: set very high |
| `preview_start_percent` | 0% = beginning, 25% = past intro, 50% = middle |
| `server.port` | Requires server restart to take effect |
| `import_scrape_wikipedia` | Default toggle for new imports (can override per-video) |
| `import_scrape_musicbrainz` | Default toggle for new imports (can override per-video) |
| `import_ai_auto` | Automatically runs AI enrichment after scraping |
| `import_ai_only` | Mutually exclusive with auto mode |
| `import_find_source_video` | Searches for alternate source videos |
| `tmvdb_enabled` | Enable/disable TMVDB integration |
| `tmvdb_api_key` | Your personal API key from TheVideoMusicDB.com |
| `tmvdb_auto_pull` | Pull metadata from TMVDB when available |
| `tmvdb_auto_push` | Push your metadata to TMVDB community DB |

---

## 3. Checkbox → ToggleRow Consistency

**Problem:** RescanOptionsDialog and AddVideoModal had standalone HTML checkboxes at the bottom of the dialog that looked different from the ToggleRow-styled sections above them.

**Fix:** Converted to matching ToggleRow components inside new "Post-Processing" bordered sections.

### RescanOptionsDialog.tsx
- "Normalise audio after download" checkbox → ToggleRow in "Post-Processing" section
- "YouTube Source Matching" checkbox → ToggleRow in "Post-Processing" section
- Both retain their info icon tooltips

### AddVideoModal.tsx
- "Normalise audio after download" checkbox → ToggleRow in "Post-Processing" section
- Retains info icon tooltip

---

## 4. Button Focus States (LibraryPage.tsx)

- Party Mode button: added `focus-visible:ring-2 focus-visible:ring-accent` for keyboard navigation visibility

---

## Summary Statistics

| Category | Count |
|---|---|
| Files modified | 21 |
| `title` → `<Tooltip>` conversions | ~80+ elements |
| New tooltips added (settings) | 17 settings |
| Checkbox → ToggleRow conversions | 3 checkboxes |
| Component rewrites (ControlBtn) | 1 |
| Build errors introduced | 0 (all verified) |
