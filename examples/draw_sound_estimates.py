"""Render the experimental chart from the listening-chart benchmark for one saved view report."""
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw
from reel.common import font

COLORS = {'AST': '#56c4e7', 'YAMNet': '#f2b66d'}
BACKGROUND, FOREGROUND, MUTED = '#11151b', '#ebeff5', '#aeb7c5'


def evidence_rows(report):
    audio = report.get('audio') or {}
    if not audio or audio.get('signal') == 'digital_silence':
        return []
    first, last = report['start_s'], report['end_s']
    intervals = defaultdict(lambda: defaultdict(set))

    def put(label, channels, model, left, right):
        if 0 <= left < right <= last and first < right:
            for ch in channels:
                intervals[label][ch].add((model, left, right))

    sources = audio.get('source_estimates') or {}
    if sources.get('status') == 'available':
        for w in sources.get('windows', []):
            for ch in w['channels']:
                for c in ch['candidates']:
                    put(c['sound'], [ch['channel']], 'AST', w['context_start_s'], w['end_s'])
    events = audio.get('sound_events') or {}
    if events.get('status') == 'available':
        for c in events.get('candidates', []):
            put(c['sound'], c['channels'], 'YAMNet', c['start_s'], c['end_s'])
    rows = []
    for label, channels in intervals.items():
        groups = defaultdict(list)
        for channel, spans in sorted(channels.items()):
            groups[tuple(sorted(spans))].append(channel)
        for spans, group in groups.items():
            rows.append((label, tuple(group), spans))
    return sorted(rows, key=lambda r: (min(s[1] for s in r[2]), r[0], r[1]))


def draw(report, directory):
    rows = evidence_rows(report)
    if not rows:
        return []
    first, last = report['start_s'], report['end_s']
    start = min(first, min(s[1] for _, _, spans in rows for s in spans))
    title, body, small = font(20), font(15), font(13)
    labels = [label + '  [' + ','.join(map(str, ch)) + ']' for label, ch, _ in rows]
    axis_left = max(192, int(max(body.getlength(label) for label in labels)) + 30)
    axis_right = axis_left + 560

    def x(t):
        return round(axis_left + (axis_right - axis_left) * (t - start) / (last - start))

    paths = []
    for idx in range(0, len(rows), 12):
        chunk = rows[idx:idx + 12]
        h = 108 + 30 * len(chunk)
        img = Image.new('RGB', (axis_right + 30, h), BACKGROUND)
        pen = ImageDraw.Draw(img)
        pen.text((14, 7), 'Possible sounds in the recording', font=title, fill=FOREGROUND)
        pen.text((14, 35), 'AST', font=body, fill=COLORS['AST'])
        pen.text((54, 35), 'YAMNet', font=body, fill=COLORS['YAMNet'])
        pen.text((124, 35), 'Bars show the audio each estimate used.', font=body, fill=MUTED)
        pen.text((14, 65), 'Sound  [channel]', font=small, fill=MUTED)
        for tick in range(5):
            t = start + (last - start) * tick / 4
            xx = x(t)
            pen.line((xx, 83, xx, h - 12), fill='#384451')
            pen.text((xx, 65), f'{t:.2f}s', anchor='mt', font=small, fill=MUTED)
        if start < first:
            pen.line((x(first), 83, x(first), h - 12), fill='#e1e9f3', width=2)
            pen.text((axis_right, 12), f'Turn begins {first:.2f}s', anchor='rt', font=small, fill=MUTED)
        for offset, (_, _, spans) in enumerate(chunk):
            y = 87 + offset * 30
            pen.text((14, y), labels[idx + offset], font=body, fill=FOREGROUND)
            for model, a, b in spans:
                yy = y + (1 if model == 'AST' else 11)
                pen.rectangle((x(a), yy, x(b), yy + 6), fill=COLORS[model])
        path = Path(directory) / f'sound-estimates-{report["turn"]}-{idx // 12 + 1}.png'
        img.save(path)
        paths.append(path)
    return paths

if __name__ == '__main__':
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path, help='JSON text from a single MCP view')
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    for page in draw(json.loads(args.report.read_text()), args.directory):
        print(page)
