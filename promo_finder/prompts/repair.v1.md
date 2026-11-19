You proposed promos for episode {{episode}} of the vertical microdrama "{{title}}", but an automatic checker rejected some of them. Fix them.

The rules the checker enforces:
- Total length from the start of the start_beat to the end of the cliffhanger_beat: between 20 and 60 seconds. Compute it from the timestamps in the table (END of cliffhanger_beat minus START of start_beat).
- The hook beat must start at most 3 seconds after the clip starts (start_beat is the hook beat or a beat starting up to 3 s before it).
- The clip must end on the cliffhanger beat: an unresolved moment, not after the tension is resolved.
- Use only beat IDs from the table.

REJECTED PROPOSALS AND WHY:
{{failures}}

For each rejected proposal, either fix it (usually by choosing a different cliffhanger_beat so the length lands between 20 and 60 seconds, ideally 25-45 s, or a hook beat with a cut just before it) or drop it if no valid version is a good promo. Do not repeat a proposal unchanged.

EPISODE SCRIPT (BEAT_ID | START-END | TYPE or SPEAKER (tags) | TEXT):
{{beats}}

Reply with JSON exactly in this shape (only the "promos" list):
{"promos": [{"hook_beat": "E01-B012", "start_beat": "E01-B011", "end_beat": "E01-B058", "cliffhanger_beat": "E01-B058", "hook_type": "humiliation", "why": "...", "hook": 4, "cliffhanger": 5, "appeal": 4}]}
