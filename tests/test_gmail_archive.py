import base64
from unittest.mock import Mock

import pytest

from elektro_vienna.gmail import GmailReader, decode_bytes
from elektro_vienna.models import Attachment, ProviderError, historical_cutoff_ms


def encoded(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def reader(response):
    session = Mock()
    session.get.return_value = Mock(status_code=200)
    session.get.return_value.json.return_value = response
    return GmailReader(session, sleep=lambda _: None), session


def test_original_bytes_no_reserialization():
    data = b"Subject: synthetic\r\n\r\nBinary\x00\xff\r\n"
    source, session = reader({"id":"a", "threadId":"t", "internalDate":str(historical_cutoff_ms()), "raw":encoded(data)})
    result = source.get_original("a")
    assert result.data == data and result.message_id == "a" and result.thread_id == "t"
    assert result.internal_ms == historical_cutoff_ms()
    assert session.get.call_args.kwargs["params"] == {"format":"raw", "fields":"id,threadId,internalDate,raw"}
    assert session.get.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("update", [{"id":"wrong"}, {"raw":""}, {"raw":"secret@example.invalid"},
    {"internalDate":-1}, {"threadId":None}, {"raw":None}])
def test_invalid_original_is_sanitized(update):
    response = {"id":"a", "threadId":"t", "internalDate":"1", "raw":encoded(b"synthetic")}
    response.update(update)
    source, _ = reader(response)
    with pytest.raises(ProviderError) as error:
        source.get_original("a")
    assert str(error.value) in {"gmail_invalid_original", "gmail_invalid_source_bytes"}


def test_attachment_endpoint_and_size():
    source, session = reader({"size":3, "data":encoded(b"\x00\xffA")})
    attachment = Attachment("1", "opaque/id", "unsafe/name.pdf", "application/pdf", 3)
    assert source.get_attachment("a", attachment) == b"\x00\xffA"
    assert session.get.call_args.args[0].endswith("/messages/a/attachments/opaque%2Fid")
    assert session.get.call_args.kwargs["params"] == {"fields":"data,size"}


def inline_payload(part_id="0", data=b"inline"):
    return {"id":"a", "threadId":"t", "internalDate":"1", "payload":{
        "partId":part_id, "mimeType":"image/png", "filename":"inline.png",
        "body":{"size":len(data), "data":encoded(data)}}}


@pytest.mark.parametrize("part_id", ["", "0", "0.1"])
def test_inline_without_attachment_id_uses_exact_part_id(part_id):
    response = inline_payload(part_id)
    if part_id:
        response["payload"] = {"partId":"", "mimeType":"multipart/mixed", "parts":[response["payload"]]}
    source, session = reader(response)
    assert source.get_attachment("a", Attachment(part_id,None,"inline.png","image/png",6)) == b"inline"
    assert session.get.call_args.kwargs["params"]["format"] == "full"
    assert "attachmentId,size,data" in session.get.call_args.kwargs["params"]["fields"]


@pytest.mark.parametrize("update,code", [({"size":5,"data":encoded(b"abc")}, "attachment_size_mismatch"),
    ({"size":3}, "attachment_bytes_unavailable"), ({"size":True,"data":encoded(b"abc")},"attachment_size_mismatch"),
    ({"size":3,"data":"raw private provider body"}, "gmail_invalid_source_bytes")])
def test_bad_attachment_response_is_sanitized(update,code):
    source,_ = reader(update)
    with pytest.raises(ProviderError,match=f"^{code}$"):
        source.get_attachment("a",Attachment("0","id","file","text/plain",3))


def test_inline_missing_or_changed_part_explicit_failure():
    source,_ = reader(inline_payload())
    with pytest.raises(ProviderError,match="inline_part_not_found"):
        source.get_attachment("a",Attachment("wrong",None,"inline.png","image/png",6))
    response = inline_payload()
    del response["payload"]["body"]["data"]
    source,_ = reader(response)
    with pytest.raises(ProviderError,match="attachment_bytes_unavailable"):
        source.get_attachment("a",Attachment("0",None,"inline.png","image/png",6))


def test_noncontainer_with_children_is_invalid():
    response = inline_payload()
    response["payload"]["parts"] = [{"partId":"1", "mimeType":"text/plain", "body":{"size":0}}]
    source,_ = reader(response)
    with pytest.raises(ProviderError,match="gmail_invalid_inline_structure"):
        source.get_attachment("a",Attachment("0",None,"inline.png","image/png",6))


def test_empty_container_body_and_nested_inline_bytes():
    response = {"id":"a", "payload":{"partId":"", "mimeType":"multipart/alternative",
        "body":{"size":0}, "parts":[{"partId":"1", "mimeType":"text/html",
        "body":{"size":3,"data":encoded(b"\xfb\xffA")}}]}}
    source, session = reader(response)
    assert source.get_inline_attachment("a",Attachment("",None,"","multipart/alternative",0)) == b""
    assert source.get_inline_attachment("a",Attachment("1",None,"","text/html",3)) == b"\xfb\xffA"
    fields = session.get.call_args.kwargs["params"]["fields"]
    assert "body(attachmentId,size,data)" in fields
    assert all(value not in fields for value in ("headers", "labelIds", "threadId", "internalDate", "raw", "snippet"))


@pytest.mark.parametrize("mutation,code", [
    ({"attachmentId":"external"},"inline_attachment_id_unexpected"),
    ({"data":"invalid secret content"},"gmail_invalid_source_bytes"),
    ({"data":""},"attachment_bytes_unavailable"),
    ({"size":7},"attachment_size_mismatch"),
    ({"size":True},"attachment_size_mismatch"),
])
def test_inline_body_failures_sanitized(mutation,code):
    response = inline_payload()
    response["payload"]["body"].update(mutation)
    source,_ = reader(response)
    with pytest.raises(ProviderError,match=f"^{code}$"):
        source.get_inline_attachment("a",Attachment("0",None,"inline.png","image/png",6))


@pytest.mark.parametrize("field,value", [("mimeType","text/plain"),("filename","changed.png")])
def test_inline_identity_mismatch(field,value):
    response = inline_payload()
    response["payload"][field] = value
    source,_ = reader(response)
    with pytest.raises(ProviderError,match="^attachment_identity_mismatch$"):
        source.get_inline_attachment("a",Attachment("0",None,"inline.png","image/png",6))


def test_inline_decoded_bytes_authoritative_with_stable_provider_size():
    data = b"x" * 1116 + b"\x00\xff"
    response = inline_payload(data=data)
    response["payload"]["body"]["size"] = 1111
    source,_ = reader(response)
    assert source.get_attachment("a",Attachment("0",None,"inline.png","image/png",1111)) == data


def test_duplicate_inline_part_ids_rejected():
    part = inline_payload()["payload"]
    source,_ = reader({"id":"a","payload":{"partId":"","mimeType":"multipart/mixed","parts":[part,part]}})
    with pytest.raises(ProviderError,match="^attachment_identity_mismatch$"):
        source.get_inline_attachment("a",Attachment("0",None,"inline.png","image/png",6))


def test_external_occurrence_cannot_enter_inline_method():
    source,session = reader({})
    with pytest.raises(ProviderError,match="^inline_attachment_id_unexpected$"):
        source.get_inline_attachment("a",Attachment("0","external","file","text/plain",1))
    session.get.assert_not_called()


@pytest.mark.parametrize("external", [False, True])
def test_empty_leaf_bytes_explicitly_preserved(external):
    response = inline_payload(data=b"")
    del response["payload"]["body"]["data"]
    source,_ = reader({"size":0} if external else response)
    assert source.get_attachment("a",Attachment("0","id" if external else None,"inline.png","image/png",0)) == b""


@pytest.mark.parametrize("data", ["A", "!!", "a b", "é", None, 4, "A==="])
def test_invalid_base64(data):
    with pytest.raises(ProviderError,match="gmail_invalid_source_bytes"):
        decode_bytes(data)


def test_archive_gets_share_pacing_and_retry_policy():
    now = [0.0]
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    session = Mock()
    session.get.side_effect = [Mock(status_code=429), Mock(status_code=200), Mock(status_code=200)]
    responses = session.get.side_effect = list(session.get.side_effect)
    responses[1].json.return_value = {"id":"a","threadId":"t","internalDate":"1","raw":encoded(b"synthetic")}
    responses[2].json.return_value = {"size":1,"data":encoded(b"A")}
    session.get.side_effect = responses
    source = GmailReader(session,sleep=sleep,clock=lambda:now[0],jitter=lambda:0)
    source.get_original("a")
    source.get_attachment("a",Attachment("0","id","file","text/plain",1))
    assert sleeps == [1,0.25]
