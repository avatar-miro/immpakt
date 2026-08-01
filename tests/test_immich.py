import json
import httpx
import pytest

from immpakt.immich import ImmichClient

ALBUM = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


def client_with(handler):
    c = ImmichClient("http://immich.test:2283", "key")
    c._client = httpx.Client(
        base_url="http://immich.test:2283/api",
        transport=httpx.MockTransport(handler),
    )
    return c


def asset(i):
    return {"id": f"a{i}", "exifInfo": {"exifImageWidth": 4000, "exifImageHeight": 3000}}


def test_album_with_inline_assets_is_read_directly():
    def handler(request):
        assert request.url.path == f"/api/albums/{ALBUM}"
        return httpx.Response(200, json={"assetCount": 2, "assets": [asset(1), asset(2)]})

    assert [a.id for a in client_with(handler).album_assets(ALBUM)] == ["a1", "a2"]


def test_album_without_inline_assets_falls_back_to_search():
    """Newer Immich returns album metadata only -- no `assets` key. Reading it
    inline yields zero photos and the frame goes blank, so we must fall back."""
    seen = {}

    def handler(request):
        if request.url.path.startswith("/api/albums/"):
            # assetCount says 7874, but there is no assets array at all.
            return httpx.Response(200, json={"assetCount": 7874, "albumName": "All"})
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={
            "assets": {"items": [asset(1), asset(2), asset(3)], "nextPage": None}})

    assets = client_with(handler).album_assets(ALBUM)
    assert [a.id for a in assets] == ["a1", "a2", "a3"]
    assert ALBUM in seen["body"], "the search must be scoped to the album"


def test_genuinely_empty_album_does_not_hit_search():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"assetCount": 0, "albumName": "Other"})

    assert client_with(handler).album_assets(ALBUM) == []
    assert len(calls) == 1, "an empty album should not trigger a library search"


def test_search_paginates_until_nextpage_is_null():
    def handler(request):
        page = json.loads(request.read())["page"]
        return httpx.Response(200, json={"assets": {
            "items": [asset(page)], "nextPage": str(page + 1) if page < 3 else None}})

    assert len(client_with(handler).search_assets()) == 3


def test_aspect_is_derived_from_exif_dimensions():
    def handler(request):
        return httpx.Response(200, json={"assetCount": 1, "assets": [asset(1)]})

    assert client_with(handler).album_assets(ALBUM)[0].aspect == pytest.approx(4 / 3)


def test_faces_are_normalised_against_the_reported_detection_size():
    def handler(request):
        return httpx.Response(200, json={"people": [{"faces": [{
            "imageWidth": 1000, "imageHeight": 500,
            "boundingBoxX1": 250, "boundingBoxY1": 100,
            "boundingBoxX2": 750, "boundingBoxY2": 400}]}]})

    assert client_with(handler).faces("a1") == [(0.25, 0.2, 0.75, 0.8)]


def test_faces_tolerate_an_unreachable_asset():
    def handler(request):
        return httpx.Response(500)

    assert client_with(handler).faces("a1") == []
