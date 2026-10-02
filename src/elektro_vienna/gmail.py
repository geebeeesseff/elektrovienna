"""GET-only Gmail adapter; byte acquisition is separate from Phase 1 metadata reads."""

import base64
import binascii
import json
import os
import re
import random
import time
from urllib.parse import quote

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException

from .config import Config, ConfigurationError
from .models import Attachment, Message, OriginalMessage, Page, ProviderError, SCOPES

BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
HEADER_NAMES = frozenset({"from", "to", "cc", "bcc", "date", "message-id", "in-reply-to", "references"})
MAX_MIME_DEPTH = 20
REQUEST_INTERVAL = 0.25
MAX_RETRIES = 6
MAX_BACKOFF = 32.0


def http_failure(response) -> tuple[str, bool]:
    """Only allowlisted reason codes cross the provider boundary, never error text."""
    status = response.status_code
    fallback = (f"gmail_http_{status}", status in {429, 500, 502, 503, 504})
    if status != 403:
        return fallback
    try:
        errors = response.json()["error"]["errors"]
        if not isinstance(errors, list) or not errors:
            return fallback
        reasons = {error["reason"] for error in errors}
    except (ValueError, KeyError, TypeError):
        return fallback
    # Policy/daily failures win over a simultaneous transient reason.
    for reason, code in (("domainPolicy", "gmail_domain_policy"),
                         ("dailyLimitExceeded", "gmail_daily_limit_exceeded")):
        if reason in reasons:
            return code, False
    if not reasons.issubset({"rateLimitExceeded", "userRateLimitExceeded"}):
        return fallback
    if "userRateLimitExceeded" in reasons:
        return "gmail_user_rate_limit_exceeded", True
    return "gmail_rate_limit_exceeded", True


def part_fields(depth: int) -> str:
    fields = "partId,mimeType,filename,headers(name,value),body(attachmentId,size)"
    # Sentinel children reveal depth overflow without ever requesting body.data.
    return fields + ",parts(" + (part_fields(depth - 1) if depth else "partId") + ")"


MESSAGE_FIELDS = "id,threadId,internalDate,labelIds,payload(" + part_fields(MAX_MIME_DEPTH) + ")"


def inline_part_fields(depth: int) -> str:
    fields = "partId,mimeType,filename,body(attachmentId,size,data)"
    return fields + ",parts(" + (inline_part_fields(depth - 1) if depth else "partId") + ")"


INLINE_FIELDS = "id,payload(" + inline_part_fields(MAX_MIME_DEPTH) + ")"


def decode_bytes(value) -> bytes:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]*={0,2}", value):
            raise ValueError
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise ProviderError("gmail_invalid_source_bytes") from None


def check_scopes(scopes) -> None:
    if set(scopes or ()) != set(SCOPES):
        raise ConfigurationError("OAuth must grant exactly gmail.readonly. Remove the local token and authorize a dedicated Desktop client again.")


def authenticate(config: Config) -> AuthorizedSession:
    if not config.client_file.is_file():
        raise ConfigurationError(
            f"Desktop OAuth credentials missing. Enable Gmail API, create an OAuth Desktop app, "
            f"and place its downloaded JSON at {config.client_file}. Then rerun the command."
        )
    try:
        client = json.loads(config.client_file.read_text(encoding="utf-8"))
        installed = client.get("installed", {})
        if (installed.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth" or
                installed.get("token_uri") != "https://oauth2.googleapis.com/token"):
            raise ConfigurationError("Expected a Google OAuth Desktop app credential file with standard Google endpoints.")
        credentials = None
        if config.token_file.exists():
            token = json.loads(config.token_file.read_text(encoding="utf-8"))
            check_scopes(token.get("scopes"))
            if token.get("token_uri") != installed["token_uri"] or token.get("client_id") != installed.get("client_id"):
                raise ConfigurationError("Token does not belong to the configured Google Desktop client.")
            credentials = Credentials.from_authorized_user_info(token)
            if not credentials.valid and credentials.refresh_token:
                credentials.refresh(Request())
        if credentials is None or not credentials.valid:
            flow = InstalledAppFlow.from_client_config(client, scopes=list(SCOPES), autogenerate_code_verifier=True)
            credentials = flow.run_local_server(
                host="localhost", port=0, timeout_seconds=180, prompt="consent",
                include_granted_scopes="false",
            )
        check_scopes(credentials.granted_scopes or credentials.scopes)
        config.credentials_dir.mkdir(parents=True, exist_ok=True)
        temporary = config.token_file.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as output:
            os.chmod(temporary, 0o600)
            output.write(credentials.to_json())
        temporary.replace(config.token_file)
        return AuthorizedSession(credentials)
    except ConfigurationError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, OSError, GoogleAuthError, RequestException, OAuth2Error):
        raise ConfigurationError("OAuth failed. Check the Desktop client, consent access, and local token; no mailbox processing occurred.") from None


def parse_message(raw: dict) -> Message:
    try:
        message_id, thread_id = raw["id"], raw["threadId"]
        if any(not isinstance(value, str) or not value for value in (message_id, thread_id)):
            raise ValueError
        internal_date = raw["internalDate"]
        if not isinstance(internal_date, str) or not internal_date.isdecimal():
            raise ValueError
        labels = raw.get("labelIds", [])
        if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
            raise ValueError
        headers = tuple((h["name"].lower(), h["value"]) for h in raw["payload"].get("headers", [])
                        if h["name"].lower() in HEADER_NAMES)
        attachments = []
        seen = set()

        def walk(part, depth=0):
            if depth > MAX_MIME_DEPTH or "mimeType" not in part:
                raise ProviderError("mime_structure_incomplete")
            body = part.get("body", {})
            disposition = next((h["value"].lower() for h in part.get("headers", [])
                                if h["name"].lower() == "content-disposition"), "")
            if part.get("filename") or body.get("attachmentId") or disposition.startswith(("attachment", "inline")):
                part_id = part["partId"]  # Empty string is Gmail's valid root-part identity.
                if not isinstance(part_id, str) or part_id in seen:
                    raise ValueError
                seen.add(part_id)
                size = body["size"]
                if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                    raise ValueError
                if body.get("attachmentId") is not None and not isinstance(body["attachmentId"], str):
                    raise ValueError
                if not isinstance(part.get("filename", ""), str) or not isinstance(part["mimeType"], str):
                    raise ValueError
                attachments.append(Attachment(part_id, body.get("attachmentId"), part.get("filename", ""), part["mimeType"], size))
            for child in part.get("parts", []):
                walk(child, depth + 1)

        walk(raw["payload"])
        return Message(message_id, thread_id, int(internal_date), headers, tuple(labels), tuple(attachments))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProviderError("invalid_message_metadata") from None


class GmailReader:
    def __init__(self, session, *, sleep=time.sleep, clock=time.monotonic, jitter=random.random):
        self._session = session
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter
        self._next_request = 0.0

    def _get(self, path: str, params: dict) -> dict:
        try:
            for attempt in range(MAX_RETRIES + 1):
                # Pace all Gmail GETs, including retries and list/profile overhead.
                # First request is immediate; slow requests already satisfy the interval.
                delay = self._next_request - self._clock()
                if delay > 0:
                    self._sleep(delay)
                self._next_request = self._clock() + REQUEST_INTERVAL
                response = self._session.get(BASE_URL + path, params=params, timeout=30, allow_redirects=False)
                if response.status_code == 200:
                    result = response.json()
                    if not isinstance(result, dict):
                        raise ValueError
                    return result
                code, retryable = http_failure(response)
                if not retryable or attempt == MAX_RETRIES:
                    raise ProviderError(code)
                self._sleep(min(2 ** attempt + self._jitter(), MAX_BACKOFF))
        except GoogleAuthError:
            raise ProviderError("gmail_authentication_failed") from None
        except RequestException:
            raise ProviderError("gmail_transport_failure") from None
        except ValueError:
            raise ProviderError("gmail_invalid_response") from None

    def mailbox(self) -> str:
        result = self._get("/profile", {"fields": "emailAddress"})
        if not isinstance(result.get("emailAddress"), str):
            raise ProviderError("invalid_mailbox_identity")
        return result["emailAddress"].casefold()

    def list_messages(self, cutoff_ms: int, token: str | None, size: int) -> Page:
        params = {"q": f"after:{cutoff_ms // 1000 - 1} -in:spam -in:trash -in:drafts",
                  "includeSpamTrash": "false", "maxResults": size,
                  "fields": "messages/id,nextPageToken"}
        if token:
            params["pageToken"] = token
        result = self._get("/messages", params)
        try:
            ids = tuple(item["id"] for item in result.get("messages", []))
            if any(not isinstance(item, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", item) for item in ids):
                raise ValueError
            next_token = result.get("nextPageToken")
            if next_token is not None and (not isinstance(next_token, str) or not next_token):
                raise ValueError
            return Page(ids, next_token)
        except (KeyError, TypeError, ValueError):
            raise ProviderError("invalid_page_metadata") from None

    def get_message(self, message_id: str) -> Message:
        raw = self._get("/messages/" + quote(message_id, safe=""), {"format": "full", "fields": MESSAGE_FIELDS})
        result = parse_message(raw)
        if result.message_id != message_id:
            raise ProviderError("message_identity_mismatch")
        return result

    def get_original(self, message_id: str) -> OriginalMessage:
        raw = self._get("/messages/" + quote(message_id, safe=""),
                        {"format": "raw", "fields": "id,threadId,internalDate,raw"})
        try:
            if (raw["id"] != message_id or not isinstance(raw["threadId"], str)
                    or not raw["threadId"] or not isinstance(raw["internalDate"], str)
                    or not raw["internalDate"].isdecimal()):
                raise ValueError
            data = decode_bytes(raw["raw"])
            if not data:
                raise ValueError
            return OriginalMessage(message_id, raw["threadId"], int(raw["internalDate"]), data)
        except (KeyError, TypeError, ValueError):
            raise ProviderError("gmail_invalid_original") from None

    def get_attachment(self, message_id: str, attachment: Attachment) -> bytes:
        path = "/messages/" + quote(message_id, safe="")
        if attachment.attachment_id:
            body = self._get(path + "/attachments/" + quote(attachment.attachment_id, safe=""),
                             {"fields": "data,size"})
        else:
            return self.get_inline_attachment(message_id, attachment)
        # Google's JSON may omit the empty base64 string for a zero-length leaf.
        if "data" not in body and not (body.get("size") == 0 and attachment.size == 0):
            raise ProviderError("attachment_bytes_unavailable")
        data = decode_bytes(body.get("data", ""))
        if (type(body.get("size")) is not int or body["size"] != len(data)
                or attachment.size != len(data)):
            raise ProviderError("attachment_size_mismatch")
        return data

    def get_inline_attachment(self, message_id: str, attachment: Attachment) -> bytes:
        """Read an exact Gmail part body, including a declared empty container body."""
        if attachment.attachment_id is not None:
            raise ProviderError("inline_attachment_id_unexpected")
        raw = self._get("/messages/" + quote(message_id, safe=""),
                        {"format": "full", "fields": INLINE_FIELDS})
        if raw.get("id") != message_id:
            raise ProviderError("attachment_identity_mismatch")
        matches = []
        seen = set()

        def walk(part, depth=0):
            if (depth > MAX_MIME_DEPTH or not isinstance(part, dict)
                    or not isinstance(part.get("partId"), str)
                    or not isinstance(part.get("mimeType"), str)):
                raise ProviderError("gmail_invalid_inline_structure")
            identity = part["partId"]
            if identity in seen:
                raise ProviderError("attachment_identity_mismatch")
            seen.add(identity)
            children = part.get("parts", [])
            if not isinstance(children, list):
                raise ProviderError("gmail_invalid_inline_structure")
            if children and not part["mimeType"].startswith(("multipart/", "message/")):
                raise ProviderError("gmail_invalid_inline_structure")
            if identity == attachment.part_id:
                matches.append(part)
            for child in children:
                walk(child, depth + 1)

        walk(raw.get("payload"))
        if not matches:
            raise ProviderError("inline_part_not_found")
        part = matches[0]
        if part["mimeType"] != attachment.mime_type or part.get("filename", "") != attachment.filename:
            raise ProviderError("attachment_identity_mismatch")
        body = part.get("body")
        if not isinstance(body, dict):
            raise ProviderError("gmail_invalid_inline_structure")
        if body.get("attachmentId") is not None:
            raise ProviderError("inline_attachment_id_unexpected")
        size = body.get("size")
        if type(size) is not int or size < 0 or size != attachment.size:
            raise ProviderError("attachment_size_mismatch")
        if "data" not in body or body["data"] == "":
            if size:
                raise ProviderError("attachment_bytes_unavailable")
            # This is the body's declared zero bytes, not a reconstructed MIME subtree.
            return b""
        data = decode_bytes(body["data"])
        # Inline data is authoritative after identity and provider-size stability checks.
        # Archival state records any difference from attachment.size as an anomaly.
        return data
