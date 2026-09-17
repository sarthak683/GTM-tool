"""Deal Document model — files a rep attaches to a deal, from their computer
or by linking an existing Google Drive file.

Local uploads are stored directly in Postgres (LargeBinary column), not an
external bucket — there is no S3/GCS configured anywhere in this app yet.
Fine for the proposal/contract/deck-sized files this is meant for; not meant
for very large files or high volume (see MAX_DEAL_DOCUMENT_BYTES in the
endpoint). Drive-linked documents carry no bytes at all — `source="drive"`
rows only ever populate the drive_* fields and `data` stays NULL; opening one
sends the rep straight to `drive_web_view_link` in their own Drive, since
the OAuth scope already granted (drive.readonly) can browse a file but not
fetch its bytes on the rep's behalf as a downloadable copy.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, LargeBinary, Text
from sqlmodel import Field, SQLModel


class DealDocument(SQLModel, table=True):
    __tablename__ = "deal_documents"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    deal_id: UUID = Field(foreign_key="deals.id", index=True)
    filename: str
    content_type: Optional[str] = None
    # NULL for a Drive-linked document — Drive doesn't always report a size,
    # and we never re-fetch it for a linked file.
    size_bytes: Optional[int] = None
    # The file itself — only for source="upload". Never included in list/read
    # responses — served only by the dedicated download endpoint, so listing
    # documents stays cheap even with many/large attachments.
    data: Optional[bytes] = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    # "upload" (bytes live in `data`) | "drive" (bytes live in Drive; only a
    # reference is stored here).
    source: str = Field(default="upload", index=True)
    drive_file_id: Optional[str] = None
    drive_web_view_link: Optional[str] = Field(default=None, sa_column=Column(Text))
    uploaded_by_id: Optional[UUID] = Field(default=None, foreign_key="users.id", index=True)
    # Denormalized like Deal.assigned_rep_name elsewhere — avoids a join for
    # the common case of just listing who uploaded what.
    uploaded_by_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class DealDocumentRead(SQLModel):
    id: UUID
    deal_id: UUID
    filename: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    source: str = "upload"
    drive_file_id: Optional[str] = None
    drive_web_view_link: Optional[str] = None
    uploaded_by_id: Optional[UUID] = None
    uploaded_by_name: Optional[str] = None
    created_at: datetime
