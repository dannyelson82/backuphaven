# 0001: License GPL-3.0

- Status: proposed
- Date: 2026-10-16

## Context
BackupHaven will be published on GitHub and listed in Unraid Community
Applications, and outside authors may write plugins for it.

## Decision
GNU GPL v3, the same as ReelHaven. Plugins talk to BackupHaven through a
documented message protocol or recipe files (data). They aren't linked into it,
so plugin authors may choose their own licence.

## Consequences
Improvements to BackupHaven itself stay open source. Plugin authors aren't
forced to use the GPL.
