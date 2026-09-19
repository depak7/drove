-- Why a run failed. It was emitted over SSE and nowhere else, so refreshing the page lost the
-- only account of what went wrong.

ALTER TABLE runs ADD COLUMN error TEXT;
