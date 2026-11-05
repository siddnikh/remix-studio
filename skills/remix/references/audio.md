# Audio plan

Real recordings only. Code may trim, align, mix, fade and limit. It never makes the sound: no synthesised beats, kicks, whooshes or impacts. The renderer takes only files with an audio stream and a stated provenance.

## Choosing the track, in order

1. **The user's supplied track** or footage audio.
2. **The reference's own audio**, if it fits and the user has the rights or the work stays private. Say which.
3. **A CC0 recording** chosen for tempo and mood. Bundled excerpt below; more at Free Music Archive (filter by CC0) or by searching the artist HoliznaCC0. Read the licence on the track's own page; an artist's other tracks may differ.
4. **None**, with `none_reason`, if nothing fits. Report it: K1 in the spec requires audio.

Never fetch music from TikTok or similar for delivery without checking the rights.

## Placing it

Measure the track's beat grid before writing the EDL with `remix-studio analyze beats track.mp3`, then cut to it. On the bundled excerpt, strong hits fall about every second from 0.11 s, with a 120 BPM pulse: `"in": 0.11` puts a beat on frame 0 and every 12 frames at 24 fps. Those figures are measurements of the waveform; nobody has listened to this excerpt yet, so someone should before it ships.

Hits go on planned visual events only: a flash or whip, not every cut. Keep them under the music, starting around -5 to -10 dB. Swishes lead a whip: put the cue at the whip's midpoint. Source effects already baked into retained audio shouldn't be doubled.

Keep any locked source audio unchanged: don't limit or normalise it. The renderer's mix path re-encodes, so a claim of unchanged source audio needs a separate stream-copy mux and a packet hash check, which this renderer doesn't do yet.

## What is bundled

See `assets/LICENSES.md`. Nine short effects and one 45 s music excerpt, 2.3 MB in all with the font.

| File | Use | Length |
|---|---|---|
| `sfx/hit_owlish.wav` | Main impact, flash | 0.35 s |
| `sfx/slap_owlish.wav` | Sharp accent | 0.48 s |
| `sfx/djembe1_owlish.wav`, `djembe2_owlish.wav` | Soft low hit | 0.26, 0.36 s |
| `sfx/clamour5_owlish.wav` | Metallic clatter | 0.44 s |
| `sfx/swish2`, `5`, `8`, `13_artisticdude.wav` | Whip | 0.07 to 0.15 s |

## Gaps

There are no risers, booms, glitches, static or long whooshes. For more: OpenGameArt's CC0 filter (the OwlishMedia pack has about 170 files, 211 MB unpacked; skip its Synth folder), or Freesound's CC0 filter with an account. Kenney's Impact Sounds is CC0 too, but its site refused scripted downloads, so that one needs a person to click.

CC0 is the uploader's claim. Keep the source page and date in `provenance`. This is not legal advice.
