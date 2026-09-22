"""Build valid, ordered tree mutations; use a snapshot for complex churn."""

from copy import deepcopy

from .model import ProtocolError, TreeReplica
from .protocol import surface_to_dict


def changes_for(before, after):
    old, new = surface_to_dict(before), surface_to_dict(after)
    return {
        key: value
        for key, value in new.items()
        if key != "surface_id" and old[key] != value
    }


def plan_deltas(replica: TreeReplica, desired: list, *, limit=16):
    """Return (operation, surface_id, value) steps or None when snapshot is safer."""
    target = {surface.surface_id: surface for surface in desired}
    if len(target) != len(desired):
        raise ProtocolError("duplicate surface IDs")
    TreeReplica._validate(target)
    if any(sid in replica._seen and sid not in replica.surfaces for sid in target):
        raise ProtocolError("surface ID reused")
    trial = deepcopy(replica)
    steps = []
    while trial.surfaces != target:
        candidates = []
        for sid, surface in target.items():
            if sid not in trial.surfaces:
                candidates.append(("add", sid, surface))
        for sid, surface in target.items():
            before = trial.surfaces.get(sid)
            if before is not None and before != surface:
                candidates.append(("update", sid, surface))
        for sid in trial.surfaces:
            if sid not in target:
                candidates.append(("remove", sid, None))
        advanced = False
        for op, sid, value in candidates:
            candidate = deepcopy(trial)
            try:
                candidate.delta(
                    candidate.revision + 1,
                    op,
                    sid,
                    surface=value if op == "add" else None,
                    **(
                        {
                            key: getattr(value, key)
                            for key in changes_for(trial.surfaces[sid], value)
                        }
                        if op == "update"
                        else {}
                    ),
                )
            except (ProtocolError, TypeError):
                continue
            trial = candidate
            steps.append((op, sid, value))
            advanced = True
            break
        if not advanced or len(steps) > limit:
            return None
    return steps
