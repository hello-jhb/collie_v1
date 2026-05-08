import streamlit as st
from pathlib import Path

from extractor import run_extraction
from flexible_extractor import scan_uploaded_files
from analysis_engine import generate_performance_analysis
from gpt_engine import (
    ask_gpt,
    generate_asset_management_narrative
)


# ---------------------------------------------------
# Page setup
# ---------------------------------------------------
st.set_page_config(
    page_title="Fantastic Beasts",
    layout="wide"
)

st.title("Fantastic Beasts and Where to Find Them")
st.caption(
    "Source files → snapshot metrics → core questions → asset management diagnosis"
)


# ---------------------------------------------------
# Session state
# ---------------------------------------------------
for key in [
    "known_result",
    "flexible_result",
    "analysis",
    "narrative"
]:
    if key not in st.session_state:
        st.session_state[key] = None


# ---------------------------------------------------
# Folders
# ---------------------------------------------------
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

REPOSITORY_DIR = Path("repository")
REPOSITORY_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------
# Upload files
# ---------------------------------------------------
st.header("1. Upload Source Files")

uploaded_files = st.file_uploader(
    "Upload acquisition models, business plans, financial statements, and supporting files",
    type=["xlsx", "xlsm", "csv", "pdf"],
    accept_multiple_files=True
)

if uploaded_files:
    for uploaded_file in uploaded_files:
        file_path = UPLOAD_DIR / uploaded_file.name

        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

    st.success(f"{len(uploaded_files)} file(s) uploaded.")


# ---------------------------------------------------
# Run analysis
# ---------------------------------------------------
st.header("2. Run Analysis")

if st.button("Run Extraction + Analysis"):

    with st.spinner("Running precise extraction..."):
        known_result = run_extraction("uploads")

    with st.spinner("Running metric catalog scan..."):
        flexible_result = scan_uploaded_files("uploads")

    if known_result["status"] == "missing_files":
        st.error("Missing required demo files:")
        st.write(known_result["missing"])

    else:
        with st.spinner("Building analysis context..."):
            analysis = generate_performance_analysis(
                known_result,
                flexible_result
            )

        with st.spinner("Generating asset management narrative..."):
            narrative = generate_asset_management_narrative(
                analysis
            )

        st.session_state.known_result = known_result
        st.session_state.flexible_result = flexible_result
        st.session_state.analysis = analysis
        st.session_state.narrative = narrative

        st.success("Analysis complete.")


# ---------------------------------------------------
# Metric coverage
# ---------------------------------------------------
if st.session_state.flexible_result:

    flexible_result = st.session_state.flexible_result

    st.header("3. Snapshot Metric Coverage")

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "Total Catalog Metrics",
        flexible_result.get("total_metrics", 0)
    )

    col2.metric(
        "Metrics Found",
        flexible_result.get("extracted_count", 0)
    )

    col3.metric(
        "Metrics Missing",
        flexible_result.get("missing_count", 0)
    )

    total_metrics = flexible_result.get("total_metrics", 0)
    extracted_metrics = flexible_result.get("extracted_count", 0)

    if total_metrics > 0:
        progress_value = extracted_metrics / total_metrics
    else:
        progress_value = 0
        st.warning("Metric catalog did not load correctly.")

    st.progress(progress_value)

    with st.expander("View Extracted Metrics"):
        st.dataframe(flexible_result.get("extracted_metrics", []))

    with st.expander("View Missing Metrics"):
        st.dataframe(flexible_result.get("missing_metrics", []))


# ---------------------------------------------------
# Narrative
# ---------------------------------------------------
if st.session_state.narrative:

    st.header("4. Asset Management Assessment")

    st.markdown(st.session_state.narrative)


# ---------------------------------------------------
# Raw outputs
# ---------------------------------------------------
if st.session_state.analysis:

    with st.expander("View Structured Analysis Context"):
        st.json(st.session_state.analysis)

    with st.expander("View Precise Extraction Output"):
        st.json(st.session_state.known_result)

    with st.expander("View Flexible Metric Scan Output"):
        st.json(st.session_state.flexible_result)


# ---------------------------------------------------
# GPT Q&A
# ---------------------------------------------------
if (
    st.session_state.analysis
    and st.session_state.known_result
    and st.session_state.flexible_result
):

    st.header("5. Ask the Asset")

    question = st.chat_input(
        "Ask a question about this property..."
    )

    if question:

        st.markdown(f"**Question:** {question}")

        with st.spinner("Thinking..."):

            answer = ask_gpt(
                question,
                st.session_state.known_result,
                st.session_state.flexible_result,
                st.session_state.analysis
            )

        st.markdown("### Answer")
        st.write(answer)
