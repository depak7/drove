# Polished Local Engineering Workspace

Created: 2026-09-17
Category: UX
Status: Final
Research: None

## Problem Statement

Drove already has a local engineering pipeline: a user selects a workspace of one or more
repositories, describes a feature, approves a plan, and receives a reviewed branch plus an
evidence pack. Its current localhost UI exposes that capability, but it feels like an internal
control panel: repository paths are pasted, feature state is terse, and the most important
information is fragmented across simple tabs and raw event logs.

The product should instead feel like a deliberate local engineering workspace for a Mac developer:
pick repositories, ask for a feature, understand what will happen, supervise work with confidence,
and inspect what was delivered. The web app is the primary surface now. A native Mac wrapper is a
later packaging decision, after the underlying pipeline and API are stable.

## Core User Flows

### Flow 1: Set up a workspace

1. The user opens the local Drove web app.
2. The user creates or selects a named workspace.
3. The user chooses local Git repositories through a Mac-friendly repository picker.
4. The app confirms the repositories, their base branches, availability, and verification setup.
5. The user can see when a workspace spans multiple repositories and the implication that related
   branches must land together.

### Flow 2: Ask for a feature

1. The user selects a workspace from the persistent workspace navigation.
2. The user describes the feature in a focused composer.
3. The app creates the feature and shows that planning is underway.
4. When the plan is ready, the user sees its summary, intended work, affected repositories, risks,
   and completion criteria in one reviewable view.

### Flow 3: Make the approval decision

1. The user reviews the proposed plan.
2. The user either approves it, sends revision feedback, or declines it.
3. The app makes the resulting state and next action unambiguous.
4. If the user changes direction later, they can pivot while retaining the feature's existing
   branch and agent context.

### Flow 4: Supervise a run

1. After approval, the user sees a legible timeline for planning, implementation, review, fixes,
   verification, and delivery.
2. The app surfaces important events, failures, connection state, and rate-limit pressure without
   requiring the user to interpret a raw stream of tool output.
3. The user can open detailed live activity when needed without losing the high-level status.

### Flow 5: Inspect delivery

1. When work finishes, the user sees the final status, reviewer outcome, verification outcome,
   changed repositories and branches, and clear next actions.
2. The user can inspect the combined diff and the evidence pack.
3. A multi-repository feature clearly states that its branches must be landed together.

## Scope

### In Scope

- A cohesive, desktop-quality localhost web experience for existing Drove workspaces and
  features.
- Persistent workspace navigation that makes selected repositories, repository health, base
  branches, verification setup, and feature counts easy to understand.
- A repository onboarding experience suited to a local Mac installation, using a safe local
  repository picker rather than requiring path entry as the primary path.
- A focused feature composer and clear empty states that lead a new user from workspace setup to
  their first planned feature.
- An improved plan-review and approval experience that presents the existing plan content,
  revision feedback, approval, decline, and pivot actions in context.
- A readable lifecycle view that emphasizes stage, progress, reviewer and verification outcomes,
  with detailed live logs available on demand.
- Delivery views for diffs and evidence that explain the feature's branches and multi-repository
  landing constraints.
- Responsive layout that remains usable in a browser window on a Mac laptop.

### Explicitly Out of Scope

- A native macOS application or menu-bar app — the web experience comes first; a wrapper can use
  the stable API later.
- Changes to the engineering pipeline's plan, execute, review, fix, verify, or evidence semantics
  — this milestone improves how existing behavior is understood and controlled.
- Cloud accounts, hosted workspaces, remote repositories, or team collaboration — Drove remains
  a local-first tool.
- An in-app code editor, terminal emulator, or replacement for a developer's existing IDE.
- GitHub PR creation and merge automation — these remain separate delivery roadmap work.
- New model providers, model pricing policy, or changes to model-selection behavior.

## Technical Context

- **Relevant architecture:** The React/Vite client in `web/` communicates with the local FastAPI
  server in `drove/api/server.py`; both use the same workspace, feature, and run data maintained
  by the Python application.
- **Existing capabilities:** Workspaces already hold one or more Git repositories, and feature
  creation already creates isolated worktrees, starts planning, and exposes plan, live event,
  diff, and evidence data through the local API.
- **Current UX surface:** `web/src/App.jsx`, `web/src/Workspaces.jsx`, and
  `web/src/components.jsx` provide the current workspace management, feature controls, lifecycle
  tabs, and event display.
- **Constraints:** The application runs locally and must preserve the existing safety boundaries:
  repository access is explicit, work occurs in feature-specific worktrees, and multi-repository
  branches cannot be merged atomically.
- **Future seam:** The local API is the product foundation; a future Mac desktop shell should be
  able to use it without changing the feature/workspace model.

## Key Decisions

| Decision | Choice | Why |
|---|---|---|
| Primary product surface | Polished local web app | It reuses the working API and pipeline, delivers the biggest usability gain now, and avoids splitting effort across two frontends. |
| Native-app timing | Later, after backend/API stability | A desktop shell should package a proven local workflow rather than become a second source of product risk. |
| Repository onboarding | Local Mac-friendly picker | Selecting a repository should feel intentional and safe; pasted filesystem paths are an expert fallback, not the main experience. |
| Workspace model | Preserve named, multi-repository workspaces | This is already the product's correct abstraction for both one-repo and cross-repo work. |
| UX emphasis | Decision clarity over terminal imitation | Users need to understand state, risk, review, verification, and next action; raw logs remain useful detail, not the primary interface. |
| Scope boundary | No pipeline-semantic change in this milestone | Separating UX polish from new execution behavior keeps the work testable and makes later product additions easier to reason about. |
