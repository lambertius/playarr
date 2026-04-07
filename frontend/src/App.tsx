import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@/components/Toast";
import { Layout } from "@/components/Layout";
import { LibraryPage } from "@/pages/LibraryPage";
import { ArtistsPage } from "@/pages/ArtistsPage";
import { YearsPage } from "@/pages/YearsPage";
import { GenresPage } from "@/pages/GenresPage";
import { AlbumsPage } from "@/pages/AlbumsPage";
import { RatingsPage } from "@/pages/RatingsPage";
import { PlaylistsPage } from "@/pages/PlaylistsPage";
import { VideoDetailPage } from "@/pages/VideoDetailPage";
import { QueuePage } from "@/pages/QueuePage";
import { SettingsPage } from "@/pages/SettingsPage";
import ReviewQueuePage from "@/pages/ReviewQueuePage";
import MatchDetailPage from "@/pages/MatchDetailPage";
import { ImportLibraryPage } from "@/pages/ImportLibraryPage";
import { NowPlayingPage } from "@/pages/NowPlayingPage";
import { VideoEditorPage } from "@/pages/VideoEditorPage";
import { ScraperTesterPage } from "@/pages/ScraperTesterPage";
import { NewVideosPage } from "@/pages/NewVideosPage";
import { QualityPage } from "@/pages/QualityPage";
import { ArchivePage } from "@/pages/ArchivePage";
import { MetadataManagerPage } from "@/pages/MetadataManagerPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Navigate to="/library" replace />} />
              <Route path="library" element={<LibraryPage />} />
              <Route path="artists" element={<ArtistsPage />} />
              <Route path="years" element={<YearsPage />} />
              <Route path="genres" element={<GenresPage />} />
              <Route path="albums" element={<AlbumsPage />} />
              <Route path="ratings" element={<RatingsPage />} />
              <Route path="quality" element={<QualityPage />} />
              <Route path="playlists" element={<PlaylistsPage />} />
              <Route path="video/:videoId" element={<VideoDetailPage />} />
              <Route path="queue" element={<QueuePage />} />
              <Route path="review" element={<ReviewQueuePage />} />
              <Route path="review/:videoId" element={<MatchDetailPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="library-import" element={<ImportLibraryPage />} />
              <Route path="now-playing" element={<NowPlayingPage />} />
              <Route path="video-editor" element={<VideoEditorPage />} />
              <Route path="metadata-manager" element={<MetadataManagerPage />} />
              <Route path="archive" element={<ArchivePage />} />
              <Route path="scraper-tester" element={<ScraperTesterPage />} />
              <Route path="new-videos" element={<NewVideosPage />} />
              <Route path="*" element={<Navigate to="/library" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

export default App;
