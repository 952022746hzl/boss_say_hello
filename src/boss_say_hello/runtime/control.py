from dataclasses import dataclass, field
from threading import Event


class RunStartBlocked(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass
class RunControl:
    pause_requested: Event = field(default_factory=Event)
    closing: Event = field(default_factory=Event)
    default_message_required: Event = field(default_factory=Event)
    default_message_acknowledged: Event = field(default_factory=Event)
