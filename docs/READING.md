# Watching a film through its reel

You cannot play video or hear sound. `reel watch` turns a whole film into a reel: pages that run strictly forward in
time, 30 seconds each, plus `stream.md`, a line for every shot. Read them front to back and you go through the film in
order, the way a viewer does. Nothing on a page is a verdict. It is what a viewer would see and hear, laid out so you
can see it.

## How to watch

1. If there is a `pages/page_00.png`, look at it first: it shows what a normal frame of this kind of film looks
   like, the picture a fan of the genre carries in their head. Keep it in mind while watching.
   Do not skim ahead and do not read summaries first. Open `pages/page_01.png`, then the lines of `stream.md` for
   the same 30 seconds, then page 2, and so on to the end.
2. After each page write two or three lines in your viewing log before you open the next one: what happened, and
   anything that felt wrong to a viewer. Felt wrong means things like: I lost track of where we are in the story; the
   picture stopped; the sound stopped or jumped; I heard the same thing twice; the voice and the picture do not belong
   together; something came out of nowhere; I could not read it in time; this is boring or confusing.
3. Keep the earlier pages in mind. Much of what a viewer notices is a comparison with what came before.
4. When something feels off, rewind: `reel zoom REEL_DIR --t0 S --t1 S` draws the same lanes for a few
   seconds at many more pixels per second, with thumbnails 12 times a second. Look at it, then go on reading.
5. At the end, write what a viewer would say about the film, worst first, each with the time.

## The lanes, top to bottom (time runs left to right, the same on every lane)

| Lane | What it is | What a viewer notices there |
|---|---|---|
| picture | Every shot at its real length. The thin strip under the pictures is the frame shape: grey when the picture fills the frame, orange (with "picture fills W% wide x H% high") when there are black bars or the picture sits in a smaller box. A viewer sees that at once: letterboxed footage loses the top and bottom of the image, for example the game's own scoreboard and bottom bar. A strip along the top of the lane is picture quality: dark when the picture holds the fine detail of native HD footage, light blue "slightly soft" and blue "soft picture (low detail)" when it looks upscaled, blurred or low resolution; pink "much softer than around it" when a stretch of 3 s or more holds far less detail than the footage on either side, or softens in the middle of a shot. Some footage is soft by choice (a dreamy close-up, an old archive clip): enlarge it with zoom and ask whether a viewer would see a defect, such as signs and text that are crisp elsewhere turning to mush. In the lane itself, a long shot shows several thumbnails, a very short one is a thin sliver. Red lines in the lower lanes are cuts. | What is on screen, who, where. Slivers are flashes or very fast cuts. |
| subject | When the film is about one person (`--subject`): a mark wherever their name tag is seen inside the picture, green "eye on" when a viewer's eye is predicted to land on them, yellow "eye near", red "eye away from". Grey: seen, no gaze data. | Whether the film shows the person it is about, and whether a viewer is looking at them or at something else. |
| on screen text | Text big enough to be read (titles, captions, banners), at the moment it appears, with a blue bar while it stays. | The story: dates, places, scores, names. Whether they run in an order you can follow. Whether a line is gone before it could be read. |
| change | White bars: how much each frame differs from the one before (the tallest frame in each column is kept, so a single frame shows). Yellow line: brightness. Red ticks on top: the picture did not move at all for half a second or more. Labels at the bottom say how the picture went to black and came back: "fades to black ... fades back in" (blue, a transition the eye follows) or "hard cut to black" / "hard cut back" (red, a jolt). Yellow labels near the top mark a flash or blink of a few frames that jumps away from the picture and straight back. | Cuts, flashes, a picture going black (yellow line drops to the floor), a picture that froze. |
| seen again | Coloured where the picture is the same as something earlier in the film (in another shot), labelled with when it was first seen. | Reused footage. A viewer notices "this again". |
| sound | Spectrogram, low at the bottom. Green triangles: the sound changes character (a new scene of sound). | Music, voices, crowd, silence (black). Whether the sound changes where the picture cuts, or somewhere else. |
| sounds like | What each second sounds like to a listener, as sound-category labels (AudioSet), written when they change: "Explosion", "Whoosh", "Slap, smack", "Crowd", "Chant", "Electronic music", "Synthesizer"... Grey: only generic "Music" or "Speech". | The character of the sound, which the spectrogram cannot tell you: whether an impact sounds like thunder or like a slap, whether the music is an orchestra or a cheap synth, whether a crowd is there. Labels are a listener's first guess, not a verdict on quality. Orange triangles mark hits (sudden loud impacts); each hit is heard on its own and labelled "hit: ...". A hit that is meant to be thunder, an explosion or a big impact but is heard as "Slap", "Whack", "Clap", "Tick" or "Burst, pop" sounds thin to a listener, like a hand slap or a toy: a viewer hears that as a cheap effect. "Synthesizer", "Electronic tuner" or "Sine wave" on a bed that should be orchestral or cinematic reads as home-made music. |
| level | Loudness in 10 ms steps (the loudest step in each column is kept). | A cliff down to nothing is a sudden silence; a jump is a sudden loud entry. |
| (red marks in level) | Jolts the ear feels: "sound cuts dead" (sound falls to digital silence within 30 ms, where a natural ending would ring out), "sound snaps back", and "same sound, suddenly +N dB" (a held sound turned up or down in one step, its spectrum unchanged). | An edit that chopped the sound, a gap, a fader slammed up. Natural pauses and endings fade; these do not. |
| in sync? | Where the sound sits against the picture, moment by moment: a heat strip whose middle line (green) is "in sync", upper half "sound comes early", lower half "sound comes late", up to 2 s either way. Bright = sure. A bright band on the green line: lips, hits and steps land with their sound. A bright band below it: the sound trails the picture. Dim everywhere: nothing on screen says when its sound should come (music over scenery, a voice-over). A label "sound 0.6 s late" marks a stretch where the belief holds over several seconds: red "(sure)", orange "(likely)". Animation and music-only stretches give weaker belief; look at the picture and the sound before trusting a "likely". | Lips moving before the words, a ball struck before the thwack: viewers feel this at a fifth of a second and find it hard to watch. An inserted or dropped second of sound leaves everything after it out of sync. |
| crowd | The crowd, separated from music and commentary, in the voice range (300 Hz to 4 kHz). Bright texture is cheering; arcs that repeat at a steady beat are a chant, the same few syllables shouted together. | Whether a crowd is there, and whether it is chanting. A chant cannot be transcribed, so this lane is how you hear it. A green bar labelled "crowd chanting" marks where the crowd's voices pulse at a steady beat, with the word as it was heard and the name it sounds most like among the names on screen and the genre's usual names (a chant is filled in from names a listener knows, as people do), plus the vote over all chants in the film; quiet chants under music can be missed by the label but still show as arcs. |
| said | Words of the voices (commentary, interviews, sung lyrics), at the moment they are said. The subject's name is drawn in yellow when it is said. Grey words are ones the transcriber was unsure of; over music it sometimes invents words, and shouted commentary it often cannot catch at all, so an empty lane does not prove silence (check the level and sound lanes). | What is said, whether it matches the subtitles and the picture, whether a sentence is cut off. |
| heard again | Red, with "SAME SOUND AGAIN": the sound here is a copy, sample for sample, of what played a moment before (a small red mark shows where it first played), with the words that were said. Orange: voices that were already heard more than 3 s earlier, labelled with when. Red "SAME RECORDING AGAIN as 0:16" means the very same recording plays again (not the same person speaking again): a stretch pasted twice. "(picture too)" in seen again means the picture was pasted with it. | Red is what a viewer hears as a stutter or echo: the same second of sound twice. Orange is a line, a call or a crowd recording coming back. Music repeating itself is normal and is not marked. |

Comparing lanes is how you notice what belongs together. For example: subtitles in the text lane and the same words
in the said lane should sit at the same time; a cut in the picture usually comes with a green triangle in the sound
if the clip's own sound is used; a black picture with a sound that keeps going, or a sound that dies while the picture
goes on, is something a viewer feels.

## What this does not give you

Fine facial expression, small text inside game footage, and whether a piece of music is good. For one sound, use
`listen.py card`; for one moment at full detail, use `watch.py --at` moment cards.
