"""Unit tests for LunkserverManager backend logic.

Tests backup file operations, JSON validation, recipe integrity, and
get_all_statuses status mapping — without a running docker daemon.
Run: python3 -m pytest tests/ -v
"""
import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

#Import without instantiating DockerManager (avoids docker.from_env()).
import docker_manager as dm_mod
import recipes


@pytest.fixture
def fake_dm():
    """DockerManager with docker client mocked out."""
    with patch.object(dm_mod.docker, "from_env", return_value=MagicMock()):
        inst = dm_mod.DockerManager()
    return inst


@pytest.fixture
def tmp_server_data(monkeypatch):
    """Redirect ~/Documents paths to a temp dir for isolated file tests."""
    tmp = tempfile.mkdtemp()
    real_home = os.path.realpath(os.path.expanduser("~"))
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", real_home).replace(real_home, tmp, 1) if p.startswith("~") else p)
    yield tmp


#─── Recipe integrity ───────────────────────────────────────────

class TestRecipes:
    def test_minecraft_recipes_have_whitelist_enabled(self):
        mc_ids = [k for k, v in recipes.SERVER_RECIPES.items() if v.get("game_type") == "minecraft"]
        assert mc_ids, "No minecraft recipes found"
        for sid in mc_ids:
            env = recipes.SERVER_RECIPES[sid].get("env", {})
            assert env.get("ENABLE_WHITELIST") == "true", f"{sid} missing ENABLE_WHITELIST=true"

    def test_all_recipes_have_image(self):
        for sid, recipe in recipes.SERVER_RECIPES.items():
            assert "image" in recipe, f"{sid} has no image"

    def test_minecraft_recipes_have_whitelist_config_file(self):
        mc_ids = [k for k, v in recipes.SERVER_RECIPES.items() if v.get("game_type") == "minecraft"]
        for sid in mc_ids:
            config_files = recipes.SERVER_RECIPES[sid].get("config_files", {})
            assert "Whitelist" in config_files, f"{sid} missing Whitelist config_file"
            assert config_files["Whitelist"] == "whitelist.json"


#─── Backup methods (real file ops) ─────────────────────────────

class TestBackups:
    def test_get_backup_list_empty(self, fake_dm, tmp_server_data):
        result = fake_dm.get_backup_list("testserver", recipes.SERVER_RECIPES["jellyfin"])
        assert result["status"] == "success"
        assert result["backups"] == []

    def test_get_backup_list_finds_snapshots(self, fake_dm, tmp_server_data):
        sid = "testserver"
        backup_dir = os.path.expanduser(f"~/Documents/server_backups/{sid}")
        os.makedirs(backup_dir, exist_ok=True)
        #Create fake snapshots
        for ts in ["2026-01-01_10-00-00", "2026-01-02_10-00-00"]:
            with open(os.path.join(backup_dir, f"snapshot_{ts}.tar.gz"), "w") as f:
                f.write("fake")
        result = fake_dm.get_backup_list(sid, recipes.SERVER_RECIPES["jellyfin"])
        assert result["status"] == "success"
        assert len(result["backups"]) == 2
        #Newest first
        assert "2026-01-02" in result["backups"][0]["filename"]
        assert result["backups"][0]["type"] == "Lunkserver Backup"

    def test_get_backup_list_finds_native_saves(self, fake_dm, tmp_server_data):
        sid = "factorio_test"
        recipe = {"native_auto_save": True, "native_save_path": "saves", "native_save_ext": ".zip"}
        save_dir = os.path.expanduser(f"~/Documents/server_data/{sid}/saves")
        os.makedirs(save_dir, exist_ok=True)
        with open(os.path.join(save_dir, "world.zip"), "w") as f:
            f.write("fake save")
        result = fake_dm.get_backup_list(sid, recipe)
        assert result["status"] == "success"
        native = [b for b in result["backups"] if b["type"] == "In-Game Auto-Save"]
        assert len(native) == 1
        assert native[0]["filename"] == "world.zip"

    def test_delete_backup_rejects_path_traversal(self, fake_dm, tmp_server_data):
        result = fake_dm.delete_backup("testserver", "../../etc/passwd", "Lunkserver Backup", {})
        assert result["status"] == "error"
        assert "Invalid" in result["message"]

    def test_delete_backup_missing_file(self, fake_dm, tmp_server_data):
        result = fake_dm.delete_backup("testserver", "nope.tar.gz", "Lunkserver Backup", {})
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_delete_backup_works(self, fake_dm, tmp_server_data):
        sid = "testserver"
        backup_dir = os.path.expanduser(f"~/Documents/server_backups/{sid}")
        os.makedirs(backup_dir, exist_ok=True)
        fpath = os.path.join(backup_dir, "snapshot_test.tar.gz")
        with open(fpath, "w") as f:
            f.write("data")
        assert os.path.exists(fpath)
        result = fake_dm.delete_backup(sid, "snapshot_test.tar.gz", "Lunkserver Backup", {})
        assert result["status"] == "success"
        assert not os.path.exists(fpath)

    def test_create_snapshot_missing_source(self, fake_dm, tmp_server_data):
        result = fake_dm.create_snapshot("nonexistent", recipes.SERVER_RECIPES["jellyfin"])
        assert result["status"] == "error"
        assert "No data" in result["message"]

    def test_create_snapshot_creates_backup_outside_source(self, fake_dm, tmp_server_data):
        """Backups must land in server_backups/, NOT server_data/ (recursion bug)."""
        sid = "testserver"
        source_dir = os.path.expanduser(f"~/Documents/server_data/{sid}")
        os.makedirs(source_dir, exist_ok=True)
        with open(os.path.join(source_dir, "world.txt"), "w") as f:
            f.write("game data")
        #Mock: no running container (no minecraft freeze needed).
        with patch.object(fake_dm, "_get_container_safe", return_value=None):
            result = fake_dm.create_snapshot(sid, recipes.SERVER_RECIPES["jellyfin"], retention_limit=3)
        assert result["status"] == "success"
        backup_dir = os.path.expanduser(f"~/Documents/server_backups/{sid}")
        backups = [f for f in os.listdir(backup_dir) if f.endswith(".tar.gz")]
        assert len(backups) == 1
        #Source dir must NOT contain a backups subfolder (the old recursion bug).
        assert not os.path.exists(os.path.join(source_dir, "backups"))


#─── Mods methods ───────────────────────────────────────────────

class TestMods:
    def test_list_mods_empty(self, fake_dm, tmp_server_data):
        result = fake_dm.list_mods("testserver")
        assert result["status"] == "success"
        assert result["mods"] == []

    def test_list_mods_finds_jars(self, fake_dm, tmp_server_data):
        sid = "testserver"
        mods_dir = os.path.expanduser(f"~/Documents/server_data/{sid}/mods")
        os.makedirs(mods_dir, exist_ok=True)
        for f in ["mod1.jar", "mod2.jar", "readme.txt", "mod3.zip"]:
            with open(os.path.join(mods_dir, f), "w") as fh:
                fh.write("x")
        result = fake_dm.list_mods(sid)
        assert result["status"] == "success"
        assert set(result["mods"]) == {"mod1.jar", "mod2.jar", "mod3.zip"}

    def test_delete_mod_rejects_traversal(self, fake_dm, tmp_server_data):
        result = fake_dm.delete_mod("testserver", "../../../etc/passwd")
        assert result["status"] == "error"


#─── Status mapping logic ───────────────────────────────────────

class TestStatusMapping:
    def test_satellite_host_down_marks_unknown(self, fake_dm, monkeypatch):
        """When SSH AND HTTP both fail, container status = unknown."""
        def fake_run(*a, **kw):
            raise OSError("connection refused")
        monkeypatch.setattr(dm_mod.subprocess, "run", fake_run)
        monkeypatch.setattr("httpx.get", lambda *a, **kw: (_ for _ in ()).throw(Exception("down")))
        statuses = fake_dm.get_all_statuses(
            ["satellite_01"],
            recipes={"satellite_01": {"remote_host": f"ssh://lunkman@{dm_mod.SATELLITE_IP}:22"}}
        )
        assert statuses["satellite_01"] == "unknown"


#─── Settings save JSON validation (unit-level) ─────────────────

class TestJSONValidation:
    def test_valid_json_passes(self):
        assert json.loads('[{"uuid": "abc"}]') is not None

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            json.loads("{broken")

    def test_empty_whitelist_is_valid_json_array(self):
        #The UI may send an empty array for whitelist
        assert json.loads("[]") == []


class TestSatelliteHealth:
    SATELLITE_URL = "http://<VPS_TAILSCALE_IP>:8765/api/health"

    def test_vps_satellite_reachable(self):
        """LunkVPS satellite must respond on the tailscale network."""
        import urllib.request
        try:
            req = urllib.request.Request(self.SATELLITE_URL, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            assert data.get("status") == "ok"
            assert "lunkserver-satellite" in data.get("service", "")
        except Exception as e:
            pytest.fail(f"Satellite at {self.SATELLITE_URL} unreachable: {e}")

    def test_vps_satellite_returns_stats(self):
        """Satellite /api/stats must return cpu/ram/disk/bandwidth."""
        import urllib.request
        url = self.SATELLITE_URL.replace("/health", "/stats")
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            pytest.fail(f"Satellite stats unreachable: {e}")
        assert "cpu" in data and "percent" in data["cpu"]
        assert "ram" in data and "percent" in data["ram"]
        assert "disk" in data
        assert "bandwidth" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
