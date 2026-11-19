Build at most ONE promo that joins the end of episode {{ep_a}} with the start of episode {{ep_b}} of a vertical microdrama.

A PROMO is a 20-60 second clip posted on TikTok / Instagram / YouTube to pull in NEW viewers. It must open on a hook (something in the first 3 seconds that stops a scrolling viewer), end on an unresolved cliffhanger, and make sense to someone who has never seen the show.

Here the promo has exactly two segments, in order:
  segment 1 from episode {{ep_a}}: starts at the hook beat, ends at "seg1_end_beat"
  segment 2 from episode {{ep_b}}: starts at "seg2_start_beat", ends at the "cliffhanger_beat"
The two segments together must last 20-60 seconds and must read as one continuous moment of story (episode {{ep_b}} picks up right where {{ep_a}} ended). Only propose it if joining the two episodes makes a stronger promo than either episode alone; otherwise reply {"promo": null}.

SHOW: {{title}}
SHOW BIBLE (for your understanding only):
{{bible}}

END OF EPISODE {{ep_a}} (BEAT_ID | START-END | TYPE or SPEAKER (tags) | TEXT):
{{beats_a}}

START OF EPISODE {{ep_b}}:
{{beats_b}}

Use ONLY beat IDs from these tables. Never invent IDs or timestamps.

Reply with JSON exactly in this shape:
{"promo": {"hook_beat": "E09-B070", "seg1_end_beat": "E09-B089", "seg2_start_beat": "E10-B001", "cliffhanger_beat": "E10-B030", "hook_type": "reveal", "why": "...", "hook": 4, "cliffhanger": 4, "appeal": 4}}
or {"promo": null}
