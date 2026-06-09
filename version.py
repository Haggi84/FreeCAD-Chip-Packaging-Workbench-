# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Single source of truth for the workbench version.

Follows Semantic Versioning (https://semver.org/):
  MAJOR — incompatible API / file-format changes
  MINOR — new backwards-compatible features
  PATCH — backwards-compatible bug fixes
"""

MAJOR = 0
MINOR = 6
PATCH = 2

VERSION = (MAJOR, MINOR, PATCH)
VERSION_STRING = f"{MAJOR}.{MINOR}.{PATCH}"  
