import base64
import copy
from datetime import datetime, timezone
from email.message import EmailMessage
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from elektro_vienna import archive_store, shadow_input, pending_replies as pr, pending_suggestions as ps
from elektro_vienna.gmail import GmailReader
from elektro_vienna.models import Page, OriginalMessage, ProviderError
from elektro_vienna.shadow_store import ShadowStore

NOW = datetime(2026, 10, 20, 10, tzinfo=timezone.utc)
MS = int(NOW.timestamp() * 1000)


def message(mid, *, sender="Customer <customer@example.invalid>", text="Was kostet die Montage?",
            labels=(), offset=0, subject="Anfrage", extra=None, tid="t1"):
    headers = {"From": sender, "To": "customer@example.invalid" if sender == pr.MAILBOX else pr.MAILBOX,
               "Subject": subject, **(extra or {})}
    raw = {"id": mid, "threadId": tid, "internalDate": str(MS + offset),
           "labelIds": list(labels), "payload": {"mimeType": "text/plain", "partId": "",
           "headers": [{"name": k, "value": v} for k, v in headers.items()],
           "body": {"data": base64.urlsafe_b64encode(text.encode()).decode(), "size": len(text.encode())}}}
    mail = EmailMessage()
    for k, v in headers.items():
        mail[k] = v
    mail.set_content(text)
    return raw, OriginalMessage(mid, tid, MS + offset, mail.as_bytes())


@pytest.fixture
def env(tmp_path, monkeypatch):
    root, local = tmp_path / "archive", tmp_path / "local"
    monkeypatch.setattr(archive_store, "AUTHORIZED_ROOT", root)
    monkeypatch.setattr(archive_store, "local_app_data", lambda: local)
    monkeypatch.setattr(shadow_input, "local_app_data", lambda: local)
    monkeypatch.setattr(shadow_input, "validate_path", lambda p, _: p)
    store = ShadowStore()
    reader = Mock(spec=["mailbox", "list_threads", "get_thread", "get_original", "get_attachment"])
    reader.mailbox.return_value = pr.MAILBOX
    reader.list_threads.return_value = Page(("t1",), None)
    state = local / "pending.json"
    return store, reader, state


def configure(env, *messages):
    store, reader, state = env
    reader.get_thread.return_value = {"id": "t1", "messages": [m[0] for m in messages]}
    originals = {m[0]["id"]: m[1] for m in messages}
    reader.get_original.side_effect = originals.__getitem__


def suggestion(packets, contexts):
    decisions = []
    for p in packets:
        latest = p["messages"][-1]
        decisions.append({"thread_id": p["thread_id"], "latest_customer_id": latest["id"],
            "state": "pending", "customer": "Customer", "customer_email": "customer@example.invalid",
            "reason": "Customer asks a current question.", "previous_context": "",
            "suggested_response": "Guten Tag,\n\nbitte senden Sie uns ein Foto der Montagestelle.\n\n" + ps.SIGNOFF,
            "internal_checks": []})
    return decisions


def run(env, suggest=suggestion):
    store, reader, state = env
    return pr.reconcile(reader, store, state, {"price_book": {"text": "fixture"}}, now=NOW, suggest=suggest)


@pytest.mark.parametrize("labels", [(), ("INBOX",), ("INBOX", "UNREAD")])
def test_customer_inbound_unanswered_included_regardless_of_read_state(env, labels):
    configure(env, message("a", labels=labels))
    result = run(env)
    assert result["pending_customer_threads"] == 1
    page = Path(result["review_path"]).read_text("utf-8")
    assert "Suggested response" in page and "Copy response" in page
    assert "CRM" not in page and "Gmail inputs" not in page


@pytest.mark.parametrize("out_labels", [("SENT",), ()])
def test_actual_later_outbound_excludes_independent_reply_without_history_observation(env, out_labels):
    configure(env, message("a"), message("b", sender=pr.MAILBOX, offset=1000, labels=out_labels))
    model = Mock(side_effect=AssertionError("answered thread must not reach generation"))
    assert run(env, model)["pending_customer_threads"] == 0
    env[1].get_thread.assert_called_once_with("t1")
    model.assert_not_called()


def test_ev_then_customer_question_reopens_thread(env):
    configure(env, message("a", sender=pr.MAILBOX), message("b", text="Ist Material dabei?", offset=1000))
    assert run(env)["pending_customer_threads"] == 1


def test_customer_ev_customer_followup_reopens(env):
    configure(env, message("a"), message("b", sender=pr.MAILBOX, offset=1000),
              message("c", text="Wann könnten Sie kommen?", offset=2000))
    assert run(env)["pending_customer_threads"] == 1


@pytest.mark.parametrize("technician_reply", [False, True])
def test_internal_forward_and_technician_reply_do_not_answer_customer(env, technician_reply):
    messages = [message("customer"), message("internal", sender=pr.MAILBOX, offset=1000,
                extra={"To": "electrician@example.invalid"}, text="Bitte technisch beurteilen.")]
    if technician_reply:
        messages.append(message("technical", sender="electrician@example.invalid", offset=2000,
                                text="Dazu muss ich den Verteiler prüfen."))
    configure(env, *messages)
    def draft_for_customer(packets, contexts):
        result = suggestion(packets, contexts)
        result[0]["latest_customer_id"] = "customer"
        return result
    result = run(env, draft_for_customer)
    batch = env[0].read(result["batch_reference"])
    assert result["pending_customer_threads"] == 1
    assert batch["rows"][0]["latest"]["id"] == "customer"


@pytest.mark.parametrize("later", ["customer", "answered"])
def test_internal_context_does_not_allow_stale_customer_turn(env, later):
    configure(env, message("a"), message("b", offset=1000,
        sender="customer@example.invalid" if later == "customer" else pr.MAILBOX),
        message("technical", sender="electrician@example.invalid", offset=2000))
    def stale(packets, contexts):
        result = suggestion(packets, contexts)
        result[0]["latest_customer_id"] = "a"
        return result
    result = run(env, stale)
    if later == "answered":
        assert result["pending_customer_threads"] == 0
    else:
        rows = env[0].read(result["batch_reference"])["rows"]
        assert rows[0]["latest"]["id"] == "b"
        assert result["suggestions_unavailable"] == 1
        assert rows[0]["decision"]["suggested_response"] == ""


def test_customer_ev_customer_thanks_does_not_reopen_quoted_question(env):
    configure(env, message("a"), message("b", sender=pr.MAILBOX, offset=1000),
              message("c", text="Vielen Dank!\n\nAm Montag schrieb Giovanni:\nWas kostet die Montage?", offset=2000))
    assert run(env, Mock(side_effect=AssertionError("bare thanks needs no model")))["pending_customer_threads"] == 0


@pytest.mark.parametrize("sender", ["Wix <forms@wix.com>", pr.MAILBOX])
def test_wix_submission_is_incoming_even_with_company_envelope(env, sender):
    configure(env, message("a", sender=sender, subject="Neue Nachricht über Wix Formular",
              text="Name: Ada Test\nE-Mail: ada@example.invalid\nNachricht: Können Sie eine Lampe montieren?",
              extra={"Reply-To": "ada@example.invalid", "Auto-Submitted": "auto-generated"}))
    def form_suggestion(packets, contexts):
        assert packets[0]["messages"][0]["direction"] == "incoming"
        assert packets[0]["messages"][0]["form_submission"]
        result = suggestion(packets, contexts)
        result[0].update(customer="Ada Test", customer_email="ada@example.invalid")
        return result
    assert run(env, form_suggestion)["pending_customer_threads"] == 1


@pytest.mark.parametrize("extra", [{"List-Unsubscribe": "<https://example.invalid/unsubscribe>"},
                                  {"Auto-Submitted": "auto-generated"}])
def test_newsletter_and_automated_messages_excluded_without_suggestion(env, extra):
    configure(env, message("a", extra=extra))
    assert run(env, Mock(side_effect=AssertionError("not a customer")))["pending_customer_threads"] == 0


def test_internal_outbound_excluded(env):
    configure(env, message("a", sender="giovanni@elektrovienna.at"))
    assert run(env)["pending_customer_threads"] == 0


def test_semantic_marketing_technician_or_no_reply_decisions_stay_out_of_primary_page(env):
    configure(env, message("a", text="Unser Angebot für Ihre Werbung"))
    def exclude(packets, contexts):
        d = suggestion(packets, contexts)[0]
        d.update(state="non_customer", suggested_response="", reason="Marketing")
        return [d]
    result = run(env, exclude)
    html = Path(result["review_path"]).read_text("utf-8")
    assert result["pending_customer_threads"] == 0
    assert "Unser Angebot" not in html and "<article>" not in html


def test_latest_mail_not_hidden_by_history_backlog_and_all_recent_pages_reconciled(env):
    store, reader, state = env
    configure(env, message("a"))
    # A wholly separate queued history state must not be read or consumed.
    state.parent.mkdir()
    history = state.with_name("checkpoint.json")
    history.write_text('{"page":{"pending":["old-draft"]},"page_token":"backlog"}')
    before = history.read_bytes()
    reader.list_threads.side_effect = [Page((), "next"), Page(("t1",), None)]
    assert run(env)["pending_customer_threads"] == 1
    assert history.read_bytes() == before
    assert reader.list_threads.call_count == 2


def test_pending_thread_is_rechecked_after_aging_out_and_replied_card_disappears(env):
    configure(env, message("a"))
    assert run(env)["pending_customer_threads"] == 1
    env[1].list_threads.return_value = Page((), None)
    configure(env, message("a"), message("b", sender=pr.MAILBOX, offset=1000))
    assert run(env)["pending_customer_threads"] == 0


def test_cache_does_not_bypass_actual_thread_check_and_read_flag_ignored(env):
    configure(env, message("a", labels=("UNREAD",)))
    model = Mock(side_effect=suggestion)
    assert run(env, model)["pending_customer_threads"] == 1
    configure(env, message("a", labels=()))
    assert run(env, model)["pending_customer_threads"] == 1
    assert model.call_count == 1
    assert env[1].get_thread.call_count == 4


@pytest.mark.parametrize("dated", [False, True])
def test_next_day_reuses_unchanged_decisions_but_refreshes_dated_drafts(env, dated):
    configure(env, message("a"))
    def draft(packets, contexts):
        result = suggestion(packets, contexts)
        if dated:
            result[0]["suggested_response"] = "Bitte nennen Sie uns Ihre Verfügbarkeit morgen.\n\n" + ps.SIGNOFF
        return result
    model = Mock(side_effect=draft)
    run(env, model)
    later = NOW.replace(day=21)
    pr.reconcile(env[1], env[0], env[2], {"price_book": {"text": "fixture"}}, now=later, suggest=model)
    assert model.call_count == (2 if dated else 1)
    assert env[1].get_thread.call_count == 4


def test_guidance_change_prevents_cross_date_reuse(env):
    configure(env, message("a"))
    model = Mock(side_effect=suggestion)
    run(env, model)
    pr.reconcile(env[1], env[0], env[2], {"price_book": {"text": "new price"}},
                 now=NOW.replace(day=21), suggest=model)
    assert model.call_count == 2


def test_inference_payload_retains_full_text_documents_and_related_context_without_duplicates():
    packet = {"thread_id": "t1", "messages": [{"id": "m1", "text": "Full current and quoted evidence",
              "latest_text": "Full current", "attachments": [{"filename": "photo.jpg", "attachment_id": "opaque"}]}],
              "sources": [{"path": "private"}], "documents": [{"text": "Plan contents"}],
              "related_recent_threads": [{"thread_id": "t2", "messages": [{"id": "m2", "text": "Actual reply"}]}]}
    compact = ps.inference_packet(packet)
    assert compact["messages"][0]["text"] == packet["messages"][0]["text"]
    assert compact["documents"] == packet["documents"]
    assert compact["related_recent_threads"][0]["messages"][0]["text"] == "Actual reply"
    assert "latest_text" not in compact["messages"][0] and "sources" not in compact
    assert "attachment_id" not in compact["messages"][0]["attachments"][0]


def test_rotating_attachment_download_handle_does_not_invalidate_content_or_refresh(env):
    raw, original = message("a")
    part = {"partId": "1", "mimeType": "application/pdf", "filename": "plan.pdf",
            "body": {"attachmentId": "first-opaque-handle", "size": 4}}
    raw["payload"] = {"partId": "", "mimeType": "multipart/mixed",
                      "headers": raw["payload"]["headers"], "parts": [raw["payload"], part]}
    configure(env, (raw, original))
    env[1].get_attachment.return_value = b"test"
    first = copy.deepcopy(env[1].get_thread.return_value)
    second = copy.deepcopy(first)
    second["messages"][0]["payload"]["parts"][1]["body"]["attachmentId"] = "rotated-handle"
    assert first != second and pr.fingerprint(first) == pr.fingerprint(second)
    env[1].get_thread.side_effect = [first, second, second, second]
    model = Mock(side_effect=suggestion)
    assert run(env, model)["pending_customer_threads"] == 1
    assert run(env, model)["pending_customer_threads"] == 1
    assert model.call_count == 1
    changed = copy.deepcopy(second)
    changed["messages"][0]["payload"]["parts"][1]["body"]["size"] = 5
    assert pr.fingerprint(first) != pr.fingerprint(changed)


def test_reply_arriving_during_generation_prevents_stale_publication(env):
    configure(env, message("a"))
    raw = copy.deepcopy(env[1].get_thread.return_value)
    changed = copy.deepcopy(raw)
    changed["messages"].append(message("b", sender=pr.MAILBOX, offset=1000)[0])
    env[1].get_thread.side_effect = [raw, changed]
    result = run(env)
    assert result["pending_customer_threads"] == 0
    assert "<article>" not in Path(result["review_path"]).read_text("utf-8")


def test_new_customer_turn_during_generation_never_shows_stale_suggestion(env):
    a, b = message("a"), message("b", text="Und ist das Material inklusive?", offset=1000)
    configure(env, a, b)
    current = copy.deepcopy(env[1].get_thread.return_value)
    initial = {**current, "messages": [a[0]]}
    env[1].get_thread.side_effect = [initial, current, current]
    model = Mock(side_effect=suggestion)
    result = run(env, model)
    assert result["pending_customer_threads"] == 1 and model.call_count == 1
    assert result["suggestions_unavailable"] == 1
    batch = env[0].read(result["batch_reference"])
    assert batch["rows"][0]["latest"]["id"] == "b"


def test_drafts_spam_trash_and_autoreplies_do_not_answer_customer(env):
    configure(env, message("a"), message("draft", sender=pr.MAILBOX, offset=1000, labels=("DRAFT",)),
              message("auto", sender=pr.MAILBOX, offset=2000, extra={"Auto-Submitted": "auto-replied"}))
    def reply(packets, contexts):
        d = suggestion(packets, contexts)[0]
        d["latest_customer_id"] = "a"
        return [d]
    assert run(env, reply)["pending_customer_threads"] == 1


def test_incomplete_listing_must_not_report_empty_current_inbox(env):
    env[1].list_threads.return_value = Page((), "cycle")
    with pytest.raises(ValueError, match="pagination_incomplete"):
        run(env)
    assert not env[2].exists()


def test_suggestion_failure_and_invalid_draft_publish_queue_with_explicit_unavailable(env):
    configure(env, message("a"))
    result = run(env, Mock(side_effect=ProviderError("model_unavailable")))
    assert result["pending_customer_threads"] == 1 and env[2].exists()
    assert "Suggested response currently unavailable" in Path(result["review_path"]).read_text("utf-8")
    def invalid(packets, contexts):
        d = suggestion(packets, contexts)[0]
        d["suggested_response"] = ""
        return [d]
    result = run(env, invalid)
    assert result["suggestions_unavailable"] == 1
    assert "data-copy=" not in Path(result["review_path"]).read_text("utf-8")


def test_output_is_escaped_and_no_network_or_form_submission(env):
    configure(env, message("a", subject="<script>bad()</script>", text="Was kostet <img src=x onerror=bad()>?"))
    html = Path(run(env)["review_path"]).read_text("utf-8")
    assert "<script>bad()" not in html and "&lt;img" in html
    assert "default-src 'none'" in html and "form-action 'none'" in html
    assert "fetch(" not in html


def test_thread_reader_uses_get_and_no_unread_filter():
    session = Mock()
    response = session.get.return_value
    response.status_code = 200
    response.json.return_value = {"threads": [{"id": "t1"}]}
    reader = GmailReader(session, sleep=lambda _: None)
    assert reader.list_threads(MS).message_ids == ("t1",)
    url, = session.get.call_args.args
    assert url.endswith("/users/me/threads")
    query = session.get.call_args.kwargs["params"]["q"]
    assert "unread" not in query and "-in:spam -in:trash -in:drafts" in query
    raw = {"id": "t1", "messages": [message("a")[0]]}
    response.json.return_value = raw
    assert reader.get_thread("t1") == raw
    assert session.get.call_args.args[0].endswith("/threads/t1")
    assert not session.post.called and not session.patch.called


def test_codex_runtime_disables_side_effect_capabilities(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "executable", lambda: "codex.exe")
    args = ps.cli_command(tmp_path, tmp_path / "schema", tmp_path / "output", [])
    assert "--ignore-user-config" in args and "--ephemeral" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    for setting in ("features.shell_tool=false", "features.apps=false", "features.plugins=false",
                    "features.hooks=false", "features.multi_agent=false", "mcp_servers={}"):
        assert setting in args


def test_codex_runtime_preserves_service_tier_without_loading_other_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('model = "gpt-6-astra"\nservice_tier = "default"\n'
                                         '[features]\nshell_tool = true\n')
    monkeypatch.setattr(ps, "executable", lambda: "codex.exe")
    args = ps.cli_command(tmp_path, tmp_path / "schema", tmp_path / "output", [])
    assert args[args.index("--model") + 1] == "gpt-6-astra"
    assert 'service_tier="default"' in args
    assert "--ignore-user-config" in args and "features.shell_tool=false" in args


def test_runtime_credit_error_is_explicit_and_preserves_private_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "cli_command", lambda *args: ["codex.exe"])
    monkeypatch.setattr(shadow_input, "runtime_file", lambda name: tmp_path / name)
    execute = Mock(return_value=Mock(returncode=1, stderr="ERROR: Your workspace is out of credits."))
    monkeypatch.setattr(ps.subprocess, "run", execute)
    wait = Mock()
    monkeypatch.setattr(ps.time, "sleep", wait)
    with pytest.raises(ProviderError, match="pending_suggestion_runtime_credit_limit"):
        ps.generate([{"thread_id": "t1"}], {})
    assert execute.call_count == 2
    wait.assert_called_once_with(30)
    assert json.loads((tmp_path / "last-inference-error.json").read_bytes())["thread_ids"] == ["t1"]


def test_partial_generation_failure_preserves_completed_decisions_next_day(env):
    store, reader, state = env
    pairs = [message(f"m{i}", tid=f"t{i}", offset=i) for i in range(8)]
    raw = {p[0]["threadId"]: {"id": p[0]["threadId"], "messages": [p[0]]} for p in pairs}
    originals = {p[0]["id"]: p[1] for p in pairs}
    reader.list_threads.return_value = Page(tuple(raw), None)
    reader.get_thread.side_effect = raw.__getitem__
    reader.get_original.side_effect = originals.__getitem__
    count = 0
    def interrupted(packets, contexts):
        nonlocal count
        count += 1
        if count == 2:
            raise ProviderError("inference_failure")
        return suggestion(packets, contexts)
    first = run(env, interrupted)
    assert first["pending_customer_threads"] == 8 and first["suggestions_unavailable"] == 4
    assert len(json.loads(state.read_bytes())["cache"]) == 4
    model = Mock(side_effect=suggestion)
    assert pr.reconcile(reader, store, state, {"price_book": {"text": "fixture"}},
                        now=NOW.replace(day=21), suggest=model)["pending_customer_threads"] == 8
    assert model.call_count == 1
    assert {p["thread_id"] for p in model.call_args.args[0]} == {"t0", "t1", "t2", "t3"}


@pytest.mark.parametrize("response", [{"threads": None}, {"threads": [None]}, {"threads": [{"id": "../unsafe"}]},
                                      {"nextPageToken": 1}])
def test_invalid_thread_list_is_explicit(response):
    session = Mock()
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = response
    with pytest.raises(ProviderError, match="invalid_thread_page"):
        GmailReader(session).list_threads(MS)


def test_wix_primary_message_is_submission_not_platform_footer(env):
    configure(env, message("a", sender=pr.MAILBOX, subject="Anfrage hat eine neue Einreichung",
        text="Ein Besucher hat dein Formular eingereicht.\nIhre Anfrage : Bitte einen Herd anschließen.\n\n"
             "Name : Customer\nE-Mail : customer@example.invalid\n\nWenn dir eine Einreichung verdächtig vorkommt...\n"))
    page = Path(run(env)["review_path"]).read_text("utf-8")
    assert "Bitte einen Herd anschließen." in page
    assert "Wenn dir eine Einreichung" not in page and "Ein Besucher" not in page


def test_missing_name_uses_evidenced_email_without_invention(env):
    configure(env, message("a", sender="customer@example.invalid"))
    def unnamed(packets, contexts):
        d = suggestion(packets, contexts)[0]
        d["customer"] = ""
        return [d]
    result = run(env, unnamed)
    assert result["pending_customer_threads"] == 1
    assert env[0].read(result["batch_reference"])["rows"][0]["decision"]["customer"] == "customer@example.invalid"


def test_unknown_customer_identity_is_uncertain_not_a_blank_card(env):
    configure(env, message("a"))
    def unknown(packets, contexts):
        d = suggestion(packets, contexts)[0]
        d.update(customer="", customer_email="")
        return [d]
    result = run(env, unknown)
    assert result["pending_customer_threads"] == 0 and result["uncertain_threads"] == 1
    assert "<article>" not in Path(result["review_path"]).read_text("utf-8")


def configure_split_threads(env, *, different_job=False):
    pairs = [message("form", tid="t1", sender=pr.MAILBOX, subject="Wix Formular",
                     text="Name: Customer\nE-Mail: customer@example.invalid\nNachricht: Bitte Herd anschließen."),
             message("inbound", tid="t2", text="Eine andere Wallbox-Anfrage" if different_job else "Bitte Herd anschließen."),
             message("outbound", tid="t2", sender=pr.MAILBOX, offset=1000,
                     text="Unser Angebot für die Wallbox." if different_job else "Herdanschluss ist erledigt.",
                     extra={"To": "customer@example.invalid"})]
    if not different_job:
        pairs.append(message("followup", tid="t2", offset=2000, text="Bitte noch um Rechnungsausstellung."))
    raw = {tid: {"id": tid, "messages": [m[0] for m in pairs if m[0]["threadId"] == tid]} for tid in ("t1", "t2")}
    env[1].list_threads.return_value = Page(tuple(raw), None)
    env[1].get_thread.side_effect = raw.__getitem__
    env[1].get_original.side_effect = {m[0]["id"]: m[1] for m in pairs}.__getitem__
    return raw


def test_answered_wix_form_in_another_thread_does_not_duplicate_unresolved_followup(env):
    configure_split_threads(env)
    def classify(packets, contexts):
        result = suggestion(packets, contexts)
        if "related_recent_thread_rule" in contexts:
            for p, d in zip(packets, result):
                assert p["related_recent_threads"]
                if p["thread_id"] == "t1":
                    d.update(state="no_reply_needed", suggested_response="", reason="Answered in t2; newer invoice followup there.")
        return result
    model = Mock(side_effect=classify)
    result = run(env, model)
    assert result["pending_customer_threads"] == 1
    batch = env[0].read(result["batch_reference"])
    assert [r["thread_id"] for r in batch["rows"]] == ["t2"]
    assert model.call_count == 1
    run(env, model)
    assert model.call_count == 1  # Unchanged supporting correspondence also reuses inference.


def test_same_customer_other_job_is_context_not_automatic_answer_exclusion(env):
    configure_split_threads(env, different_job=True)
    model = Mock(side_effect=suggestion)
    result = run(env, model)
    assert result["pending_customer_threads"] == 1
    assert model.call_count == 1
    context = model.call_args.args[0][0]["related_recent_threads"][0]
    assert context["thread_id"] == "t2"
    assert "different jobs" in model.call_args.args[1]["related_recent_thread_rule"]


def test_quoted_customer_email_does_not_link_unrelated_threads():
    m = message("other", sender="other@example.invalid", text="Previous customer@example.invalid inquiry")
    assert not pr.has_correspondent({"id": "t1", "messages": [m[0]]}, "customer@example.invalid")


def test_uncertain_related_evidence_is_rechecked_after_window_boundary(env):
    configure_split_threads(env, different_job=True)
    def uncertain(packets, contexts):
        result = suggestion(packets, contexts)
        if "related_recent_thread_rule" in contexts:
            for d in result:
                d.update(state="uncertain", suggested_response="", reason="Need to clarify whether this is the same job.")
        return result
    model = Mock(side_effect=uncertain)
    assert run(env, model)["uncertain_threads"] == 1
    assert json.loads(env[2].read_bytes())["related_tracked"] == ["t2"]
    env[1].list_threads.return_value = Page((), None)
    assert run(env, model)["uncertain_threads"] == 1
    assert model.call_count == 1


@pytest.mark.parametrize("error", ["pending_suggestion_runtime_credit_limit", "pending_suggestions_require_current_signed_in_codex", "pending_suggestion_runtime_failed"])
def test_runtime_unavailable_still_publishes_read_unanswered_customer_queue(env, error):
    configure(env, message("a", labels=("INBOX",)))
    model = Mock(side_effect=ProviderError(error))
    result = run(env, model)
    assert result["pending_customer_threads"] == 1
    assert result["suggestions_unavailable"] == 1 and result["suggestion_calls"] == 1
    batch = env[0].read(result["batch_reference"])
    row = batch["rows"][0]
    assert row["latest"]["id"] == "a" and row["decision"]["customer_email"] == "customer@example.invalid"
    assert row["suggestion_error"] == error
    assert "Suggested response currently unavailable" in Path(result["review_path"]).read_text("utf-8")
    assert batch["external_actions"] == []


def test_new_customer_message_invalidates_cached_suggestion(env):
    configure(env, message("a"))
    model = Mock(side_effect=suggestion)
    run(env, model)
    configure(env, message("a"), message("b", offset=1000, text="Bitte auch die zweite Lampe montieren."))
    result = run(env, model)
    assert result["suggestions_reused"] == 0 and model.call_count == 2
    assert env[0].read(result["batch_reference"])["rows"][0]["latest"]["id"] == "b"


@pytest.mark.parametrize("kind", ["answered", "marketing", "system"])
def test_ineligible_threads_never_process_attachments_or_call_model(env, kind):
    raw, original = message("a", sender="noreply@example.invalid" if kind == "system" else "customer@example.invalid",
                            text="Unser Angebot für Ihre Werbung" if kind == "marketing" else "Bitte eine Lampe montieren.")
    raw["payload"] = {"mimeType": "multipart/mixed", "headers": raw["payload"]["headers"],
        "parts": [raw["payload"], {"partId": "1", "mimeType": "application/pdf", "filename": "document.pdf",
                                  "body": {"attachmentId": "opaque", "size": 100}}]}
    pairs = [(raw, original)]
    if kind == "answered":
        pairs.append(message("b", sender=pr.MAILBOX, offset=1000))
    configure(env, *pairs)
    model = Mock(side_effect=AssertionError("Ineligible thread must not reach inference"))
    assert run(env, model)["pending_customer_threads"] == 0
    model.assert_not_called()
    env[1].get_original.assert_not_called()
    env[1].get_attachment.assert_not_called()


def test_first_runtime_failure_stops_more_calls_but_keeps_whole_queue(env):
    pairs = [message(f"m{i}", tid=f"t{i}", sender=f"customer{i}@example.invalid", offset=i) for i in range(9)]
    raw = {p[0]["threadId"]: {"id": p[0]["threadId"], "messages": [p[0]]} for p in pairs}
    env[1].list_threads.return_value = Page(tuple(raw), None)
    env[1].get_thread.side_effect = raw.__getitem__
    env[1].get_original.side_effect = {p[0]["id"]: p[1] for p in pairs}.__getitem__
    model = Mock(side_effect=ProviderError("pending_suggestion_runtime_credit_limit"))
    result = run(env, model)
    assert result["pending_customer_threads"] == result["suggestions_unavailable"] == 9
    assert model.call_count == 1 and env[1].get_original.call_count == 4
    assert result["threads_requiring_new_suggestion"] == 9 and result["suggestion_threads_attempted"] == 4


def test_explicit_cross_thread_reply_excludes_original_without_model(env):
    raw = configure_split_threads(env)
    raw["t1"]["messages"][0]["payload"]["headers"].append({"name": "Message-ID", "value": "<form@example.invalid>"})
    raw["t2"]["messages"][1]["payload"]["headers"].append({"name": "In-Reply-To", "value": "<form@example.invalid>"})
    model = Mock(side_effect=ProviderError("model_unavailable"))
    result = run(env, model)
    rows = env[0].read(result["batch_reference"])["rows"]
    assert [r["thread_id"] for r in rows] == ["t2"]
    assert [p["thread_id"] for p in model.call_args.args[0]] == ["t2"]
