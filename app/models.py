from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class EmailMessage:
    subject: str
    text: str | None = None
    html: str | None = None
    sender: str | None = None
    to: Sequence[str] = field(default_factory=list)
    cc: Sequence[str] = field(default_factory=list)
    bcc: Sequence[str] = field(default_factory=list)
    headers: Mapping[str, str] = field(default_factory=dict)

    raw_mime: str | None = None


@dataclass(frozen=True)
class EventContext:
    #ce attributes
    id: str
    source: str
    type: str
    subject: str | None
    time: str | None
    dataschema: str | None
    datacontenttype: str | None
    emailto: str | None
    emailcc: str | None
    emailbcc: str | None
    #raw data
    data: Any
    extensions: Mapping[str, Any]

    def as_template_dict(self) -> dict[str, Any]:
        return {
            "ce": {
                "id": self.id,
                "source": self.source,
                "type": self.type,
                "subject": self.subject,
                "time": self.time,
                "dataschema": self.dataschema,
                "datacontenttype": self.datacontenttype,
                "emailto": self.emailto,
                "emailcc": self.emailcc,
                "emailbcc": self.emailbcc,
                **self.extensions,
            },
            "data": self.data,
        }
