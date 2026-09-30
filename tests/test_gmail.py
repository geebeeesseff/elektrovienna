import ast
import inspect
import json
from unittest.mock import Mock

import pytest
from requests.exceptions import Timeout

from elektro_vienna import gmail
from elektro_vienna.config import Config, ConfigurationError
from elektro_vienna.models import ProviderError, SCOPES, historical_cutoff_ms


def raw_message():
    return {"id": "abc", "threadId": "thread", "internalDate": str(historical_cutoff_ms()),
            "labelIds": ["SENT"], "payload": {"partId": "", "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": "sender@example.invalid"},
                        {"name": "In-Reply-To", "value": "synthetic-parent"},
                        {"name": "Subject", "value": "not persisted"}],
            "parts": [{"partId": "0", "mimeType": "text/plain", "body": {"size": 12}},
                      {"partId": "1", "mimeType": "multipart/mixed", "parts": [
                          {"partId": "1.0", "filename": "synthetic.pdf", "mimeType": "application/pdf",
                           "body": {"attachmentId": "attachment", "size": 42}},
                          {"partId": "1.1", "filename": "inline.png", "mimeType": "image/png", "body": {"size": 7}}
                      ]}]}}


def test_recursive_attachment_and_part_identity():
    message = gmail.parse_message(raw_message())
    assert message.thread_id == "thread"
    assert [a.part_id for a in message.attachments] == ["1.0", "1.1"]
    assert message.attachments[1].attachment_id is None
    assert dict(message.headers) == {"from": "sender@example.invalid", "in-reply-to": "synthetic-parent"}


def test_root_attachment_empty_part_id():
    raw = raw_message()
    raw["payload"] = {"partId": "", "filename": "file.txt", "mimeType": "text/plain", "body": {"size": 0}}
    assert gmail.parse_message(raw).attachments[0].part_id == ""


@pytest.mark.parametrize("field,value", [("labelIds", "DRAFT"), ("internalDate", -1),
    ("internalDate", "-1"), ("internalDate", "not-a-time"), ("id", 123), ("threadId", None)])
def test_malformed_required_metadata_fails_closed(field, value):
    raw = raw_message()
    raw[field] = value
    with pytest.raises(ProviderError, match="invalid_message_metadata"):
        gmail.parse_message(raw)


def test_provider_timestamp_overrides_date_header():
    raw = raw_message()
    raw["payload"]["headers"].append({"name": "Date", "value": "Mon, 1 Jan 1990 00:00:00 +0000"})
    assert gmail.parse_message(raw).qualifies(historical_cutoff_ms())


def test_forwarded_and_reply_metadata_do_not_filter_eligibility():
    for subject in ("Fwd: synthetic", "Re: synthetic", "No relevance inferred"):
        raw = raw_message()
        raw["payload"]["headers"].append({"name": "Subject", "value": subject})
        assert gmail.parse_message(raw).qualifies(historical_cutoff_ms())


def test_incomplete_mime_fails_explicitly():
    raw = raw_message()
    raw["payload"]["parts"] = [{"partId": "depth-sentinel"}]
    with pytest.raises(ProviderError, match="mime_structure_incomplete"):
        gmail.parse_message(raw)


def session_returning(data, status=200):
    session = Mock(spec=["get"])
    response = Mock(status_code=status)
    response.json.return_value = data
    session.get.return_value = response
    return session


def test_adapter_gets_only_metadata_and_broad_query():
    session = session_returning({"messages": [{"id": "abc"}], "nextPageToken": "next"})
    reader = gmail.GmailReader(session)
    assert reader.list_messages(historical_cutoff_ms(), None, 10).next_token == "next"
    params = session.get.call_args.kwargs["params"]
    assert params["q"] == f"after:{historical_cutoff_ms() // 1000 - 1} -in:spam -in:trash -in:drafts"
    assert params["includeSpamTrash"] == "false"
    reader.list_messages(historical_cutoff_ms(), "next", 10)
    assert session.get.call_args.kwargs["params"]["pageToken"] == "next"
    session.get.return_value.json.return_value = raw_message()
    reader.get_message("abc")
    assert session.get.call_args.kwargs["params"] == {"format": "full", "fields": gmail.MESSAGE_FIELDS}
    assert "data" not in gmail.MESSAGE_FIELDS and "raw" not in gmail.MESSAGE_FIELDS
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.args[0].endswith("/messages/abc")


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 429, 500, 503])
def test_http_failures_sanitized(status):
    reader = gmail.GmailReader(session_returning({"secret": "must not leak"}, status))
    with pytest.raises(ProviderError, match=f"^gmail_http_{status}$"):
        reader.get_message("abc")


def test_transport_failure_sanitized():
    session = Mock(spec=["get"])
    session.get.side_effect = Timeout("sensitive content")
    with pytest.raises(ProviderError, match="^gmail_transport_failure$"):
        gmail.GmailReader(session).mailbox()


@pytest.mark.parametrize("scopes", [[], ["https://mail.google.com/"], [*SCOPES, "extra"], None])
def test_broader_and_missing_scopes_rejected(scopes):
    with pytest.raises(ConfigurationError):
        gmail.check_scopes(scopes)
    gmail.check_scopes(SCOPES)


def test_missing_credentials_explicit_no_fallback(tmp_path):
    config = Config(tmp_path / "credentials", tmp_path / "state.sqlite3")
    with pytest.raises(ConfigurationError, match="Desktop OAuth credentials missing") as error:
        gmail.authenticate(config)
    assert str(config.client_file) in str(error.value)
    assert not config.token_file.exists()


def test_no_mutation_or_attachment_download_capability():
    assert {name for name, fn in inspect.getmembers(gmail.GmailReader, inspect.isfunction)} == {
        "__init__", "_get", "mailbox", "list_messages", "get_message"}
    tree = ast.parse(inspect.getsource(gmail.GmailReader))
    session_calls = [node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Attribute)
                     and node.func.value.attr == "_session"]
    assert session_calls == ["get"]
    paths = [node.args[0].value for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "_get"
             and isinstance(node.args[0], ast.Constant)]
    assert paths == ["/profile", "/messages"]


def test_oauth_exact_scope_and_atomic_token(tmp_path, monkeypatch):
    config = Config(tmp_path, tmp_path.parent / "state.sqlite3")
    config.client_file.write_text(json.dumps({"installed": {"auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token", "client_id": "synthetic"}}))
    credentials = Mock(valid=True, granted_scopes=SCOPES, scopes=SCOPES)
    credentials.to_json.return_value = '{"synthetic": true}'
    flow = Mock()
    flow.run_local_server.return_value = credentials
    factory = Mock(return_value=flow)
    monkeypatch.setattr(gmail.InstalledAppFlow, "from_client_config", factory)
    monkeypatch.setattr(gmail, "AuthorizedSession", Mock())
    gmail.authenticate(config)
    assert factory.call_args.kwargs["scopes"] == list(SCOPES)
    assert flow.run_local_server.call_args.kwargs["include_granted_scopes"] == "false"
    assert json.loads(config.token_file.read_text()) == {"synthetic": True}
    assert not config.token_file.with_suffix(".json.tmp").exists()


def test_cached_broad_token_rejected_before_network(tmp_path, monkeypatch):
    config = Config(tmp_path, tmp_path.parent / "state.sqlite3")
    config.client_file.write_text(json.dumps({"installed": {"auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token", "client_id": "synthetic"}}))
    config.token_file.write_text(json.dumps({"scopes": [*SCOPES, "https://mail.google.com/"]}))
    factory = Mock(side_effect=AssertionError("Must not start OAuth"))
    monkeypatch.setattr(gmail.InstalledAppFlow, "from_client_config", factory)
    with pytest.raises(ConfigurationError, match="exactly gmail.readonly"):
        gmail.authenticate(config)
    factory.assert_not_called()


def test_cached_token_refresh_and_reuse(tmp_path, monkeypatch):
    config = Config(tmp_path, tmp_path.parent / "state.sqlite3")
    config.client_file.write_text(json.dumps({"installed": {"auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token", "client_id": "synthetic"}}))
    config.token_file.write_text(json.dumps({"scopes": list(SCOPES), "client_id": "synthetic",
        "token_uri": "https://oauth2.googleapis.com/token"}))
    credentials = Mock(valid=False, refresh_token="synthetic", granted_scopes=SCOPES, scopes=SCOPES)
    credentials.refresh.side_effect = lambda _: setattr(credentials, "valid", True)
    credentials.to_json.return_value = '{"synthetic": "refreshed"}'
    monkeypatch.setattr(gmail.Credentials, "from_authorized_user_info", Mock(return_value=credentials))
    monkeypatch.setattr(gmail, "AuthorizedSession", Mock())
    factory = Mock(side_effect=AssertionError("No new consent required"))
    monkeypatch.setattr(gmail.InstalledAppFlow, "from_client_config", factory)
    gmail.authenticate(config)
    credentials.refresh.assert_called_once()
    factory.assert_not_called()
    assert json.loads(config.token_file.read_text()) == {"synthetic": "refreshed"}
