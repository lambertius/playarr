"""Final-feed ordering policies shared by refresh and backfill paths."""


def diversity_rerank(scored: list[tuple], limit: int) -> list[tuple]:
    """Round-robin artists without discarding the ranked backfill pool.

    A simple per-artist cap still lets the highest-scoring artist occupy the
    first, third and fifth slots. Grouping first and then taking one item per
    artist per pass makes the visible row genuinely varied while retaining the
    original score order within each artist's recommendations.
    """
    if not scored or limit <= 1:
        return scored

    buckets: dict[str, list[tuple]] = {}
    bucket_order: list[str] = []
    for index, item in enumerate(scored):
        artist = (item[0].artist or "").strip().casefold()
        # Unknown artists must not collapse into one artificial mega-artist.
        key = artist or f"__unknown_{index}"
        if key not in buckets:
            buckets[key] = []
            bucket_order.append(key)
        buckets[key].append(item)

    visible: list[tuple] = []
    pass_index = 0
    while len(visible) < limit:
        added = False
        for key in bucket_order:
            bucket = buckets[key]
            if pass_index < len(bucket):
                visible.append(bucket[pass_index])
                added = True
                if len(visible) == limit:
                    break
        if not added:
            break
        pass_index += 1

    selected_ids = {id(item) for item in visible}
    return visible + [item for item in scored if id(item) not in selected_ids]
