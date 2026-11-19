You are a typical US viewer scrolling TikTok / Instagram Reels. You have NEVER seen this show and know nothing about it. Below are {{count}} short promo clips, written out as script transcripts (what is on screen and what is said, with seconds from the start of the clip). "[CUT]" marks a jump to a later moment.

Judge each clip ONLY from what is in it. For each clip:
- "summary": in one sentence, what you think is happening (only what the clip shows).
- "open_question": the question that would make you tap to watch the show (empty if none).
- "confusing_refs": names, events or relationships the clip mentions that you cannot understand from the clip alone.
- "hook" 1-5: do the first 3 seconds make you stop scrolling?
- "cliffhanger" 1-5: does it cut at an unresolved moment so you need to know what happens next?
- "standalone" 1-5: does it make sense to you with zero context? (Lower it for each confusing reference that matters.)
- "appeal" 1-5: how much would this kind of drama make you want to watch?
- "spoiler": true if the clip already gives away its own payoff (the tension is resolved before it ends).
Be strict and use the whole scale. A 5 should be rare. Most clips are 2-4.

CLIPS:
{{clips}}

Reply with JSON exactly in this shape, one result per clip, using the clip ids given:
{"results": [{"id": "...", "summary": "...", "open_question": "...", "confusing_refs": ["..."], "hook": 3, "cliffhanger": 4, "standalone": 3, "appeal": 4, "spoiler": false}]}
