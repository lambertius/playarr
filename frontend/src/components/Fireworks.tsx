import { useEffect, useRef } from "react";

interface FireworksProps {
  duration?: number;
  blobUrl: string | null;
  onComplete: () => void;
}

/**
 * Plays the pre-rendered fireworks WebM video.
 * If no blob is available yet, fires onComplete immediately (skip animation).
 */
export function Fireworks({ blobUrl, onComplete }: FireworksProps) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (!blobUrl) {
      // No pre-rendered video ready — skip animation gracefully
      onComplete();
      return;
    }

    const video = videoRef.current;
    if (!video) return;

    const handleEnded = () => onComplete();
    video.addEventListener("ended", handleEnded);
    video.play().catch(() => onComplete());

    return () => {
      video.removeEventListener("ended", handleEnded);
      video.pause();
    };
  }, [blobUrl, onComplete]);

  if (!blobUrl) return null;

  return (
    <video
      ref={videoRef}
      src={blobUrl}
      muted
      playsInline
      className="fixed inset-0 z-[9999] pointer-events-none"
      style={{ width: "100vw", height: "100vh", objectFit: "cover" }}
    />
  );
}
