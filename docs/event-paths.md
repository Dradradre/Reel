# Following an event through playback

On the first pass, a reader can report “I heard a brief sound on the right” and keep that observation when a later picture suggests a cause. The four operations below use the same released film clock in a native-media client and in an image-and-text client.

| Operation | Evidence the reader receives |
|---|---|
| `view(turn=1)` | A short selection of measured changes. Each `id` identifies one observation. A `track` can connect multiple observations across turns. |
| `recall_events(turn=1)` | Measured observations and the reader's own dated reports. `next_after` pages measurements. `next_after_revision` pages reports. A turn and a revision freeze what was known. |
| `inspect_event(turn=1, event_id="1.2", sense="sound")` | Audio evidence on that event's original clock. Picture, sources, layers, words and scene are other choices. Use an ID actually returned by this viewing. |
| `remember_event(turn=1, event_id="1.2", judgment="unidentified", claim="a quick metallic rattle")` | A dated report. A reader can use an arbitrary released interval, a channel or a screen rectangle for events the automatic selection missed. |

A later report can cite the same event with `revises`, or cite a second event with `related_id` and `relation`. Use `prediction` to record an expectation before advancing. A subsequent observation can support or contradict it. Each report keeps its first exposure, access to replays, optional confidence and film cutoff. Reader reports belong to the current server session. The saved turn manifests keep the measured events. Inspect those with `reel events DIR --turn 4`. Older manifests without an index report their missing turn numbers.

## What can be followed

| Capability | Implementation and limits |
|---|---|
| Causal sight and hearing | The input to each measurement comes from the exposed frames and released PCM samples. An attack confirmed after a pause retains its original time and gets its ID when released. |
| Detection and discrimination | Attacks, short frequency tracks, relative channel levels and pitch preserve details the reader can mention without naming a source. Silence, unclear speech and an unidentified sound are distinct reports. |
| Simultaneous sources | Narrow frequency components share a clock. Mixture separation and the remaining waveform can be auditioned independently. Several harmonics may belong to one source. |
| Recognition | `find_sound` aligns the waveform and its evolving spectrum to a later channel. `listen_layer(reference=[a,b])` can rank mixture estimates against an earlier sound by embedding resemblance. Open descriptions and reference recordings supply possible names. |
| Space and visibility | Recorded channel balance and arrival, picture coordinates and visible spans support relative position. A stereo pan, a mono mix and a cropped camera view can all leave the physical source position unresolved. |
| Speech, music and environment | The event index preserves optional sound-class candidates, supported timed words and anonymous voice matches. Regular runs of at least five attacks supply pulse period. A repeated machine sound can also have a pulse. |
| Picture and sound together | A five-second rolling analysis compares movement in twelve screen regions with rising channel level. It compares the strongest region with other regions, global changes and shifts of 240–480 ms. Incidental synchronization remains possible. |
| Attention | First views select prominent details. Every indexed ID and original media remain accessible. The reader can select an unindexed detail, revisit a channel or magnify any released region. |
| Order and expectation | Observed time and report time are separate. Predictions remain alongside later observations and revisions. |
| Memory and continuity | Scoped IDs follow local picture texture or narrow spectral peaks. Gaps in visible texture are recorded. A rediscovered texture, similar voice or similar sound still needs interpretation. |
| Uncertainty | Separate detected, unidentified, identified, uncertain, no signal, unclear words and prediction. A probability on a reader report is self-reported. |
| Transfer between clients | Native WAVs or playable movies, spectra, pictures and attributed local model readings share a clock. A client must support the offered content type to perceive it directly. |

Install the optional voice model with `pip install -e '.[voices]'` and `reel setup --voices`. Start `reel mcp film.mp4 --voices` or `reel play film.mp4 --voices --out turns`. A 1.5-second window advances every 0.75 seconds. The encoder uses speech-active samples from each recorded channel. Similarity to session-local voice profiles can yield a new profile, an existing one or an unresolved match. Simultaneous speakers on one channel can merge. All optional models keep their own setup requirements.

## Measured trials

| Task | Result | What was tested |
|---|---|---|
| Find an earlier sound inside a later mixture | 96/96 top positions within 50 ms. 79 passed the strong waveform cutoff. 0/96 background controls passed. | Half-second excerpts from 96 held-out FSD50K recordings. Each target was mixed with another recording at a new position with inverted polarity and adjusted level. This tests repetition of the recorded signal. |
| Recognize an anonymous voice on a different utterance | 9/14 correct unique voice matches, 2 without an assignment | Two LibriSpeech test-clean utterances per speaker. All 14 speakers enrolled before the independent second utterances. A split profile counts as a miss. |
| Follow a human-annotated visible point | 142/574 points had an automatic landmark within 0.08 normalized screen units initially. 126/142 were near the point after 29 frames, versus 119/142 holding the initial landmark still. | 18 videos with point annotations. Both methods used the same automatically proposed starting points. 133/142 tracks remained visible. |
| Same scene with and without a key sound | With the cue, local GPT described a click and metallic rattle. Without it, GPT reported steady hiss. On silence it reported silence. | Sound alone through 8.5 seconds. Changing later footage produced identical evidence bytes. The matching control reused the answer. The image-and-text GPT received spectra and an Audio Flamingo waveform reading. |

Each task has a different unit and reference. These measurements do not give a percentage for overall human perception. See the actual utterance IDs, time estimates, point coordinates and GPT responses in the [trial records](benchmarks/event-paths.json). Run `python examples/check_event_paths.py` to rescore them.
