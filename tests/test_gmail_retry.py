"""Quota handling with synthetic responses and a clock that never really sleeps."""

from unittest.mock import Mock
from contextlib import nullcontext

import pytest

from elektro_vienna import gmail
from elektro_vienna.inventory import inventory, scan_key
from elektro_vienna.models import ProviderError, historical_cutoff_ms
from elektro_vienna.state import SQLiteState

MAILBOX = "office@elektrovienna.at"
SECRET = "private@example.invalid https://example.invalid/?token=synthetic-secret"


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def response(status=200, reason=None, data=None):
    result = Mock(status_code=status)
    result.json.return_value = data if data is not None else {
        "error": {"message": SECRET, "errors": [{"reason": reason, "message": SECRET}]}}
    return result


def metadata(identity="abc"):
    return {"id": identity, "threadId": "synthetic-thread", "internalDate": str(historical_cutoff_ms()),
            "payload": {"partId": "0", "mimeType": "application/pdf", "filename": "synthetic.pdf",
                        "body": {"attachmentId": "synthetic-attachment", "size": 5}}}


def reader_with(responses):
    clock = Clock()
    session = Mock(spec=["get"])
    session.get.side_effect = responses
    reader = gmail.GmailReader(session, sleep=clock.sleep, clock=clock, jitter=lambda: 0.5)
    return reader, session, clock


@pytest.mark.parametrize("status,reason", [(403, "rateLimitExceeded"), (403, "userRateLimitExceeded"),
    (429, None), (500, None), (502, None), (503, None), (504, None)])
def test_transient_error_retries_then_succeeds(status, reason):
    reader, session, clock = reader_with([response(status, reason), response(status, reason),
                                         response(data=metadata())])
    assert reader.get_message("abc").message_id == "abc"
    assert session.get.call_count == 3
    assert clock.waits == [1.5, 2.5]
    assert all(call == session.get.call_args_list[0] for call in session.get.call_args_list)


@pytest.mark.parametrize("reason,code", [("rateLimitExceeded", "gmail_rate_limit_exceeded"),
    ("userRateLimitExceeded", "gmail_user_rate_limit_exceeded")])
def test_retry_exhaustion_is_bounded_and_sanitized(reason, code, capsys):
    reader, session, clock = reader_with([response(403, reason)] * 7)
    with pytest.raises(ProviderError) as error:
        reader.get_message("abc")
    assert str(error.value) == code
    assert vars(error.value) == {"code": code}
    assert session.get.call_count == 7
    assert clock.waits == [1.5, 2.5, 4.5, 8.5, 16.5, 32.0]
    assert SECRET not in str(error.value)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("status,reason,code", [
    (403, "domainPolicy", "gmail_domain_policy"),
    (403, "dailyLimitExceeded", "gmail_daily_limit_exceeded"),
    (403, "authError", "gmail_http_403"), (403, "insufficientPermissions", "gmail_http_403"),
    (403, SECRET, "gmail_http_403"), (401, None, "gmail_http_401"), (501, None, "gmail_http_501"),
])
def test_non_transient_errors_never_retry(status, reason, code):
    reader, session, clock = reader_with([response(status, reason)])
    with pytest.raises(ProviderError, match=f"^{code}$"):
        reader.get_message("abc")
    assert session.get.call_count == 1
    assert clock.waits == []


@pytest.mark.parametrize("payload", [None, [], {"error": []}, {"error": {"errors": []}},
    {"error": {"errors": [{"reason": ["rateLimitExceeded"]}]}}])
def test_malformed_403_is_generic_and_not_retried(payload):
    failure = response(403)
    failure.json.return_value = payload
    reader, session, clock = reader_with([failure])
    with pytest.raises(ProviderError, match="^gmail_http_403$"):
        reader.get_message("abc")
    assert session.get.call_count == 1
    assert not clock.waits


def test_non_json_403_and_mixed_reasons_fail_closed():
    failure = response(403)
    failure.json.side_effect = ValueError(SECRET)
    assert gmail.http_failure(failure) == ("gmail_http_403", False)
    for hard, expected in [("domainPolicy", "gmail_domain_policy"),
                           ("dailyLimitExceeded", "gmail_daily_limit_exceeded"),
                           ("unknown", "gmail_http_403")]:
        failure = response(403, data={"error": {"errors": [
            {"reason": "rateLimitExceeded"}, {"reason": hard}]}})
        assert gmail.http_failure(failure) == (expected, False)


def test_pacing_first_request_immediate_and_no_catchup_burst():
    reader, session, clock = reader_with([response(data=metadata())] * 4)
    reader.get_message("abc")
    assert clock.waits == []
    reader.get_message("abc")
    assert clock.waits == [0.25]
    clock.now += 10  # Slow response/operator pause already satisfies pacing.
    reader.get_message("abc")
    assert clock.waits == [0.25]
    reader.get_message("abc")
    assert clock.waits == [0.25, 0.25]


def test_authentication_failure_does_not_retry_or_leak():
    reader, session, clock = reader_with([gmail.GoogleAuthError(SECRET)])
    with pytest.raises(ProviderError, match="^gmail_authentication_failed$"):
        reader.get_message("abc")
    assert session.get.call_count == 1
    assert not clock.waits


def test_cli_displays_only_safe_reason(tmp_path, monkeypatch, capsys):
    from elektro_vienna import cli
    from elektro_vienna.config import Config

    reader, session, clock = reader_with([response(403, "domainPolicy")])
    cfg = Config(tmp_path / "credentials", tmp_path / "state.sqlite3")
    monkeypatch.setattr(Config, "from_environment", lambda: cfg)
    monkeypatch.setattr(cli, "local_app_data", lambda: tmp_path)
    monkeypatch.setattr(cli, "authenticate", lambda _: nullcontext(session))
    monkeypatch.setattr(cli, "GmailReader", lambda _: reader)
    assert cli.main(["inventory"]) == 1
    assert capsys.readouterr() == ("", "Inventory stopped: gmail_domain_policy\n")
    assert not cfg.state_path.exists()


def test_paced_validation_and_inventory_resume_without_duplicates(tmp_path):
    clock = Clock()
    attempts = 0
    blocked = True
    request_times = []

    def get(url, **kwargs):
        nonlocal attempts
        request_times.append(clock.now)
        if url.endswith("/profile"):
            return response(data={"emailAddress": MAILBOX})
        if url.endswith("/messages"):
            return response(data={"messages": [{"id": "a"}, {"id": "b"}]})
        identity = url.rsplit("/", 1)[1]
        if identity == "b" and blocked:
            attempts += 1
            return response(403, "userRateLimitExceeded")
        return response(data=metadata(identity))

    session = Mock(spec=["get"])
    session.get.side_effect = get
    reader = gmail.GmailReader(session, sleep=clock.sleep, clock=clock, jitter=lambda: 0.5)
    path = tmp_path / "pipeline.sqlite3"
    state = SQLiteState(path)
    cutoff = historical_cutoff_ms()
    try:
        assert inventory(reader, state, MAILBOX, cutoff, limit=1)["qualifying"] == 1
        with pytest.raises(ProviderError, match="gmail_user_rate_limit_exceeded"):
            inventory(reader, state, MAILBOX, cutoff)
        assert attempts == 7
        key = scan_key(MAILBOX, cutoff, "historical")
        assert state.pending(key) == ["b"]
        assert state.scan(key)["failures"] == 1
        assert state.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
    finally:
        state.close()
    blocked = False
    state = SQLiteState(path)
    try:
        assert inventory(reader, state, MAILBOX, cutoff)["completed"]
        assert inventory(reader, state, MAILBOX, cutoff)["completed"]
        assert state.db.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
        assert state.db.execute("SELECT count(*) FROM attachments").fetchone()[0] == 2
        assert not state.pending(key)
        assert all(b - a >= 0.25 for a, b in zip(request_times, request_times[1:]))
    finally:
        state.close()
    assert SECRET.encode() not in path.read_bytes()
