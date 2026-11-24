# Following an event through playback

[한국어 사용법과 시험 결과](event-paths.ko.md)

On the first pass, a reader can report “I heard a brief sound on the right” and keep that observation when a later picture suggests a cause. The four operations below use the same released film clock in a native-media client and in an image-and-text client.

| Operation | Evidence the reader receives |
|---|---|
| `view(turn=1)` | A short selection of measured changes. Each `id` identifies one observation. A `track` can connect multiple observations across turns. |
| `recall_events(turn=1)` | Measured observations and the reader's own dated reports. `next_after` pages measurements. `next_after_revision` pages reports. A turn and a revision freeze what was known. |
| `inspect_event(turn=1, event_id="1.2", sense="sound")` | Audio evidence on that event's original clock. Picture, depth, sources, foreground, layers, words, scene and binding are other choices. Use an ID actually returned by this viewing. |
| `remember_event(turn=1, event_id="1.2", judgment="unidentified", claim="a quick metallic rattle")` | A dated report. A reader can use an arbitrary released interval, a channel or a screen rectangle for events the automatic selection missed. |

A later report can cite the same event with `revises`, or cite a second event with `related_id` and `relation`. Use `prediction` to record an expectation before advancing. A subsequent observation can support or contradict it. Each report keeps its first exposure, access to replays, optional confidence and film cutoff. Reader reports stay in the current server session by default. The optional journal below archives them. The saved turn manifests keep the measured events. Inspect those with `reel events DIR --turn 4`. Older manifests without an index report their missing turn numbers.

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
| Find an earlier sound inside a later mixture | 96/96 top positions within 50 ms. 79 passed the strong waveform cutoff. The top match passed on 0/96 background controls. | Half-second excerpts from 96 held-out FSD50K recordings. Each target was mixed with another recording at a new position with inverted polarity and adjusted level. This tests repetition of the recorded signal. |
| Recognize an anonymous voice on a different utterance | 9/14 correct unique voice matches, 2 without an assignment | Two LibriSpeech test-clean utterances per speaker. All 14 speakers enrolled before the independent second utterances. A split profile counts as a miss. |
| Follow a human-annotated visible point | 142/574 points had an automatic landmark within 0.08 normalized screen units initially. 126/142 were near the point after 29 frames, versus 119/142 holding the initial landmark still. | 18 videos with point annotations. Both methods used the same automatically proposed starting points. 133/142 tracks remained visible. |
| Same scene with and without a key sound | With the cue, local GPT described a click and metallic rattle. Without it, GPT reported steady hiss. On silence it reported silence. | Sound alone through 8.5 seconds. Changing later footage produced identical evidence bytes. The matching control reused the answer. The image-and-text GPT received spectra and an Audio Flamingo waveform reading. |

Each task has a different unit and reference. A human-relative score for free sound recall appears below. See the actual utterance IDs, time estimates, point coordinates and GPT responses in the [trial records](benchmarks/event-paths.json). Run `python examples/check_event_paths.py` to rescore them.

## Hearing an unfamiliar instance

A short filtered replay can help when the background drowns out an event. With `hear_change(turn=3, reference=[0,1.5], target=[2,3], channel=2)`, Reel returns the earlier channel, the original target and the target with persistent background frequencies attenuated. All three stay at recorded level. `inspect_event(sense="foreground")` uses released sound around the selected event and the preceding background. For overlapping sources, give `listen_layer` an earlier example. Its comparison can choose the original mixture if that sounds closest.

In 43 mixtures, the query sound came from a **different recording by another uploader** with the same FSD50K label as the second source. The selected output improved that source’s SI-SDR over the unfiltered mixture in 25 cases, worsened it in 15 and retained the mixture in three. Median gain was 5.8 dB. The model received the reference waveform alone. Selection by resemblance has a cost when the wrong component wins.

On 51 four-second field excerpts, the prior whole-mixture and split replays each led GPT to name a short human-annotated source in two clips. The additional background-filtered reading preserved both and added a passing car, for **3/51**. The filter could draw on up to 1.5 seconds of earlier background. Partial descriptions and other unannotated sounds receive no exact-name credit. This is the weakest measured part of the listening path.

## Relative depth and durable reports

Run `pip install -e '.[depth]'` and `reel setup --depth`. Then call `depth_at(turn=2, at_s=1.5, points=[[0.2,0.4],[0.7,0.6]])`. The MCP reply contains the original released picture, a relative depth map and the reader’s ordering of those points. Bright areas are estimated nearer. Two points with almost the same normalized values give an unresolved result. On 64 images selected across all eight DA-2K scenarios, it ordered 115/127 annotated pairs correctly, left nine unresolved and reversed three. Physical distance and the depth of an unseen sound source are unknown. [Model](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf) · [Benchmark](https://huggingface.co/datasets/depth-anything/DA-2K)

To keep a reader’s notes after a session, start `reel mcp film.mp4 --journal observations.sqlite`. Each stored report has a viewing ID, revision and digest of the evidence published by that moment. Separate viewers sharing a database get separate sessions. List them with `reel reports observations.sqlite`. Use `reel reports observations.sqlite --session ID --through-turn 4` to read reports known at a cutoff. Opening the archive is explicit. A new playback starts with fresh impressions.

## Agreement with people

Sixteen Clotho recordings had five independent human descriptions each. Local GPT described each recording before seeing any questions. It received the original spectrum, cached independent waveform readings of those same samples and the new first-pass attention events. Its same-recording advantage over descriptions of other recordings was **43.5% of a held-out human’s advantage** (film-bootstrap 95% interval 19.6–66.0%). The previous playback scored 42.7% on these recordings. This small difference is within the uncertainty of the comparison.

Each recording also had four questions, each with three human votes. After the local waveform reader heard the questions, GPT matched the human majority on 42/64, answered three incorrectly and left 19 unresolved. Another fresh run with the same questions, probability prompt, spectrum and waveform readings, omitting the new landmarks, matched 43/64 with three wrong and 18 unresolved. Human raters agreed with one another on 75.0% of individual pairs. GPT with landmarks matched a randomly chosen human vote on 58.9%, including abstentions as misses. Its mean Brier loss against individual votes was 0.1684, versus 0.1682 in the matched control. The older prompt's 37/64 result is a historical comparison. These trials measured existing human responses and fresh GPT outputs.

The transport test also opened the same bounded film through two real MCP sessions. Both exposed 61 events and the same playable movie bytes. An audio-capable client received WAV blocks. An image/text client received text and pictures. Both recorded a first impression and a later revision. A request for a picture beyond the cutoff was rejected in both. This exercises the transport contract independently of a particular model provider.

## Run a blind listener study

Prepare the media files through the study cutoff before distributing them. Each trial in a study manifest names a released file and its SHA-256, its tracks, question, choices and phase. The collector anonymizes playback filenames and records answer latency from playback completion to the listener’s response. First-pass questions appear after playback. Directed questions are visible before playback as well. For a delayed question, set the phase to `recall` and `from_trial` to the ID of the original presentation. Use opaque trial IDs. The collector asks recall questions after the presentation block without playing the clip again. It saves the observed delay and response time.

```json
{"panel":"hearing-01","trials":[{"id":"c07","media":"released/c07.wav","sha256":"<digest of released bytes>","tracks":"sound","phase":"first_pass","question":"Did you hear a voice?","choices":["yes","no"]}]}
```

```sh
python examples/listener_study.py collect study.json study.sqlite --reader listener-01
python examples/listener_study.py import-model model-reports.json study.sqlite
python examples/listener_study.py score study.sqlite
```

The model report file is a JSON list. Each entry gives `panel`, `trial`, `phase`, `evidence` (the digest), `tracks`, `question`, `reader`, `answer` and optionally `prob_yes`. Use a different pseudonym for each independent listener. The comparison requires matching exposure, phase and question, and at least two human responses. Duplicate responses from the same reader are rejected. The score includes majority agreement, abstentions, human–human and model–human agreement, and Brier loss where probabilities are available. These future studies can include new human observers in addition to the archived references above.

## Checking a visible source

A local timing measurement found a region in three of 24 clips with a visible, annotated sound source. None of the three region centres fell inside the human box. It also proposed a region in one of 24 clips where the annotated source was hidden. Shifting the audio by 480 ms produced one proposal across the 48 clips, again outside the human box. These measurements remain in the event index for inspection. First-pass attention omits the proposed regions.

Use `inspect_event(turn=N, event_id=ID, sense="binding")` to review the independent sound impression and spectrum before the complete picture at the event endpoint. The response identifies which interval the waveform reader heard and which exposure the client received. Record an uncertain or offscreen interpretation when the evidence is ambiguous. The `sync_regions` argument of `ask_scene` lets the reader compare competing movements over time. [Perception Test source-visibility annotations](https://github.com/google-deepmind/perception_test)


To rerun the signal and voice trials from the original dataset releases, point the commands below at the FSD50K evaluation WAV directory, the extracted LibriSpeech `test-clean` directory or the DA-2K archive. The scripts use the published trial roster.

```sh
python examples/check_event_paths.py --fsd /path/to/FSD50K.eval_audio
python examples/check_event_paths.py --speech /path/to/LibriSpeech/test-clean
python examples/check_picture_depth.py /path/to/DA-2K.zip
```

## Matched pictures, swapped recordings and playback cost

On the same 48 held-out films, a fresh local GPT received the independent sound impression and spectrum plus the recorded picture. It placed a point inside the human box in 1/24 visible-source clips. When the annotated source was offscreen, GPT selected a visible point in 0/24, explicitly reported an unseen source in 4 and left 20 unresolved. Keeping each picture and swapping in the sound evidence of another annotated class produced 0/24 points inside the original visible-source box and 0/24 visible guesses on hidden-source clips. A frame can contain another plausible source. These scores concern the annotated sound. The reader received no event label or candidate boxes.

On the door control, changing only the future left all 204 exposed picture timestamps, decoded picture sheets, PCM and 171 measured observations identical through 8.5 seconds. Removing the cue changed the PCM and observations while preserving the pictures.

On this Mac, publishing the first eight seconds of the demo took a median 1.15 s before the event index and 1.53 s with it across three runs per version. The first two-second turn took 0.27 s and 0.32 s respectively. These timings cover the movie-only path with semantic readers disabled.
