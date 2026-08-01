import type { QueryClient } from "@tanstack/react-query";
import { libraryApi } from "@/lib/api";
import type { VideoItemDetail, VideoItemUpdate } from "@/types";


export async function updateVideoWithRevision(
  queryClient: QueryClient,
  id: number,
  data: VideoItemUpdate,
): Promise<VideoItemDetail> {
  const key = ["video", id] as const;
  const cached = queryClient.getQueryData<VideoItemDetail>(key);
  const expectedRevision = data.expected_revision ?? cached?.revision
    ?? (await libraryApi.get(id)).revision;
  const changedFields = Object.keys(data).filter(keyName =>
    keyName !== "expected_revision" && !keyName.endsWith("_set")
  );
  if (changedFields.length > 0 && changedFields.every(
    keyName => keyName === "song_rating" || keyName === "video_rating"
  )) {
    const accepted = await libraryApi.queueRating(id, {
      expected_revision: expectedRevision,
      song_rating: data.song_rating,
      video_rating: data.video_rating,
    });
    const { operationsApi } = await import("@/lib/api");
    await operationsApi.wait(accepted.operation_id);
    return libraryApi.get(id);
  }
  return libraryApi.update(id, { ...data, expected_revision: expectedRevision });
}
