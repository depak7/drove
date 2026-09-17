# Vorflux Desktop for macOS

Created: 2026-09-17
Category: UX
Status: Final
Research: Standard

## Problem Statement

Vorflux needs to feel like a native Mac developer tool: choose an existing local repository,
supervise a feature, and act on approvals without a browser tab or typed filesystem paths.

## Core User Flows

### Choose a repository

1. User selects Add repository.
2. macOS presents its native folder picker.
3. User selects an existing Git repository.
4. Vorflux validates and adds the original checkout; feature worktrees are created only later.

### Supervise work

1. User creates a feature from the dark command center.
2. The app shows planning, execution, review, verification, and delivery.
3. A compact panel below the MacBook notch surfaces active work and approval decisions.
4. Macs without a notch use the same floating-pill surface.

## Scope

### In Scope

- Electron shell for macOS with the existing Python engine managed as a local sidecar.
- Raycast-inspired dark command center using the existing React renderer.
- Native folder selection for existing Git repositories.
- Typed desktop bridge for native functionality and engine control.
- Notch Pulse for active run, approval, failure, and completion states.
- Menu-bar controls, notifications, Dock badge, and command palette.

### Explicitly Out of Scope

- Windows and Linux packaging in the first desktop release.
- Drawing inside the physical camera cutout.
- Pipeline-semantic, cloud, collaboration, or embedded-editor changes.

## Technical Context

- The existing Python service owns workspaces, SQLite, Git worktrees, and harness execution.
- The existing React client becomes the Electron renderer.
- Electron main owns native permissions and sidecar lifecycle; renderer access is through a typed bridge.
- Platform capabilities remain isolated for later Windows/Linux implementations.

## Key Decisions

| Decision | Choice | Why |
|---|---|---|
| Target | macOS first | Supports the requested notch experience and gives a focused release target. |
| Shell | Electron | Fits process management, native UI, and the existing React stack. |
| Repository selection | Native folder picker | Existing repos are selected directly, without typed paths or a custom browser. |
| Visual direction | Raycast-inspired | Dense, keyboard-first, dark, and intentional. |
| Status surface | Contextual notch panel | Shows actionable run state without becoming decoration. |
