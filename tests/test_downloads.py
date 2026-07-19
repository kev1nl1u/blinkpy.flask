import json
from pathlib import Path
from datetime import datetime
import asyncio
import pytest
from app.services.blink import downloads


class FakeItem:
    def __init__(self, id, name, created_at, ok=True):
        self.id = id
        self.name = name
        self.created_at = created_at
        self._ok = ok
    def url(self, manifest_id=None):
        return "/clip/url"
    async def prepare_download(self, blink):
        return True
    async def download_video(self, blink, path):
        if self._ok:
            Path(path).write_bytes(b"video-bytes")
            return True
        return False


def _item(ok=True):
    return FakeItem("c1", "Front Door", datetime(2026, 6, 9, 12, 0, 0), ok=ok)


def test_skips_existing(tmp_path):
    dest = tmp_path / "mod"
    dest.mkdir()
    (dest / "c1_Front_Door.mp4").write_bytes(b"x")
    res = asyncio.run(downloads.download_one_clip(blink=None, mod_name="mod",
                                                  item=_item(), manifest_id="m1", root=tmp_path))
    assert res["status"] == "skipped"


def test_downloads_and_writes_meta(tmp_path):
    res = asyncio.run(downloads.download_one_clip(blink=None, mod_name="mod",
                                                  item=_item(), manifest_id="m1", root=tmp_path))
    assert res["status"] == "downloaded"
    mp4 = tmp_path / "mod" / "c1_Front_Door.mp4"
    assert mp4.exists()
    meta = json.loads((mp4.with_suffix(".json")).read_text())
    assert meta["camera_name"] == "Front Door"


def test_cleans_partial_on_failure(tmp_path):
    res = asyncio.run(downloads.download_one_clip(blink=None, mod_name="mod",
                                                  item=_item(ok=False), manifest_id="m1", root=tmp_path))
    assert res["status"] == "error"
    assert not (tmp_path / "mod" / "c1_Front_Door.mp4").exists()
