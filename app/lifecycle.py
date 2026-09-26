from dataclasses import dataclass
from typing import Literal

from app.models import Hunt, HuntStatus

ActionKind = Literal["move", "retire", "reactivate"]
StageState = Literal["done", "current", "upcoming"]

LIFECYCLE_ORDER: tuple[HuntStatus, ...] = (
    HuntStatus.idea,
    HuntStatus.scoped,
    HuntStatus.active,
    HuntStatus.retired,
)


@dataclass(frozen=True)
class LifecycleAction:
    label: str
    kind: ActionKind
    status: HuntStatus | None = None


@dataclass(frozen=True)
class LifecycleStage:
    status: HuntStatus
    label: str
    state: StageState


@dataclass(frozen=True)
class LifecycleView:
    stages: tuple[LifecycleStage, ...]
    primary: LifecycleAction
    others: tuple[LifecycleAction, ...]


_RETIRE_EARLY = LifecycleAction("Retire early…", "retire")

_ACTIONS: dict[HuntStatus, tuple[LifecycleAction, tuple[LifecycleAction, ...]]] = {
    HuntStatus.idea: (
        LifecycleAction("Scope this hunt", "move", HuntStatus.scoped),
        (_RETIRE_EARLY,),
    ),
    HuntStatus.scoped: (
        LifecycleAction("Start hunt", "move", HuntStatus.active),
        (LifecycleAction("Move back to Idea", "move", HuntStatus.idea), _RETIRE_EARLY),
    ),
    HuntStatus.active: (
        LifecycleAction("Retire…", "retire"),
        (LifecycleAction("Move back to Scoped", "move", HuntStatus.scoped),),
    ),
    HuntStatus.retired: (
        LifecycleAction("Reactivate", "reactivate"),
        (),
    ),
}


def lifecycle_view(hunt: Hunt) -> LifecycleView:
    current = LIFECYCLE_ORDER.index(hunt.status)
    stages = tuple(
        LifecycleStage(
            status=status,
            label=status.value.capitalize(),
            state="done" if index < current else "current" if index == current else "upcoming",
        )
        for index, status in enumerate(LIFECYCLE_ORDER)
    )
    primary, others = _ACTIONS[hunt.status]
    return LifecycleView(stages=stages, primary=primary, others=others)
