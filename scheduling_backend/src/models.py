from pydantic import BaseModel
from typing import List, Optional

class EmailAddress(BaseModel):
    address: str
    name: str

class AttendeeEntry(BaseModel):
    emailAddress: EmailAddress
    type: str = "optional"   # "required" | "optional"
    userId: Optional[str] = None

class EventTime(BaseModel):
    dateTime: str  # ISO 8601 UTC
    timeZone: str  # IANA tz e.g. "UTC"

class OnlineMeeting(BaseModel):
    joinUrl: str

class Event(BaseModel):
    id: str
    subject: str
    bodyPreview: str = ""
    start: EventTime
    end: EventTime
    location: str = "Virtual"
    attendees: List[AttendeeEntry] = []
    organizer: EmailAddress
    onlineMeeting: Optional[OnlineMeeting] = None
    isOnlineMeeting: bool = True
    importance: str = "normal"   # "low" | "normal" | "high"

class User(BaseModel):
    id: str
    displayName: str
    mail: str
    jobTitle: str
    department: str
    officeLocation: str = "HQ"
    timeZone: str = "Asia/Kolkata"

class Room(BaseModel):
    id: str
    displayName: str
    emailAddress: str
    capacity: int = 10
    officeLocation: str = "HQ"
    bookingType: str = "standard"
