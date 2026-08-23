"""Local-storage manifest hygiene.

blinkpy accumulates every manifest build into one ``SortedSet`` that is never
cleared. Membership in that set is decided by ``LocalStorageMediaItem.__hash__``
— which returns the clip id — while ``__eq__`` compares only ``created_at``.
The two disagree, so when Blink re-issues clip ids for a regenerated manifest
the same recording is stored once per build: the set grows without bound and
the API hands the UI a pile of twins, one per refresh since the process
started.

A recording is identified here by ``(camera, created_at)`` — the two
properties that survive a manifest rotation. Ids are not identity.
"""

import logging

logger = logging.getLogger(__name__)


def clip_key(item):
    """Identity of a recording, stable across manifest rotations."""
    return (item.name, item.created_at)


def _from_current_build(item, manifest_id):
    return manifest_id is not None and getattr(item, "_manifest_id", None) == manifest_id


def prune_manifest(mod):
    """Collapse repeated builds of the same clip in place, newest build wins.

    Returns the surviving clips, newest first. The copy from the current
    manifest is preferred because its id is the one whose download URL still
    resolves; older copies point at a build Blink has since replaced.
    """
    storage = getattr(mod, "_local_storage", None) or {}
    manifest = storage.get("manifest")
    if not manifest:
        return []
    manifest_id = storage.get("last_manifest_id")

    best = {}
    for item in manifest:
        key = clip_key(item)
        kept = best.get(key)
        if kept is None or (
            _from_current_build(item, manifest_id)
            and not _from_current_build(kept, manifest_id)
        ):
            best[key] = item

    removed = len(manifest) - len(best)
    if removed:
        logger.warning(
            "manifest carried %d duplicate clip(s) from earlier builds; kept %d",
            removed, len(best),
        )
        manifest.clear()
        manifest.update(best.values())

    return sorted(best.values(), key=lambda i: i.created_at, reverse=True)
