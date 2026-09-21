---
name: paperless-inbox
description: Sort and triage documents in the Paperless-ngx intake path when the user asks to sort, organize, process, or review their Paperless inbox or Eingang. Do not use for broad archive cleanup.
---

# Paperless Inbox

Use the Paperless-ngx MCP tools only. Treat "Inbox bitte sortieren" and equivalent requests as
authorization to update metadata on documents currently in the configured intake path. It does not
authorize trashing documents, deleting anything, changing organization objects, or reprocessing
OCR.

## Locate the inbox

1. Check `paperless_status`.
2. Call `list_workflows` and find the single enabled workflow named
   `Standard-Eingang – neue Dokumente`. Read it with `get_workflow`. Accept its
   `assign_storage_path` only when the workflow has an unfiltered `Document Added` trigger and an
   assignment action. Resolve that ID with `list_metadata` instead of assuming a fixed path name or
   ID.
3. If that workflow is missing, duplicated, disabled, filtered, or does not assign a storage path,
   list the storage paths and ask the user which one is the inbox. Show only plausible choices and
   wait for the selection; do not guess, create a path, or change a workflow. Reuse the selection
   for the current request.
4. Read every document assigned to the resolved path with `find_documents_by_metadata`, following
   all pages. An empty inbox is a successful outcome.
5. Load the compact correspondent, document-type, storage-path, and tag catalogs once. Reuse
   existing metadata; do not create near-duplicates.

## Classify

For each inbox document:

- Read its OCR text with `get_document` and consult `get_document_suggestions`.
- Use `search_documents` in `similar` mode when existing assignments are not obvious. Existing
  archive patterns are evidence, not authority; the document itself must support the result.
- Prefer an existing correspondent, document type, destination storage path, and useful tags.
- Change title or document date only when the document states them clearly.
- Mark the classification as confident only when the document evidence is unambiguous and agrees
  with the chosen existing metadata. Leave uncertain, contradictory, unreadable, or incomplete
  documents in the inbox.

## Apply safe changes

- Apply title or date with `update_document`, assignments with `set_document_metadata_field`, and
  additive tags with `modify_document_tags`.
- Do not replace the complete tag list and do not remove existing tags during routine inbox sorting.
- Set the destination `storage_path` last. Moving it out of the inbox means processing is complete.
- Group documents that receive the same assignment when the tool supports batching.
- If a write fails, leave that document in the inbox and report the failure. Do not retry a
  non-idempotent operation blindly.

Never call trash, delete, metadata-creation, metadata-deletion, workflow, or OCR-reprocessing tools
as part of inbox sorting. If the needed metadata does not exist, propose it and leave the document
in the inbox until the user explicitly requests that separate change.

## Report

Return a compact summary with:

- documents sorted and their destination paths;
- metadata changed per document;
- documents left for review and the specific uncertainty;
- failures, without exposing document OCR text unnecessarily.
