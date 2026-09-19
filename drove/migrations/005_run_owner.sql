-- Which process owns a run. Job state lives in memory, so a daemon that dies takes with it the
-- only record that work was in flight, leaving the row claiming to run forever. The pid makes
-- that recoverable: at startup a run whose owner is gone is provably orphaned, while one owned
-- by a live `drove execute` in a terminal is left alone.

ALTER TABLE runs ADD COLUMN owner_pid INTEGER;
