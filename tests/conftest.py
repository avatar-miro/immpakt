import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from immpakt import app as app_mod
from immpakt import config as config_mod
from immpakt.immich import Asset
from immpakt.store import Store


class FakeClient:
    """Stands in for ImmichClient; no network, deterministic pixels."""

    def __init__(self):
        self.image_calls = 0

    def image_bytes(self, asset_id, size="preview"):
        self.image_calls += 1
        seed = int(asset_id.split("-")[1])
        buf = io.BytesIO()
        Image.new("RGB", (1600, 1200), (seed * 37 % 256, 90, 160)).save(buf, format="JPEG")
        return buf.getvalue()

    def faces(self, asset_id):
        return []

    def ping(self):
        return True

    def close(self):
        pass


class FakePool:
    def __init__(self, assets):
        self._assets = assets

    def ensure_fresh(self, force=False):
        return self._assets

    @property
    def status(self):
        return {"count": len(self._assets), "fetched_at": 0, "age_s": 0, "error": None}


@pytest.fixture
def client(tmp_path):
    def build(n_assets=8, **server_kw):
        cfg = config_mod.Config()
        cfg.server.data_dir = str(tmp_path)
        for k, v in server_kw.items():
            setattr(cfg.server, k, v)

        st = app_mod.State.__new__(app_mod.State)
        st.cfg = cfg
        st.client = FakeClient()
        st.pool = FakePool([Asset(id=f"asset-{i:03d}") for i in range(n_assets)])
        st.store = Store(tmp_path / "t.db")
        app_mod.state = st
        app_mod._RENDER_CACHE.clear()
        # Bypass lifespan so no real Immich client is constructed.
        return TestClient(app_mod.app), st

    return build
