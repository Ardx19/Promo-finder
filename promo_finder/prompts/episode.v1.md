Find the best social-media promos in one episode of a vertical microdrama.

A PROMO is a 20-60 second clip from the show, posted on TikTok / Instagram / YouTube to pull in NEW viewers who have never seen the show. A good promo:
- OPENS ON A HOOK: something in the first 3 seconds that makes a scrolling viewer stop (a slap, a humiliation, a betrayal, a shocking line, a reveal, a threat, a power reversal).
- ENDS ON A CLIFFHANGER: it cuts at an unresolved moment, so the viewer must know what happens next. Never end after the tension is resolved.
- MAKES SENSE ON ITS OWN to someone who has never seen the show (clear conflict, no unexplained backstory needed).
Promos that work best for these shows rely on instantly readable conflict: cruelty, humiliation, betrayal, hidden identity, revenge, forbidden romance.

SHOW: {{title}} (episode {{episode}} of {{total}})
SHOW BIBLE (for your understanding only):
{{bible}}

EPISODE LOGLINE: {{logline}}

EPISODE SCRIPT as a beat table. Columns: BEAT_ID | START-END (MM:SS.ss) | TYPE or SPEAKER (emotion tags) | TEXT
CAMERA rows are shots; promo cuts can only happen between shots.
{{beats}}

Your tasks:
A) Tag the notable beats (at most 25): for each, a "hook" score 0-5 (how hard it stops a scrolling stranger if it is in the first 3 seconds), a "hook_type" from [violence, humiliation, betrayal, reveal, threat, romance, power_reversal, shocking_line, other], and a "cliffhanger" score 0-5 (how strongly a viewer would need to know what happens next if the clip cut right after this beat).
B) Propose up to {{max_promos}} promos from THIS episode (fewer if the episode has no good ones). Each promo is one continuous stretch:
   - "hook_beat": the beat that is the hook. "start_beat": where the clip starts; it must be the hook beat itself or a beat that starts at most 3 seconds before it.
   - "cliffhanger_beat": the unresolved beat the clip ends on. "end_beat": the same as cliffhanger_beat unless the clip needs one more reaction beat.
   - The clip from start to cliffhanger must last between 20 and 60 seconds (check the timestamps).
   - Different promos should use clearly different moments.
   - "why": one sentence (max 25 words) on why it works for a cold audience.
   - Scores 1-5 for "hook", "cliffhanger", "appeal" (how strongly the trope/conflict appeals to a microdrama audience). Be strict: 5 is rare.

Use ONLY beat IDs that appear in the table above. Never invent IDs or timestamps.

Reply with JSON exactly in this shape:
{"beats": [{"id": "E01-B012", "hook": 4, "hook_type": "humiliation", "cliffhanger": 1}],
 "promos": [{"hook_beat": "E01-B012", "start_beat": "E01-B011", "end_beat": "E01-B058", "cliffhanger_beat": "E01-B058", "hook_type": "humiliation", "why": "...", "hook": 4, "cliffhanger": 5, "appeal": 4}]}
