# Inbox Agent Architecture and Flow

## Purpose

This document explains why the Inbox Agent requires deliberate engineering time. The agent is not a simple email reader; it is a reliability and data-quality pipeline that must safely ingest operational emails into production databases without duplicates, data loss, or silent parsing errors.

## Business Outcome

- Reduce manual inbox handling effort for operations teams.
- Ingest reports into the database in near real-time.
- Improve data consistency, traceability, and audit readiness.
- Prevent operational mistakes caused by malformed or duplicate emails.

## Why Development Takes Time

The complexity is in safe automation, not in fetching emails:

1. Email formats are inconsistent (free text, HTML, forwarded chains, attachments).
2. Multiple report types require different parsing and validation rules.
3. Database writes must be idempotent and auditable.
4. Failures must be recoverable (retry, dead-letter, replay).
5. Production operations require observability, alerts, and operator controls.

## System Architecture

### 1) Mailbox Connector Layer

- Connects to IMAP or Graph API.
- Handles auth refresh, rate limits, and connection retries.
- Tracks mailbox checkpoints (`last_uid`, `last_sync_time`) to avoid re-reading entire inboxes.

### 2) Ingestion Orchestrator

- Poll scheduler with jitter/backoff.
- Pulls new messages in controlled batches.
- Assigns a `correlation_id` per message for full traceability.

### 3) Message Preprocessor

- Parses MIME structure (plain text, HTML, forwarded content).
- Extracts attachments and computes hashes (`sha256`) for dedupe.
- Normalizes encoding, line breaks, and timezone fields.

### 4) Classification Engine

- Determines report type (noon, weather, port, incident, unknown).
- Uses deterministic rules first; optional model fallback for ambiguous inputs.
- Produces confidence score and routing decision.

### 5) Extraction and Mapping

- Applies report-type-specific extractors.
- Parses body text and tabular attachments (CSV/XLS/XLSX).
- Maps raw values into a canonical schema used by downstream services.

### 6) Validation and Enrichment

- Enforces required fields and type checks.
- Standardizes units/time formats.
- Enriches records with vessel and voyage context from master tables.

### 7) Persistence Layer

- Stores immutable raw payload metadata for audit/debug.
- Upserts parsed records with idempotency keys.
- Writes processing audit events for each stage.

### 8) Reliability and Recovery

- Retry queue for transient failures (network, temporary DB issues).
- Dead-letter queue for poison messages and parsing failures.
- Replay endpoint/tool to reprocess specific failed messages.

### 9) Observability and Operations

- Structured logs with stage and error code.
- Metrics: throughput, success rate, duplicate rate, parse latency.
- Alerts on backlog growth, failure spikes, or connector outages.

## End-to-End Processing Flow

1. Scheduler triggers inbox poll.
2. Connector fetches only new messages since checkpoint.
3. Preprocessor creates canonical `InboundMessageEnvelope`.
4. Deduplication check runs using message ID + attachment hash.
5. Classifier assigns report type and confidence.
6. Extractor parses and maps to canonical data model.
7. Validator enforces quality rules and enriches missing context.
8. Persistence writes raw metadata + parsed records + audit trail in transaction.
9. On success, checkpoint advances.
10. On failure, route to retry or dead-letter with reason.

## Non-Functional Requirements

- **Idempotency:** replay-safe and duplicate-proof ingestion.
- **Data integrity:** transactional writes and schema validation.
- **Security:** secret-safe auth handling and PII-safe logs.
- **Scalability:** batch processing for daily volume spikes.
- **Operability:** dashboards, alerts, and reprocessing controls.

## One-Week Delivery Plan (Management View)

### Day 1: Connectivity and checkpointing
- Implement connector abstraction (IMAP/Graph-ready).
- Add mailbox sync checkpoint model.

### Day 2: Message preprocessing foundation
- MIME/body parser and attachment extraction.
- Envelope and metadata contracts.

### Day 3: Classification and routing
- Rule-based classifier with confidence output.
- Unknown/ambiguous routing paths.

### Day 4: Extraction and validation
- Report-specific field extraction.
- Validation rules and unit/time normalization.

### Day 5: Persistence and idempotency
- Raw metadata archive + parsed upsert logic.
- Idempotency keying and transaction boundaries.

### Day 6: Reliability controls
- Retry queue, dead-letter handling, replay utility.
- Structured error codes and failure reason taxonomy.

### Day 7: Hardening and readiness
- Integration tests with sample real-world messages.
- Metrics/alerts wiring and operational runbook.

## Risks and Mitigations

- **Risk:** Unexpected email formats break parsing.
  - **Mitigation:** fallback parser + dead-letter + replay path.
- **Risk:** Duplicate ingestion from mailbox re-sync.
  - **Mitigation:** idempotency keys and checkpoint discipline.
- **Risk:** Silent data quality drift.
  - **Mitigation:** validation gates and quality metrics alarms.

## Definition of Done

- Inbox poller processes new messages continuously.
- Parsed records are correctly written without duplicates.
- Failed messages are observable and reprocessable.
- Operational metrics and alerts are available.
- Runbook exists for support and incident response.
