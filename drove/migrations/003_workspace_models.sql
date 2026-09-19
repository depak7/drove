-- Which model each stage runs. Null means "let the CLI decide", which stays the default: pinning
-- a model id in config is how a workspace silently breaks when a provider retires one.

ALTER TABLE workspaces ADD COLUMN models TEXT;
