# Writing the shot list

```json
{
  "duration_s": 4,
  "subject": {"name": "FOX", "description": "what the subject image shows: species or person, colours, face, clothing, in plain concrete words"},
  "source_subject": "young woman with long dark hair in a white T-shirt",
  "setting": "what stays: room, light, props, where each thing sits in frame",
  "keep": ["Anything else that must survive, such as burned-in captions with their words and timings",
           "The framing cuts FOX off at the chest, so the legs and tail stay out of frame."],
  "shots": [
    {"start": 0.0, "end": 4.0,
     "framing": "One continuous shot: selfie framing, FOX centred, head about a third of the frame height.",
     "camera": "locked off and still from start to end.",
     "action": "0.00-0.55 s: one arm rises from the lower left with the hand flat across the eyes. 0.55-0.75 s: the second hand rises large from the bottom edge, close to the lens. ..."}
  ]
}
```

Shots must be contiguous, start at 0 and end at `duration_s`. Boundaries that disagree with the measured cuts come back as warnings: check them on the frames, because the detector can miss cuts too.

## What the evidence says

Two clips, one job per setup, so these are leads and not laws.

- A shot-by-shot prompt beat a one-line prompt on the same model and inputs: tracking 0.855 against 0.677. Nothing else changed.
- Write the action as timed beats from what you see, and describe only one action per beat. Timings are hints, not guarantees: outputs have run early and late.
- Models ignore some explicit instructions. On the second clip the head came out larger than asked and a caption gap was ignored. Check, don't assume.
- Describe the subject fresh from its image rather than from memory of an earlier run.
- A prompt for a fixed-length job must not read as an edit: the model can classify it as one and fail the job. `compose_prompt` handles this, so use `--task edit` only on a route that has an edit task.

## Inputs

- Non-whole-second clips get truncated or extended with invented shots. `prepare` normalises them.
- Captions burned into a source come back garbled or reordered. Prefer sources without them.
- Strobes and one-frame cuts get merged or reordered.
- Two performers can swap roles or one can become a faceless mannequin: check every take.
- A bare photo drags its own background in. Dark objects lose colour.
- Some models refuse clips showing realistic people (one refused with `InputVideoSensitiveContentDetected.PrivacyInformation`). Don't reword or switch model to get around a refusal: report it to the user, who decides.
- Frames returned differ from frames asked: 89 and 97 came back from 96-frame jobs. `run` trims or retimes within 10%, and refuses beyond that.
