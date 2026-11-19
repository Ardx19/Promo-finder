# AI usage log

Which AI tools were used, for what, and where they got it wrong. Updated during the build.

## Tools

| Tool | Used for |
|---|---|
| Claude (Claude Code, Opus) | Web research on how studios and apps pick promo moments; choosing the problem; the build plan; writing the code and tests with me reviewing each step |
| Gemini 3.1 Flash-Lite (Google AI Studio free tier, via the OpenAI-compatible API) | Inside the product: show bible, per-episode promo proposals, cross-episode stitching, cold-viewer judge |
| A separate AI tool ([tool name]) | Made the 26 reference picks used for evaluation, from the scripts alone. A human blind review is still to do. |

## Where the AI got it wrong (caught and fixed)

1. **Wrong default model.** In the confirmed plan, Claude proposed "Gemini Flash" as the free default. A check of current free-tier limits showed Flash allows about 20 requests a day, but one show needs about 110 calls. The default was switched to Flash-Lite (about 500 a day) before any code depended on it.
2. **Bad arithmetic in a test.** Claude wrote a test claiming that extending a too-short promo to the line "Know your place" would make it at least 20 s. The test failed: that line ends 16.7 s in. The code was right; the test's expectation was wrong.
3. **Keyword rules misread a tender scene.** In rules mode the top pick on the real show opened on a character *kneeling* to help someone. The keyword `kneel` is tagged as humiliation, but the scene is kind. This is why rules mode is only the fallback, and why an LLM reads the scene in context.
4. **"Pours" was treated as an attack.** The first humiliation keyword list matched "she pours tea", scoring it the same as a slap. The pattern was narrowed to pouring something *over a person*, and physical violence was weighted higher.
5. **Outdated model ID.** Claude wrote `gemini-2.5-flash-lite` into the config from memory. The first live call returned 404 because that model has been retired. The fix came from listing the models the key can actually use and testing each with a tiny call; the config now uses `gemini-3.1-flash-lite`. Lesson: verify model IDs against the provider, not from memory.
6. **The in-product model can't do timestamp arithmetic.** On the full 50-episode run, Gemini Flash-Lite proposed 51 promos (about 25%) with the wrong length. 28 were under 20 s and 23 over 60 s, some by less than a second, even though the prompt states the 20–60 s rule and gives every timestamp. The validator rejected all of them, so no bad promo was output, but 3 episodes were left with no promo at all. The fix was a repair round: each episode with rejections gets one more call that includes the checker's exact reason ("15.6 s is shorter than 20 s"). The model's fixes go through the same validator.
7. **My own validator rule was too strict, and only the comparison against a reference set exposed it.** I required a promo's *first* frame to be a shot boundary, but nothing plays before a promo's first frame, so that cut doesn't need to be clean. Combined with the model placing hooks late, this caused most of the misses against the reference set (26 promos picked by a separate AI tool from the scripts alone): the AI had found the right moment, but the cut was invalid. The fix was to let a promo open on any beat (never mid-line), re-pick the ending when the length is wrong, and open on an earlier strong hook when the episode runs out. These were chosen on half the reference episodes and checked on the other half. On the held-out half the AI went from 38% to 54%; rules mode reached 31% and the baseline 23%.
