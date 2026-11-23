"""Show pictures, frame-to-frame change and recorded channel peaks on the same short time axis."""
from bisect import bisect_right
from contextlib import ExitStack
from fractions import Fraction
import heapq
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

if __package__:
    from .common import font, lut, open_recording, recording_levels, sample_boundary, save_observation_png
    from .frames import SHEET_COLUMNS, SHEET_ROWS, frame_sheets, mean_rgb_change, native_frames, sheet_size
    from .sound_glimpses import band_levels, sheet_picture
else:
    from common import font, lut, open_recording, recording_levels, sample_boundary, save_observation_png
    from frames import SHEET_COLUMNS, SHEET_ROWS, frame_sheets, mean_rgb_change, native_frames, sheet_size
    from sound_glimpses import band_levels, sheet_picture


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
    return first, samples


BAND_HEIGHT = 68


def draw_bands(image, samples, rate, first, start, end, left, top, width):
    """Place each channel's octave bands under the pictures on their film clock."""
    if not len(samples):
        return
    hop = max(2, round(rate * .02))
    # Each 20 ms cell reads a 40 ms Hann window centred on the cell. Include
    # the first and last samples without reading outside this displayed row.
    data = np.pad(samples, ((hop // 2, 2 * hop), (0, 0)))
    levels, hz = band_levels(data, 2 * hop, hop, rate)
    offset = float(start * rate - first)
    positions = offset + (np.arange(width) + .5) * float(end - start) * rate / width
    indices = np.clip(np.floor(positions / hop).astype(int), 0, (len(samples) - 1) // hop)
    palette = lut()
    draw = ImageDraw.Draw(image)
    height = BAND_HEIGHT - 14
    for ch in range(samples.shape[1]):
        y = top + ch * BAND_HEIGHT
        strength = np.rint(np.clip((levels[indices, ch, ::-1].T + 80) / 80, 0, 1) * 255).astype(np.uint8)
        strip = Image.fromarray(palette[strength]).resize((width, height), Image.Resampling.NEAREST)
        image.paste(strip, (left, y + 14))
        draw.text((3, y), f"ch {ch + 1} bands Hz", font=font(11), fill="white")
        # Upper and lower labels give the edges of the two outer frequency bands.
        draw.text((3, y + 14), f"{hz[-2]:g}-{hz[-1]:g}", font=font(10), fill=(210, 205, 200))
        draw.text((3, y + BAND_HEIGHT - 13), f"0-{hz[1]:g}", font=font(10), fill=(210, 205, 200))


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


def exposure_view(out, scene, changes):
    """Show consecutive released exposures and the sound recorded while each was on screen."""
    limit = sample_boundary(scene["picture_end"], 1_000_000)
    times = list(dict.fromkeys(pts for page in scene["pages"] for pts in page["pts_us"] if pts < limit))
    if not times:
        return None
    selected = times
    event = None
    if len(times) > SHEET_ROWS * SHEET_COLUMNS:
        # The analyser timestamps a change at the final sample it has heard.
        rises = [e for e in changes if "rise" in e
                 and max(scene["start"], times[0] / 1e6) <= e["time_s"] < scene["picture_end"]]
        if not rises:
            return None
        event = max(rises, key=lambda e: e["rise"]["db"])
        anchor = bisect_right(times, event["time_s"], key=lambda pts: pts / 1e6) - 1
        count = 2 * SHEET_COLUMNS
        first = min(max(0, anchor - count // 2), len(times) - count)
        selected = times[first:first + count]
    get = sheet_picture(scene["pages"], scene["size"])
    images = [get(pts)[1] for pts in selected]
    if len(images) > 1 and all(image == images[0] for image in images[1:]):
        return None
    rows = sound_during_pictures(scene["wav"], scene["first_sample"], times, set(selected), scene["picture_end"])
    width, height = scene["size"]
    tile = max(width, 176)
    columns = min(SHEET_COLUMNS, len(rows))
    stride = height + 44
    sheet = Image.new("RGB", (columns * tile, math.ceil(len(rows) / columns) * stride), (16, 17, 19))
    draw = ImageDraw.Draw(sheet)
    for index, (row, image) in enumerate(zip(rows, images, strict=True)):
        x, y = index % columns * tile, index // columns * stride
        sheet.paste(image, (x + (tile - width) // 2, y))
        # The first picture may already have been on screen when the replay began.
        at = max(scene["start"], row["pts_us"] / 1e6)
        draw.text((x + 4, y + height + 2), f"{at:.6f} s", font=font(13), fill="white")
        peaks = row.get("peak_dbfs")
        finite = [v for v in peaks or [] if v is not None]
        caption = (f"peak {max(finite):.1f} dBFS" if finite else
                   "digital silence" if peaks is not None else "no audio sample")
        draw.text((x + 4, y + height + 21), caption, font=font(12), fill=(149, 217, 244))
    page = {"file": save_observation_png(out, sheet, "exposures"), "frames": rows,
            "basis": "Row order follows consecutive displayed frames. Each peak is the loudest channel "
                     "while that picture was on screen inside this replay. Per-channel levels and sample "
                     "ranges refer to the released recording for this interval."}
    if event is not None:
        page["selected_by_band_rise"] = event
    return page


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
    for pts, delta in changes:
        height = round(min(delta / 32, 1) * 27)
        if height and first <= pts < stop:
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
    row_height = 193 + ((30 + BAND_HEIGHT) * wav.channels if wav else 0)
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
            first, samples = draw_peaks(draw, wav, first_sample, a, b, left, top + 186, span)
            draw_bands(image, samples, wav.samplerate, first, a, b, left, top + 186 + 30 * wav.channels, span)
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
            page_start_us = sample_boundary(a, 1_000_000)
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
                    upcoming = next(frames, None)
                    if upcoming is not None and upcoming[0] == pts:
                        continue  # The later frame at this timestamp is the one displayed.
                    values = np.frombuffer(pixels, np.uint8)
                    if previous is not None and pts >= page_start_us:
                        changes.append((pts, mean_rgb_change(values, previous)))
                    previous = values
                    preview = held[2] if held is not None and pixels == held[1] else thumbnail(pixels, size, cell, thumb_h)
                    held = pts, pixels, preview
                    at = max(float(begin), pts / 1e6)
                    if at < stop:
                        if pictures and pictures[-1][0] == at:
                            pictures.pop()  # A new frame replaces the held picture at this instant.
                        pictures.append((at, pts, preview))
                centre = next((row for row in reversed(pictures) if row[0] <= t), None) if t < stop else None
                middle = (t, centre[1], centre[2]) if centre else (t, None, None)
                cells.append((middle, additional_frame(pictures, centre, stop)))
            extra_times = [{"at_s": e[0], "pts_us": e[1]} if e else None for _, e in cells]
            page = {"start_s": float(a), "end_s": float(b), "at_s": times, "pts_us": [c[1] for c, _ in cells],
                    "additional_frames": extra_times if any(extra_times) else [],
                    "rgb_change": {"pts_us": [pts for pts, _ in changes],
                                   "mean_abs_rgb": [round(delta, 4) for _, delta in changes]}}
            if chronological:
                page["file"], page["rows"] = chronological_page(out, cells, changes, a, b, wav, first_sample)
            else:
                page["file"] = overview_page(out, cells, changes, a, b, wav, first_sample)
            pages.append(page)
            index += 1
    return {"box": list(box) if box is not None else [0, 0, 1, 1], "pages": pages,
            "basis": (("Read each row left to right, then down. The line below a picture shows its observation time. "
                       "The frequency bands below the channel peaks run from low at the bottom to high at the top. "
                       "Brighter bands contain more energy on the same scale across rows and channels. "
                       if chronological else "Each column shows the scene at its centre and any extra view below. ")
                      + "A different view can show the first exposure in the cell or a local change. "
                      "Orange marks frame-to-frame pixel change. Blue is the recorded peak in each channel "
                      "from -72 to 0 dBFS. Sound covers the full mix even when the picture is cropped.")}
