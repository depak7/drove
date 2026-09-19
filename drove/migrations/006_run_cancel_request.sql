-- A cancel asked for by a process that does not own the run. Signals proved unusable for this:
-- a `drove execute` streaming a harness ignores SIGINT, to the pid and to its process group
-- alike, and runs to completion regardless. So the request is written down and the owner acts on
-- it itself, which also means the mechanism does not depend on the two processes sharing a
-- machine's signal semantics.

ALTER TABLE runs ADD COLUMN cancel_requested REAL;
