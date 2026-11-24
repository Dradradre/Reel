"""An optional local archive of a reader's dated impressions."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


class EventJournal:
    def __init__(self, path):
        self.path = Path(path)
        self.session = uuid4().hex
        self.digest = hashlib.sha256()
        self.hashed_turns = 0
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS reel_event_reports (
                session TEXT NOT NULL, revision INTEGER NOT NULL, turn INTEGER NOT NULL,
                report TEXT NOT NULL, PRIMARY KEY(session,revision))""")

    def connect(self):
        # Each MCP call can run on a different worker thread.
        return sqlite3.connect(self.path, timeout=10)

    def save(self, paths, row):
        # Hash only assets that this playback has released, even when the full film
        # is locally accessible. A later watch gets a new journal session.
        for path in paths[self.hashed_turns :]:
            packet = json.loads(path.read_text())
            names = [page["file"] for page in packet.get("pages", [])]
            if audio := packet.get("audio"):
                names.append(audio["wav"])
            if frame := packet.get("current_frame"):
                names.append(frame["file"])
            # Extend the chain only after every asset for this turn has been read.
            digest = self.digest.copy()
            digest.update(hashlib.sha256(path.read_bytes()).digest())
            for name in names:
                with (path.parent / name).open("rb") as source:
                    digest.update(hashlib.file_digest(source, "sha256").digest())
            self.digest = digest
            self.hashed_turns += 1
        stored = dict(row, journal_session=self.session, published_evidence_sha256=self.digest.hexdigest())
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO reel_event_reports VALUES(?,?,?,?)",
                (self.session, row["revision"], row["observed_through_turn"], json.dumps(stored)),
            )
        return stored


def read(path, session=None, through=None, after=0, limit=32):
    if not 1 <= limit <= 128 or after < 0 or (through is not None and through < 1):
        raise ValueError("choose a positive turn, nonnegative cursor and a page size from 1 to 128")
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if session is None:
            return {
                "sessions": [
                    r[0]
                    for r in db.execute(
                        "SELECT session FROM reel_event_reports GROUP BY session ORDER BY MIN(rowid) LIMIT ? OFFSET ?",
                        (limit, after),
                    )
                ]
            }
        cursor = db.execute(
            """SELECT report FROM reel_event_reports WHERE session=? AND revision>?
                             AND (? IS NULL OR turn<=?) ORDER BY revision LIMIT ?""",
            (session, after, through, through, limit + 1),
        )
        rows = [json.loads(r[0]) for r in cursor]
        return {
            "session": session,
            "reports": rows[:limit],
            "next_after": rows[limit - 1]["revision"] if len(rows) > limit else None,
        }
