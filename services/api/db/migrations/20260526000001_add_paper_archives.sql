-- migrate:up
-- Raw PDF storage and parsed text for Semantic Scholar papers.
-- Keyed by S2 paperId. Parsed text is also written as a
-- source_type="paper_fulltext" row in company_context_documents (linked
-- to the metadata row via parent_document_id) so BM25 indexes it; this
-- table is the source-of-truth for the original bytes + parse metadata
-- so we can re-render the company_context_documents body from a stored
-- PDF without re-fetching from the publisher.

CREATE TABLE paper_archives (
    paper_id        TEXT PRIMARY KEY,
    source_url      TEXT NOT NULL,
    mime_type       TEXT NOT NULL,
    size_bytes      BIGINT NOT NULL,
    pdf_sha256      TEXT NOT NULL,
    pdf_bytes       BYTEA NOT NULL,
    parsed_text     TEXT NOT NULL,
    parser_used     TEXT NOT NULL,
    truncated       BOOLEAN NOT NULL DEFAULT FALSE,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    archived_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX paper_archives_pdf_sha256_idx ON paper_archives (pdf_sha256);
CREATE INDEX paper_archives_archived_at_idx ON paper_archives (archived_at DESC);

-- migrate:down
DROP TABLE IF EXISTS paper_archives;
