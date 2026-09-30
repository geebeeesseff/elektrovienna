from pathlib import Path
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from elektro_vienna import cli, config
from elektro_vienna.config import Config, ConfigurationError, validate_path
from elektro_vienna.models import ProviderError


def test_defaults_and_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "local_app_data", lambda: tmp_path)
    monkeypatch.delenv("EV_CREDENTIALS_DIR", raising=False)
    monkeypatch.delenv("EV_STATE_PATH", raising=False)
    result = Config.from_environment()
    assert result.credentials_dir == tmp_path / "ElektroViennaKnowledge" / "credentials"
    assert result.state_path == tmp_path / "ElektroViennaKnowledge" / "state" / "pipeline.sqlite3"
    monkeypatch.setenv("EV_STATE_PATH", str(tmp_path / "other" / "pipeline.sqlite3"))
    assert Config.from_environment().state_path.parent.name == "other"


@pytest.mark.parametrize("parts", [("SharePoint", "file"), ("Knowledgebase", "file"), ("OneDrive - Example", "file")])
def test_sync_paths_rejected(tmp_path, parts):
    with pytest.raises(ConfigurationError):
        validate_path(tmp_path.joinpath(*parts), tmp_path)


def test_outside_local_and_git_rejected(tmp_path):
    with pytest.raises(ConfigurationError):
        validate_path(tmp_path.parent / "outside", tmp_path)
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").write_text("gitdir: worktree")
    with pytest.raises(ConfigurationError, match="Git"):
        validate_path(repository / "credentials", tmp_path)


def test_sync_environment_and_traversal_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OneDriveCommercial", str(tmp_path / "company"))
    with pytest.raises(ConfigurationError):
        validate_path(tmp_path / "company" / "file", tmp_path)
    with pytest.raises(ConfigurationError):
        validate_path(tmp_path / "a" / ".." / "file", tmp_path)


def test_redirected_file_rejected(tmp_path, monkeypatch):
    original = Path.resolve
    target = tmp_path / "token.json"
    def resolve(path, *args, **kwargs):
        return tmp_path.parent / "sync" / "token.json" if path == target else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ConfigurationError):
        validate_path(target, tmp_path)


def test_registered_sync_root_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "synchronized_roots", lambda: [tmp_path / "arbitrary-company-name"])
    with pytest.raises(ConfigurationError, match="synchronized"):
        validate_path(tmp_path / "arbitrary-company-name" / "state.sqlite3", tmp_path)


def test_cli_stats_never_authenticates(tmp_path, monkeypatch, capsys):
    cfg = Config(tmp_path / "credentials", tmp_path / "state.sqlite3")
    monkeypatch.setattr(Config, "from_environment", lambda: cfg)
    auth = Mock(side_effect=AssertionError("No auth for stats"))
    monkeypatch.setattr(cli, "authenticate", auth)
    assert cli.main(["stats"]) == 0
    assert '"originals_archived": 0' in capsys.readouterr().out
    auth.assert_not_called()
    assert not cfg.state_path.exists()


def test_cli_missing_credentials_is_failure(tmp_path, monkeypatch, capsys):
    cfg = Config(tmp_path / "credentials", tmp_path / "state.sqlite3")
    monkeypatch.setattr(Config, "from_environment", lambda: cfg)
    monkeypatch.setattr(cli, "local_app_data", lambda: tmp_path)
    assert cli.main(["validate"]) == 1
    assert str(cfg.client_file) in capsys.readouterr().err
    assert not cfg.state_path.exists()


@pytest.mark.parametrize("arguments", [
    ["validate"], ["inventory"],
    ["validate", "--restart-pagination"], ["inventory", "--restart-pagination"],
])
@pytest.mark.parametrize("profile_failure", [False, True])
def test_cli_verifies_mailbox_before_creating_state(tmp_path, monkeypatch, capsys, arguments, profile_failure):
    cfg = Config(tmp_path / "credentials", tmp_path / "state" / "pipeline.sqlite3")
    monkeypatch.setattr(Config, "from_environment", lambda: cfg)
    monkeypatch.setattr(cli, "local_app_data", lambda: tmp_path)
    monkeypatch.setattr(cli, "authenticate", lambda _: nullcontext(Mock()))
    reader = Mock()
    reader.mailbox.return_value = "wrong@example.invalid"
    if profile_failure:
        reader.mailbox.side_effect = ProviderError("gmail_http_503")
    monkeypatch.setattr(cli, "GmailReader", lambda _: reader)
    assert cli.main(arguments) == 1
    assert ("gmail_http_503" if profile_failure else "mailbox_identity_mismatch") in capsys.readouterr().err
    assert not cfg.state_path.parent.exists()
    reader.list_messages.assert_not_called()
    reader.get_message.assert_not_called()


def test_cli_wrong_mailbox_cannot_restart_existing_checkpoint(tmp_path, monkeypatch, capsys):
    from elektro_vienna.inventory import scan_key

    cfg = Config(tmp_path / "credentials", tmp_path / "pipeline.sqlite3")
    state = cli.SQLiteState(cfg.state_path)
    key = scan_key(cfg.mailbox, cfg.cutoff_ms, "historical")
    state.begin(key, cfg.mailbox, cfg.cutoff_ms)
    with state.db:
        state.db.execute("UPDATE scans SET token='synthetic-checkpoint' WHERE scan_key=?", (key,))
    state.close()
    original = cfg.state_path.read_bytes()
    monkeypatch.setattr(Config, "from_environment", lambda: cfg)
    monkeypatch.setattr(cli, "local_app_data", lambda: tmp_path)
    monkeypatch.setattr(cli, "authenticate", lambda _: nullcontext(Mock()))
    reader = Mock()
    reader.mailbox.return_value = "wrong@example.invalid"
    monkeypatch.setattr(cli, "GmailReader", lambda _: reader)
    assert cli.main(["inventory", "--restart-pagination"]) == 1
    assert "mailbox_identity_mismatch" in capsys.readouterr().err
    assert cfg.state_path.read_bytes() == original


@pytest.mark.parametrize("value", ["0", "-1"])
def test_cli_rejects_invalid_limit(value):
    with pytest.raises(SystemExit) as error:
        cli.main(["validate", "--limit", value])
    assert error.value.code == 2
