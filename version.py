"""
Single source of truth for the workbench version.

Follows Semantic Versioning (https://semver.org/):
  MAJOR — incompatible API / file-format changes
  MINOR — new backwards-compatible features
  PATCH — backwards-compatible bug fixes
"""

MAJOR = 0
MINOR = 5
PATCH = 0

VERSION = (MAJOR, MINOR, PATCH)
VERSION_STRING = f"{MAJOR}.{MINOR}.{PATCH}"  # 0.7.0
