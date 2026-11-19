# Promo Finder

Reads a microdrama show's timestamped screenplays and returns **ranked 20–60 s promos** for TikTok, Reels and Shorts. For each promo it gives every segment's start and end, the **hook** in the first 3 seconds, the **cliffhanger** it cuts on, and one line on why it works.

Built for the Kuku AI Generalist assignment (Problem 1).

## How it works

```
screenplays ─► parse (code) ─► propose (LLM or rules, beat IDs only) ─► validate + snap (code) ─► repair once (LLM)
            ─► stitch across episodes ─► cold-viewer judge (LLM, no show context) ─► rank ─► outputs
```

- **The AI never writes a timestamp.** It picks beat IDs such as `E07-B042`, and code turns those IDs into times, so no cut ever lands mid-line:
  - the promo's opening can be any beat
  - joins between segments and the ending are real shot boundaries

  Invented IDs are rejected and logged in `rejected.csv`.
- **Wrong lengths are fixed in code first.** The model is weak at timestamp arithmetic: about 25% of its proposals had the wrong length.
  - The validator first re-picks the ending to the most unresolved beat that makes the promo fit.
  - If the episode is ending and nothing later fits, the promo opens on the strongest earlier hook instead.
  - Anything still rejected goes back to the model once, with the checker's exact reason.
- **The code enforces the brief's rules:**
  - total length is 20–60 s
  - the hook starts within 3 s of the promo start
  - the promo ends right after an unresolved beat
  - at most 3 segments, from at most two consecutive episodes
- **A cold-viewer judge** sees only the promo's own lines, as a stranger scrolling past would. It reports what it thinks is happening, what it would tap to find out, and anything it can't follow.
- **Ranking** blends the scores for hook, cliffhanger, standalone and appeal. It gives a slight bonus to early episodes, so a viewer who taps through lands near episode 1. No two top-10 picks overlap, and no episode has more than 2 of them.

## Results on the sample show (50 episodes)

A separate AI tool read the scripts of 10 episodes and picked 26 reference promos, without seeing this tool's output. So these numbers measure agreement with an independent AI reader, not with a human editor; a human blind review (`eval/blind_review.py`) is the next check. A tool promo counts as a match when it overlaps one of the reference picks by at least half and is in the tool's top 4 for that episode.

| | AI (Gemini 3.1 Flash-Lite) | rules (no AI) | baseline (last 45 s) |
|---|---|---|---|
| held-out episodes (13 picks) | **54%** | 31% | 23% |
| all 10 episodes (26 picks) | **54%** | 31% | 31% |

The length-fixing rules were chosen on 5 of the 10 episodes, and the other 5 were held out for the first row. Before the fix, the AI scored 38% on the held-out episodes. Of its remaining 12 misses, 6 are moments it never proposed and 5 are the right moment cut differently. With 13 picks per split and an AI-made reference, these numbers are a signal, not proof.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -e ".[app,dev]"
copy .env.example .env            # then paste a free API key (Gemini or Groq)
```

Run on a show (a `.zip` or a folder of episode `.txt` files):

```bash
python -m promo_finder run --input path/to/show.zip --mode rules          # no AI, ~2 s
python -m promo_finder run --input path/to/show.zip --mode llm            # AI proposals + judge
python -m promo_finder run --input path/to/show.zip --mode llm --max-episodes 5   # quick demo
python -m promo_finder run --input path/to/show.zip --mode baseline       # naive "last 45 s" cut
```

Viewer app: `streamlit run app.py`

## Modes

| Mode | Needs a key | What it does |
|---|---|---|
| `llm` | yes | Show bible (1 call), one call per episode, one repair call per episode that had rejected proposals, stitching calls, and the judge in batches of 5. On the 50-episode pack this was about 130 calls, roughly 460k input tokens and 12–14 minutes on the free tier (10 requests a minute). Cached, so a re-run costs 0 calls. |
| `rules` | no | Keyword and emotion-tag heuristics. Instant. Weaker: it can misread tender moments as conflict. |
| `baseline` | no | The last ~45 s of each episode. Used as the bar the other modes must beat. |

If an LLM call fails, that episode quietly falls back to rules mode and is flagged `fallback`. If the quota runs out mid-run, the remaining episodes use rules mode and the run says so in its warnings.

## Swapping the model

Everything is behind one OpenAI-compatible client. Pick a preset in `config.yaml` (`gemini-flash-lite`, `groq-gpt-oss`, `openrouter`, `custom`) or override it with env vars:

```bash
LLM_PROVIDER=custom LLM_BASE_URL=https://your-endpoint/v1 LLM_MODEL=your-model LLM_API_KEY=...
JUDGE_MODEL=a-different-model      # optional: use another model for the cold-viewer judge
```

Prompts are versioned files in `promo_finder/prompts/`. The version is part of the cache key and is recorded in `run_meta.json`.

## Outputs

Each run writes `outputs/<show>/<mode>-<timestamp>/`:

- `top_picks.md`: the 5 promos to post first, with the reason for each
- `promos.csv` / `promos.json`: every valid promo with segments, hook, cliffhanger, scores and the judge's read
- `rejected.csv`: every proposal the validator refused, with the reason
- `run_meta.json`: model, prompt versions, call and token counts, rejection histogram, warnings, timing

## Checking quality

1. Make the labelling kit: `python eval/make_gold_sheet.py --input path/to/show.zip`. This writes `data/gold/reading_sheet.md` (the scripts of 10 episodes) and `data/gold/gold.csv` (blank answer rows). Both are gitignored.
2. Read the sheet and fill in `gold.csv` with the promos *you* would post, before looking at the tool's output.
3. Compare the modes:
   `python eval/evaluate.py --runs outputs/<show>/llm-* outputs/<show>/rules-* outputs/<show>/baseline-*`
3. Blind-rate the top 20 without seeing the scores:
   `python eval/blind_review.py export --run outputs/<show>/llm-...`, fill in the ratings, then `python eval/blind_review.py score`.

## Confidentiality

Show scripts never enter this repo: `data/`, `outputs/`, `*.zip` and `.cache/` are gitignored. The tests use an original 3-episode synthetic show in `tests/fixtures/`. The app processes uploads in memory and keeps nothing.

## Tests

```bash
pytest -q
```

## Known limits

- **Script only.** It can't see acting, faces, music or AI-video glitches, so an editor approves every cut.
- **Timestamps come from the screenplay.** If the final edit drifts from the script, cuts need a small nudge.
- **Hand-set ranking weights.** The scoring weights in `config.yaml` were chosen by hand, not learned. Real click-through data should replace them.
