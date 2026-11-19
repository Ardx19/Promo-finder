"""Promo Finder viewer: upload a show's screenplays, find ranked promos, review them as script excerpts.

    streamlit run app.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from pathlib import Path

import streamlit as st

from promo_finder.config import load_config
from promo_finder.export import segments_str
from promo_finder.llm import LLMError
from promo_finder.parse import ParseError, show_from_uploads
from promo_finder.pipeline import run
from promo_finder.rank import post_first_reason

st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide")
CFG = load_config()
SERVER_KEY_MAX_EPISODES = 10


def server_key(env_name: str) -> str | None:
    try:
        return st.secrets.get(env_name)  # type: ignore[attr-defined]
    except Exception:
        return None


def text_of(line: str) -> str:
    for sep in ("]: ", ": ", "] "):
        if sep in line:
            return line.split(sep, 1)[1].strip()
    return line.strip()


def render_excerpt(p: dict) -> str:
    hook, cliff = text_of(p["hook_text"]), text_of(p["cliffhanger_text"])
    out = []
    for line in p["excerpt"]:
        if line == "[CUT]":
            out.append("— ✂ cut to later —")
            continue
        body = line
        if hook and hook in line:
            body = f"**🪝 {line}**"
        elif cliff and cliff in line:
            body = f"**⏸ {line}**"
        elif "[shot]" in line:
            body = f"<span style='opacity:.6'>{line}</span>"
        out.append(body)
    return "  \n".join(out)


# ---------------------------------------------------------------- sidebar
st.sidebar.title("🎬 Promo Finder")
source = st.sidebar.radio("Input", ["Upload a show", "Open a previous run"], horizontal=False)

data = None
if source == "Upload a show":
    files = st.sidebar.file_uploader("Episode screenplays: one .zip or several .txt", type=["zip", "txt"],
                                     accept_multiple_files=True)
    mode = st.sidebar.selectbox("Mode", ["rules", "llm", "baseline"], index=0,
                                help="rules: no AI, instant. llm: AI proposals + cold-viewer judge. baseline: last 45 s of each episode.")
    provider, api_key, max_eps = None, None, None
    if mode == "llm":
        presets = list(CFG["providers"])
        provider = st.sidebar.selectbox("Provider", presets, index=presets.index(CFG["provider"]))
        api_key = st.sidebar.text_input("API key (kept only for this session)", type="password") or None
        if not api_key:
            env_name = CFG["providers"][provider].get("api_key_env")
            api_key = server_key(env_name) if env_name else None
            if api_key:
                st.sidebar.caption(f"Using the app's demo key: capped at {SERVER_KEY_MAX_EPISODES} episodes.")
                max_eps = SERVER_KEY_MAX_EPISODES
            elif not os.getenv(env_name or ""):
                st.sidebar.warning("Paste an API key, or use rules mode.")
    limit = st.sidebar.number_input("Only the first N episodes (0 = all)", min_value=0, value=0, step=1)
    if limit:
        max_eps = min(limit, max_eps) if max_eps else limit
    stitch = st.sidebar.checkbox("Allow promos across two episodes", value=True)
    go = st.sidebar.button("Find promos", type="primary", disabled=not files)

    if go and files:
        try:
            show = show_from_uploads([(f.name, f.getvalue()) for f in files])
        except ParseError as e:
            st.error(f"Could not read the screenplays: {e}")
            st.stop()
        bar = st.progress(0.0, text="Starting…")

        def progress(stage, done, total):
            bar.progress(min(1.0, done / total) if total else 0.0, text=f"{stage}: {done}/{total}")

        with tempfile.TemporaryDirectory() as tmp:
            try:
                res = run(show, mode, cfg=CFG, provider=provider, api_key=api_key, stitch=stitch,
                          max_episodes=max_eps or None, out_root=tmp, progress=progress)
            except LLMError as e:
                st.error(str(e))
                st.stop()
            files_out = {p.name: p.read_bytes() for p in Path(res.out_dir).iterdir()}
        bar.empty()
        st.session_state["files"] = files_out
        st.session_state["data"] = json.loads(files_out["promos.json"])
        st.session_state["meta"] = json.loads(files_out["run_meta.json"])
        st.session_state["feedback"] = {}
else:
    up = st.sidebar.file_uploader("promos.json from a previous run", type=["json"])
    if up:
        st.session_state["data"] = json.loads(up.getvalue())
        st.session_state["files"] = {"promos.json": up.getvalue()}
        st.session_state["meta"] = None
        st.session_state.setdefault("feedback", {})

st.sidebar.markdown("---")
st.sidebar.caption("Scripts are processed in memory and not stored. The tool only reads screenplays: "
                   "it cannot see acting, faces or AI-video glitches, so an editor should approve every cut.")

# ---------------------------------------------------------------- main
data = st.session_state.get("data")
if not data:
    st.title("Find the promos in a microdrama")
    st.markdown(
        "Upload a show's timestamped screenplays (one `.zip` or several episode `.txt` files) and the tool returns "
        "ranked **20-60 s promos**: every segment's timestamps, the **hook** in the first 3 seconds, the "
        "**cliffhanger** it cuts on, and why it works.\n\n"
        "- **AI only picks beat IDs.** Code turns them into timestamps, so every cut is a real shot boundary "
        "and never lands mid-line.\n"
        "- A separate **cold-viewer judge** (no show context) checks that each promo makes sense to a stranger.\n"
        "- **Rules mode** works with no API key.")
    st.stop()

meta = st.session_state.get("meta")
promos = data["promos"]
ranked = [p for p in promos if p.get("rank") is not None]
st.title(f"Promos: {data.get('show', '')}")
cols = st.columns(5)
cols[0].metric("Ranked promos", len(ranked))
if meta:
    cols[1].metric("Mode", meta["mode"])
    cols[2].metric("Rejected proposals", meta["counts"]["rejected"])
    cols[3].metric("LLM calls", (meta.get("llm") or {}).get("calls", 0))
    cols[4].metric("Time", f"{meta['wall_seconds']} s")
    for w in meta.get("warnings", []):
        st.warning(w)

tab_top, tab_all, tab_rej, tab_info = st.tabs(["Post first", "All promos", "Rejected", "Run info"])
fb = st.session_state.setdefault("feedback", {})

with tab_top:
    n_show = st.slider("How many", 5, min(40, max(5, len(ranked))), min(10, max(5, len(ranked)))) if len(ranked) > 5 else len(ranked)
    for p in ranked[:n_show]:
        with st.container(border=True):
            left, right = st.columns([3, 1])
            left.subheader(f"#{p['rank']} · {p['segments_str']}  ({p['duration']:.0f}s)")
            right.metric("Score", f"{p['score']:.0f}")
            st.markdown(f"**🪝 Hook ({p['hook_type'].replace('_', ' ')}):** {p['hook_text']}  \n"
                        f"**⏸ Cliffhanger:** {p['cliffhanger_text']}  \n"
                        f"**Why it works:** {p['why'] or '-'}")
            if p.get("judge"):
                j = p["judge"]
                st.caption(f"Cold viewer: {j.get('summary', '')} · wants to know: {j.get('open_question', '')}"
                           + (f" · confused by: {', '.join(j['confusing_refs'])}" if j.get("confusing_refs") else ""))
            s = p["scores"]
            st.caption(f"hook {s['hook']:.1f} · cliffhanger {s['cliffhanger']:.1f} · standalone {s['standalone']:.1f} · "
                       f"appeal {s['appeal']:.1f} · source {p['source']}" + (" · rules fallback" if p.get("fallback") else ""))
            with st.expander("Script of this promo"):
                st.markdown(render_excerpt(p), unsafe_allow_html=True)
            choice = st.radio("Editor decision", ["—", "Approve", "Reject"], horizontal=True, key=f"fb-{p['id']}",
                              index=["—", "Approve", "Reject"].index(fb.get(p["id"], "—")))
            if choice != "—":
                fb[p["id"]] = choice
    if fb:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "segments", "decision"])
        by_id = {p["id"]: p for p in promos}
        for pid, d in fb.items():
            w.writerow([pid, by_id[pid]["segments_str"] if pid in by_id else "", d])
        st.download_button("Download editor feedback (feedback.csv)", buf.getvalue(), "feedback.csv", "text/csv")

with tab_all:
    import pandas as pd
    df = pd.DataFrame([{
        "rank": p.get("rank"), "score": p["score"], "segments": p["segments_str"], "sec": p["duration"],
        "hook_type": p["hook_type"], "hook": p["hook_text"], "cliffhanger": p["cliffhanger_text"],
        "standalone": p["scores"]["standalone"], "source": p["source"], "excluded": p.get("excluded_reason") or "",
    } for p in promos])
    st.dataframe(df, width="stretch", hide_index=True)

with tab_rej:
    if "rejected.csv" in st.session_state.get("files", {}):
        import pandas as pd
        rej = pd.read_csv(io.BytesIO(st.session_state["files"]["rejected.csv"]))
        st.caption("Proposals the validator refused, with the reason. Nothing is silently dropped.")
        st.dataframe(rej, width="stretch", hide_index=True)
    else:
        st.info("Not available for a loaded run.")

with tab_info:
    if meta:
        st.json(meta, expanded=False)
    files = st.session_state.get("files", {})
    for name, blob in files.items():
        st.download_button(f"Download {name}", blob, name, key=f"dl-{name}")
