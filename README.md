<div align="center">

# Reel

**Let a language model watch a film.**

Reel lets a model follow a film as it unfolds. At each pause it delivers the pictures and the recording up to that moment. A model that reads pictures and text can use a local listener’s account of the same sound. The reader can revisit something it noticed or follow the film on 30-second pages.

![python](https://img.shields.io/badge/python-3.11%2B-3776AB)
![platform](https://img.shields.io/badge/macOS-Apple%20Silicon-black)
![license](https://img.shields.io/badge/license-MIT-green)

[한국어](README.ko.md)

<img src="assets/reel_page_1.png" alt="The first 30 seconds of the demo on one time axis" width="100%">

</div>

## The problem

A viewer can hear a voice before its speaker comes into view. A hand can move and return to the same spot between two stills. To follow either event, the reader needs the pictures and the recording on the same clock. It also needs to know when the source of a sound is still out of sight.

Editing gives a practical test. A repeated second of commentary, a hard cut to silence or a subtitle that appears before the voice should be perceptible to a reader seeing the film for the first time.

## The idea

A reader that only reads still lives through time: reading order is its time. So give it the whole film in reading
order, with nothing left out.

- **Gapless.** Every frame and every 10 ms of sound leaves a mark on a page. Values are max-pooled per pixel column,
  never sampled, so a one-frame flash or a one-second repeat survives.
- **One time axis.** Picture, on-screen text, sound, loudness, crowd and speech sit on the same axis, so you can see
  whether a voice belongs to its picture or a cut lands with its sound.
- **Memory.** A viewer thinks "I just heard that" and "I have seen this before". The reel marks both: a sample-exact
  replay of the same recording in red, similar footage and voices coming back from earlier with the time they first played.
- **Read before judging.** Pages run strictly forward, 30 seconds each, and are read in order with a short log after
  each page, before any summary. Nothing on a page is a verdict.

## What a page shows

<img src="assets/reel_page_2.png" alt="Second page of the demo reel" width="100%">

| Lane | What it is | What a viewer notices there |
|---|---|---|
| frames over time | selected whole frames at their recorded presentation times. A frame can stay up across a long pause between updates. Reel maps tagged PQ and HLG pictures to sRGB with a colour-managed FFmpeg build | See who and what share the screen. During playback, extra timestamped frames can reveal a brief local change. A pair of times and a box let you inspect its original pixels with `look`. [Reading trial](docs/perception-check.md#잠깐-나타난-글자를-다시-읽을-때) |
| follow (playback) | the point you selected in an earlier picture, followed through each released frame | compare the selected detail with the last picture in which the tracker located it |
| subject | where the person the film is about appears, and whether the eye is predicted to land on them | the film losing its subject |
| on screen | recognized words and numbers during the frames in which they appear | read a score or sign alongside the sound at that moment |
| change | frame-to-frame change, brightness, freeze marks. On MCP replay, `ask_scene(exposure_detail=true)` places the recorded peak under each consecutive picture in a short sequence. With `sync_regions`, each motion trace covers the whole requested rectangle | cuts, flashes, blackouts, a frozen picture |
| seen again | pictures that already appeared | reused footage |
| sound | power in each frequency band on a fixed −80 to 0 dBFS scale, with changes in the sound marked | the same sound keeps its brightness across recordings and zooms. During playback you can rehear a sustained band or a brief impact alongside the original. The speech reader measures what remains in the gaps before, between and after detected voices. Playback also reports the frequency of a clearly repeating sound. |
| sounds like | short sound changes on the page. Live playback also gives the time, channel and frequency range of sharp rises or falls. Source estimates cover up to ten seconds in `stream.md` and four seconds per channel in `play`. First-listen descriptions can cover ten seconds. A first MCP view includes available local waveform readings and supported words with or without a WAV attachment. On replay, compare the one-second estimates with one continuous reading of the sound. | a single impact, music underneath it, or voices in a busy room |
| level | recorded mix level in 10 ms steps, with the typical level and strongest moment of each window in `stream.md` | a slow fade, a quiet background or a brief rise |
| L level, R level | 10 ms levels for each recorded channel, numbered on larger layouts | a sound that is louder on one side or moves across the mix |
| 1 early, 2 early | Which recorded channel received a matching waveform first, in quarter-second windows | Two sounds can have the same left and right levels and still be heard toward one ear. A gap means the timing is unresolved. |
| film time, pixel change, ch peak, ch bands Hz | During playback each column shows a centre frame and sometimes an extra view below it. When revisiting a range with `observe` or looking closer with `view(focus=...)`, read each row left to right. Follow the line under a picture to the motion trace, the channel peaks and the frequency bands on replay. Brighter bands carry more energy. Cropping the picture keeps the original mix. | Follow a brief movement and check when it happened relative to a sound. |
| joint (optional) | Two readings of the same recording, one with moving pictures on their display clock. A live reader can ask about the scene across pauses. | Follow a sound and the movement that accompanies it. |
| in sync? | where the sound sits against the picture, from 2 s early to 2 s late, moment by moment | lips moving before the words, a ball struck before its sound |
| crowd stem | estimated audience sound on the same band-power scale as the mix, with repeated rhythms marked | the timing of a possible chant and its relation to the recorded mix |
| said | On replay, `heard_words` can check just the selected interval. Playback marks possible voices found only after amplification and keeps their times separate from the transcript. The page shows words that agree in two listening windows, checked against the mix when only the stem suggests speech. Gain-assisted candidates are available in `reel listen` | what is said; whether it matches the subtitles |
| heard again | the very same recording played again, in red (an echo within 3 s or a stretch pasted from far back), or a similar voice from earlier | an edit echo, a recycled crowd recording |

## Does it work?

When one of two filmed actions supplied the soundtrack, GPT chose the correct picture on 9 of 14 plays. It followed both soundtrack swaps in 3 of 7 pairs. On eight separate single scenes, GPT left source visibility unresolved every time. [Responses and comparison with the previous version](docs/perception-check.md#화면-가장자리에서-난-소리)

With frame-by-frame replay available, local Codex GPT matched the human annotations on **35 of 48 questions (72.9%)** on the same 16 films. It matched 15/16 picture answers, 10/16 sounds and 10/16 source-visibility answers. On a separate timing control, a brief tone moved through all 12 frames of the same sequence. GPT read the number on the sounding frame **12/12** times with the option available and **6/12** with the previous replay. [Comparison and individual responses](docs/perception-check.md#소리가-난-프레임을-다시-보는-시험)

In the preceding version, on 16 films new to Reel development, local GPT matched the human annotations on **38 of 48 questions (79.2%)**. Each of eight sounds had one film with a visible source and one with a hidden source. GPT guessed 23 of 48 correctly from the questions and choices alone. Across these and the 28 earlier films, agreement was **76.5% (101/132)**. [Results on the 16 new films](docs/perception-check.md#처음-사용한-영상-16편)

On all 28 filmed scenes, local Codex GPT matched the human annotations on **63/84 questions (75.0%)**. It matched 25/28 for the picture, 20/28 for the sound in a specific interval and 18/28 for whether the sound source was visible. It gave 12 wrong answers and abstained on 9. Each film ran in a fresh session of the same version with pictures and local sound readings. The three panels used the original questions and choices. [Film-by-film answers and scoring](docs/perception-check.md#같은-버전으로-다시-본-실사-영상-28편)

On 23 urban recordings, GPT and individual listeners overlapped on **56.3%** of the sound categories either picked. Two people listening to the same recording averaged **58.9%**. GPT missed children’s voices in all three recordings where a majority marked them. [Individual recordings and listener comparison](docs/listening-check.md#도시-녹음에서-gpt와-청취자가-고른-소리)

Fresh readers (a model given only the [reading guide](docs/READING.md) and the reel, never told what was wrong)
watched real fan edits that a person had already reviewed:

| Film | What the person complained about | Noticed by the blind reader |
|---|---|---|
| 10-minute esports documentary, first cut | the order of years jumps around; abrupt blackouts; a second of commentary plays twice; subtitles drift from the voice; a frozen picture | 4 of 5 in the first round; **5 of 5** after exact-repeat hearing was added |
| 6-minute player tribute, first cut | a team chant looped under the title cards; choppy joins; rushed second half; letterboxed game footage that loses the scoreboard; the player not in focus | **4 of 5**: the chant (named **T1** by sound alone, in 4 of 4 chant windows), the holes of dead silence, the pile of still cards, the game footage cropped to 74-79% height with the score bar gone. Focus was only touched on ("the later wins are called through other players") |
| 2-minute hype trailer, **never used while building Reel** | thunder effects that sound like a hand slap; scenes that mean nothing; cheap-sounding music | **3 of 3**: "2 of the 9 hits sound like a slap", impacts heard as a splash, a car, a quack; a 13 s strobe of years over the same two stills and a black cold open; the bed taken for "free stock music" (that verdict leaned on the credits as much as on the sound) |
| 1.5-minute hype trailer, **never used while building Reel** | plays with no impact; a glitch effect that fights the shot; kill banners naming the wrong player; poor picture quality | **4 of 4**: "it feels like the film ran out of moments"; a 5 s glitch strobe of one press-conference shot; a Faker kill banner in Zeus's debut; soft, upscaled-looking footage |
| the same documentary, revised | nothing yet | found faults the editor's own checks had missed: commentary over quote cards, sound dropping at chapter turns, an unreadable results grid |

### Random videos from the web

26 videos picked at random from searches across genres: talk shows, local TV news, animation, skate and drum videos,
cooking, drone footage, football, tennis and F1 highlights, a TED talk, trailers, a podcast, live music, a crash-test
compilation, a chess stream, vlogs. A script planted editing faults at random times (repeats, echoes, blackouts,
freezes, flashes, letterboxing, low-resolution stretches, dropouts, volume jumps, sound running early or late against
the picture) and kept the answer key away from the reader. One video per round was left untouched.

| Round | Planted faults noticed | On the untouched video |
|---|---|---|
| 1 | 9 of 15 | called the original editor's fade a hard cut |
| 2 | 14 of 15 | "nothing wrong" |
| 3 | 10 of 15 | "nothing wrong" |
| 4 | 10 of 14 | "nothing wrong" |
| **5: code frozen, nothing changed after** | **12 of 17** (14 counting a slip in sync noticed by the stutter it leaves) | **"nothing wrong"** |

Senses were added between rounds 1 and 4, after looking at what the reader missed: sound that cuts dead or jumps in
volume, flashes of a few frames, footage suddenly softer than around it, picture-sound sync, and the very same recording
replayed. Round 5 is the honest number: new videos, code untouched.

In round 5, by kind: letterboxing 3 of 3, blackouts 3 of 3, echoes 3 of 3, a pasted repeat 1 of 1, dropouts 1 of 2,
sound early or late 1 of 3, a freeze 0 of 1, a volume jump 0 of 1. One false alarm: a hum in a chess stream marked as a
sudden volume jump. Across all five rounds the reader never invented a fault in an untouched video.

## Quickstart

Reel is built for macOS on Apple Silicon (it uses Apple Vision for text and MLX for speech). It needs `ffmpeg`.

```bash
git clone <this repo> reel && cd reel
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"           # or pick extras: separate, crowd, mlx, speech, ocr, gaze
reel setup --sync                  # fetch the gaze model and the sync model (1.1 GB)
reel doctor                        # what this machine can see and hear

reel play film.mp4 --out reels/first-view --until 8.5
reel play film.mp4 --out reels/sound-only --until 8.5 --tracks sound
reel watch film.mp4 --out reels/film --subject Zeus --genre lol --lang ko
reel watch film.mp4 --until 8.5 --out reels/view-01
reel zoom reels/film --t0 72 --t1 80          # re-watch a stretch closer
reel frame reels/film --at 73.25              # inspect the full frame on display here
reel frame reels/film --t0 73 --t1 74         # read every displayed frame in this second
reel listen reels/view-01 --t0 4 --t1 7       # inspect channel levels and changes on the film's clock
reel observe reels/view-01 --t0 4 --t1 4.5    # see each frame with the audio that played during it
```

To find out what a viewer picked up from the picture or the soundtrack, start fresh sessions on the same file with `--tracks picture`, `--tracks sound` and the default `--tracks both`. Both `play` and `mcp` keep the original film clock and cutoff. Later looks and listens in each session use the selected tracks. [What people and GPT noticed in eight films](docs/perception-check.md#같은-영상을-보거나-듣거나-둘-다-했을-때)

When a file has several picture or sound tracks, Reel uses the first track marked default within each type. With no marked default it takes the first eligible track. Attached cover art and thumbnail tracks are excluded from the moving picture. The same selection supplies the pictures, recording and transcript.

Reel measures pitch changes in each recorded channel. A clear repeating sound leaves a series of frequency estimates in `audio.pitch_contour`, so a reader can follow a rise or fall even when the source is unclear.

An MCP client can receive the pictures and recorded sound directly. Install `pip install -e '.[mcp]'` and configure a local stdio server with the command `reel mcp /path/to/film.mp4 --until 8.5`. For Codex CLI, run this command from the active Reel environment.

```bash
codex mcp add reel -- "$(command -v reel)" mcp /path/to/film.mp4 --until 8.5 --image-client
```

A host that accepts video can call `view_media(turn=1, continuous_seconds=5)` and pass the returned movie to its model or player. The movie keeps the picture timing and recorded channels together. It shares the cutoff and turn numbers with `view`. The host reads the MCP resource links in order. In our Codex CLI test GPT received encoded bytes, so use `view` there. [Playback and file format](docs/READING.md#watching-a-little-at-a-time)

A normal `view(turn=1)` includes the original WAV, the prepared local listener’s description of that turn and any timed words supported by two transcriptions. This lets clients use the same listening evidence when their model accepts only pictures and text. With `--image-client` or `native_audio=false`, the WAV stays available on request. Use `sound_estimates=false` to get the recording and measurements without interpretations. Install the speech extra and run `reel setup --speech --describe-sounds` to prepare the local readers. [Test with the same pictures and different recordings](docs/perception-check.md#wav를-첨부했을-때-gpt가-실제로-받아들인-소리)

If a client omits the WAV, it can still report indirect observations from the channel measurements and local readings. The first-impression viewer uses the same guidance. [Speech and surrounding sounds](docs/perception-check.md#목소리와-그-목소리가-가리키는-소리)

For a picture-and-text viewer the timed readings come first. Each channel also carries measurements from the original recording, including its level, the frequency range containing the middle 80% of its spectral power, and the share in its strongest narrow band. These can distinguish a steady tone from broadband noise even at the same level.

The first view also shows when one channel was louder or received the same waveform earlier, even when the levels were equal. Replay with `full_detail=true` for the individual measurements and encoder timings. [Blind listening and viewing results](docs/perception-check.md#원래-녹음의-주파수와-첫-설명)

Ask the model to view the film. When the sound changes sharply, the model also receives the pictures displayed around that instant. Start the server with `--describe-sounds` to include the local waveform reader’s impression at each pause. Add `--gaze` for a sequence of details selected by the visual gaze model. Each `view` call returns one interval. To inspect a moment you have passed, use `observe(start_s, end_s)` to get the successive pictures and the matching recording together. Audio-capable clients get its WAV. An image-and-text client can inspect the pictures and spectrum, ask the local listener about a sound, or check spoken words. [Tool usage](docs/READING.md#watching-a-little-at-a-time) · [Codex MCP configuration](https://developers.openai.com/codex/mcp/)

An ordinary turn also includes a chronological sheet when the overview skips a visible change and every successive picture fits on one sheet. Repeated identical previews can share one cell. To see a quick action unfold on a busy screen, call `view(turn=1, max_frames=12)`. Reel shows every displayed picture in that turn, in time order, and stops the recording at the same boundary. Choose 1–12 pictures on any advance. On the command line use `reel play film.mp4 --out reels/frames --continuous --max-frames 12`. [Brief actions and reversed visual cues](docs/perception-check.md#빠른-동작을-프레임-순서대로-본-경우)

In a busy scene, a normal turn can also show up to twelve original pictures of brief local changes that the overview skipped. Their timestamps appear in `selected_picture_pts_us`. [Camera footage with human action timings](docs/perception-check.md#움직이는-장면에서-대표-화면-사이에-보인-모습)

For the pictures shown on a turn, `audio.during_pictures` gives the peak and RMS level in each channel while that exposure was on screen. Each row identifies the samples in the turn’s WAV. Sounds can also fall between the pictured exposures.

To follow an exchange or movement through a cut, use `view(turn=1, continuous_seconds=8)`. Reel returns the pictures in order and the recording from the same interval. The next turn can continue at that pace or stop on changes again. On the command line, `reel play film.mp4 --out reels/view --step 8 --continuous` uses eight-second intervals. [What human viewers and GPT recalled](docs/perception-check.md#한-장면을-이어-본-뒤-기억한-내용)

For a question about the sound and action together, call `view(turn=2, question="What changes when the sound begins?")`. The local reader sees the moving pictures and hears the same interval. Add `joint_seconds=6` to look back through earlier pauses. With `reel mcp FILM --joint --image-client`, a continuous view includes that audiovisual reading on the first watch. [First descriptions of eight films](docs/perception-check.md#사람들이-같은-영상을-설명한-문장)

To see how a sound relates to a movement that began before the last pause, use `view(turn=N, joint_seconds=6)`. The local reader hears the last six seconds and then sees the same interval with its sound. It uses only frames and samples released by turn N. An image-and-text client receives both descriptions in `joint`. To ask about a shorter event inside a turn, use `ask_scene(start_s=2.4, end_s=3.7, question="What moved when the sound changed?")`. The pictures, measured channel levels, local reading and optional WAV cover that same interval. With just the two times, Reel returns the pictures, channel levels and times of frequency-band changes without loading a model. At a terminal playback pause, enter `joint 6`. [Comparison with human event annotations](docs/perception-check.md#여러-번-멈춘-장면의-화면과-소리를-이어-볼-때)

To follow changes in the sound alongside the pictures, call `ask_scene(start_s=2, end_s=7, alternatives=["water running", "a person speaking"])`. Each channel has a comparison for each second and a second reading of the uninterrupted recording. The first uses short clips repeated inside the encoder. The second plays the original sequence once and pads the unused encoder input with zeros. At a playback pause use `match 2 7 --per-second`. Use `alternatives=[]` to see nearby catalogue sounds when you have no name in mind. [Tests on sound order](docs/perception-check.md#원음에서-소리-두-가지의-순서-짚기)

To check two visible movements against the sound, call `ask_scene(start_s=2, end_s=5, sync_regions=[[0.1,0.3,0.4,0.8],[0.6,0.3,0.9,0.8]])`. Each numbered box outlines the full area measured, including movement at the edges. Each trace shares its clock with the recorded channel peaks. With `reel setup --sync`, the local sync reader also measures how closely each region moves in time with each channel.

To magnify a fixed part of the picture, use `view(turn=2, focus=[0.7, 0.4], max_seconds=0.3)`. This pauses within 0.3 s and shows the chosen part of the scene through time, the whole picture and the same sound. The final picture also shows the exact region enlarged next to its location. You can choose another point or pace on the next turn. To inspect original pixels from an earlier moment, use `look(turn=2, at_s=0.15, box=[0.5, 0.2, 0.9, 0.6])`.

To follow something that moves across the screen, select a point in a picture you have seen. For example, if the point was at `[0.7,0.4]` in the picture at 1.25 seconds, use `view(turn=2, focus=[0.7,0.4], follow_from_s=1.25)`. Reel follows it through the end of turn 2, up to eight seconds. The two close-ups show the point you selected and its most recent match. Reel uses the original picture when available. If the tracker loses the point, the second close-up gives the time of the last match. Basic tracking uses optical flow. Run `reel setup --follow` to prepare the local online BootsTAPIR model, which can pick up a point again after it was hidden. [Tracking checks](docs/perception-check.md#움직이는-점을-따라본-결과)

For a first look without suggested sound names or speech, use `view(turn=1, sound_estimates=false)`. Once playback has reached the time you want, `listen(turn=N, start_s=2, end_s=7, native_audio=true, describe=false)` returns the recorded channels and their spectrum from 2 to 7 seconds, even across pauses. A reader that needs help identifying the sound can set `describe=true` to ask the local waveform reader about that same interval.

When you have two explanations for a sound, compare them with the original recording. For example, `listen(turn=3, start_s=2, end_s=5, describe=false, alternatives=["a voice humming", "an electric motor running"])` returns a similarity score for each description on each channel. Use `alternatives=[]` to ask for nearby catalogue sounds. Prepare the local model with `reel setup --compare-sounds`. A close match can still misidentify the source.

When sounds overlap, try `listen(turn=N, start_s=2, end_s=7, component="percussive")` to bring out brief, broadband sounds. Use `"harmonic"` for frequency bands that persist. The spectrum and optional WAV follow that choice. The local listener hears the selected component and the original recording together. A single source can appear in both components. [Listening comparison](docs/listening-check.md#겹쳐-들리는-소리를-원음과-대조)

For a first viewing, `play` works directly from the film. Open the printed `view.json` and its frame sheets, sound image and final still. The WAV plays the same interval on its original channels. Type `next` when ready, `next 0.4` for a closer look at the next part, or `quit`. An agent can write these commands to standard input. A change in the picture or recorded level can bring the next pause forward. Add `--pause-on-sound-change` to `play` or `mcp` to pause on a sharp shift between frequency bands, even at steady volume. Each turn reports those shifts at either pace. Quiet stretches continue in order. Use `reel listen reels/first-view/turn_00001` for local sound estimates from a delivered interval.

At each pause, `audio.signal` says whether any recorded sample was nonzero. With cached AudioSet weights, `audio.source_estimates` also gives possible sources per channel over the past four seconds. Use `reel setup --sound-labels` to prepare the weights. A channel can carry sound with no identifiable source.

Run `reel setup --captions` once to enable first-listen descriptions. The next `play` writes a short impression under `audio.scene` as the recording unfolds. It starts with two seconds of sound and can draw on the last ten seconds. The model runs locally in a separate Python 3.10 environment, which setup creates with uv or an installed Python 3.10. Read the description with the channel measurements. The model can mistake a hiss for rain or infer a machine from a steady tone. [Comparison with human listeners](docs/listening-check.md#선택지-없이-현장-녹음을-들었을-때)

When sounds overlap, `reel setup --layers` prepares a local MixIT separator. At a pause, `layer 2 5 2` plays back the chosen channel’s mix from 2–5 seconds and its second-loudest estimate. In MCP, use `listen_layer(turn=3, start_s=2, end_s=5, layer=2)` after the fifth second has played. Pass `channel=2` for the second recorded channel. The mix keeps the channel’s original sample rate, while the estimate uses 16 kHz. Estimates are ranked afresh for each interval and can split one event or contain several.

To seek a sound by ear, prepare CLAP with `reel setup --compare-sounds` and pass `focus="a distant voice"` in place of the layer number. CLAP compares the description with the mix and all eight estimates. The waveform reader hears its inputs without that description. Audio clients can receive both WAVs. Image clients get the spectra and independent readings. [Overlapping sound checks](docs/listening-check.md#겹친-생활음을-하나씩-다시-들었을-때)

To hear what changed against an earlier background, type `compare-heard 0 2 4 6` at a playback pause. It replays the original channels from 0–2 and 4–6 seconds and reads each excerpt independently, so a sound in the second cannot affect the first description. In MCP, call `compare_heard(turn=3, reference=[0,2], target=[4,6])` once that turn has reached six seconds. Both ranges must already have played. Each can be up to eight seconds long. Audio clients can receive both WAVs. Image clients receive the spectra and a separate description for each recording.

For a second listen, type `match 2 4` once playback has reached four seconds. Local CLAP returns similar sounds from a general catalogue. Add your own descriptions, for example `match 2 4 "fabric rubbing" "water running"`, to see how well each fits alongside the catalogue matches. Use `reel match TURN --t0 2 --t1 4` from another process. Set up CLAP with `reel setup --compare-sounds`. [Listening checks](docs/listening-check.md#화면을-두고-녹음만-바꿨을-때)

At a pause, type `listen` for an open description or ask your own question, such as `listen Does the rhythm speed up?`. The local audio model listens to up to 30 seconds leading up to the pause. The reply includes that stretch as a WAV with its recorded channels. Another process can ask the same question with `reel listen reels/first-view/turn_00005 --question "Does the rhythm speed up?"`. [Field recordings compared with human answers](docs/listening-check.md#현장-녹음에서-궁금한-소리-다시-듣기)

When a voice draws attention, type `speech` into the player. It returns possible speech intervals and the words that survived two transcriptions of the recorded audio up to this pause. A detected voice can have no clear words. The same request works from another process with `reel speech reels/first-view/turn_00001`. Add `--speech` to `play` for words at every pause. Give `--lang en` or another language code if you know the language.

To check whether a voice was audible during a particular action, pass the film times to `heard_words(turn=3, start_s=2.1, end_s=4.8)` or use `reel speech TURN --t0 2.1 --t1 4.8`. In the player, type `speech 2.1 4.8`. The selected recording can cross pauses. Voice activity and recognized words are reported separately. An `ask_scene` question or sound comparison also includes this reading.

If you know which language someone is speaking, type `speech bn` for Bengali, for example, or call `heard_words(turn=1, lang="bn")` over MCP. This rereads the sound available through that turn. The result records the supplied language. [Read speech in six languages](docs/perception-check.md#여섯-언어에서-실제로-인용할-수-있었던-말)

When the local waveform reader is active, it can fill gaps in the timed words. It listens to the same channel without seeing pictures or proposed words. The two readers must first agree on an existing word within the same speech interval. When another word matches in spelling and order, it appears under `possible_words`. A reader can mention that word as uncertain. Use `words` for quotations. To request this on a saved turn, use `reel speech TURN --verify`.

At any pause, an image-capable reader can choose a detail from the current frame. Type `look 0.2 0.1 0.6 0.5` into the running player to get the cropped PNG and stay at the same moment. From another process, `reel look reels/first-view/turn_00001 --box 0.2 0.1 0.6 0.5` returns those original pixels and their film timestamp. Coordinates run from 0 to 1, measured from the top left. Add `--gaze` to `play` for an optional crop around the point where UNISAL predicts a viewer would look. [Comparison with recorded human fixations](docs/perception-check.md#같은-화면에서-처음-눈에-띈-곳)

To inspect a detail that has already gone by, type `look --at 1.25 0.2 0.1 0.6 0.5` in the running player. This returns the original pixels from the picture on display at 1.25 seconds. With `--gaze`, each pause can also show the predicted point moving across the preceding second in `gaze.recent_details.file`. [Gaze over time compared with human viewers](docs/perception-check.md#소리가-달라지기-전후의-시선)

At a playback pause, `observe 2 4 --focus 0.1 0.3 0.5 0.8` revisits the chosen rectangle from 2 to 4 seconds. Open the PNGs in `action_view.pages`. The orange marks follow changes in that part of the picture. The blue trace below follows the peaks of the whole recorded channel. Append `--question Does the sound repeat?` to have the local audio reader listen to that exact interval. For a motion between thumbnails, `action_view.frame_pages` contains every displayed frame of the selected region. Playback resumes at the same pause.

To compare hearing a sound with seeing its possible source, type `observe 2 4 --joint`. A local model hears the waveform alone, then receives the same waveform together with the full scene. Read both descriptions in `joint.windows`. You can append `--question What moves when that sound begins?` or use `reel play film.mp4 --out reels/view --until 8.5 --joint` for every pause. Both commands use the hearing environment described below. If the recording continues past the last frame or a movement needs several windows, `joint.recording` describes the sound across the requested interval. A question also gets a separate answer from the waveform alone, including on audio-only files. [What GPT said when the same pictures had different soundtracks](docs/perception-check.md#같은-화면에-다른-영상의-녹음을-붙인-실험)

In a stereo recording, `play`, `observe` and `listen` report the level and arrival-time difference between channels in `stereo_cues`. This also works for named pairs in surround mixes. A soundtrack can put an offscreen sound to one side. To locate a visible source, look for the action that produced it.

When the recorded sound changes sharply, `play` and `observe` also show the full picture just before, during and after the change in `sound_views`. The last panel highlights pixels that changed. A reader can inspect any part of the scene and can leave the sound's source open. Add `--media` to either command for a playable excerpt with the pictures and original-channel PCM together. [Reading an interval](docs/READING.md)

With `--until 8.5`, Reel analyses the first 8.5 seconds and uses the clipped media for zoom, frame inspection and audio excerpts.
Use a new or empty output directory for each snapshot. Give the reader the pages in order with
`stream.md`, then `now.png` if present. This is the full frame on screen immediately before the stop.
When only audio is still playing, there is no current picture. Ask “What did you notice?” before asking about a particular event. [Door trial and reader responses (Korean)](docs/door-trial.md)

For a quick action, the range form of `reel frame` returns JSON pointing to PNG sheets in reading order. Each cell has its presentation time on the film clock. The first frame may already be on screen at the start. A held frame appears once and the end of the requested interval is exclusive. Use `--at` for the full-resolution picture.

Give any reader with image and text input the same page PNG, the matching part of `stream.md`, and the JSON from `reel listen`. A reader with shell access can request its own interval. The JSON identifies its format as `reel.listen.v1` and includes a local WAV path, measured channel levels, times of louder stretches, and possible sources when the local AudioSet model is available. A reader with native audio input can also take the WAV itself.

For a closer look and listen together, `reel observe` returns frame sheets, the native-channel WAV and a JSON entry for each displayed frame. Each entry pairs its image with the exact sample span that played while it was on screen and the measured level in each channel. An agent can use this without any sound recognition model.

With Synchformer installed, add one or more `--sync-region LEFT TOP RIGHT BOTTOM` options to `reel observe`. Coordinates run from 0 to 1 across the displayed frame. Each box gets a timing score against the same sound for each distinct channel. Motion can coincide with sound from somewhere else. [How to read the scores](docs/READING.md)

For an additional comparison, run `reel setup --compare-sounds` once and add `--compare-sounds` to `reel listen`. Local CLAP ranks 527 general sound descriptions and returns its three nearest matches per window. Even an unfamiliar sound can have three poor matches. The scores measure similarity and provide no transcript. Set `REEL_COMPARE_PYTHON` to use a separate Python with the `tags` extra installed.

Run `reel setup --describe-sounds` to prepare local listening and joint picture/sound reads. Setup creates a separate Python environment when needed and fetches the 8-bit Qwen3-Omni weights (about 39 GB, tested on a 64 GB Apple Silicon Mac). If you already have an environment with the `hearing` extra, point `REEL_HEAR_PYTHON` at its Python. Add `--describe-sounds` to `reel play` or `reel listen` for an audio-only interpretation and per-channel measurements. Use the speech track for quoted words.

Then read `reels/film/pages/` in order, following [docs/READING.md](docs/READING.md). About ten minutes of work
per ten minutes of film on an M4 Pro, sync included.

Want to see it first? `python examples/make_demo.py examples/out/demo.mp4 --music any-track-you-may-use.wav` builds
the 70-second demo above, with six faults planted in it.

If a separator needs its own environment, point Reel at it: `REEL_SEP_PYTHON`, `REEL_CROWD_PYTHON`,
`REEL_ASR_PYTHON`, `REEL_GAZE_PYTHON`. Models and caches live in `~/.cache/reel` (`REEL_HOME`).

## How it works

```mermaid
flowchart LR
    F[film] --> P[every frame<br/>thumbnails, change, shape, picture memory]
    F --> A[sound<br/>spectrogram, 10 ms level, scene changes]
    F --> T[on-screen text<br/>Apple Vision, displayed frames]
    F --> S[separation<br/>Demucs voices + crowd model]
    S --> W[speech<br/>Whisper large-v3-turbo]
    S --> R[exact repeats<br/>sample-level correlation]
    S --> C[chants<br/>beat, heard words, sound-alike names]
    F --> G[gaze<br/>UNISAL saliency]
    F --> L[sounds like<br/>AudioSet tagger]
    F --> Y[in sync?<br/>Synchformer]
    P & A & T & W & R & C & G & L & Y --> PG[pages, 30 s each<br/>+ stream.md]
    PG --> RD[a reader goes through them in order]
```

## Limits

- Commentary shouted over a roaring crowd is often still not transcribed. The repeat is marked; the words may not be.
- A chant's word is inferred by sound from names the film shows and the genre's usual names; a name never shown can
  be misheard.
- Gaze is predicted by a saliency model trained on eye tracking, not measured, and it does not know who the star is.
- Sync is read from what the picture shows: lips, hits, steps. Over a voice-over, music under scenery, a small webcam
  face or a fast montage there is little to read, and in animation the reading is weaker ("likely" rather than "sure").
- A freeze on a shot that hardly moves anyway, a dropout over an already quiet stretch, and a volume jump inside lively
  speech are still missed often.
- Sound labels name what a sound is like (a slap, a siren, a car), not whether music is good. Judging a track as cheap
  still rests on the reader.
- A careless reader can peek at `meta.json` or `stream.md` ahead of the page; two of the blind readers above admitted
  a glimpse and said they kept it out.
- Reel shows; it does not decide taste.

## Credits

The read-speech check uses the Bengali, English, Hindi, Korean, Latin American Spanish and Thai test sets from Google FLEURS (CC BY 4.0). [Recordings and reference transcripts](https://huggingface.co/datasets/google/fleurs).

The same-clip speech comparison uses the stimuli released by Magnotti et al. (2024) under CC0 and participant summaries from their deposited analysis. [Stimulus archive](https://openwetware.org/wiki/Beauchamp:Stimuli) and [authors’ analysis](https://zenodo.org/records/10689633).

A separate comparison used Meta’s Apache-2.0 XLSR phoneme recognizer. [Model card](https://huggingface.co/facebook/wav2vec2-xlsr-53-espeak-cv-ft) and [speech results](docs/perception-check.md#같은-발화를-들은-사람-28명과-gpt).

The natural video comparison uses the human sound-only and audiovisual captions in AVCaps by Sudarsanam, Martín Morató, Hakala and Virtanen. We used the authors’ non-commercial research release (CC BY-NC 4.0). [AVCaps dataset card](https://huggingface.co/datasets/TUT-ARG/AVCaps) and [listening results](docs/listening-check.md#사람들이-낯선-영상을-한-문장으로-설명한-경우)

The pitch check uses the 40 solo recordings and musician annotations released by Bittner, Pasalo, Bosch, Meseguer-Brocal and Rubinstein under CC BY 4.0. [vocadito recordings and annotations](https://zenodo.org/records/5578807)

The caption comparison in `examples/check_first_impressions.py` uses the sentence-transformers all-MiniLM-L6-v2 encoder under Apache-2.0. [Encoder and evaluation procedure](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)

Reel stands on open work: [UNISAL](https://github.com/rdroste/unisal) (Apache-2.0) for gaze,
[Demucs](https://github.com/facebookresearch/demucs) (MIT) and
[audio-separator](https://github.com/nomadkaraoke/python-audio-separator) (MIT) with the Mel-Band Roformer crowd
model by aufr33 and viperx for separation, [Silero VAD](https://github.com/snakers4/silero-vad) (MIT) for speech activity and [Whisper](https://github.com/openai/whisper) (MIT) through
[mlx-whisper](https://github.com/ml-explore/mlx-examples) and [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
for speech, [ocrmac](https://github.com/straussmaximilian/ocrmac) for Apple Vision text, the
[Synchformer](https://github.com/v-iashin/Synchformer) (MIT) for picture-sound sync, the [AST AudioSet model](https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593) (BSD-3) for sound labels, librosa, OpenCV and PyTorch.

Optional sound comparison uses LAION's [CLAP HTSAT model](https://huggingface.co/laion/clap-htsat-unfused) (Apache 2.0).

Optional point tracking uses Google DeepMind’s online BootsTAPIR and its causal checkpoint, released under Apache-2.0. [Code and weights](https://github.com/google-deepmind/tapnet)

Optional sound separation uses Google Research’s MixIT checkpoint trained on YFCC100M. The authors release the checkpoint under Apache-2.0. [Model and license](https://github.com/google-research/sound-separation/tree/master/models/neurips2020_mixit#yfcc100m-model-checkpoint)

For the listening comparison we also tested Google’s Gemma 4 E4B-IT with the mlx-community 4-bit conversion. The original model card lists Apache-2.0. [Google model card](https://huggingface.co/google/gemma-4-E4B-it), [MLX conversion](https://huggingface.co/mlx-community/gemma-4-e4b-it-4bit)

Optional waveform descriptions use the Qwen team's Qwen3-Omni and the mlx-community 8-bit conversion. The model card lists Apache-2.0. [Original model](https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct), [MLX weights](https://huggingface.co/mlx-community/Qwen3-Omni-30B-A3B-Instruct-8bit)
Check each model's own licence before commercial use.

The demo images use the track "Crypto" by Kevin MacLeod (incompetech.com), licensed under Creative Commons
Attribution 4.0, and narration made with macOS `say`.

The speech check uses [ESC-10](https://github.com/karolpiczak/ESC-50) (CC BY 3.0) and the [Free Spoken Digit Dataset](https://github.com/Jakobovski/free-spoken-digit-dataset) (CC BY-SA 4.0).

The overlap check used recordings from all 50 categories of Karol J. Piczak’s ESC-50 dataset (CC BY-NC 3.0). [Dataset licence and individual recording credits](https://github.com/karolpiczak/ESC-50/blob/master/LICENSE)

The picture and sound checks use recordings, questions and visible sound annotations from Google DeepMind’s [Perception Test](https://github.com/google-deepmind/perception_test) (CC BY 4.0).
The full-frame and source-inspection comparison is recorded in [the perception check (Korean)](docs/perception-check.md).

First-listen descriptions use the MIT-licensed CoNeTTE model by Étienne Labbé, Thomas Pellegrini and Julien Pinquier. [Model and limitations](https://huggingface.co/Labbeti/conette) and [implementation](https://github.com/Labbeti/conette-audio-captioning)

The field listening check uses Clotho-AQA by Lipping, Sudarsanam, Drossos and Virtanen, and the captions from Clotho v2.1. The questions and answers use the MIT licence. Each Freesound recording has its own licence, and the captions use the Tampere University licence. [Clotho-AQA](https://zenodo.org/records/6473207) and [Clotho v2.1](https://zenodo.org/records/4783391)

The spoken grounding check uses the voice recordings, timed descriptions and pointer traces in Localized Narratives by Pont-Tuset, Uijlings, Changpinyo, Soricut and Ferrari (CC BY 4.0) with COCO validation photographs. [Localized Narratives](https://google.github.io/localized-narratives/) and [COCO terms](https://cocodataset.org/#termsofuse)

A local waveform experiment used Gemma 4 E2B IT. The base model card lists Apache 2.0 and the tested MLX conversion lists the Gemma licence. [Google model card](https://huggingface.co/google/gemma-4-E2B-it) and [MLX conversion](https://huggingface.co/mlx-community/gemma-4-e2b-it-4bit).

The sign-reading check uses human word transcriptions from COCO-Text V2 and COCO photographs. The source images retain their individual licences. [COCO-Text V2](https://bgshih.github.io/cocotext/) and [COCO image terms](https://cocodataset.org/dataset/termsofuse.htm)

The free-viewing check uses fixation data from the DIEM Project (Mital, Smith, Hill and Henderson), available for non-commercial research under CC BY-NC-SA 3.0. [DIEM data and licence](https://jhenderson.org/vclab/DIEM.html)

The headphone direction check uses the Two!Ears localization experiment by Wierstorf, Spors and Raake (CC BY 4.0). We rendered a fixed forward-facing head from their impulse responses and compared results with their listeners’ pointing responses. The screen localization comparison used the authors’ SSL-SaN weights and MIT implementation. [Two!Ears data and licence](https://docs.twoears.eu/en/latest/database/listening-tests/localisation/) and [SSL-SaN](https://github.com/xavijuanola/SSL_SaN)

The automatic localization experiment used the SLAVC authors’ audiovisual encoders trained on VGG-Sound 144k, with their Apache-2.0 implementation. [SLAVC paper, code and checkpoint](https://github.com/stoneMo/SLAVC)

The listening experiments also use MACS annotations from the Machine Listening Group at Tampere University and TAU Urban Acoustic Scenes 2019 recordings from the Audio Research Group at Tampere University of Technology, under their non-commercial experimental licences. [Listening results (Korean)](docs/listening-check.md) · [MACS](https://zenodo.org/records/5114771) · [TAU recordings](https://zenodo.org/records/2589280)

## License

MIT
