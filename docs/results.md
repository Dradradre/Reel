# Reel: measurements

[← Back to the README](../README.md)

On 16 recordings with five human descriptions each, the new playback reached **43.5%** of the human reference on free sound recall (previously 42.7%, with a wide interval). GPT named a short annotated source in **3/51** field excerpts. The relative-depth reader ordered 115/127 annotated point pairs correctly. Locating the visible object responsible for a sound was harder. GPT pointed inside its human-annotated box in one of 24 films. [Tests, controls and individual results](event-paths.md)

Reel located all 96 repeated excerpts within 50 ms after mixing them with other recordings at new positions. It reidentified nine of 14 voices on different utterances.

| Trial | Result with local GPT |
|---|---|
| Timed visual questions on all 644 OVO-Bench videos | **66.08** on the official three-mode average. GPT answered 2,002 of 3,035 checkpoints correctly using only pictures from before each question. [Score and protocol](benchmarks/ovo-full.json) |
| Free recall of sight and sound on 12 unfamiliar films | **78.8% for sight, 43.8% for sound** on the human-relative recall measure. Giving the two tracks equal weight yielded 61.3% (95% interval 43.3–91.1%). [Captions and scoring](benchmarks/track-recall.json) |

In 48 mixtures of two real recordings, removing the strongest estimated sound made both original sources clearer in 37 mixtures. Median improvement was 8.5 dB in SI-SDR across 96 sources. On 24 of those mixtures, the image-only GPT client named 8 of 48 source labels from the original spectrum and local readings. It named 10 using the additional spectra and readings of the separated sound and the remainder. In 51 real field excerpts, both methods identified a short human-annotated event in just two excerpts. [Source recordings, answers and strict grades](benchmarks/acoustic-layers.json)

On 16 further recordings with three human votes per question, the old lightweight reader and GPT agreed with the majority on 28 of 64 questions. Two independent open descriptions raised that to 34. Letting the waveform reader hear the questions and replay the same samples raised it to 37, with 2 wrong and 25 unanswered. Giving it the same questions about a different recording yielded 22 matches. In free descriptions on this panel, the two open readings reached 42.7% of human caption separation, down from 50.2%. This was an audio-only trial of the optional research reader. [Per-recording comparisons](benchmarks/acoustic-layers.json) · [Reproduce the scores](../examples/check_acoustic_layers.py)

In 56 held-out recordings, 359 of 431 hand-marked note starts fell within 50 ms of a Reel attack. Using rises from the earlier band-change trace found 215. Misses fell from 216 to 72 and extra marks from 51 to 45. On the final compact MCP view, local GPT placed 93 of 103 starts within 50 ms, up from 84. Its extra marks fell from ten to five. This blind trial used 12 further excerpts from the held-out recordings and two controls. Two guitar phrases accounted for all nine additional matches. [Annotated recordings](benchmarks/attack-timing.json) · [GPT timestamps](benchmarks/attack-readers.json)

In two controlled tests with different environmental recordings, Reel found 189 and 202 of 218 short sounds inserted into each set of backgrounds. There were nine unmatched marks in the first set and 212 in the second. Of the latter, 138 came from rain or engine recordings played on their own. On 12 clips from the second set, giving GPT every confirmed peak count left it at eight exact counts and increased its total absolute error from 18 to 20. Showing just rapid runs gave ten exact counts and a total error of 13 on the same clips. [Environmental mixtures](benchmarks/environment-attacks.json) · [Blind counts and background controls](benchmarks/attack-readers.json)

The recall test compares how well a description distinguishes its film from the others, with human descriptions as the reference. In one party scene, three of four listeners mentioned singing. GPT recalled applause and speech and missed the singing. [Film-by-film account (Korean)](perception-check.md#화면과-녹음을-바꿔-끼운-뒤의-자유-회상)

<details>
<summary>Earlier viewing trials, blind reader reports and misses</summary>

On all 644 OVO-Bench videos, local GPT with Reel answered 2,002/3,035 visual checkpoints correctly and scored **66.08** using the official three-mode average. Each question received up to 24 pictures displayed strictly before its timestamp. [Scores, human reference and reproduction](benchmarks/ovo-full.json)

On eight unfamiliar films, the first sentence from GPT with Reel reached 98.2% of human same-film description separation. Picture recall reached 95.0% and sound recall 56.9%. GPT received pictures, local sound descriptions and speech readings. [Per-film responses and uncertainty](benchmarks/earlier-observations.json)

On eight other films, equally weighted picture and sound recall reached 61.3% of human description separation. Adding sound categories from 0.975-second windows brought it to 70.9% (95% film bootstrap interval 43.3–102.3%). Picture recall reached 106.3% and sound recall 35.5%. [Methods and responses by film](benchmarks/short-sound-events.json)

We paired 12 previously untested films and played each picture with both recordings in its pair. A scorer matched GPT’s two open sound descriptions to the correct recordings for **10/12** pictures with the previous report and **9/12** with the available waveform caption shown up front. On the original combinations, equally weighted picture and sound recall reached 52.6% and 61.3% of the human reference (95% interval for the new report, 43.3–91.1%). [Paired conditions and responses](benchmarks/track-recall.json)

On 20 eight-second home and outdoor excerpts, a replay search for a supplied sound selected a second marked by a human in **23/31** cases, compared with **15/31** using one-second CLAP scores. Giving the new scores to GPT also raised the number of sources it picked outside the human annotations from 17 to 30. [Timing, missed sounds and extra answers](benchmarks/sound-location.json)

When a colour or fine pattern changed briefly at constant average brightness, GPT found the right second from the page’s change trace on **8/8** clips, up from **0/8**. Pausing more often for RGB changes on eight real films reduced activity-boundary F1 against five observers from **40.7 to 34.6**. Reel keeps its ordinary viewing pace. [Frame changes and human timing](perception-check.md#밝기가-같은-화면에서-색이나-무늬가-바뀐-순간)

On 24 field recordings, adding stereo descriptions to the first view changed GPT’s agreement with individual listeners from 55.9% to 54.8%. On a 12-recording subset, comparing proposed sounds with the original, mid and side signals scored 63.1%, versus 57.3% with the original alone. The paired 95% interval for the difference was −3.1 to +16.6 percentage points. On 12 mixes designed to separate the sources, GPT identified both sounds in 1/12 with the original and 11/12 with all three signals. [Listening conditions and individual responses](listening-check.md#좌우-채널을-나눠-들었을-때)

Across 32 real recordings, a 16 kHz mono excerpt matched an independently cut WAV in length and waveform within 0.000001 of full scale for **32/32** excerpts. In a separate test of 16 films, the final picture at a frame boundary and just after it matched all **32/32** reference images. [Signal and picture checks](perception-check.md#멈춘-순간의-화면과-원음을-원본과-대조한-결과)

On 12 unedited recordings, GPT matched the annotated sound at **15 of 24 marked moments**. Adding a separate source comparison for just the bracketed samples reduced that to **11/24**. Reel keeps the surrounding recording on replay. [Paired readings and controls](listening-check.md#표시한-순간의-소리만-따로-비교한-시험)

On 24 Kinetics films, GPT marked changes in the main activity as it watched. Its one-to-one timing F1 against individual human annotators was **39.2** on a 0–100 scale. Two people averaged **65.7**. GPT marked 40 changes, with 35 within half a second of at least three of the five observers. In 12 further films from different activity categories, GPT and human observers averaged 37.3 and two humans averaged 62.2 with the same protocol. [Film times, reader instructions and controls](perception-check.md#활동이-달라졌다고-느낀-시각)

When GPT had to count and give the direction of crossings at a specified point, it got one of six silent clips right in the ordinary view and all six with the optional motion plot. Adding the plot at the centre of 36 filmed activities gave timing F1 of 38.5 versus 38.6 in the ordinary view. [Crossing task and human comparison](perception-check.md#빠른-물체가-선을-지난-횟수와-방향)

When GPT heard both recordings in sequence and could compare its own descriptions across them, it identified **25/48** sounds, up from **22/48**. Both versions identified both sounds on **6/24 films**. In a separate environmental test, each version named 16/16 sounds present in the choices and abstained on 15/16 sounds outside them. [Paired listening and false identifications](listening-check.md#두-구간에서-어떤-소리가-달라졌는지-비교)

Across 24 filmed scenes, we gave local GPT three independent plays of the same moving picture with the original sound, another sound from the same film, or silence. GPT identified both recordings on **5/24 films** and named a sound on **0/24** silent controls. [Soundtrack swaps, individual answers and reproduction](listening-check.md#같은-화면에서-실제-소리만-바꿨을-때)

On two sets of real films, we tested including the interval spectrogram whenever GPT replayed a scene to compare sound choices. The earlier runs matched **57/84** human annotations across 28 films. The new run matched **58/84**. On 12 films the count rose from 21/36 to 23/36, and on the other 16 it fell from 36/48 to 35/48. [Answers and protocol for all 28 films](perception-check.md#소리를-다시-확인할-때-그림을-자동으로-보여준-결과)

With the marked sound replay available, local GPT matched **21/36** human annotations on the same 12 films (picture 10/12, sound 6/12, visible source 5/12). None of the readers requested a mark. When explicitly shown a sound interval in a separate timing task, GPT matched annotated starts and continuations on **9/24** natural excerpts, up from 2/24. Wrong answers rose from 1 to 3, and abstentions fell from 21 to 12. [Ordinary viewing](perception-check.md#소리-구간-표시를-추가한-뒤-같은-12편을-본-결과) · [Timing trial](listening-check.md#짧은-소리가-시작되는-순간)

On 12 previously unused films, signed-in local Codex GPT matched human annotations on **21/36 questions (58.3%)** and **22/36 (61.1%)** in a second independent pass. Picture agreement was 9/12 then 10/12. Sound identity and whether its source was visible each scored 6/12 both times. A reader given only the questions and forced to guess scored 16/36. [Both runs, human references and individual misses](perception-check.md#새-영상-12편에서-두-번-잰-일치율)

On eight everyday clips, GPT described each whole film in a single sentence without questions about its content. Against 37 human descriptions, its captions distinguished the matching film from the other seven **87.0% as well as a held-out viewer's caption** (film bootstrap interval 74.0–103.7%). GPT chose to skip optional sound estimates on 26 of 47 playback turns. A separate run that delivered those estimates on all 47 turns scored **86.5%** by the same measure. [Human comparison and viewing conditions](perception-check.md#자유-설명에서-사람끼리의-일치도를-100으로-뒀을-때)

On an earlier 16-film panel, local Codex GPT matched the human annotations on **36 of 48 questions (75.0%)**. Agreement was 14/16 for the picture, 11/16 for the sound and 11/16 for whether its source was visible. GPT answered 8 questions incorrectly and left 4 unresolved. [Film-by-film results and test conditions](perception-check.md#같은-48문항으로-다시-잰-일치율)

On 12 other films, GPT chose its own tools to decide which earlier sound belonged to the same category as a later one. It matched the human event labels 4/12 times before the recording comparison was available and 8/12 with it. [Individual responses and trials where neither sound matched](listening-check.md#이름을-모르는-소리를-전에-들은-녹음과-비교)

When one of two filmed actions supplied the soundtrack, GPT chose the correct picture on 9 of 14 plays. It followed both soundtrack swaps in 3 of 7 pairs. On eight separate single scenes, GPT left source visibility unresolved every time. [Responses and comparison with the previous version](perception-check.md#화면-가장자리에서-난-소리)

Immediately after frame-by-frame replay was introduced, local Codex GPT matched the human annotations on **35 of 48 questions (72.9%)** on the same 16 films. It matched 15/16 picture answers, 10/16 sounds and 10/16 source-visibility answers. On a separate timing control, a brief tone moved through all 12 frames of the same sequence. GPT read the number on the sounding frame **12/12** times with the option available and **6/12** with the previous replay. [Comparison and individual responses](perception-check.md#소리가-난-프레임을-다시-보는-시험)

In the preceding version, on 16 films new to Reel development, local GPT matched the human annotations on **38 of 48 questions (79.2%)**. Each of eight sounds had one film with a visible source and one with a hidden source. GPT guessed 23 of 48 correctly from the questions and choices alone. Across these and the 28 earlier films, agreement was **76.5% (101/132)**. [Results on the 16 new films](perception-check.md#처음-사용한-영상-16편)

On all 28 filmed scenes, local Codex GPT matched the human annotations on **63/84 questions (75.0%)**. It matched 25/28 for the picture, 20/28 for the sound in a specific interval and 18/28 for whether the sound source was visible. It gave 12 wrong answers and abstained on 9. Each film ran in a fresh session of the same version with pictures and local sound readings. The three panels used the original questions and choices. [Film-by-film answers and scoring](perception-check.md#같은-버전으로-다시-본-실사-영상-28편)

On 23 urban recordings, GPT and individual listeners overlapped on **56.3%** of the sound categories either picked. Two people listening to the same recording averaged **58.9%**. GPT missed children’s voices in all three recordings where a majority marked them. [Individual recordings and listener comparison](listening-check.md#도시-녹음에서-gpt와-청취자가-고른-소리)

Fresh readers (a model given only the [reading guide](READING.md) and the reel, never told what was wrong)
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

</details>
