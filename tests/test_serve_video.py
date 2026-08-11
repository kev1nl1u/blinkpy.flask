import pytest


@pytest.fixture
def clip(tmp_path, app, monkeypatch):
    """Point the video route at a temp local_clips dir holding one fake clip."""
    root = tmp_path / "local_clips" / "Home"
    root.mkdir(parents=True)
    data = bytes(range(256)) * 40  # 10240 bytes
    (root / "42_Cucina.mp4").write_bytes(data)
    monkeypatch.setattr(app, "root_path", str(tmp_path / "app"))
    return data


def _get(client, rng=None):
    headers = {"Range": rng} if rng else {}
    return client.get("/videos/Home/42_Cucina.mp4", headers=headers)


def test_full_request_returns_whole_file(clip, logged_in_client):
    res = _get(logged_in_client)
    assert res.status_code == 200
    assert res.get_data() == clip
    assert res.headers["Accept-Ranges"] == "bytes"


def test_byte_range_returns_exact_slice(clip, logged_in_client):
    res = _get(logged_in_client, "bytes=1000-1999")
    assert res.status_code == 206
    assert res.headers["Content-Range"] == f"bytes 1000-1999/{len(clip)}"
    assert res.get_data() == clip[1000:2000]


def test_suffix_range_returns_tail_not_head(clip, logged_in_client):
    """`bytes=-500` means the last 500 bytes, not the first 500."""
    res = _get(logged_in_client, "bytes=-500")
    assert res.status_code == 206
    assert res.get_data() == clip[-500:]


def test_unsatisfiable_range_is_416(clip, logged_in_client):
    res = _get(logged_in_client, "bytes=99999-")
    assert res.status_code == 416
    assert res.headers["Content-Range"] == f"bytes */{len(clip)}"


def test_clips_are_cacheable_but_never_by_the_cdn(clip, logged_in_client):
    cc = _get(logged_in_client).cache_control
    assert cc.private
    assert not cc.public
    assert cc.max_age and cc.max_age > 0


def test_traversal_and_missing_files_are_rejected(clip, logged_in_client):
    assert logged_in_client.get("/videos/../config.py").status_code == 404
    assert logged_in_client.get("/videos/Home/nope.mp4").status_code == 404


def test_requires_login(clip, app):
    res = app.test_client().get("/videos/Home/42_Cucina.mp4")
    assert res.status_code in (301, 302)
