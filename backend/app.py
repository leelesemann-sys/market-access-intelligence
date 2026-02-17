"""HTA Intelligence — Streamlit Dashboard + RAG Chat.

Usage:
    streamlit run app.py
"""
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

import db

st.set_page_config(
    page_title="HTA Intelligence",
    page_icon="🔬",
    layout="wide",
)

st.title("HTA Intelligence")
st.caption("Cross-Country Health Technology Assessment Analysis — G-BA (DE) & NICE (UK)")

tab_dashboard, tab_compare, tab_chat = st.tabs(["Dashboard", "Drug Comparison", "RAG Chat"])

# ============================================================
# Tab 1: Dashboard
# ============================================================
with tab_dashboard:
    # Load data
    @st.cache_data(ttl=300)
    def load_stats():
        counts = {}
        for agency in ["gba", "nice"]:
            r = db.fetch_all(
                f"SELECT COUNT(*) FROM assessments WHERE agency_id = '{agency}'"
            )
            counts[agency] = r[0][0]
        r = db.fetch_all("SELECT COUNT(DISTINCT inn) FROM v_matched_drug_pairs")
        counts["matched"] = r[0][0]
        r = db.fetch_all(
            "SELECT concordance, COUNT(*) FROM v_matched_drug_pairs GROUP BY concordance"
        )
        counts["concordance"] = {row[0]: row[1] for row in r}
        return counts

    stats = load_stats()

    # KPI row
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("G-BA Assessments", stats["gba"])
    c2.metric("NICE Assessments", stats["nice"])
    c3.metric("Matched Drugs (INN)", stats["matched"])
    conc = stats["concordance"]
    conc_pct = conc.get("concordant", 0) / max(sum(conc.values()), 1) * 100
    c4.metric("Concordance Rate", f"{conc_pct:.0f}%")

    st.divider()

    # Outcome distribution by agency
    col1, col2 = st.columns(2)

    @st.cache_data(ttl=300)
    def load_outcome_dist():
        return db.fetch_df("""
            SELECT agency_id, overall_outcome, COUNT(*) AS cnt
            FROM assessments
            WHERE overall_outcome IS NOT NULL
            GROUP BY agency_id, overall_outcome
        """)

    df_outcomes = load_outcome_dist()

    with col1:
        st.subheader("Outcome Distribution")
        fig = px.bar(
            df_outcomes,
            x="agency_id", y="cnt", color="overall_outcome",
            barmode="group",
            color_discrete_map={
                "positive": "#2ecc71", "restricted": "#f39c12", "negative": "#e74c3c"
            },
            labels={"cnt": "Count", "agency_id": "Agency", "overall_outcome": "Outcome"},
        )
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)

    # Cross-country concordance matrix
    @st.cache_data(ttl=300)
    def load_cross_tab():
        return db.fetch_df("""
            SELECT gba_outcome, nice_outcome, COUNT(*) AS cnt
            FROM v_matched_drug_pairs
            GROUP BY gba_outcome, nice_outcome
        """)

    df_cross = load_cross_tab()

    with col2:
        st.subheader("Concordance Matrix (G-BA vs NICE)")
        if not df_cross.empty:
            pivot = df_cross.pivot(index="gba_outcome", columns="nice_outcome", values="cnt").fillna(0)
            order = ["positive", "restricted", "negative"]
            pivot = pivot.reindex(index=order, columns=order, fill_value=0)
            fig2 = px.imshow(
                pivot.values,
                x=pivot.columns.tolist(),
                y=pivot.index.tolist(),
                text_auto=True,
                color_continuous_scale="YlOrRd",
                labels={"x": "NICE Outcome", "y": "G-BA Outcome", "color": "Count"},
            )
            fig2.update_layout(height=400)
            st.plotly_chart(fig2, use_container_width=True)

    # Timeline
    @st.cache_data(ttl=300)
    def load_timeline():
        return db.fetch_df("""
            SELECT agency_id, YEAR(decision_date) AS year, COUNT(*) AS cnt
            FROM assessments
            WHERE decision_date IS NOT NULL AND YEAR(decision_date) >= 2012
            GROUP BY agency_id, YEAR(decision_date)
            ORDER BY year
        """)

    df_timeline = load_timeline()

    st.subheader("Assessments per Year")
    if not df_timeline.empty:
        fig3 = px.bar(
            df_timeline, x="year", y="cnt", color="agency_id",
            barmode="group",
            color_discrete_map={"gba": "#3498db", "nice": "#e74c3c"},
            labels={"cnt": "Assessments", "year": "Year", "agency_id": "Agency"},
        )
        fig3.update_layout(height=350)
        st.plotly_chart(fig3, use_container_width=True)

# ============================================================
# Tab 2: Drug Comparison
# ============================================================
with tab_compare:
    st.subheader("Drug Comparison: G-BA vs NICE")

    @st.cache_data(ttl=300)
    def load_matched_inns():
        rows = db.fetch_all("""
            SELECT DISTINCT inn FROM v_matched_drug_pairs ORDER BY inn
        """)
        return [r[0] for r in rows]

    matched_inns = load_matched_inns()

    selected_inn = st.selectbox(
        "Select drug (INN)", matched_inns,
        index=None, placeholder="Search for a drug..."
    )

    if selected_inn:
        # Load all assessments for this INN from both agencies
        df_drug = db.fetch_df("""
            SELECT a.agency_id, a.source_id, a.indication_short,
                   a.source_rating, a.overall_outcome, a.decision_date,
                   a.source_url, d.brand_name, d.atc_code
            FROM assessments a
            JOIN drugs d ON a.drug_id = d.drug_id
            WHERE LOWER(d.inn) = LOWER(:inn)
            ORDER BY a.agency_id, a.decision_date DESC
        """, {"inn": selected_inn})

        col_gba, col_nice = st.columns(2)

        with col_gba:
            st.markdown("### G-BA (Germany)")
            df_gba = df_drug[df_drug["agency_id"] == "gba"]
            if df_gba.empty:
                st.info("No G-BA assessments found")
            else:
                for _, row in df_gba.iterrows():
                    color = {"positive": "🟢", "restricted": "🟡", "negative": "🔴"}.get(
                        row["overall_outcome"], "⚪"
                    )
                    st.markdown(
                        f"{color} **{row['source_rating']}** — {row['overall_outcome']}"
                    )
                    if row["indication_short"]:
                        st.caption(row["indication_short"][:200])
                    st.caption(f"{row['decision_date']} | [{row['source_id']}]({row['source_url']})")
                    st.divider()

        with col_nice:
            st.markdown("### NICE (UK)")
            df_nice = df_drug[df_drug["agency_id"] == "nice"]
            if df_nice.empty:
                st.info("No NICE assessments found")
            else:
                for _, row in df_nice.iterrows():
                    color = {"positive": "🟢", "restricted": "🟡", "negative": "🔴"}.get(
                        row["overall_outcome"], "⚪"
                    )
                    st.markdown(
                        f"{color} **{row['source_rating']}** — {row['overall_outcome']}"
                    )
                    if row["indication_short"]:
                        st.caption(row["indication_short"][:200])
                    st.caption(f"{row['decision_date']} | [{row['source_id']}]({row['source_url']})")
                    st.divider()

# ============================================================
# Tab 3: RAG Chat
# ============================================================
with tab_chat:
    st.subheader("Ask the HTA Knowledge Base")
    st.caption(
        "Ask questions in German or English about HTA assessments. "
        "Answers are based on G-BA and NICE data with source citations."
    )

    # Chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Display chat history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    if prompt := st.chat_input("z.B. 'Compare G-BA and NICE decisions for pembrolizumab'"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Searching and analyzing..."):
                try:
                    from chat.retriever import retrieve
                    from chat.responder import respond
                    import config

                    context = retrieve(prompt, top_k=8)
                    answer = respond(prompt, context, model=config.OPENAI_CHAT_DEPLOYMENT)
                except Exception as e:
                    answer = f"Error: {e}"

            st.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})
