import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { usePlaybackDiagnostics } from "@/hooks/usePlaybackDiagnostics";
import { playbackApi } from "@/lib/api";
import { PlaybackSessionGuard, PlaybackTimelineGuard } from "@/lib/playbackSession";
import { usePlaybackStore, type PlaybackTrack } from "@/stores/playbackStore";

type PlaybackState = "loading" | "playing" | "buffering" | "error";
type PlaybackProfile = "browser" | "tv" | "cast";

interface PlaybackMediaSessionOptions {
  videoRef: RefObject<HTMLVideoElement | null>;
  track: PlaybackTrack | null;
  profile: PlaybackProfile;
  transcode: boolean;
  videoSurfaceEpoch: number;
  onNeedsGesture?: (needs: boolean) => void;
}

/** Owns one guarded media-source lifecycle for the current queue occurrence. */
export function usePlaybackMediaSession({
  videoRef,
  track,
  profile,
  transcode,
  videoSurfaceEpoch,
  onNeedsGesture,
}: PlaybackMediaSessionOptions) {
  const tvMode = profile !== "browser";
  const videoId = track?.videoId ?? null;
  const occurrenceId = track?.queueEntryId ?? null;
  const setCurrentTime = usePlaybackStore((state) => state.setCurrentTime);
  const [needsGesture, setNeedsGesture] = useState(false);
  const [playbackState, setPlaybackState] = useState<PlaybackState>("loading");
  const [playbackError, setPlaybackError] = useState<string | null>(null);
  const mediaSessionRef = useRef(new PlaybackSessionGuard());
  const timelineRef = useRef(new PlaybackTimelineGuard());
  const durationRef = useRef(track?.duration);

  useEffect(() => {
    durationRef.current = track?.duration;
  }, [track?.duration]);

  useEffect(() => {
    onNeedsGesture?.(needsGesture);
  }, [needsGesture, onNeedsGesture]);

  usePlaybackDiagnostics(videoRef, { videoId, mode: profile });

  const tryTvPlay = useCallback(() => {
    const element = videoRef.current;
    if (!element) return;
    element.play()
      .then(() => {
        setNeedsGesture(false);
        setPlaybackState("playing");
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "NotAllowedError") {
          setNeedsGesture(true);
        }
      });
  }, [videoRef]);

  const handleCanPlay = useCallback(() => {
    setPlaybackState("playing");
    if (!tvMode) return;
    const element = videoRef.current;
    const resumeAt = timelineRef.current.highWaterMark;
    if (element && resumeAt > 5 && element.currentTime < resumeAt - 2) {
      try {
        element.currentTime = resumeAt;
      } catch {
        // The next timeupdate retries if this media engine is not seekable yet.
      }
    }
    tryTvPlay();
  }, [tvMode, tryTvPlay, videoRef]);

  const handlePlaying = useCallback(() => {
    setPlaybackState("playing");
    if (tvMode) return;
    const element = videoRef.current;
    if (!element) return;
    const audioTime = usePlaybackStore.getState().currentTime;
    if (Math.abs(element.currentTime - audioTime) > 0.05) {
      element.currentTime = audioTime;
    }
  }, [tvMode, videoRef]);

  const markBuffering = useCallback(() => setPlaybackState("buffering"), []);

  const retryPlayback = useCallback(() => {
    const element = videoRef.current;
    if (!element) return;
    const resumeAt = tvMode ? timelineRef.current.highWaterMark : 0;
    setPlaybackState("loading");
    setPlaybackError(null);
    element.load();
    if (resumeAt > 2) {
      try {
        element.currentTime = resumeAt;
      } catch {
        // Metadata/canplay will retry through the timeline guard.
      }
    }
    element.play().then(() => setPlaybackState("playing")).catch(() => {
      if (tvMode) setNeedsGesture(true);
    });
  }, [tvMode, videoRef]);

  useEffect(() => {
    const element = videoRef.current;
    if (!element || videoId === null) return;
    const session = mediaSessionRef.current.begin();
    const timeline = timelineRef.current;
    timeline.reset();
    setPlaybackState("loading");
    setPlaybackError(null);

    const expectedDuration = () => durationRef.current ?? usePlaybackStore.getState().duration;
    const finishPlayback = () => {
      const state = usePlaybackStore.getState();
      // Desktop audio is the master clock and advances the queue itself.
      if (!state.individualTrack && !tvMode) return;
      if (!mediaSessionRef.current.claimTransition(session)) return;
      if (state.individualTrack) {
        state.stopIndividual();
        return;
      }
      if (state.repeat === "one") {
        mediaSessionRef.current.releaseTransition(session);
        timeline.reset();
        setCurrentTime(0);
        element.currentTime = 0;
        element.play().catch(() => {});
        return;
      }
      state.next();
    };
    const handleTimeUpdate = () => {
      if (!tvMode || !mediaSessionRef.current.isCurrent(session)) return;
      const observation = timeline.observe(element.currentTime);
      if (observation.restoreTo !== null) {
        // A TV browser attached a timestamp-zero refill to this occurrence.
        setPlaybackState("buffering");
        try {
          element.currentTime = observation.restoreTo;
        } catch {
          // A later progress/canplay event retries once the range is available.
        }
        element.play().catch(() => {});
        return;
      }
      setCurrentTime(observation.acceptedTime);
      const state = usePlaybackStore.getState();
      if (state.repeat !== "one" && timeline.hasReached(expectedDuration(), 0.25)) {
        finishPlayback();
      }
    };
    const handleEnded = () => {
      const duration = expectedDuration();
      if (tvMode && duration > 0 && !timeline.hasReached(duration, 2)) {
        setPlaybackState("error");
        setPlaybackError("The stream ended early. Retry resumes the current track.");
        return;
      }
      finishPlayback();
    };
    const handleError = () => {
      if (!mediaSessionRef.current.isCurrent(session)) return;
      setPlaybackState("error");
      setPlaybackError("This stream could not be decoded. Retry keeps the current queue occurrence.");
    };

    element.addEventListener("ended", handleEnded);
    element.addEventListener("error", handleError);
    if (tvMode) element.addEventListener("timeupdate", handleTimeUpdate);
    element.src = tvMode
      ? playbackApi.streamUrl(videoId, transcode)
      : playbackApi.videoOnlyStreamUrl(videoId, transcode);
    element.load();
    if (usePlaybackStore.getState().isPlaying) {
      element.play().then(() => setPlaybackState("playing")).catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "NotAllowedError") {
          setNeedsGesture(true);
        }
      });
    }
    return () => {
      element.removeEventListener("ended", handleEnded);
      element.removeEventListener("error", handleError);
      if (tvMode) element.removeEventListener("timeupdate", handleTimeUpdate);
    };
  }, [setCurrentTime, tvMode, occurrenceId, videoId, transcode, videoSurfaceEpoch, videoRef]);

  return {
    needsGesture,
    playbackState,
    playbackError,
    startPlayback: tryTvPlay,
    retryPlayback,
    handleCanPlay,
    handlePlaying,
    markBuffering,
  };
}
