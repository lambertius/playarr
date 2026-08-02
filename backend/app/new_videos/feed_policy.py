"""Final-feed ordering policies shared by refresh and backfill paths."""


def diversity_rerank(scored: list[tuple], limit: int) -> list[tuple]:
    """Prefer artist diversity without discarding the backfill pool."""
    if not scored or limit <= 1:
        return scored
    cap = max(1, int(limit * 0.20))
    remaining = list(scored)
    visible: list[tuple] = []
    artist_counts: dict[str, int] = {}

    def artist_key(item: tuple) -> str:
        return (item[0].artist or "").strip().casefold()

    while remaining and len(visible) < limit:
        previous = artist_key(visible[-1]) if visible else ""
        selected_index = next((
            index for index, item in enumerate(remaining)
            if not artist_key(item) or (
                artist_key(item) != previous
                and artist_counts.get(artist_key(item), 0) < cap
            )
        ), None)
        if selected_index is None:
            selected_index = next((
                index for index, item in enumerate(remaining)
                if not artist_key(item) or artist_key(item) != previous
            ), None)
        if selected_index is None:
            selected_index = 0
        selected = remaining.pop(selected_index)
        visible.append(selected)
        artist = artist_key(selected)
        if artist:
            artist_counts[artist] = artist_counts.get(artist, 0) + 1

    selected_ids = {id(item) for item in visible}
    return visible + [item for item in scored if id(item) not in selected_ids]
