"""Read immutable observations and preserve what a particular reader reported at the time."""
import json
import math
from bisect import bisect_left


def attention(events):
    """Small first-look selection. The complete list stays available on the released turn."""
    groups = {}
    for event in events:
        groups.setdefault(event["kind"], []).append(event)
    chosen = []
    for kind, rows in groups.items():
        if kind == "picture_detail":
            rows = sorted(rows, key=lambda e: -e["travel"])
        elif kind == "spectral_peak":
            rows = sorted(rows, key=lambda e: -max(e["channel_peak_dbfs"]))
        chosen.extend(rows[:2])
    return [{"id": e["id"], **{k: e[k] for k in ("track", "kind", "start_s", "end_s", "at_s",
             "channels", "hz", "last_point") if k in e}} for e in chosen]


class EventMemory:
    def __init__(self, paths):
        self.paths = paths
        self.reports = []
        self.delivery = {}
        self.reviewed_through = 0

    def packet(self, turn):
        if not 1 <= turn <= len(self.paths):
            raise ValueError("choose an already published turn")
        return json.loads(self.paths[turn-1].read_text())

    def delivered(self, turn, blocks, directed):
        transport = [b.type for b in blocks[1:]]
        if turn in self.delivery or directed:
            self.reviewed_through = max(self.reviewed_through, len(self.paths))
        report = json.loads(blocks[0].text)
        self.delivery.setdefault(turn, dict(offered=list(report.get("images_in_order", [])),
                                            local_readers={k: (report[k].get("model") if isinstance(report[k], dict) else True)
                                                           for k in ("hearing", "voices", "speech", "joint", "sound_descriptions")
                                                           if k in report}, images=transport.count("image"), audio=transport.count("audio"),
                                            media_links=transport.count("resource_link"), directed=directed))

    def event(self, turn, key):
        self.packet(turn)
        if key.startswith("mark-"):
            try:
                n = int(key.removeprefix("mark-"))
            except ValueError:
                n = 0
            if 1 <= n <= len(self.reports):
                note = self.reports[n-1]
                if note["observed_through_turn"] <= turn and note["event"]["id"] == key:
                    return note["event"]
        else:
            try:
                owner, index = map(int, key.split("."))
            except ValueError:
                owner = index = 0
            if 1 <= owner <= turn:
                events = self.packet(owner).get("observations", [])
                if 1 <= index <= len(events) and events[index-1]["id"] == key:
                    return events[index-1]
        raise ValueError("unknown event at this point in the viewing")

    def recall(self, turn, first_turn=1, track=None, after=None, limit=32, revision=None, after_revision=0):
        self.packet(turn)
        if not 1 <= first_turn <= turn or not 1 <= limit <= 128:
            raise ValueError("choose a published turn range and a page size from 1 to 128")
        begin = first_turn
        first_index = 0
        if after:
            self.event(turn, after)
            if after.startswith("mark-"):
                raise ValueError("use the last measured event ID as the page cursor")
            page, first_index = map(int, after.split("."))
            if page < first_turn:
                raise ValueError("cursor precedes the requested range")
            first_turn = page
        rows = []
        missing = []
        for i in range(first_turn, turn+1):
            packet = self.packet(i)
            if "observations" not in packet:
                missing.append(i)
            for row in packet.get("observations", [])[first_index:]:
                if track is None or row.get("track") == track:
                    rows.append(row)
                if len(rows) > limit:
                    break
            if len(rows) > limit:
                break
            first_index = 0
        if revision is None:
            revision = len(self.reports)
        if not 0 <= after_revision <= revision <= len(self.reports):
            raise ValueError("unknown memory revision")
        reports = [r for r in self.reports[after_revision:revision] if r["observed_through_turn"] <= turn and
                   r["observed_through_turn"] >= begin and
                   (track is None or r["event"].get("track") == track)]
        return dict(observations=rows[:limit], next_after=rows[limit-1]["id"] if len(rows)>limit else None,
                    reader_reports=reports[:limit], next_after_revision=reports[limit-1]["revision"] if len(reports)>limit else None,
                    unindexed_turns_on_this_page=missing, through_turn=turn,
                    revision=max((r["revision"] for r in self.reports[:revision] if r["observed_through_turn"] <= turn), default=0))

    def remember(self, turn, claim, judgment, event_id=None, interval=None, channel=None, box=None,
                 revises=None, recalled=False, confidence=None, related_id=None, relation=None):
        packet = self.packet(turn)
        if turn != len(self.paths):
            raise ValueError("record what you know at the latest published turn; cite earlier evidence by ID")
        if not claim.strip() or len(claim) > 1600 or judgment not in (
                "detected", "unidentified", "identified", "uncertain", "no_signal", "words_unclear", "prediction"):
            raise ValueError("supply a short observation and a supported judgment type")
        if bool(event_id) == (interval is not None):
            raise ValueError("choose one measured event ID or a released interval")
        if event_id:
            if channel is not None or box is not None:
                raise ValueError("choose a manual interval to supply a channel or picture box")
            event = self.event(turn, event_id)
        else:
            if len(interval) != 2 or not 0 <= interval[0] < interval[1] <= packet["end_s"]:
                raise ValueError("choose a nonempty released interval")
            left, right = interval
            first = bisect_left(self.paths, left, key=lambda path: json.loads(path.read_text())['end_s'])
            if box is not None and (len(box)!=4 or not all(math.isfinite(x) for x in box)
                                    or not (0<=box[0]<box[2]<=1 and 0<=box[1]<box[3]<=1)):
                raise ValueError("choose a nonempty normalized picture box")
            for path in self.paths[first:turn]:
                window = json.loads(path.read_text())
                if window['end_s'] <= left or window['start_s'] >= right:
                    continue
                if channel is not None and not 1 <= channel <= (window.get('audio') or {}).get('channels', 0):
                    raise ValueError("the selected channel is unavailable in the interval")
                if box is not None and (not window['pages'] or min(right,window['end_s']) > window.get('picture_end_s',window['end_s'])):
                    raise ValueError("no picture throughout the selected interval")
            event = dict(kind="reader_selection", start_s=left, end_s=right,
                         id=f"mark-{len(self.reports)+1}")
            if channel is not None:
                event['channels'] = [channel]
            if box is not None:
                event['box'] = box
        if revises is not None:
            if not 1 <= revises <= len(self.reports) or self.reports[revises-1]["event"]["id"] != event["id"]:
                raise ValueError("revise a report about this same event")
            if any(r.get("revises")==revises for r in self.reports):
                raise ValueError("that revision already has a successor")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("confidence must be between zero and one")
        link = None
        if related_id is not None or relation is not None:
            if relation not in ("same_source", "different_source", "possible_cause", "followed_by",
                                "supports_prediction", "contradicts_prediction") or not related_id:
                raise ValueError("supply a related event ID and supported relation")
            if related_id == event["id"]:
                raise ValueError("choose two distinct events")
            link = dict(relation=relation, event_id=self.event(turn, related_id)["id"])
        prior = event["end_s"] <= packet["start_s"]
        phase = "recall" if recalled else ("directed_or_replayed" if self.reviewed_through >= turn or prior else "first_pass")
        row = dict(revision=len(self.reports)+1, observed_through_turn=turn, observed_through_s=packet["end_s"],
                   phase=phase, judgment=judgment, claim=claim.strip(), event=event,
                   confidence=confidence, reader_link=link,
                   first_delivery=self.delivery.get(turn), replay_or_directed_access=self.reviewed_through >= turn, revises=revises)
        self.reports.append(row)
        return row
