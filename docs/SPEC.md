# remix-studio

Our own replacement for two Higgsfield products. Written from observed behaviour; contains no Higgsfield scripts.

## Products to match

Genjutsu: one source video + reference images + optional prompt -> a video where the subject is replaced or performs the
source motion, keeping scene, camera and timing. Modes: motion transfer, object swap.

Katana: reference edit + subject inputs -> a finished new edit in the reference's visual language (cut rhythm, flashes,
shakes, camera moves, typography, music), run by a coding agent following a skill. Generates stills and clips, assembles
from an edit decision list (EDL), checks the result, delivers.

## Layout

studio/analyze.py   probe, cut detection, energy map, contact sheets
studio/score.py     objective comparison of an output against its source
studio/providers.py treg routes and the shared job types
studio/services.py  any HTTP generation API, from services.json
studio/ledger.py    spend ledger with a finite cap
studio/gen.py       runs a plan of paid stills and clips under the ledger
studio/swap.py      subject-swap pipeline
studio/edl.py       EDL validation and ffmpeg render
studio/cli.py       the remix-studio command
skills/             the remix and subject-swap skills
runs/               outputs, git-ignored

## Acceptance criteria

"As good as" is decided by these, against real Higgsfield output on the same inputs:

G1 Tracking: studio/score.py tracking score >= the Genjutsu baseline on the same clip.
G2 Beats: every action beat in the source appears in order; timing error no worse than the baseline.
G3 Identity: reference subject recognisable and consistent in every sampled frame, by visual review.
G4 Duration: output duration within 0.1 s of source. (Genjutsu's replace job missed this: 3.71 s of 4.)
K1 Finished edit: renders with audio, target duration, no black or frozen frames, no repeated placement.
K2 Language: cut rate and flash/shake rates within 25% of the reference's.
K3 Quality: side-by-side visual review against a real Katana output finds it no worse overall.
C1 Cost: lower than Higgsfield on the same job, including measured agent tokens where they apply.

Report any criterion that fails. Numbers can pass while the video looks bad, so visual review is required too.
