# North Star

Build a continuously improving operational and technical knowledge system for Elektro Vienna.

The transformation to learn is:

**customer inquiry → information gathering → technical understanding → pricing / offer → scheduling / execution → outcome → new operational knowledge**

Document management is not the main purpose. Emails, documents, offers, invoices, and human feedback are evidence from which reusable company knowledge can be derived.

The system should improve with every processed case without allowing the newest case, newest offer, or newest feedback to silently replace established knowledge.

Preserve the operational chronology of each Case through attributable CaseEvents. Calls, WhatsApp communication, manual notes, technician feedback, site visits, offer creation/sending, appointment and status changes, and outcomes explain how an inquiry becomes a scoped and priced job, including events that never appear in Gmail. Timeline entries retain their sources; they do not automatically become reusable knowledge.

## Who the system serves

Elektro Vienna will increasingly use its own employees instead of external technicians. Capture the reasoning and experience of current external technicians and completed cases so future employees can:

- Understand inquiries, identify missing information, and ask useful questions.
- Scope work, estimate effort, prepare prices, and prepare offers.
- Prepare appointments and execute jobs with the necessary context.
- Compare expectations with results and learn from outcomes.

## Knowledge discipline

**SOURCE → EXTRACTION → EVIDENCE → KNOWLEDGE**

A source is not knowledge. An AI extraction is not automatically truth. A technician offer is evidence about a particular context, not a global company pricing rule. Human feedback is additional evidence, not an instruction to immediately rewrite global knowledge.

Reusable knowledge remains linked to its supporting and conflicting evidence and, through that evidence, to immutable originals. Uncertainty, applicability, versions, and review decisions remain visible. Improvements to extraction must allow historical reprocessing without erasing previous interpretations.

## Operational context

- Email: Gmail mailbox `office@elektrovienna.at`; historical import starts `2026-01-01 00:00:00 Europe/Vienna`, inclusive. Include received, sent, and archived mail, replies, and forwarded messages; initially exclude spam, trash, and drafts. Do not restrict by sender or technician. Reading that mailbox in Outlook does not change the ingestion provider.
- CRM: Airtable Customers, Tickets, and Craftsmen. Reuse existing identities and ticket information where appropriate; avoid duplicate customer and case systems.
- Scheduling: Microsoft Outlook Calendar, separate from Gmail email ingestion.
- Knowledge storage: the SharePoint-synchronized Knowledgebase root defined in `ARCHITECTURE.md`.

Appointments are structured operational records linked to cases. They should help employees execute work and provide the basis for customer confirmation drafts. Case facts, appointment details, and customer communications remain distinct.

## Initial boundaries and success

Human review governs uncertain associations, knowledge promotion, and prepared customer-facing output. No autonomous customer pricing or communication is allowed initially. Preparation does not authorize publishing a calendar event, sending invitations, or sending messages.

Success means employees can find supported guidance, identify what is missing, understand why a recommendation applies, and learn from actual results. Archive size alone is not a measure of success. Future evaluation should assess traceability, matching errors, extraction quality, useful knowledge, and differences between estimated and actual work; targets remain to be agreed.
