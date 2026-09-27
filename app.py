from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from finance_dashboard.analysis import classify, common_overlap, mark_new_spending_for_review, week_start
from finance_dashboard.data import load_transactions
from finance_dashboard.settings import (
    add_rubric,
    assign_rubric,
    load_settings,
    move_to_junk,
    move_to_review,
    register_imported_transactions,
    restore_from_junk,
    save_settings,
)


ROOT = Path(__file__).parent
SETTINGS_ROOT = ROOT / "users"
USER_COLOR_PALETTE = px.colors.qualitative.Dark24

st.set_page_config(page_title="Household spending", page_icon="₪", layout="wide")

# These rules keep the reporting area readable and make horizontal desktop layouts
# stack on small screens.
st.markdown(
    """
    <style>
    [data-testid="stAppViewContainer"] .main .block-container {
        max-width: 1600px;
        padding-top: 1.75rem;
        padding-bottom: 2.5rem;
    }
    [data-testid="stSidebar"] {
        border-right: 1px solid var(--st-secondary-background-color);
    }
    [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
        padding-top: 1rem;
    }
    .household-users {
        margin: -0.5rem 0 0;
        color: var(--st-text-color);
        font-size: 1.2rem;
        opacity: 0.75;
    }
    .summary-card {
        min-height: 10rem;
        box-sizing: border-box;
        padding: 1.25rem;
        border: 1px solid var(--st-secondary-background-color);
        border-radius: 0.9rem;
        background: var(--st-background-color);
        box-shadow: 0 0.25rem 0.8rem rgba(15, 23, 42, 0.06);
        animation: summary-count-in 700ms cubic-bezier(.2, .8, .2, 1) both;
    }
    .summary-card__heading {
        display: flex;
        align-items: center;
        gap: 0.7rem;
        color: var(--st-text-color);
        font-size: 1rem;
        font-weight: 600;
        opacity: 0.82;
    }
    .summary-card__icon {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 2.6rem;
        height: 2.6rem;
        border-radius: 999px;
        background: color-mix(in srgb, var(--summary-accent) 14%, transparent);
        color: var(--summary-accent);
        font-size: 1.5rem;
        font-weight: 700;
    }
    .summary-card__value {
        margin-top: 1.15rem;
        color: var(--summary-accent);
        font-size: 2rem;
        font-weight: 700;
        line-height: 1.25;
    }
    .summary-card__hint {
        margin-top: 0.35rem;
        color: var(--st-text-color);
        font-size: 0.8rem;
        opacity: 0.6;
    }
    .chart-section-spacer {
        height: 1.5rem;
    }
    .st-key-top_summary [data-testid="stColumn"] {
        display: flex;
    }

    @keyframes summary-count-in {
        from { opacity: 0; transform: translateY(0.35rem) scale(0.94); }
        to { opacity: 1; transform: translateY(0) scale(1); }
    }

    @media (max-width: 768px) {
        [data-testid="stAppViewContainer"] .main .block-container {
            padding: 1rem 0.85rem 2rem;
            max-width: 100%;
        }

        [data-testid="stAppViewContainer"] h1 {
            font-size: 1.7rem;
            line-height: 1.2;
        }

        [data-testid="stMetric"] { padding: 0.6rem 0; }
        [data-testid="stMetricLabel"] { font-size: 0.8rem; }
        [data-testid="stMetricValue"] { font-size: 1.15rem; }

        /* Keep the primary figures paired, with net and review on full rows. */
        .st-key-top_summary [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap;
            gap: 0.35rem 0.8rem;
        }
        .st-key-top_summary [data-testid="stColumn"] {
            flex: 1 1 calc(50% - 0.4rem) !important;
            min-width: 0 !important;
        }
        .st-key-top_summary [data-testid="stColumn"]:nth-child(n+3) {
            flex-basis: 100% !important;
        }
        .summary-card__value {
            font-size: 1.15rem;
        }

        [data-testid="stPlotlyChart"],
        [data-testid="stDataFrame"],
        [data-testid="stDataEditor"] { min-width: 0; }

        [data-testid="stDataFrame"],
        [data-testid="stDataEditor"] { font-size: 0.8rem; }

    }
    </style>
    """,
    unsafe_allow_html=True,
)

if "settings" not in st.session_state:
    st.session_state.settings = load_settings(SETTINGS_ROOT)
if "selected_rubric" not in st.session_state:
    st.session_state.selected_rubric = st.session_state.settings.get("selected_rubric")
if "pending_assignments" not in st.session_state:
    st.session_state.pending_assignments = []

chart_background = "rgba(0,0,0,0)"
chart_text = "var(--st-text-color)"
chart_muted = "var(--st-text-color)"
chart_grid = "var(--st-secondary-background-color)"

st.markdown(
    """
    <style>
    .net-summary-label { color: var(--st-text-color); opacity: 0.7; }
    [data-testid="stBaseButton-primary"],
    [data-testid="stBaseButton-primary"] p { color: #ffffff !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner="Reading bank and card exports…")
def read_all() -> tuple[pd.DataFrame, list[str]]:
    return load_transactions(ROOT)


def money(value: float) -> str:
    return f"₪{value:,.0f}"


def format_user_names(users: list[str]) -> str:
    """Join user names naturally for one or more imported users."""
    if len(users) < 2:
        return users[0] if users else ""
    return f"{', '.join(users[:-1])} and {users[-1]}"


def user_color_map(users: list[str]) -> dict[str, str]:
    """Assign a stable chart color to every imported user."""
    return {
        user: USER_COLOR_PALETTE[index % len(USER_COLOR_PALETTE)]
        for index, user in enumerate(users)
    }


def summary_card(container, label: str, value: str, hint: str, icon: str, accent: str) -> None:
    """Render a dashboard summary card with the supplied metric content."""
    container.markdown(
        f'<div class="summary-card" style="--summary-accent:{accent}">'
        f'<div class="summary-card__heading"><span class="summary-card__icon">{icon}</span>{escape(label)}</div>'
        f'<div class="summary-card__value">{escape(value)}</div>'
        f'<div class="summary-card__hint">{escape(hint)}</div></div>',
        unsafe_allow_html=True,
    )


def apply_chart_theme(chart) -> None:
    chart.update_layout(
        paper_bgcolor=chart_background,
        plot_bgcolor=chart_background,
        font={"color": chart_text},
        legend={"font": {"color": chart_text}},
    )
    chart.update_xaxes(gridcolor=chart_grid, linecolor=chart_grid, zerolinecolor=chart_grid)
    chart.update_yaxes(gridcolor=chart_grid, linecolor=chart_grid, zerolinecolor=chart_grid)


def save(show_sidebar_message: bool = True) -> None:
    st.session_state.settings["selected_rubric"] = st.session_state.selected_rubric
    save_settings(SETTINGS_ROOT, st.session_state.settings)
    if show_sidebar_message:
        st.sidebar.success("Saved users/.dashboard_settings.json")


def audit_flags(rows: pd.DataFrame, container, key: str) -> None:
    """Show an audit list whose rows can be manually sent to a disposition."""
    if rows.empty:
        container.info("No transactions in this list for the selected period.")
        return

    editor = rows[["id", "date", "recorded_at", "user", "description", "amount", "source", "table_name", "rubric",
                   "is_reviewed", "is_junk", "is_transfer", "is_settlement"]].copy()
    editor["source"] = editor["source"].str.title()
    editor["rubric"] = editor["rubric"].fillna("Needs review")
    editor["flags"] = editor.apply(
        lambda row: ", ".join(name for name, active in {
            "reviewed": row["is_reviewed"], "junk": row["is_junk"],
            "transfer": row["is_transfer"], "card settlement": row["is_settlement"],
        }.items() if active) or "counted",
        axis=1,
    )
    editor.loc[editor.id.isin(st.session_state.settings.get("needs_review_ids", [])), "flags"] = "needs review"
    editor["change_to"] = "No change"
    editor = editor.drop(columns=["is_reviewed", "is_junk", "is_transfer", "is_settlement"])
    filter_columns = {
        "Date": "date", "Recorded in app": "recorded_at", "User": "user", "Description": "description", "Amount": "amount",
        "Source": "source", "Source table": "table_name", "Current rubric": "rubric", "Current flags": "flags",
    }
    filter_label = container.selectbox(
        "Filter column", list(filter_columns), key=f"{key}_filter_column",
    )
    filter_column = filter_columns[filter_label]
    filter_values = sorted(editor[filter_column].fillna("").astype(str).unique().tolist())
    selected_values = container.multiselect(
        f"{filter_label} values", filter_values, key=f"{key}_filter_values",
        placeholder="All values",
    )
    if selected_values:
        editor = editor[editor[filter_column].fillna("").astype(str).isin(selected_values)]
    edited = container.data_editor(
        editor.sort_values("date", ascending=False), key=key, width="stretch", hide_index=True,
        disabled=["id", "date", "recorded_at", "user", "description", "amount", "source", "table_name", "rubric", "flags"],
        column_config={
            "id": None,
            "date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
            "recorded_at": st.column_config.DatetimeColumn("Recorded in app", format="DD/MM/YYYY HH:mm"),
            "amount": st.column_config.NumberColumn("Amount", format="%.2f"),
            "source": "Source",
            "table_name": "Source table",
            "rubric": "Current rubric",
            "flags": "Current flags",
            "change_to": st.column_config.SelectboxColumn(
                "Change to", options=["No change", "Review", "Junk", *st.session_state.settings["rubrics"]], required=True
            ),
        },
    )
    if container.button("Apply audit changes", key=f"{key}_apply", type="primary"):
        changed = 0
        for _, row in edited.iterrows():
            target = row["change_to"]
            if target == "No change":
                continue
            transaction_id = row["id"]
            if target == "Review":
                move_to_review(st.session_state.settings, transaction_id)
            elif target == "Junk":
                move_to_junk(st.session_state.settings, transaction_id)
            else:
                assign_rubric(st.session_state.settings, transaction_id, target)
            changed += 1
        if changed:
            save()
            st.rerun()
        container.info("Choose a destination for at least one row first.")


@st.dialog("Apply rubric to matching descriptions")
def matching_items_dialog(classified: pd.DataFrame) -> None:
    assignment = st.session_state.pending_assignments[0]
    candidates = classified[
        (classified.is_spending | classified.is_income) & classified.description.eq(assignment["description"])
    ][["id", "date", "user", "description", "amount", "rubric"]].copy()
    candidates["apply"] = False
    state_key = f"matching_rows_{assignment['id']}"
    widget_key = f"matching_editor_{assignment['id']}"
    if state_key not in st.session_state:
        st.session_state[state_key] = candidates

    st.write(f"**{assignment['description']}** -> **{assignment['rubric']}**")
    st.caption("Nothing is selected automatically. Select individual transactions, or select all.")
    sort_order = st.radio("Sort by amount", ["Highest first", "Lowest first"], horizontal=True)
    if st.button("Select all matching items"):
        rows = st.session_state[state_key].copy()
        rows["apply"] = True
        st.session_state[state_key] = rows
        if widget_key in st.session_state:
            del st.session_state[widget_key]
        st.rerun()

    rows = st.session_state[state_key].sort_values("amount", ascending=sort_order == "Lowest first")
    edited_matches = st.data_editor(
        rows, key=widget_key, width="stretch", hide_index=True,
        disabled=["id", "date", "user", "description", "amount", "rubric"],
        column_config={
            "id": None,
            "date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
            "amount": st.column_config.NumberColumn("Amount", format="%.2f"),
            "apply": st.column_config.CheckboxColumn("Apply this rubric"),
        },
    )
    st.session_state[state_key] = edited_matches
    first, second = st.columns(2)
    with first:
        if st.button("Apply selected items", type="primary"):
            selected_ids = edited_matches.loc[edited_matches["apply"].astype(bool), "id"].tolist()
            for transaction_id in selected_ids:
                assign_rubric(st.session_state.settings, transaction_id, assignment["rubric"])
            st.session_state.pending_assignments.pop(0)
            del st.session_state[state_key]
            if widget_key in st.session_state:
                del st.session_state[widget_key]
            if not st.session_state.pending_assignments:
                save(show_sidebar_message=False)
            st.rerun()
    with second:
        if st.button("Skip these matches"):
            st.session_state.pending_assignments.pop(0)
            del st.session_state[state_key]
            if widget_key in st.session_state:
                del st.session_state[widget_key]
            if not st.session_state.pending_assignments:
                save(show_sidebar_message=False)
            st.rerun()


transactions, warnings = read_all()
if transactions.empty:
    st.error("No transactions could be imported. Keep one CSV/XLSX export in each source folder.")
    st.stop()
tracking_was_initialized = st.session_state.settings.get("import_tracking_initialized", False)
new_transaction_ids = register_imported_transactions(
    st.session_state.settings,
    transactions["id"].tolist(),
    datetime.now().astimezone().isoformat(timespec="seconds"),
)
transactions["recorded_at"] = pd.to_datetime(
    transactions["id"].map(st.session_state.settings["transaction_first_seen"]), errors="coerce",
)
imported_users = sorted(transactions["user"].dropna().unique().tolist())
user_names = format_user_names(imported_users)
user_colors = user_color_map(imported_users)

with st.sidebar:
    st.title("Household Finance")
    st.caption("Imports, rubrics, and review settings")
    st.divider()
    st.subheader("Controls")
    if st.button("Reload exports"):
        read_all.clear()
        st.rerun()
    if st.button("Save changes", type="primary"):
        save()
    st.divider()
    st.subheader("Add rubric")
    new_rubric = st.text_input("Rubric name")
    if st.button("Add rubric") and new_rubric.strip():
        name = new_rubric.strip()
        if name not in st.session_state.settings["rubrics"]:
            add_rubric(st.session_state.settings, name)
        st.rerun()
    st.divider()
    st.subheader("Manage rubrics")
    rubric_to_manage = st.selectbox("Rubric", st.session_state.settings["rubrics"], key="rubric_to_manage")
    renamed_rubric = st.text_input("New name", value=rubric_to_manage, key="renamed_rubric")
    rename_column, remove_column = st.columns(2)
    with rename_column:
        if st.button("Rename"):
            new_name = renamed_rubric.strip()
            if not new_name:
                st.error("Enter a rubric name.")
            elif new_name != rubric_to_manage and new_name in st.session_state.settings["rubrics"]:
                st.error("That rubric already exists.")
            else:
                settings = st.session_state.settings
                settings["rubrics"] = [new_name if value == rubric_to_manage else value for value in settings["rubrics"]]
                settings["merchant_rules"] = {
                    merchant: new_name if rubric == rubric_to_manage else rubric
                    for merchant, rubric in settings["merchant_rules"].items()
                }
                settings["transaction_overrides"] = {
                    transaction_id: new_name if rubric == rubric_to_manage else rubric
                    for transaction_id, rubric in settings["transaction_overrides"].items()
                }
                if st.session_state.selected_rubric == rubric_to_manage:
                    st.session_state.selected_rubric = new_name
                save()
                st.rerun()
    with remove_column:
        if st.button("Remove"):
            settings = st.session_state.settings
            settings["rubrics"] = [value for value in settings["rubrics"] if value != rubric_to_manage]
            settings["merchant_rules"] = {
                merchant: rubric for merchant, rubric in settings["merchant_rules"].items() if rubric != rubric_to_manage
            }
            settings["transaction_overrides"] = {
                transaction_id: rubric for transaction_id, rubric in settings["transaction_overrides"].items() if rubric != rubric_to_manage
            }
            if st.session_state.selected_rubric == rubric_to_manage:
                st.session_state.selected_rubric = None
            save()
            st.rerun()

classified = classify(transactions, st.session_state.settings)
new_spending_review_ids = mark_new_spending_for_review(
    classified, st.session_state.settings, new_transaction_ids,
)
if not tracking_was_initialized or new_transaction_ids or new_spending_review_ids:
    save_settings(SETTINGS_ROOT, st.session_state.settings)
    classified = classify(transactions, st.session_state.settings)
try:
    overlap_start, overlap_end = common_overlap(classified)
except ValueError as error:
    st.error(str(error))
    st.stop()
start, end = overlap_start.date(), overlap_end.date()
title_column, date_column = st.columns([4, 1], vertical_alignment="center")
with title_column:
    st.title("Household spending")
    st.markdown(f'<p class="household-users">{escape(user_names)}</p>', unsafe_allow_html=True)
with date_column:
    selected_range = st.date_input("Date range", value=(start, end), min_value=start, max_value=end)
for warning in warnings:
    st.warning(warning)
if not isinstance(selected_range, tuple) or len(selected_range) != 2:
    st.info("Choose both a start and end date.")
    st.stop()
filtered = classified[classified.date.between(pd.Timestamp(selected_range[0]), pd.Timestamp(selected_range[1]))]
spending = filtered[filtered.is_spending].copy()
review = spending[spending.rubric.isna() | (spending.is_one_off & ~spending.is_reviewed)].sort_values("date", ascending=False)
grouped_activity = filtered[filtered.is_spending | filtered.is_income].copy()
grouped_activity["rubric"] = grouped_activity["rubric"].fillna("Needs review")

top_summary = st.container(key="top_summary")
left, middle, net_column, right = top_summary.columns(4)
income_total = filtered.loc[filtered.is_income, "amount"].sum()
spending_total = -spending.amount.sum()
summary_card(left, "Income", money(income_total), "Selected period", "↑", "#16a34a")
summary_card(middle, "Spending", money(spending_total), "Selected period", "↓", "#dc2626")
net_value = filtered.loc[filtered.is_income, "amount"].sum() + spending.amount.sum()
net_color = "#16a34a" if net_value >= 0 else "#dc2626"
summary_card(net_column, "Net income", money(net_value), "Income less spending", "↗", net_color)
summary_card(right, "Items to review", str(len(review)), "Awaiting a decision", "!", "#d97706")

st.markdown('<div class="chart-section-spacer"></div>', unsafe_allow_html=True)
pie_title_column, weekly_title_column = st.columns(2, gap="medium")
pie_column, weekly_column = st.columns(2, gap="medium")

net_by_rubric = grouped_activity.groupby("rubric", as_index=False).agg(
    spending=("amount", lambda values: -values[values < 0].sum()),
    income=("amount", lambda values: values[values > 0].sum()),
)
net_by_rubric["net_cost"] = net_by_rubric["spending"] - net_by_rubric["income"]
pie_groups = net_by_rubric[net_by_rubric.net_cost > 0.01]
pie_groups = pie_groups.copy()
pie_groups["legend_label"] = pie_groups.apply(
    lambda row: f"{row['rubric']} ({row['net_cost'] / pie_groups.net_cost.sum():.1%})", axis=1
)
pie_total = pie_groups.net_cost.sum()
pie_colors = px.colors.sample_colorscale(
    "Turbo", samplepoints=max(len(pie_groups), 2), low=.08, high=.92
)
pie = px.pie(
    pie_groups,
    names="legend_label",
    values="net_cost",
    hole=.68,
    color="rubric",
    color_discrete_sequence=pie_colors,
    custom_data=["rubric"],
)
pie.update_traces(
    textinfo="percent",
    textposition="inside",
    insidetextfont={"color": "white", "size": 14},
    marker={"line": {"color": "white", "width": 2}},
    hovertemplate="<b>%{customdata[0]}</b><br>Net spending: %{value:,.0f} ₪<br>%{percent}<extra></extra>",
)
pie.add_annotation(
    x=.5, y=.5, showarrow=False,
    text=f"<b>{money(pie_total)}</b><br><span style='font-size:12px;color:var(--st-text-color)'>NET SPENDING</span>",
    font={"size": 23, "color": chart_text},
)
pie.update_layout(
    height=440,
    margin={"l": 10, "r": 10, "t": 20, "b": 145},
    legend={
        "title": {"text": ""}, "orientation": "h",
        "x": .5, "xanchor": "center", "y": -.12, "yanchor": "top",
        # Keep entries narrow enough for two columns in the mobile chart width;
        # Plotly then adds rows as needed on narrower screens.
        "entrywidth": 120, "entrywidthmode": "pixels",
    },
    font={"size": 14, "color": "#374151"},
)
pie.update_annotations(font={"color": chart_text})
apply_chart_theme(pie)
pie_title_column.subheader("Spending by rubric")
event = pie_column.plotly_chart(
    pie,
    width="stretch",
    key="rubric_pie",
    on_select="rerun",
    selection_mode="points",
    config={"responsive": True, "displayModeBar": False},
)
points = event.selection.get("points", []) if event else []
if points:
    point = points[0]
    custom_data = point.get("customdata")
    clicked_rubric = custom_data[0] if isinstance(custom_data, list) else custom_data
    clicked_rubric = clicked_rubric or point.get("label")
    if clicked_rubric in pie_groups["rubric"].tolist():
        st.session_state.selected_rubric = clicked_rubric
        st.session_state.weekly_chart_rubric = clicked_rubric

chart_rubrics = pie_groups["rubric"].tolist()
selected = st.session_state.selected_rubric if st.session_state.selected_rubric in chart_rubrics else None
if selected is None and chart_rubrics:
    selected = chart_rubrics[0]
st.session_state.selected_rubric = selected
if selected:
    selected = weekly_title_column.selectbox(
        "Rubric",
        chart_rubrics,
        index=chart_rubrics.index(selected),
        key="weekly_chart_rubric",
    )
    st.session_state.selected_rubric = selected
    rubric_rows = grouped_activity[grouped_activity.rubric.eq(selected)].copy()
    weekly = rubric_rows.assign(week=week_start(rubric_rows.date)).groupby(["week", "user"], as_index=False).amount.sum()
    weekly["amount"] = -weekly.amount
    all_weeks = weekly["week"].drop_duplicates().sort_values()
    weekly = (
        weekly.set_index(["week", "user"])
        .reindex(pd.MultiIndex.from_product([all_weeks, imported_users], names=["week", "user"]), fill_value=0)
        .reset_index()
    )
    fig = px.bar(weekly, x="week", y="amount", color="user", barmode="group", color_discrete_map=user_colors,
                 category_orders={"user": imported_users})
    weekly_total = weekly.groupby("week", as_index=False).amount.sum()
    period_total = weekly_total["amount"].sum()
    fig.add_scatter(
        x=weekly_total["week"], y=weekly_total["amount"], mode="lines+markers+text", name="Total",
        line={"color": chart_text, "width": 3}, marker={"size": 8},
        text=[money(value) for value in weekly_total["amount"]], textposition="top center",
    )
    fig.update_layout(height=440, margin={"l": 10, "r": 10, "t": 20, "b": 40})
    apply_chart_theme(fig)
    weekly_title_column.subheader(f"{selected} - weekly cost")
    weekly_title_column.caption(f"Period total: {money(period_total)}")
    weekly_column.plotly_chart(fig, width="stretch", config={"responsive": True, "displayModeBar": False})
else:
    weekly_title_column.subheader("Weekly cost")
    weekly_column.info("Select a rubric when spending is available for the selected period.")

income = filtered[filtered.is_income].copy()
if not income.empty:
    monthly_income = income.assign(month=income.date.dt.to_period("M").dt.to_timestamp()).groupby(["month", "user"], as_index=False).amount.sum()
    all_months = monthly_income["month"].drop_duplicates().sort_values()
    monthly_income = (
        monthly_income.set_index(["month", "user"])
        .reindex(pd.MultiIndex.from_product([all_months, imported_users], names=["month", "user"]), fill_value=0)
        .reset_index()
    )
    income_fig = px.bar(monthly_income, x="month", y="amount", color="user", barmode="group", color_discrete_map=user_colors,
                        category_orders={"user": imported_users}, title="Monthly income")
    monthly_income_total = monthly_income.groupby("month", as_index=False).amount.sum()
    income_fig.add_scatter(
        x=monthly_income_total["month"], y=monthly_income_total["amount"], mode="lines+markers+text", name="Total",
        line={"color": chart_text, "width": 3}, marker={"size": 8},
        text=[money(value) for value in monthly_income_total["amount"]], textposition="top center",
    )
    income_fig.update_layout(height=360)
    apply_chart_theme(income_fig)
    st.plotly_chart(income_fig, width="stretch", config={"responsive": True, "displayModeBar": False})

income_review = st.expander("Income review", expanded=False)
income_review.caption(f"{len(income)} income transaction{'s' if len(income) != 1 else ''} in the selected period. Click column headers to sort, including by amount.")
income_editor = income[["id", "date", "recorded_at", "user", "description", "amount", "source", "table_name", "raw_type", "rubric"]].sort_values("date", ascending=False).copy()
income_editor["rubric"] = income_editor["rubric"].fillna("Needs review")
income_editor["junk"] = False
edited_income = income_review.data_editor(
    income_editor,
    key="income_editor",
    width="stretch",
    hide_index=True,
    disabled=["id", "date", "recorded_at", "user", "description", "amount", "source", "table_name", "raw_type"],
    column_config={
        "id": None,
        "date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
        "recorded_at": st.column_config.DatetimeColumn("Recorded in app", format="DD/MM/YYYY HH:mm"),
        "amount": st.column_config.NumberColumn("Amount", format="%.2f"),
        "table_name": "Source table",
        "raw_type": "Transaction type",
        "rubric": st.column_config.SelectboxColumn(
            "Existing rubric", options=["Needs review", *st.session_state.settings["rubrics"]], required=True
        ),
        "junk": st.column_config.CheckboxColumn("Move to Junk"),
    },
)
if income_review.button("Move selected income to Junk"):
    selected_income_ids = edited_income.loc[edited_income["junk"].astype(bool), "id"].tolist()
    if selected_income_ids:
        removed_amount = income.loc[income.id.isin(selected_income_ids), "amount"].sum()
        for transaction_id in selected_income_ids:
            move_to_junk(st.session_state.settings, transaction_id)
        save()
        st.session_state.income_junk_notice = (len(selected_income_ids), removed_amount)
        read_all.clear()
        st.rerun()
    income_review.info("Select at least one income transaction first.")
if income_review.button("Update income rubrics and refresh charts"):
    changed = 0
    for _, row in edited_income.iterrows():
        category = row["rubric"]
        if category == "Needs review":
            continue
        assign_rubric(st.session_state.settings, row["id"], category)
        changed += 1
    if changed:
        save()
        st.rerun()
    income_review.info("Choose a rubric for at least one income transaction first.")
if st.session_state.get("income_junk_notice"):
    count, amount = st.session_state.pop("income_junk_notice")
    income_review.success(f"Moved {count} income item(s), totaling {money(amount)}, to Junk. Charts and totals were refreshed.")

spending_review = st.expander("Spending Review", expanded=False)
spending_review.caption(f"{len(review)} item{'s' if len(review) != 1 else ''} currently need review.")
if review.empty:
    spending_review.success("No unknown or one-off spending in this date range.")
else:
    editor = review[["id", "date", "recorded_at", "user", "description", "amount", "source", "table_name", "suggested_rubric", "rubric", "is_one_off"]].copy()
    editor["source"] = editor["source"].str.title()
    editor["rubric"] = editor["rubric"].fillna("Needs review")
    editor["junk"] = False
    spending_review.caption("Choose a rubric directly in its row, then update. Changes are applied only when you click Update.")
    edited = spending_review.data_editor(
        editor,
        key="review_editor",
        width="stretch",
        hide_index=True,
        disabled=["id", "date", "recorded_at", "user", "description", "amount", "source", "table_name", "suggested_rubric", "is_one_off"],
        column_config={
            "id": None,
            "date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
            "recorded_at": st.column_config.DatetimeColumn("Recorded in app", format="DD/MM/YYYY HH:mm"),
            "amount": st.column_config.NumberColumn("Amount (₪)", format="%.2f"),
            "source": "Source",
            "table_name": "Source table",
            "suggested_rubric": "Suggested rubric",
            "rubric": st.column_config.SelectboxColumn(
                "Rubric", options=["Needs review", *st.session_state.settings["rubrics"]], required=True
            ),
            "junk": st.column_config.CheckboxColumn("Move to Junk"),
        },
    )
    if spending_review.button("Update review, save configuration, and refresh charts", type="primary"):
        pending = []
        changed = 0
        for _, row in edited.iterrows():
            transaction_id = row["id"]
            if row["junk"]:
                move_to_junk(st.session_state.settings, transaction_id)
                changed += 1
                continue
            category = row["rubric"]
            if category == "Needs review":
                continue
            original = review.loc[review.id.eq(transaction_id), "rubric"].iloc[0]
            original = original if pd.notna(original) else "Needs review"
            if category == original:
                continue
            matching_ids = classified.loc[
                classified.is_spending & classified.description.eq(row["description"]), "id"
            ].tolist()
            if len(matching_ids) <= 1:
                assign_rubric(st.session_state.settings, transaction_id, category)
            else:
                pending.append({"id": transaction_id, "description": row["description"], "rubric": category})
            changed += 1
        if changed:
            st.session_state.pending_assignments = pending
            if not pending:
                save()
            st.rerun()
        spending_review.info("Change a rubric or select Move to Junk for at least one row first.")

junk_review = st.expander("Junk Review", expanded=False)
junk_rows = filtered[filtered.is_junk].sort_values("date", ascending=False)
junk_review.caption(f"{len(junk_rows)} dismissed item{'s' if len(junk_rows) != 1 else ''} in the selected period. Assigning a rubric restores an item to the dashboard.")
if junk_rows.empty:
    junk_review.info("No dismissed items in this date range.")
else:
    junk_editor = junk_rows[["id", "date", "recorded_at", "user", "description", "amount", "source", "table_name", "rubric"]].copy()
    junk_editor["rubric"] = junk_editor["rubric"].fillna("Needs review")
    junk_editor["restore"] = False
    edited_junk = junk_review.data_editor(
        junk_editor,
        key="junk_editor",
        width="stretch",
        hide_index=True,
        disabled=["id", "date", "recorded_at", "user", "description", "amount", "source", "table_name"],
        column_config={
            "id": None,
            "date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
            "recorded_at": st.column_config.DatetimeColumn("Recorded in app", format="DD/MM/YYYY HH:mm"),
            "amount": st.column_config.NumberColumn("Amount", format="%.2f"),
            "table_name": "Source table",
            "rubric": st.column_config.SelectboxColumn(
                "Restore to existing rubric", options=["Needs review", *st.session_state.settings["rubrics"]], required=True
            ),
            "restore": st.column_config.CheckboxColumn("Restore without rubric"),
        },
    )
    if junk_review.button("Restore classified Junk items and refresh charts"):
        restored = 0
        for _, row in edited_junk.iterrows():
            category = row["rubric"]
            should_restore = row["restore"] or category != "Needs review"
            if not should_restore:
                continue
            if category != "Needs review":
                assign_rubric(st.session_state.settings, row["id"], category)
            else:
                restore_from_junk(st.session_state.settings, row["id"])
            restored += 1
        if restored:
            save()
            st.rerun()
        junk_review.info("Choose a rubric or check Restore without rubric for at least one item.")

if st.session_state.pending_assignments:
    matching_items_dialog(classified)

with st.expander("Transactions and audit lists"):
    st.caption("Choose Review, Junk, or a rubric for any row. Review also brings an excluded transfer or card settlement back into the review workflow.")
    tabs = st.tabs(["Counted spending", "Income", "Household transfers", "Excluded card settlements", "Junk"])
    audit_flags(spending, tabs[0], "audit_spending")
    audit_flags(income, tabs[1], "audit_income")
    audit_flags(filtered[filtered.is_transfer], tabs[2], "audit_transfers")
    audit_flags(filtered[filtered.is_settlement], tabs[3], "audit_settlements")
    audit_flags(filtered[filtered.is_junk], tabs[4], "audit_junk")
