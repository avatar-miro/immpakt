import textwrap

from immpakt import config as config_mod


def write(tmp_path, body):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_defaults_when_no_file(tmp_path):
    cfg = config_mod.load(tmp_path / "missing.yaml")
    assert cfg.frame.interval_s == 21600
    assert cfg.frame.fit.crop_tolerance_wide == 8.0


def test_nested_override_keeps_untouched_siblings(tmp_path):
    p = write(tmp_path, """
        frame:
          fit:
            crop_tolerance_tall: 2.0
    """)
    cfg = config_mod.load(p)
    assert cfg.frame.fit.crop_tolerance_tall == 2.0
    assert cfg.frame.fit.crop_tolerance_wide == 8.0  # not clobbered
    assert cfg.frame.fit.face_aware is True


def test_legacy_crop_tolerance_maps_to_the_tall_knob(tmp_path):
    p = write(tmp_path, """
        frame:
          fit:
            crop_tolerance: 1.9
    """)
    cfg = config_mod.load(p)
    assert cfg.frame.fit.crop_tolerance_tall == 1.9
    assert cfg.frame.fit.crop_tolerance_wide == 8.0


def test_unknown_keys_are_ignored(tmp_path):
    p = write(tmp_path, """
        frame:
          nonsense: 1
        made_up_section: {a: 1}
    """)
    assert config_mod.load(p).frame.interval_s == 21600


def test_env_overrides_file(tmp_path, monkeypatch):
    p = write(tmp_path, """
        immich:
          url: http://from-file:2283
    """)
    monkeypatch.setenv("IMMICH_URL", "http://from-env:2283")
    assert config_mod.load(p).immich.url == "http://from-env:2283"


def test_empty_env_does_not_clobber_file(tmp_path, monkeypatch):
    p = write(tmp_path, """
        immich:
          url: http://from-file:2283
    """)
    monkeypatch.setenv("IMMICH_URL", "")
    assert config_mod.load(p).immich.url == "http://from-file:2283"


def test_per_device_override_deep_merges(tmp_path):
    p = write(tmp_path, """
        frame:
          interval_s: 3600
          enhance:
            saturation: 1.8
        devices:
          picpak-aaa:
            rotate: 90
            enhance:
              contrast: 1.5
    """)
    cfg = config_mod.load(p)
    dev = cfg.frame_for("picpak-aaa")
    assert dev.rotate == 90
    assert dev.enhance.contrast == 1.5
    assert dev.enhance.saturation == 1.8, "global enhance should survive the merge"
    assert dev.interval_s == 3600
    assert cfg.frame_for("other").rotate == 0


def test_pre_rename_env_vars_still_work(tmp_path, monkeypatch):
    """PICPAK_* was the name before the rename. An ignored PICPAK_DATA_DIR
    would point the database somewhere new and look like every device had
    vanished, so the old names stay honoured."""
    monkeypatch.delenv("IMMPAKT_DATA_DIR", raising=False)
    monkeypatch.setenv("PICPAK_DATA_DIR", "/tmp/legacy")
    assert config_mod.load(tmp_path / "none.yaml").server.data_dir == "/tmp/legacy"


def test_new_env_var_wins_over_the_legacy_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PICPAK_DATA_DIR", "/tmp/legacy")
    monkeypatch.setenv("IMMPAKT_DATA_DIR", "/tmp/current")
    assert config_mod.load(tmp_path / "none.yaml").server.data_dir == "/tmp/current"
