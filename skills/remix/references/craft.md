# Craft lessons

What makes this kind of output good or bad. Figures are from Higgsfield's recorded runs unless marked "ours"; treat them as warnings about failure modes, not as measurements of the routes we use.

## Identity

- Lock identity on one accepted keyframe, then feed it as the first image to every later still. Without that, each still drifts.
- Use one identity source per sequence. Combining a filmed close-up with a generated face gave what looked like a different person from shot to shot. A character sheet alone tended to widen the face.
- For a photoreal look, the user's real photo is the identity input, not a generated hero still. For a drawn look, the accepted keyframe is.
- Spell out clothing from the user's image in every prompt, or it drifts (boots for sneakers). Parts the photo hides are unknown: crop them out rather than inventing them. The reference performer's outfit never transfers to the hero.
- Name accessories in the photo that must not carry over: an earphone cable got drawn into a still.
- Check back views, distant shots and occluded shots too. A hidden face does not exempt a shot from being the right character.

## Faces are off limits to code

Code may cut, retime, move the whole plate, grade the whole shot, and add separate text or graphics. It must not paste, warp, repaint, retouch or swap a face, hair or skin, and must not hide a failed likeness behind graphics. A wrong face goes back to the model or gets reported. Harsh whole-plate looks can crush a face to black: soften the whole shot's treatment, not a face region. A two-frame glitch tearing a face mid-transition is an allowed whole-plate effect, but look at whether it lands on a moment that matters.

## Stills and location plates

- Generate each location as its own environment-only plate, reuse it across shots, and pass it to the clip job as its own image. A location that exists only in prompt text won't hold.
- **Plates grow people.** One photoreal model drew a person in 6 of 8 plates despite "no people", even from people-free source crops. Reject and retry within the attempt limit. Look at every plate: we have no automatic person detector here.
- That same model also ignored a cartoon style. For a drawn look, use a model that takes the style frame as an image and pass the location evidence second.
- State time of day and palette as hard requirements. A style image's palette can override the prompt: dusk came back as daylight.
- Style reference first, identity second, or the still drifts toward a photo. Take the style frame from a shot that shows how the reference draws a face, not the opening title card.
- Send the reference frame itself when composition matters. Describing a frame in words did not work.
- Image models ignore "no text" often enough that lettering on walls is a standing check. All authored text is overlay.
- Still checklist, every still: medium and stylisation; identity; clothing; style; composition; hands and finger count; props; no stray lettering; no second face in a mirror; for plates, no people.

## Clips

- One main action per clip. Multi-action prompts merge or reorder.
- A useful prompt names: what just happened, the start and end state, what each image owns (identity, location, composition), one action, one camera move with size and limit, lighting. Concrete observations beat adjectives.
- **Timecodes in a prompt are only ordering hints.** Gestures arrived 1.5 to 2.7 times late in some outputs and on time in others. Ask for a clip about twice as long as the slot, put critical beats early, and pick the in-point from the frames.
- **Frames returned differ from frames asked.** 89 came back from a job sent 96 (ours: 3.71 s from a 4 s job, 97 frames from others). The validator reads real counts; author from them.
- **Generated plates are calmer than references.** Past remixes kept only around a third of the reference's camera motion, and they felt flat. Put the energy in the edit.
- Plates can also come back less saturated, around 40% in one case. Compare against the reference frame and grade.
- Turn generated audio off, and keep music and effects out of the prompt.
- Suggestive wording with an audio reference was refused on every attempt, and copyrighted costumes on most. **Don't reword or switch model to get around a refusal.** Change the plan or report the gap.

## Motion transfer inputs (studio/genjutsu.py)

- Keep prompts to one or two short sentences, under about 40 words; the references carry the rest.
- Clips under about 4 s were rejected. Non-whole-second clips were truncated or extended with invented shots. Convert to 24 fps before padding, or the bill rises and motion slows.
- Captions burned into a driving clip come back as garbled copies. Drive per cut, away from burned text. Strobes and one-frame cuts get merged or reordered.
- Two performers can swap roles or one can become a faceless mannequin: check every take. A bare photo drags its own background in. Dark objects lose colour.

## Money

- Fix the spend cap up front, large enough for planned lines plus the retry reserve. Quote from the live catalog.
- Canary per model and identity family before the batch.
- A timeout does not prove no job exists: reconcile before resubmitting. A failed job does not prove a refund: compare balances.
- At most 3 attempts per still and 1 retry per clip. A retried shot should ask for at least 4 s, or twice its slot if longer, or it misses the same beat again.
- Never re-run a batch for one bad shot. A shot failure is one shot; re-run a whole job only when one cause ruined at least half its shots.

## Editing

- Keep the reference's rates of cuts, flashes, shakes and punches, placed on new action and beats. Each effect should serve its moment; random decoration reads cheap.
- Decide placement by actual frames. Don't place the same source frames twice unless the reference repeats there.
- Slow motion between 0.3x and 1x: blend neighbouring frames, since duplicates judder. Below 0.3x, or on fast limbs and faces: nearest frame, since blending ghosts them. Better still (ours): keep fast spans at 1x and put the slow-down in calm ones, as contiguous shots with no visible join. Measure frame-to-frame motion to find them. Blending a thrown paw at 0.8x doubled its claws, and no check saw it; only a native-size crop did.
- Match grades by comparing against frames, not by recipe. Hand-written chains are starting points. Don't add grain or halation the reference doesn't have.
- Don't crop blindly when aspect ratios differ: set `focus` after looking at what the crop loses.
- Text is drawn last, over the finished picture, so a reworded title re-renders in seconds. Choose a font by shape and check its glyphs. Never fall back to a default font silently.
- Kinetic type: look at a 5 fps sheet before choosing `anim`. A word that holds still and swaps on a hard frame is `none`, and adding a pop is not the reference's language. When the reference scales every word to the same span, set each event's `size` from the word's measured width.
- Ending: check the last frame and last audio together. A short effect file or an early export can cut the track off.

## Review

- One review round, then a regression comparison. A past second review existed only to undo a regression from the first.
- A reviewer's objections need filtering: act on wrong faces, broken lettering, shots that sit dead, cut-off words, stutter and lost frames. Keep deliberate devices that serve the story.
- Don't chase a score. A numeric pass does not prove quality, and measuring audio is not hearing it.
