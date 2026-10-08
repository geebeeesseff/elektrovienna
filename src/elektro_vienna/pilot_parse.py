"""Bounded local text parsing. Source text is data, never executable instructions."""

import io
import logging
import re
import zipfile
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from xml.etree import ElementTree

MAX_SOURCE_BYTES = 40 * 1024 * 1024
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_TEXT = 500_000
PARSER_VERSION = "local-text-v1"


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.hidden += 1
        if tag in ("br", "p", "div", "tr", "li") and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self.hidden:
            self.hidden -= 1
        if tag in ("p", "div", "tr", "li") and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_text(text: str) -> str:
    parser = TextHTML()
    parser.feed(text)
    return "".join(parser.parts)


def section(locator: str, text: str) -> dict:
    return {"locator": locator, "text": text[:MAX_TEXT],
            "status": "text_limit" if len(text) > MAX_TEXT else ("ok" if text.strip() else "empty")}


def parse_email(raw: bytes) -> dict:
    if len(raw) > MAX_SOURCE_BYTES:
        return {"headers": [], "sections": [], "issues": ["source_size_limit"]}
    message = BytesParser(policy=policy.default).parsebytes(raw)
    headers = [(str(k), str(v)) for k, v in message.items()]
    sections, issues = [], []
    for i, (name, value) in enumerate(headers):
        if name.casefold() in {"subject", "from", "to", "cc", "date", "reply-to"}:
            sections.append(section(f"header:{i}:{name.casefold()}", value))

    def visit(part, path, depth):
        if depth > 25 or len(sections) > 200:
            issues.append("mime_limit")
            return
        if part.defects:
            issues.append("mime_defects")
        if part.get_content_type() == "message/rfc822":
            issues.append("attached_message_not_parsed")
            return
        if part.get_content_disposition() == "attachment" or part.get_filename():
            return  # Attachment occurrences are read through their archived identity.
        if part.is_multipart():
            children = list(part.iter_parts())
            if part.get_content_subtype() == "alternative":
                plain = [(i, c) for i, c in enumerate(children) if c.get_content_type() == "text/plain"]
                chosen = plain[:1] or [(i, c) for i, c in enumerate(children) if c.get_content_type() == "text/html"][:1]
                if chosen:
                    for i, child in chosen:
                        visit(child, f"{path}.{i}", depth + 1)
                    return
            for i, child in enumerate(children):
                visit(child, f"{path}.{i}", depth + 1)
        elif part.get_content_type() in ("text/plain", "text/html"):
            data = part.get_payload(decode=True) or b""
            try:
                text = data.decode(part.get_content_charset() or "utf-8", errors="strict")
            except (UnicodeError, LookupError):
                text = data.decode("utf-8", errors="replace")
                issues.append("charset_uncertain")
            if part.get_content_type() == "text/html":
                text = html_text(text)
            sections.append(section(f"mime:{path}:decoded-text", text))

    visit(message, "0", 0)
    issues.extend(s["status"] for s in sections if s["status"] not in {"ok", "empty"})
    return {"headers": headers, "sections": sections, "issues": sorted(set(issues))}


def parse_document(raw: bytes, mime: str, filename: str) -> dict:
    result = {"sections": [], "issues": [], "format": mime}
    if len(raw) > MAX_DOCUMENT_BYTES:
        result["issues"] = ["document_size_limit"]
        return result
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    try:
        if mime == "application/pdf" or extension == "pdf":
            from pypdf import PdfReader
            # Parser diagnostics may contain customer source fragments. Persist safe codes only.
            logger = logging.getLogger("pypdf")
            previous = logger.disabled
            logger.disabled = True
            try:
                reader = PdfReader(io.BytesIO(raw))
                if reader.is_encrypted:
                    result["issues"] = ["encrypted_pdf"]
                    return result
                if len(reader.pages) > 80:
                    result["issues"] = ["pdf_page_limit"]
                    return result
                for index, page in enumerate(reader.pages, 1):
                    contents = page.get_contents()
                    if contents is not None and len(contents.get_data()) > 10 * 1024 * 1024:
                        result["issues"].append(f"page:{index}:content_limit")
                        continue
                    item = section(f"page:{index}:text", page.extract_text() or "")
                    result["sections"].append(item)
                    if item["status"] == "empty":
                        result["issues"].append(f"page:{index}:needs_visual_review_or_ocr")
            finally:
                logger.disabled = previous
        elif extension == "docx" or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if sum(info.file_size for info in archive.infolist()) > MAX_DOCUMENT_BYTES:
                    result["issues"] = ["docx_expansion_limit"]
                    return result
                xml = ElementTree.fromstring(archive.read("word/document.xml"))
                ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                for index, paragraph in enumerate(xml.iter(ns + "p"), 1):
                    text = "".join(n.text or "" for n in paragraph.iter(ns + "t"))
                    if text.strip():
                        result["sections"].append(section(f"document:paragraph:{index}", text))
                result["issues"].append("docx_body_only_headers_comments_not_parsed")
        elif mime in ("text/plain", "text/html") or extension in ("txt", "html", "htm"):
            text = raw.decode("utf-8-sig")
            if mime == "text/html" or extension in ("html", "htm"):
                text = html_text(text)
            result["sections"].append(section("document:text", text))
        else:
            result["issues"] = ["image_requires_visual_review" if mime.startswith("image/") else "unsupported_format"]
    except ImportError:
        result["issues"].append("pdf_dependency_missing")
    except Exception:
        # Third-party parsers may include document text/path fragments in exceptions.
        # Preserve successful earlier pages and visibly mark partial failure.
        result["issues"].append("document_parse_failed")
    result["issues"].extend(s["status"] for s in result["sections"] if s["status"] not in {"ok", "empty"})
    if not result["sections"] and not result["issues"]:
        result["issues"] = ["no_readable_text"]
    return result


TECHNICAL = {
    "electrical_capacity": r"(?<!\w)\d+(?:[.,]\d+)?\s*(?:kW|kVA|V|A|Ampere|Volt)\b|\b(?:ein|drei|1|3)[ -]phas\w*",
    "dimension_distance": r"(?<!\w)\d+(?:[.,]\d+)?\s*(?:mm²|mm2|mm|cm|m²|m2|Meter|m)\b",
    "quantity": r"(?<!\w)\d+\s*(?:Stück|Stk\.?|Lampen?|Leuchten?|Steckdosen?|Schalter)\b",
    "equipment_material": r"\b(?:Wallbox|FI[- ]?Schalter|Sicherung|Kabel|Leitung|Verteiler|Ladestation|Herd|LED|Smartmeter)\w*",
    "condition_constraint": r"\b(?:Altbau|Unterputz|Aufputz|Stemmarbeit|Zugang|Denkmalschutz|Voraussetzung|bauseits)\w*",
    "open_information": r"\b(?:offen|fehlt|fehlen|unklar|noch zu klären|bitte.*(?:Foto|Maß|Leistung))\b",
}
MONEY = re.compile(r"(?<!\w)(?:\d{1,3}(?:[. ]\d{3})+|\d+)(?:[,.]\d{1,2})?\s*(?:€|EUR\b|Euro\b)|(?:€|EUR\b)\s*\d+(?:[.,]\d+)*", re.I)


def extract_mentions(sections: list[dict], source_id: str) -> tuple[list[dict], list[dict]]:
    """Literal candidates only. No guessed total, VAT, issuer, event, or global rule."""
    technical, commercial = [], []
    for item in sections:
        if item["locator"].startswith("header:"):
            continue
        text = item["text"]
        offset = 0
        for line in text.splitlines(keepends=True):
            ref = {"source_id": source_id, "locator": item["locator"], "start": offset,
                   "end": offset + len(line), "quote": line}
            for name, pattern in TECHNICAL.items():
                if re.search(pattern, line, re.I):
                    technical.append({"field": name, "value": line.strip(), "certainty": "uncertain_candidate",
                                      "source_refs": [ref], "review_status": "pending"})
            amounts = [match.group() for match in MONEY.finditer(line)]
            labor = re.search(r"\b\d+(?:[.,]\d+)?\s*(?:Stunden?|Std\.?|Arbeitsstunden?)\b", line, re.I)
            if amounts or labor:
                # Retain literal semantic labels as mentions. They do not establish which
                # amount they qualify when a line contains several prices or quoted history.
                labels = {
                    "net_gross_mentions": r"\b(?:netto|brutto|inkl\.?\s*(?:MwSt|USt)|exkl\.?\s*(?:MwSt|USt)|zzgl\.?\s*(?:MwSt|USt))\b",
                    "hourly_rate_mentions": r"\b(?:Stundensatz|pro Stunde|je Stunde|Stundenlohn)\b|/\s*(?:h|Std)\b",
                    "material_mentions": r"\bMaterial\w*",
                    "travel_mentions": r"\b(?:Anfahrt|Anreise|Fahrtkosten|Wegzeit)\w*",
                    "price_type_mentions": r"\b(?:Pauschal\w*|Fixpreis|Festpreis|Schätzung|geschätzt|ca\.|ungefähr|Kostenvoranschlag)\b",
                    "condition_mentions": r"\b(?:inklusive|exklusive|inkl\.|exkl\.|zuzüglich|zzgl\.|bauseits|vorbehaltlich)\b",
                }
                commercial.append({"amount_mentions": amounts, "labor_mention": labor.group() if labor else None,
                    "context": line.strip(), "net_gross": "unresolved", "amount_role": "unresolved",
                    "price_type": "unresolved", "issuer": None, "certainty": "uncertain_candidate",
                    **{key: [m.group() for m in re.finditer(pattern, line, re.I)] for key, pattern in labels.items()},
                    "source_refs": [ref], "review_status": "pending",
                    "reason": "Literal mention; check tax basis, scope, total vs component, quotation and attribution in source."})
            offset += len(line)
    return technical, commercial
