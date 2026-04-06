"""Tests for Wikipedia false-positive guards (artist profession + album film/TV).

Covers:
 - Artist Wikidata profession guard rejecting non-music persons
 - Artist Wikidata profession guard allowing musician+actor hybrids
 - Album (film)/(movie) title penalties
 - Album snippet film keyword penalties
 - Album Wikidata guard rejecting film/TV pages
 - Album similarity gate (services version now matches scraper)
 - Hannah Brewer scenario: footballer artist + film album art
"""
import re
import types
import pytest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_search_result(title: str, snippet: str = "") -> dict:
    return {"title": title, "snippet": snippet}

# ---------------------------------------------------------------------------
# 1.  Artist Wikidata profession guard
# ---------------------------------------------------------------------------

class TestArtistProfessionGuard:
    """Test the extended Wikidata guard in search_wikipedia_artist."""

    @pytest.fixture(autouse=True)
    def _patch_services(self):
        """Patch network calls for services/metadata_resolver."""
        with patch("app.services.metadata_resolver._wikipedia_search_api") as mock_api, \
             patch("app.services.metadata_resolver._get_wiki_short_description") as mock_wd:
            self.mock_api = mock_api
            self.mock_wd = mock_wd
            yield

    def test_rejects_footballer(self):
        """Hannah Brewer scenario: page is about an Australian football player."""
        from app.services.metadata_resolver import search_wikipedia_artist
        self.mock_api.return_value = [
            _make_search_result("Hannah Brewer", "Hannah Brewer is a footballer"),
        ]
        self.mock_wd.return_value = "Australian women's football player"
        result = search_wikipedia_artist("Hannah Brewer")
        assert result is None

    def test_rejects_politician(self):
        from app.services.metadata_resolver import search_wikipedia_artist
        self.mock_api.return_value = [
            _make_search_result("John Smith", "politician from Ohio"),
        ]
        self.mock_wd.return_value = "American politician"
        result = search_wikipedia_artist("John Smith")
        assert result is None

    def test_rejects_cricketer(self):
        from app.services.metadata_resolver import search_wikipedia_artist
        self.mock_api.return_value = [
            _make_search_result("Steve Smith", "Steve Smith is a cricketer"),
        ]
        self.mock_wd.return_value = "Australian cricketer"
        result = search_wikipedia_artist("Steve Smith")
        assert result is None

    def test_allows_musician(self):
        from app.services.metadata_resolver import search_wikipedia_artist
        self.mock_api.return_value = [
            _make_search_result("Hannah Brewer", "Hannah Brewer is a singer-songwriter"),
        ]
        self.mock_wd.return_value = "American singer-songwriter"
        result = search_wikipedia_artist("Hannah Brewer")
        assert result is not None
        assert "Hannah_Brewer" in result or "Hannah%20Brewer" in result

    def test_allows_musician_actor_hybrid(self):
        """A person described as both musician and actor should pass."""
        from app.services.metadata_resolver import search_wikipedia_artist
        self.mock_api.return_value = [
            _make_search_result("Jared Leto", "Jared Leto is an actor and musician"),
        ]
        self.mock_wd.return_value = "American actor and musician"
        result = search_wikipedia_artist("Jared Leto")
        assert result is not None

    def test_allows_no_wikidata_desc(self):
        """When Wikidata has no description, don't reject."""
        from app.services.metadata_resolver import search_wikipedia_artist
        self.mock_api.return_value = [
            _make_search_result("Obscure Artist", "Obscure Artist is a band"),
        ]
        self.mock_wd.return_value = None
        result = search_wikipedia_artist("Obscure Artist")
        assert result is not None

    def test_rejects_soccer_player(self):
        from app.services.metadata_resolver import search_wikipedia_artist
        self.mock_api.return_value = [
            _make_search_result("Alex Morgan", "soccer player"),
        ]
        self.mock_wd.return_value = "American soccer player"
        result = search_wikipedia_artist("Alex Morgan")
        assert result is None


class TestArtistProfessionGuardScraper:
    """Same tests against the scraper copy."""

    @pytest.fixture(autouse=True)
    def _patch_scraper(self):
        with patch("app.scraper.metadata_resolver._wikipedia_search_api") as mock_api, \
             patch("app.scraper.metadata_resolver._get_wiki_short_description") as mock_wd:
            self.mock_api = mock_api
            self.mock_wd = mock_wd
            yield

    def test_rejects_footballer(self):
        from app.scraper.metadata_resolver import search_wikipedia_artist
        self.mock_api.return_value = [
            _make_search_result("Hannah Brewer", "Hannah Brewer is a footballer"),
        ]
        self.mock_wd.return_value = "Australian women's football player"
        result = search_wikipedia_artist("Hannah Brewer")
        assert result is None

    def test_allows_musician(self):
        from app.scraper.metadata_resolver import search_wikipedia_artist
        self.mock_api.return_value = [
            _make_search_result("Hannah Brewer", "Hannah Brewer is a singer-songwriter"),
        ]
        self.mock_wd.return_value = "American singer-songwriter"
        result = search_wikipedia_artist("Hannah Brewer")
        assert result is not None

    def test_allows_musician_actor_hybrid(self):
        from app.scraper.metadata_resolver import search_wikipedia_artist
        self.mock_api.return_value = [
            _make_search_result("Jared Leto", "Jared Leto is an actor and musician"),
        ]
        self.mock_wd.return_value = "American actor and musician"
        result = search_wikipedia_artist("Jared Leto")
        assert result is not None

# ---------------------------------------------------------------------------
# 2.  Album Wikipedia — film/TV penalties + Wikidata guard
# ---------------------------------------------------------------------------

class TestAlbumFilmPenalties:
    """Test that film/TV pages are rejected by title and Wikidata."""

    @pytest.fixture(autouse=True)
    def _patch_services(self):
        with patch("app.services.artist_album_scraper._wikipedia_search_api") as mock_api, \
             patch("app.services.metadata_resolver._get_wiki_short_description") as mock_wd, \
             patch("app.services.metadata_resolver._build_wikipedia_url", side_effect=lambda t: f"https://en.wikipedia.org/wiki/{t.replace(' ', '_')}"):
            self.mock_api = mock_api
            self.mock_wd = mock_wd
            yield

    def test_film_title_penalty(self):
        """A page with (film) in title should be rejected."""
        from app.services.artist_album_scraper import search_album_wikipedia
        self.mock_api.return_value = [
            _make_search_result("Child of Divorce (film)", "1946 American film"),
        ]
        self.mock_wd.return_value = "1946 American film"
        result = search_album_wikipedia("Child of Divorce", "Hannah Brewer")
        assert result is None

    def test_film_wikidata_guard(self):
        """Even without (film) in title, Wikidata should reject film pages."""
        from app.services.artist_album_scraper import search_album_wikipedia
        self.mock_api.return_value = [
            _make_search_result("Child of Divorce", "Child of Divorce is a 1946 film"),
        ]
        self.mock_wd.return_value = "1946 American film"
        result = search_album_wikipedia("Child of Divorce", "Hannah Brewer")
        assert result is None

    def test_tv_series_penalty(self):
        from app.services.artist_album_scraper import search_album_wikipedia
        self.mock_api.return_value = [
            _make_search_result("Rumspringa (tv series)", "TV show about Amish"),
        ]
        self.mock_wd.return_value = "American television series"
        result = search_album_wikipedia("Rumspringa", "Hannah Brewer")
        assert result is None

    def test_album_page_passes(self):
        """A real album page should still pass."""
        from app.services.artist_album_scraper import search_album_wikipedia
        self.mock_api.return_value = [
            _make_search_result("Child of Divorce (album)", "studio album by Hannah Brewer"),
        ]
        self.mock_wd.return_value = "album by Hannah Brewer"
        result = search_album_wikipedia("Child of Divorce", "Hannah Brewer")
        assert result is not None
        assert "Child_of_Divorce" in result

    def test_snippet_film_keywords_penalty(self):
        """Snippet containing 'directed by' should lose points."""
        from app.services.artist_album_scraper import search_album_wikipedia
        self.mock_api.return_value = [
            _make_search_result(
                "Child of Divorce",
                "Child of Divorce is a film directed by Richard Fleischer"
            ),
        ]
        self.mock_wd.return_value = "1946 American drama film"
        result = search_album_wikipedia("Child of Divorce", "Hannah Brewer")
        assert result is None

    def test_similarity_gate_rejects_different_title(self):
        """Services version now has similarity gate — dissimilar title rejected."""
        from app.services.artist_album_scraper import search_album_wikipedia
        self.mock_api.return_value = [
            _make_search_result(
                "The Modern Lovers (album)",
                "studio album by The Modern Lovers"
            ),
        ]
        self.mock_wd.return_value = "album by The Modern Lovers"
        result = search_album_wikipedia("Lovers", "Some Artist")
        assert result is None


class TestAlbumFilmPenaltiesScraper:
    """Same tests against the scraper copy."""

    @pytest.fixture(autouse=True)
    def _patch_scraper(self):
        with patch("app.scraper.artist_album_scraper._wikipedia_search_api") as mock_api, \
             patch("app.scraper.metadata_resolver._get_wiki_short_description") as mock_wd, \
             patch("app.scraper.metadata_resolver._build_wikipedia_url", side_effect=lambda t: f"https://en.wikipedia.org/wiki/{t.replace(' ', '_')}"):
            self.mock_api = mock_api
            self.mock_wd = mock_wd
            yield

    def test_film_title_penalty(self):
        from app.scraper.artist_album_scraper import search_album_wikipedia
        self.mock_api.return_value = [
            _make_search_result("Child of Divorce (film)", "1946 American film"),
        ]
        self.mock_wd.return_value = "1946 American film"
        result = search_album_wikipedia("Child of Divorce", "Hannah Brewer")
        assert result is None

    def test_film_wikidata_guard(self):
        from app.scraper.artist_album_scraper import search_album_wikipedia
        self.mock_api.return_value = [
            _make_search_result("Child of Divorce", "Child of Divorce is a 1946 film"),
        ]
        self.mock_wd.return_value = "1946 American film"
        result = search_album_wikipedia("Child of Divorce", "Hannah Brewer")
        assert result is None

    def test_album_page_passes(self):
        from app.scraper.artist_album_scraper import search_album_wikipedia
        self.mock_api.return_value = [
            _make_search_result("Child of Divorce (album)", "studio album by Hannah Brewer"),
        ]
        self.mock_wd.return_value = "album by Hannah Brewer"
        result = search_album_wikipedia("Child of Divorce", "Hannah Brewer")
        assert result is not None

    def test_similarity_gate_rejects_different_title(self):
        from app.scraper.artist_album_scraper import search_album_wikipedia
        self.mock_api.return_value = [
            _make_search_result(
                "The Modern Lovers (album)",
                "studio album by The Modern Lovers"
            ),
        ]
        self.mock_wd.return_value = "album by The Modern Lovers"
        result = search_album_wikipedia("Lovers", "Some Artist")
        assert result is None


# ---------------------------------------------------------------------------
# 3.  Hannah Brewer scenario: end-to-end
# ---------------------------------------------------------------------------

class TestHannahBrewerScenario:
    """Full scenario test: Hannah Brewer Rumspringa trace reproduction."""

    def test_footballer_artist_rejected_services(self):
        """search_wikipedia_artist should reject the footballer Hannah Brewer."""
        with patch("app.services.metadata_resolver._wikipedia_search_api") as mock_api, \
             patch("app.services.metadata_resolver._get_wiki_short_description") as mock_wd:
            mock_api.return_value = [
                _make_search_result(
                    "Hannah Brewer",
                    "Hannah Brewer (born 1998) is an Australian women&#039;s "
                    "association football (soccer) player who plays as a goalkeeper "
                    "for the Newcastle Jets"
                ),
            ]
            mock_wd.return_value = "Australian women's association football player"
            from app.services.metadata_resolver import search_wikipedia_artist
            assert search_wikipedia_artist("Hannah Brewer") is None

    def test_film_album_rejected_services(self):
        """search_album_wikipedia should reject the 1946 film Child of Divorce."""
        with patch("app.services.artist_album_scraper._wikipedia_search_api") as mock_api, \
             patch("app.services.metadata_resolver._get_wiki_short_description") as mock_wd, \
             patch("app.services.metadata_resolver._build_wikipedia_url",
                   side_effect=lambda t: f"https://en.wikipedia.org/wiki/{t}"):
            mock_api.return_value = [
                _make_search_result(
                    "Child of Divorce",
                    "Child of Divorce is a 1946 American drama film directed "
                    "by Richard O. Fleischer"
                ),
            ]
            mock_wd.return_value = "1946 American drama film"
            from app.services.artist_album_scraper import search_album_wikipedia
            assert search_album_wikipedia("Child of Divorce", "Hannah Brewer") is None
