"""Show pictures, frame-to-frame change and recorded channel peaks on the same short time axis."""
from contextlib import ExitStack
from fractions import Fraction
import heapq
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

if __package__:
    from .common import font, open_recording, recording_levels, sample_boundary, save_observation_png
    from .frames import SHEET_COLUMNS, SHEET_ROWS, frame_sheets, native_frames, sheet_size
else:
    from common import font, open_recording, recording_levels, sample_boundary, save_observation_png
    from frames import SHEET_COLUMNS, SHEET_ROWS, frame_sheets, native_frames, sheet_size


def action_frames(source, info, start, end, box):
    if box is not None:
        if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            raise ValueError("focus needs 0 <= left < right <= 1 and 0 <= top < bottom <= 1")
        aspect = info["display_aspect"] * (box[2] - box[0]) / (box[3] - box[1])
        size = sheet_size({"display_aspect": aspect})
    else:
        size = sheet_size(info)
    frames = native_frames(source, *size, math.floor(start * 1_000_000), sample_boundary(end, 1_000_000), box=box)
    return size, frames


def draw_peaks(draw, wav, origin, start, end, left, top, width):
    # An audio column includes precisely the samples whose presentation time falls inside it.
    rate = wav.samplerate
    first, stop = (sample_boundary(t, rate) for t in (start, end))
    # A published time can round just above its exact sample boundary when serialized.
    # At a known WAV edge use its native sample index, even after a round trip through JSON.
    if float(start) == origin / rate:
        first = origin
    if float(end) == (origin + len(wav)) / rate:
        stop = origin + len(wav)
    offset = first - origin
    wav.seek(offset)
    samples = wav.read(stop - first, dtype="float64", always_2d=True)
    if len(samples) != stop - first:
        raise ValueError("the recording ended inside the requested time range")
    times = (first + np.arange(len(samples))) / rate
    columns = np.clip(((times - float(start)) * width / float(end - start)).astype(int), 0, width - 1)
    if len(columns):
        edges = np.r_[0, np.flatnonzero(np.diff(columns)) + 1]
        peaks = np.maximum.reduceat(np.abs(samples), edges, axis=0)
        db = 20 * np.log10(np.maximum(1e-12, peaks))
        heights = np.rint(np.clip((db + 72) / 72, 0, 1) * 23).astype(int)
    for ch in range(wav.channels):
        bottom = top + ch * 30 + 25
        draw.text((3, bottom - 22), f"ch {ch + 1} peak", font=font(12), fill="white")
        draw.line((left, bottom, left + width - 1, bottom), fill=(65, 81, 92))
        if len(columns):
            for col, height in zip(columns[edges], heights[:, ch]):
                if height:
                    draw.line((left + int(col), bottom - height, left + int(col), bottom), fill=(119, 204, 242))


def sound_during_pictures(wav_path, origin, times, selected, picture_end):
    """Match pictured exposures to their sound using every intervening native PTS."""
    rows = []
    with open_recording(wav_path) as wav:
        rate = wav.samplerate
        stop = min(len(wav), sample_boundary(picture_end, rate) - origin)
        for i, pts in enumerate(times):
            if pts not in selected:
                continue
            first = max(0, sample_boundary(Fraction(pts, 1_000_000), rate) - origin)
            end = stop
            if i + 1 < len(times):
                next_pts = times[i + 1]
                if next_pts == pts:
                    continue  # Only the last exposure at a timestamp reached the screen.
                end = min(end, sample_boundary(Fraction(next_pts, 1_000_000), rate) - origin)
            rows.append({"pts_us": pts, **recording_levels(wav, first, end)})
    return rows


def thumbnail(pixels, size, cell, height):
    picture = Image.frombytes("RGB", size, pixels)
    picture.thumbnail((cell - 2, height), Image.Resampling.LANCZOS)
    return picture


def additional_frame(pictures, centre, stop):
    """Pick an actual exposure in this cell that shows something missing at its centre."""
    if not pictures:
        return None
    if centre is None:
        # The picture track can start late or finish before the cell centre.
        ends = [row[0] for row in pictures[1:]] + [stop]
        return max(zip(pictures, ends), key=lambda item: item[1] - item[0][0])[0]
    if len(pictures) == 1:
        return None
    first = pictures[0]
    if first[2] is not centre[2] and first[2] != centre[2]:
        return first

    def grid(picture):
        small = picture.resize((64, 48), Image.Resampling.BOX)
        return np.asarray(small, dtype=np.int16).reshape(6, 8, 8, 8, 3)

    reference = None
    best, score = None, (-1., -1.)
    for row in pictures:
        if row[2] is centre[2] or row[2] == centre[2]:
            continue
        if reference is None:
            reference = grid(centre[2])
        # A hand can move while most of the frame stays still. Compare local
        # 8x8 blocks first and use the full image to break ties.
        difference = np.abs(grid(row[2]) - reference).mean(axis=(1, 3, 4))
        candidate = (difference.max(), difference.mean())
        if candidate > score:
            best, score = row, candidate
    return best


def transient_pictures(frames, size, covered, capacity):
    """Find local appearances that the two enclosing views fail to show."""
    features = {}

    def tiles(index):
        if index not in features:
            picture = Image.frombytes("RGB", size, frames[index][2])
            small = picture.resize((32, 24), Image.Resampling.BOX)
            features[index] = np.asarray(small).reshape(6, 4, 8, 4, 3)
        return features[index]

    def difference(a, b):
        return np.abs(np.subtract(a, b, dtype=np.int16)).mean(axis=(1, 3, 4))

    candidates = []

    def find_peak(left, right):
        if right - left < 2:
            return
        # At an open end of the picture track there is only one displayed reference.
        before = tiles(left if left >= 0 else right)
        after = tiles(right if right < len(frames) else left)
        baseline = difference(before, after)
        best, witness = 0., None
        for index in range(left + 1, right):
            value = tiles(index)
            residual = np.minimum(difference(value, before), difference(value, after)) - baseline
            score = float(residual.max())
            if score > best:
                best, witness = score, index
        # Eight RGB levels averaged over a tile keep small fluctuations from filling the sheet.
        if best >= 8:
            heapq.heappush(candidates, (-best, witness, left, right))

    anchors = [i for i, seen in enumerate(covered) if seen]
    selected = []
    if not anchors:
        anchors.append(0)
        selected.append(0)
    for left, right in zip([-1, *anchors], [*anchors, len(frames)]):
        find_peak(left, right)
    while candidates and len(selected) < capacity:
        _, index, left, right = heapq.heappop(candidates)
        selected.append(index)
        if len(selected) < capacity:
            find_peak(left, index)
            find_peak(index, right)
    return [frames[i] for i in sorted(selected)]


def missing_sequence(out, frames, size, sheets, action, current):
    """Return at most one sheet of original exposures missing from the overview."""
    out = Path(out)
    sampled = {pts for page in action["pages"] for pts in page["pts_us"] if pts is not None}
    sampled.update(row["pts_us"] for page in action["pages"] for row in page["additional_frames"] if row)
    if current:
        sampled.add(current["pts_us"])
    starts, counts, covered = [], [], []
    for i, frame in enumerate(frames):
        pts, _, pixels = frame
        if i + 1 < len(frames) and frames[i + 1][0] == pts:
            continue  # A later frame replaced this one before it had any display time.
        if not starts or pixels != starts[-1][2]:
            starts.append(frame)
            counts.append(0)
            covered.append(False)
        counts[-1] += 1
        if pts in sampled:
            covered[-1] = True
    if all(covered):
        return None
    capacity = SHEET_COLUMNS * SHEET_ROWS
    if len(starts) > capacity:
        detail = transient_pictures(starts, size, covered, capacity)
        if not detail:
            return None
        sheet, = frame_sheets(out, *size, detail)
        return {"file": str(Path(sheet["file"]).relative_to(out.resolve())),
                "pts_us": sheet["pts_us"], "selection": "local appearance between overview pictures"}
    if len(sheets) == 1 and sum(counts) == len(frames):
        return sheets[0]
    sheet, = frame_sheets(out, *size, starts)
    return {"file": str(Path(sheet["file"]).relative_to(out.resolve())),
            "pts_us": sheet["pts_us"], "exposures_per_picture": counts}


def draw_motion(draw, changes, start, end, left, width, bottom):
    first, stop = (sample_boundary(t, 1_000_000) for t in (start, end))
    for pts, height in changes:
        if first <= pts < stop:
            col = int((pts / 1e6 - float(start)) / float(end - start) * width)
            x = left + min(width - 1, max(0, col))
            draw.line((x, bottom - height, x, bottom), fill=(249, 185, 109), width=2)


def overview_page(out, cells, changes, start, end, wav, first_sample):
    left, tile, thumb_h = 85, 176, 115
    width = len(cells) * tile
    top = 188 + (30 * wav.channels if wav else 0)
    extra_row = any(extra is not None for _, extra in cells)
    image = Image.new("RGB", (left + width, top + (thumb_h + 28 if extra_row else 0)), (18, 21, 25))
    draw = ImageDraw.Draw(image)
    for i, (centre, extra) in enumerate(cells):
        x = left + i * tile
        draw.line((x, 1, x, image.height - 1), fill=(62, 66, 72))
        for row, y in ((centre, 0), (extra, top)):
            if row is None:
                continue
            at, _, picture = row
            if picture is not None:
                image.paste(picture, (x + (tile - picture.width) // 2, y + 4 + (thumb_h - picture.height) // 2))
            draw.text((x + tile // 2, y + (121 if y == 0 else thumb_h + 7)), f"{at:.3f} s",
                      anchor="ma", font=font(12), fill="white")
    draw.text((3, 122), "film time", font=font(12), fill="white")
    draw.text((3, 147), "pixel change", font=font(11), fill="white")
    draw.text((3, 162), "0..32 / 255", font=font(10), fill=(178, 183, 188))
    if extra_row:
        draw.text((3, top + 7), "also seen", font=font(12), fill="white")
    draw.line((left, 172, left + width - 1, 172), fill=(83, 78, 68))
    draw_motion(draw, changes, start, end, left, width, 172)
    if wav:
        draw_peaks(draw, wav, first_sample, start, end, left, 181, width)
    return save_observation_png(out, image, "action")


def chronological_page(out, cells, changes, start, end, wav, first_sample):
    """Read across each row in time. Mark the precise observation time on its trace."""
    left, tile, thumb_h = 85, 176, 115
    paired = any(extra is not None for _, extra in cells)
    per_row = 3 if paired else 6
    slots = 2 if paired else 1
    row_height = 193 + (30 * wav.channels if wav else 0)
    n = len(cells)
    width = min(n, per_row) * slots * tile
    image = Image.new("RGB", (left + width, math.ceil(n / per_row) * row_height), (18, 21, 25))
    draw = ImageDraw.Draw(image)
    rows = []
    for offset in range(0, n, per_row):
        count = min(per_row, n - offset)
        top = offset // per_row * row_height
        a = start + (end - start) * offset / n
        b = end if offset + count == n else start + (end - start) * (offset + count) / n
        span = count * slots * tile
        clock_y = top + 145
        draw.text((3, top + 122), "film time", font=font(12), fill="white")
        draw.text((3, top + 152), "pixel change", font=font(11), fill="white")
        draw.text((3, top + 167), "0..32 / 255", font=font(10), fill=(178, 183, 188))
        draw.line((left, clock_y, left + span - 1, clock_y), fill=(101, 112, 122))
        draw.line((left, top + 179, left + span - 1, top + 179), fill=(83, 78, 68))
        times = []
        for i, (center, extra) in enumerate(cells[offset:offset + count]):
            x = left + i * slots * tile
            draw.line((x, top + 1, x, top + row_height - 1), fill=(62, 66, 72))
            views = [center] if extra is None else sorted([center, extra], key=lambda row: row[0])
            for j, (at, pts, pic) in enumerate(views):
                # Keep a lone view centred across its entire time cell.
                mid = x + (slots * tile // 2 if len(views) == 1 else j * tile + tile // 2)
                if pic is not None:
                    image.paste(pic, (mid - pic.width // 2, top + 4 + (thumb_h - pic.height) // 2))
                draw.text((mid, top + 121), f"{at:.3f} s", anchor="ma", font=font(12), fill="white")
                tick = left + min(span - 1, max(0, round((at - float(a)) / float(b - a) * span)))
                draw.line((mid, top + 136, tick, clock_y), fill=(144, 154, 167))
                times.append({"at_s": at, "pts_us": pts})
        draw_motion(draw, changes, a, b, left, span, top + 179)
        if wav:
            draw_peaks(draw, wav, first_sample, a, b, left, top + 186, span)
        rows.append({"start_s": float(a), "end_s": float(b), "pictures": times})
    return save_observation_png(out, image, "action"), rows


def action_views(out, frames, size, start, end, picture_end, wav_path, first_sample, box=None, *,
                 previous_pixels=None, chronological=False):
    """Stream the sequence in two-second pages. A carried frame gives context but no new motion."""
    out = Path(out)
    frames = iter(frames)
    pages = []
    cell, thumb_h = 176, 115
    with ExitStack() as stack:
        if hasattr(frames, "close"):
            stack.callback(frames.close)
        wav = stack.enter_context(open_recording(wav_path)) if wav_path else None
        upcoming = next(frames, None)
        held = None
        previous = np.frombuffer(previous_pixels, np.uint8) if previous_pixels is not None else None
        index = 0
        while start + 2 * index < end:
            a, b = start + 2 * index, min(end, start + 2 * (index + 1))
            count = min(6, max(1, math.ceil(float(b - a) * 3)))
            times = [float(a + (b - a) * (2 * i + 1) / (2 * count)) for i in range(count)]
            cells, changes = [], []
            for i, t in enumerate(times):
                begin = a + (b - a) * i / count
                finish = b if i + 1 == count else a + (b - a) * (i + 1) / count
                stop = float(min(finish, picture_end))
                pictures = []
                if held is not None and float(begin) < stop:
                    pictures.append((float(begin), held[0], held[2]))
                bound = sample_boundary(finish, 1_000_000)
                while upcoming is not None and upcoming[0] < bound:
                    pts, _, pixels = upcoming
                    values = np.frombuffer(pixels, np.uint8)
                    if previous is not None and pts >= float(a) * 1e6:
                        delta = float(np.abs(values.astype(np.int16) - previous).mean())
                        height = round(min(delta / 32, 1) * 27)
                        if height:
                            changes.append((pts, height))
                    previous = values
                    preview = held[2] if held is not None and pixels == held[1] else thumbnail(pixels, size, cell, thumb_h)
                    held = pts, pixels, preview
                    at = max(float(begin), pts / 1e6)
                    if at < stop:
                        if pictures and pictures[-1][0] == at:
                            pictures.pop()  # A new frame replaces the held picture at this instant.
                        pictures.append((at, pts, preview))
                    upcoming = next(frames, None)
                centre = next((row for row in reversed(pictures) if row[0] <= t), None) if t < stop else None
                middle = (t, centre[1], centre[2]) if centre else (t, None, None)
                cells.append((middle, additional_frame(pictures, centre, stop)))
            extra_times = [{"at_s": e[0], "pts_us": e[1]} if e else None for _, e in cells]
            page = {"start_s": float(a), "end_s": float(b), "at_s": times, "pts_us": [c[1] for c, _ in cells],
                    "additional_frames": extra_times if any(extra_times) else []}
            if chronological:
                page["file"], page["rows"] = chronological_page(out, cells, changes, a, b, wav, first_sample)
            else:
                page["file"] = overview_page(out, cells, changes, a, b, wav, first_sample)
            pages.append(page)
            index += 1
    return {"box": list(box) if box is not None else [0, 0, 1, 1], "pages": pages,
            "basis": (("Read each row left to right, then down. The line below a picture shows its observation time. "
                       if chronological else "Each column shows the scene at its centre and any extra view below. ")
                      + "A different view can show the first exposure in the cell or a local change. "
                      "Orange marks frame-to-frame pixel change. Blue is the recorded peak in each channel "
                      "from -72 to 0 dBFS. Sound covers the full mix even when the picture is cropped.")}
