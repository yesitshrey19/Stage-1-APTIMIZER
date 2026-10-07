"""Engine identity, so a stored layout can be told apart from the inputs it came from.

A layout saved on a project is a snapshot. If the plot boundary is edited afterwards, or
the engine's own geometry changes, that snapshot is no longer a description of the
current site — rendering it produces buildings sitting outside a boundary they were never
packed against. Consumers compare both stamps and regenerate on any mismatch.

Bump ENGINE_VERSION whenever a change alters the geometry a given input produces.
"""
import hashlib
from typing import Sequence

# 4 — site-aware curved circulation, amenity placement and access links.
ENGINE_VERSION = 4


def polygon_signature(coordinates: Sequence[Sequence[float]]) -> str:
    """Stable digest of a plot ring. Rounded to ~1 mm so float noise is not a change."""
    if not coordinates:
        return ""
    body = "|".join(f"{float(c[0]):.8f},{float(c[1]):.8f}" for c in coordinates)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]
