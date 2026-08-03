import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@/components/Toast";
import { Layout } from "@/components/Layout";
import { RouteErrorBoundary } from "@/components/RouteErrorBoundary";

const LibraryPage = lazy(() => import("@/pages/LibraryPage").then(module => ({ default: module.LibraryPage })));
const ArtistsPage = lazy(() => import("@/pages/ArtistsPage").then(module => ({ default: module.ArtistsPage })));
const YearsPage = lazy(() => import("@/pages/YearsPage").then(module => ({ default: module.YearsPage })));
const GenresPage = lazy(() => import("@/pages/GenresPage").then(module => ({ default: module.GenresPage })));
const AlbumsPage = lazy(() => import("@/pages/AlbumsPage").then(module => ({ default: module.AlbumsPage })));
const RatingsPage = lazy(() => import("@/pages/RatingsPage").then(module => ({ default: module.RatingsPage })));
const PlaylistsPage = lazy(() => import("@/pages/PlaylistsPage").then(module => ({ default: module.PlaylistsPage })));
const VideoDetailPage = lazy(() => import("@/pages/VideoDetailPage").then(module => ({ default: module.VideoDetailPage })));
const QueuePage = lazy(() => import("@/pages/QueuePage").then(module => ({ default: module.QueuePage })));
const SettingsPage = lazy(() => import("@/pages/SettingsPage").then(module => ({ default: module.SettingsPage })));
const ReviewQueuePage = lazy(() => import("@/pages/ReviewQueuePage"));
const MatchDetailPage = lazy(() => import("@/pages/MatchDetailPage"));
const ImportLibraryPage = lazy(() => import("@/pages/ImportLibraryPage").then(module => ({ default: module.ImportLibraryPage })));
const NowPlayingPage = lazy(() => import("@/pages/NowPlayingPage").then(module => ({ default: module.NowPlayingPage })));
const TvModePage = lazy(() => import("@/pages/TvModePage").then(module => ({ default: module.TvModePage })));
const CastModePage = lazy(() => import("@/pages/CastModePage").then(module => ({ default: module.CastModePage })));
const VideoEditorPage = lazy(() => import("@/pages/VideoEditorPage").then(module => ({ default: module.VideoEditorPage })));
const ScraperTesterPage = lazy(() => import("@/pages/ScraperTesterPage").then(module => ({ default: module.ScraperTesterPage })));
const NewVideosPage = lazy(() => import("@/pages/NewVideosPage").then(module => ({ default: module.NewVideosPage })));
const QualityPage = lazy(() => import("@/pages/QualityPage").then(module => ({ default: module.QualityPage })));
const ArchivePage = lazy(() => import("@/pages/ArchivePage").then(module => ({ default: module.ArchivePage })));
const MetadataManagerPage = lazy(() => import("@/pages/MetadataManagerPage").then(module => ({ default: module.MetadataManagerPage })));

function page(element: ReactNode) {
  return <RouteErrorBoundary><Suspense fallback={<div className="p-8 text-sm text-text-muted">Loading page…</div>}>{element}</Suspense></RouteErrorBoundary>;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function PreferenceRefreshBoundary({ children }: { children: ReactNode }) {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const refresh = () => setRevision(value => value + 1);
    globalThis.addEventListener("playarr:preference-changed", refresh);
    return () => globalThis.removeEventListener("playarr:preference-changed", refresh);
  }, []);
  return <div key={revision} className="contents">{children}</div>;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <PreferenceRefreshBoundary><Routes>
            <Route element={<Layout />}>
              <Route index element={<Navigate to="/library" replace />} />
              <Route path="library" element={page(<LibraryPage />)} />
              <Route path="artists" element={page(<ArtistsPage />)} />
              <Route path="years" element={page(<YearsPage />)} />
              <Route path="genres" element={page(<GenresPage />)} />
              <Route path="albums" element={page(<AlbumsPage />)} />
              <Route path="ratings" element={page(<RatingsPage />)} />
              <Route path="quality" element={page(<QualityPage />)} />
              <Route path="playlists" element={page(<PlaylistsPage />)} />
              <Route path="video/:videoId" element={page(<VideoDetailPage />)} />
              <Route path="queue" element={page(<QueuePage />)} />
              <Route path="review" element={page(<ReviewQueuePage />)} />
              <Route path="review/:videoId" element={page(<MatchDetailPage />)} />
              <Route path="settings" element={page(<SettingsPage />)} />
              <Route path="library-import" element={page(<ImportLibraryPage />)} />
              <Route path="now-playing" element={page(<NowPlayingPage />)} />
              <Route path="tv" element={page(<TvModePage />)} />
              <Route path="cast" element={page(<CastModePage />)} />
              <Route path="video-editor" element={page(<VideoEditorPage />)} />
              <Route path="metadata-manager" element={page(<MetadataManagerPage />)} />
              <Route path="archive" element={page(<ArchivePage />)} />
              <Route path="scraper-tester" element={page(<ScraperTesterPage />)} />
              <Route path="new-videos" element={page(<NewVideosPage />)} />
              <Route path="*" element={<Navigate to="/library" replace />} />
            </Route>
          </Routes></PreferenceRefreshBoundary>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

export default App;
