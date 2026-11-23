"""Compare original and reference-filtered sound around human-marked street events.

Run with a directory containing the TUT 2017 evaluation audio and meta zip files.
The two conditions use the same shipped YAMNet classifier and a frozen mapping.
"""
import argparse
from collections import Counter, defaultdict
from hashlib import file_digest
import json
from pathlib import Path
import shutil
import tempfile
from zipfile import ZipFile

import numpy as np
from scipy import signal
import soundfile as sf

from reel.common import resample_channels, sample_boundary
from reel.sound_events import Classifier, RATE, WINDOW

HERE = Path(__file__).resolve().parents[1]
HOP = 128
FRAME = 512


def spectrum(samples, **kwargs):
    return signal.stft(samples.T, fs=RATE, nperseg=FRAME, noverlap=FRAME - HOP, **kwargs)[2]


def replay(samples, background):
    transformed = spectrum(samples)
    power = np.mean(abs(transformed) ** 2, axis=0)
    gain = np.sqrt(np.maximum(0.25 ** 2, 1 - background / np.maximum(power, 1e-30)))
    _, restored = signal.istft(transformed * gain, fs=RATE, nperseg=FRAME, noverlap=FRAME - HOP)
    return restored.T[:len(samples)].astype(np.float32)


def predictions(model, samples, indices, category):
    scores, reported = [], set()
    for channel in samples.T:
        labels = model(channel, RATE)
        reported.update(name for name, _ in labels)
        # The production reader returns its top three labels above threshold. Also
        # score the annotated category below threshold for the paired ranking.
        full = model.model.get_tensor(model.output)[0]
        scores.extend(float(full[k]) for k in indices)
    return bool(reported.intersection(category)), max(scores, default=0.0)


def read_interval(recording, start, end):
    rate = recording.samplerate
    first, stop = (sample_boundary(t, rate) for t in (start, end))
    recording.seek(first)
    samples = recording.read(stop - first, dtype='float32', always_2d=True)
    return resample_channels(samples, rate, RATE)


def measure_recording(recording, events, model, mapping, indices):
    duration = WINDOW / RATE
    for start, end, label in sorted(events):
        if start < 3 or end - start < duration:
            continue
        if any(tag == label and other_start < start and other_end > start - 3
               for other_start, other_end, tag in events):
            continue
        reference = read_interval(recording, start - 3, start - duration)
        # Estimate one spectrum from complete earlier frames across both channels.
        earlier = spectrum(reference, boundary=None, padded=False)
        background = np.mean(abs(earlier) ** 2, axis=(0, 2))[:, None]
        row = dict(onset_s=start, label=label)
        windows = [('control', start - duration, start), ('event', start, start + duration)]
        for kind, left, right in windows:
            clip = read_interval(recording, left, right)[:WINDOW]
            clip = np.pad(clip, ((0, WINDOW - len(clip)), (0, 0)))
            for arm, sample in [('original', clip), ('filtered', replay(clip, background))]:
                found, score = predictions(model, sample, indices[label], mapping[label])
                row[f'{arm}_{kind}'] = [found, score]
        yield row


def measure(folder):
    prior = json.loads((HERE / 'docs/benchmarks/short-sound-events.json').read_text())['street']
    paths = {k: folder / f'TUT-sound-events-2017-evaluation.{k}.zip' for k in ('audio', 'meta')}
    for key, path in paths.items():
        with path.open('rb') as handle:
            if file_digest(handle, 'md5').hexdigest() != prior[key + '_md5']:
                raise ValueError(f'Unexpected {key} evaluation archive')
    annotations = defaultdict(list)
    with ZipFile(paths['meta']) as archive:
        member = next(p for p in archive.namelist() if p.endswith('/meta.txt'))
        for line in archive.read(member).decode().splitlines():
            name, _, start, end, label, _, _ = line.split('\t')
            annotations[name].append((float(start), float(end), label))
    model = Classifier()
    mapping = prior['mapping']
    indices = {label: [model.labels.index(k) for k in names] for label, names in mapping.items()}
    rows = []
    with tempfile.TemporaryDirectory() as tmp, ZipFile(paths['audio']) as archive:
        wav = Path(tmp) / 'recording.wav'
        for name, events in sorted(annotations.items()):
            member = next(p for p in archive.namelist() if p.endswith('/' + name))
            with archive.open(member) as source, wav.open('wb') as dest:
                shutil.copyfileobj(source, dest)
            with sf.SoundFile(wav) as recording:
                for row in measure_recording(recording, events, model, mapping, indices):
                    rows.append(dict(file=Path(name).stem, **row))
    return rows


def summarize(rows):
    result = {}
    for arm in ('original', 'filtered'):
        pairs = [(row[arm + '_event'], row[arm + '_control']) for row in rows]
        result[arm] = {
            'event_hits': sum(event[0] for event, _ in pairs),
            'control_hits': sum(control[0] for _, control in pairs),
            'event_scores_higher': sum(event[1] > control[1] for event, control in pairs),
            'tied': sum(event[1] == control[1] for event, control in pairs),
        }
    for kind in ('event', 'control'):
        transitions = Counter((row['original_' + kind][0], row['filtered_' + kind][0]) for row in rows)
        result[kind + '_changed'] = dict(gained=transitions[False, True], lost=transitions[True, False])
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive_directory', type=Path)
    args = parser.parse_args()
    rows = measure(args.archive_directory)
    by_class = {k: [r for r in rows if r['label'] == k] for k in sorted({r['label'] for r in rows})}
    print(json.dumps(dict(n=len(rows), results=summarize(rows),
                          by_class={k: dict(n=len(v), **summarize(v)) for k, v in by_class.items()},
                          rows=rows), indent=2))
