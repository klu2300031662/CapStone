"""
Adaptive Traffic Protection – Monitoring Dashboard
====================================================
Streamlit dashboard for the "Adaptive Traffic Protection Without Static Rate
Limits" B.Tech capstone project.

Run with:
    streamlit run dashboard/dashboard.py
"""

import os
import time
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Page config – must be first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ATP Dashboard",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Defaults from environment
# ---------------------------------------------------------------------------
DEFAULT_GATEWAY_URL = os.environ.get("DASHBOARD_GATEWAY_URL", "http://localhost:8000")
DEFAULT_ADMIN_KEY = os.environ.get(
    "ADMIN_API_KEY", "gateway-admin-secret-key-change-in-production"
)

# ---------------------------------------------------------------------------
# Session-state initialisation
# ---------------------------------------------------------------------------
if "last_health" not in st.session_state:
    st.session_state.last_health = None
if "last_apps" not in st.session_state:
    st.session_state.last_apps = []
if "last_controller" not in st.session_state:
    st.session_state.last_controller = None
if "prev_limits" not in st.session_state:
    st.session_state.prev_limits = {}
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = 0.0
if "gateway_ok" not in st.session_state:
    st.session_state.gateway_ok = False
if "error_msg" not in st.session_state:
    st.session_state.error_msg = ""


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _admin_headers(api_key: str) -> dict:
    return {"X-Api-Key": api_key}


def fetch_health(gateway_url: str) -> dict | None:
    """GET /health"""
    try:
        resp = requests.get(f"{gateway_url}/health", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return None
    except requests.exceptions.Timeout:
        return None
    except Exception:
        return None


def fetch_applications(gateway_url: str, api_key: str) -> list | None:
    """GET /admin/applications"""
    try:
        resp = requests.get(
            f"{gateway_url}/admin/applications",
            headers=_admin_headers(api_key),
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("applications", [])
    except requests.exceptions.ConnectionError:
        return None
    except requests.exceptions.Timeout:
        return None
    except Exception:
        return None


def fetch_controller_status(gateway_url: str, api_key: str) -> dict | None:
    """GET /admin/controller/status"""
    try:
        resp = requests.get(
            f"{gateway_url}/admin/controller/status",
            headers=_admin_headers(api_key),
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return None
    except requests.exceptions.Timeout:
        return None
    except Exception:
        return None


def post_controller_mode(gateway_url: str, api_key: str, mode: str) -> bool:
    """POST /admin/controller/mode"""
    try:
        resp = requests.post(
            f"{gateway_url}/admin/controller/mode",
            headers={**_admin_headers(api_key), "Content-Type": "application/json"},
            json={"mode": mode},
            timeout=5,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def refresh_all_data(gateway_url: str, api_key: str):
    """Fetch all endpoints and update session state."""
    health = fetch_health(gateway_url)
    if health is None:
        st.session_state.gateway_ok = False
        st.session_state.error_msg = (
            f"Cannot reach gateway at **{gateway_url}**. "
            "Make sure the gateway is running."
        )
        return

    st.session_state.gateway_ok = True
    st.session_state.error_msg = ""
    st.session_state.last_health = health

    apps = fetch_applications(gateway_url, api_key)
    if apps is not None:
        # Store previous limits for delta display
        for app in st.session_state.last_apps:
            app_id = app.get("app_id", app.get("name"))
            st.session_state.prev_limits[app_id] = app.get("current_limit")
        st.session_state.last_apps = apps

    ctrl = fetch_controller_status(gateway_url, api_key)
    if ctrl is not None:
        st.session_state.last_controller = ctrl

    st.session_state.last_refresh = time.time()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar() -> tuple[str, str, bool, int]:
    """Render sidebar controls. Returns (gateway_url, api_key, auto_refresh, interval)."""
    with st.sidebar:
        st.title("🛡️ ATP Dashboard")
        st.caption("Adaptive Traffic Protection")
        st.divider()

        gateway_url = st.text_input(
            "Gateway URL",
            value=DEFAULT_GATEWAY_URL,
            help="Base URL of the ATP gateway",
        ).rstrip("/")

        api_key = st.text_input(
            "Admin API Key",
            value=DEFAULT_ADMIN_KEY,
            type="password",
            help="Admin key for protected endpoints (X-Api-Key header)",
        )

        st.divider()

        # Connection status
        if st.session_state.gateway_ok:
            st.success("● Gateway connected", icon="✅")
        else:
            st.error("● Gateway unreachable", icon="❌")

        st.divider()

        # Auto-refresh controls
        auto_refresh = st.toggle("Auto-refresh", value=True)
        refresh_interval = st.slider(
            "Refresh interval (seconds)",
            min_value=5,
            max_value=60,
            value=10,
            step=5,
            disabled=not auto_refresh,
        )

        if st.button("🔄 Refresh Now", use_container_width=True):
            refresh_all_data(gateway_url, api_key)

        st.divider()

        # Mode switcher
        st.subheader("Controller Mode")
        current_mode = "unknown"
        if st.session_state.last_health:
            current_mode = st.session_state.last_health.get("mode", "unknown")

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button(
                "⚙️ Adaptive",
                use_container_width=True,
                type="primary" if current_mode == "adaptive" else "secondary",
            ):
                ok = post_controller_mode(gateway_url, api_key, "adaptive")
                if ok:
                    st.success("Switched to adaptive")
                    refresh_all_data(gateway_url, api_key)
                else:
                    st.error("Switch failed")
        with col_b:
            if st.button(
                "📌 Static",
                use_container_width=True,
                type="primary" if current_mode == "static" else "secondary",
            ):
                ok = post_controller_mode(gateway_url, api_key, "static")
                if ok:
                    st.success("Switched to static")
                    refresh_all_data(gateway_url, api_key)
                else:
                    st.error("Switch failed")

        st.divider()
        if st.session_state.last_refresh:
            ts = datetime.fromtimestamp(
                st.session_state.last_refresh, tz=timezone.utc
            ).strftime("%H:%M:%S UTC")
            st.caption(f"Last refresh: {ts}")

    return gateway_url, api_key, auto_refresh, refresh_interval


# ---------------------------------------------------------------------------
# Row 1 – Platform status
# ---------------------------------------------------------------------------

def render_platform_status(gateway_url: str):
    st.subheader("🖥️ Platform Status")
    health = st.session_state.last_health

    if not health:
        st.error("No health data available. Gateway may be unreachable.")
        return

    status = health.get("status", "unknown")
    mode = health.get("mode", "unknown")
    uptime_secs = health.get("uptime_seconds", 0)
    app_count = len(health.get("applications", []))

    # Format uptime
    h = int(uptime_secs // 3600)
    m = int((uptime_secs % 3600) // 60)
    s = int(uptime_secs % 60)
    uptime_str = f"{h}h {m}m {s}s"

    status_icon = "🟢" if status == "healthy" else "🔴"
    mode_icon = "⚙️" if mode == "adaptive" else "📌"

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Status", f"{status_icon} {status.capitalize()}")
    with c2:
        st.metric("Mode", f"{mode_icon} {mode.capitalize()}")
    with c3:
        st.metric("Uptime", uptime_str)
    with c4:
        st.metric("Registered Apps", app_count)
    with c5:
        st.metric("Gateway", gateway_url)

    st.caption("Live data from gateway  •  GET /health")


# ---------------------------------------------------------------------------
# Row 2 – Per-app metric cards
# ---------------------------------------------------------------------------

def _status_badge(enabled: bool) -> str:
    return "🟢 Enabled" if enabled else "🔴 Disabled"


def render_app_cards():
    apps = st.session_state.last_apps
    if not apps:
        st.info("No application data yet. Waiting for gateway response.")
        return

    st.subheader("📊 Application Metrics")
    st.caption("Live data from gateway  •  GET /admin/applications")

    for app in apps:
        app_id = app.get("app_id", app.get("name", "unknown"))
        name = app.get("name", app_id)
        enabled = app.get("enabled", False)

        with st.expander(f"{name}  —  {_status_badge(enabled)}", expanded=True):
            prev_limit = st.session_state.prev_limits.get(app_id)
            curr_limit = app.get("current_limit")
            limit_delta = None
            if prev_limit is not None and curr_limit is not None:
                limit_delta = curr_limit - prev_limit

            r1c1, r1c2, r1c3, r1c4 = st.columns(4)
            with r1c1:
                st.metric(
                    "Concurrency Limit",
                    curr_limit if curr_limit is not None else "N/A",
                    delta=limit_delta,
                    help="Current max concurrent requests allowed",
                )
            with r1c2:
                in_flight = app.get("in_flight")
                st.metric(
                    "In-Flight Requests",
                    in_flight if in_flight is not None else "N/A",
                )
            with r1c3:
                rps = app.get("requests_per_second")
                st.metric(
                    "Requests / sec",
                    f"{rps:.2f}" if rps is not None else "N/A",
                )
            with r1c4:
                total = app.get("total_requests")
                rejected = app.get("rejected_requests")
                st.metric(
                    "Total / Rejected",
                    f"{total} / {rejected}"
                    if (total is not None and rejected is not None)
                    else "N/A",
                )

            r2c1, r2c2, r2c3, r2c4 = st.columns(4)
            with r2c1:
                avg_lat = app.get("avg_latency_ms")
                st.metric(
                    "Avg Latency",
                    f"{avg_lat:.1f} ms" if avg_lat is not None else "N/A",
                )
            with r2c2:
                p95_lat = app.get("p95_latency_ms")
                st.metric(
                    "p95 Latency",
                    f"{p95_lat:.1f} ms" if p95_lat is not None else "N/A",
                )
            with r2c3:
                err_rate = app.get("error_rate")
                st.metric(
                    "Error Rate",
                    f"{err_rate * 100:.2f} %" if err_rate is not None else "N/A",
                )
            with r2c4:
                min_lim = app.get("min_limit")
                max_lim = app.get("max_limit")
                st.metric(
                    "Limit Range",
                    f"{min_lim} – {max_lim}"
                    if (min_lim is not None and max_lim is not None)
                    else "N/A",
                    help="Min / Max allowed concurrency limit",
                )


# ---------------------------------------------------------------------------
# Row 3 – Comparison bar charts
# ---------------------------------------------------------------------------

def render_comparison_charts():
    apps = st.session_state.last_apps
    if not apps:
        return

    st.subheader("📈 App Comparison")
    st.caption("Live data from gateway  •  GET /admin/applications")

    app_names = [a.get("name", a.get("app_id", "?")) for a in apps]

    col1, col2, col3 = st.columns(3)

    # -- Concurrency Limits --
    with col1:
        limits = [a.get("current_limit", 0) or 0 for a in apps]
        fig = go.Figure(
            go.Bar(
                x=app_names,
                y=limits,
                marker_color=["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"][:len(apps)],
                text=limits,
                textposition="outside",
            )
        )
        fig.update_layout(
            title="Concurrency Limits",
            yaxis_title="Limit",
            height=320,
            margin=dict(t=40, b=20, l=20, r=20),
        )
        st.plotly_chart(fig, use_container_width=True)

    # -- Latency (avg + p95) --
    with col2:
        avg_lats = [a.get("avg_latency_ms", 0) or 0 for a in apps]
        p95_lats = [a.get("p95_latency_ms", 0) or 0 for a in apps]
        fig = go.Figure(
            [
                go.Bar(name="Avg Latency (ms)", x=app_names, y=avg_lats),
                go.Bar(name="p95 Latency (ms)", x=app_names, y=p95_lats),
            ]
        )
        fig.update_layout(
            title="Latency Comparison",
            barmode="group",
            yaxis_title="Latency (ms)",
            height=320,
            margin=dict(t=40, b=20, l=20, r=20),
        )
        st.plotly_chart(fig, use_container_width=True)

    # -- Error rates --
    with col3:
        err_rates = [
            ((a.get("error_rate") or 0) * 100) for a in apps
        ]
        colors = [
            "#d62728" if e > 5 else "#ff7f0e" if e > 1 else "#2ca02c"
            for e in err_rates
        ]
        fig = go.Figure(
            go.Bar(
                x=app_names,
                y=err_rates,
                marker_color=colors,
                text=[f"{e:.2f}%" for e in err_rates],
                textposition="outside",
            )
        )
        fig.update_layout(
            title="Error Rates (%)",
            yaxis_title="Error Rate (%)",
            height=320,
            margin=dict(t=40, b=20, l=20, r=20),
        )
        st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Row 4 – Concurrency limit history line chart
# ---------------------------------------------------------------------------

def render_history_chart():
    ctrl = st.session_state.last_controller
    if not ctrl:
        return

    history: dict = ctrl.get("recent_history", {})
    if not history:
        st.info("No history data available yet from the controller.")
        return

    st.subheader("📉 Concurrency Limit History")
    st.caption("Live data from gateway  •  GET /admin/controller/status")

    # Build a combined DataFrame
    frames = []
    for app_id, entries in history.items():
        if not entries:
            continue
        df = pd.DataFrame(entries)
        df["app"] = app_id
        # Convert unix timestamp to datetime
        if "timestamp" in df.columns:
            df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        else:
            df["time"] = pd.NaT
        frames.append(df)

    if not frames:
        st.info("History entries are empty.")
        return

    combined = pd.concat(frames, ignore_index=True)

    # Line chart of limit over time, one trace per app
    fig = go.Figure()
    for app_id, grp in combined.groupby("app"):
        grp_sorted = grp.sort_values("time")
        hover_text = []
        for _, row in grp_sorted.iterrows():
            direction = row.get("direction", "")
            avg_lat = row.get("avg_latency_ms", float("nan"))
            p95_lat = row.get("p95_latency_ms", float("nan"))
            err = row.get("error_rate", float("nan"))
            hover_text.append(
                f"App: {app_id}<br>"
                f"Limit: {row.get('limit', 'N/A')}<br>"
                f"Direction: {direction}<br>"
                f"Avg Latency: {avg_lat:.1f} ms<br>"
                f"p95 Latency: {p95_lat:.1f} ms<br>"
                f"Error Rate: {err * 100:.2f} %"
            )
        fig.add_trace(
            go.Scatter(
                x=grp_sorted["time"],
                y=grp_sorted["limit"],
                mode="lines+markers",
                name=app_id,
                hovertext=hover_text,
                hoverinfo="text",
            )
        )

    fig.update_layout(
        xaxis_title="Time (UTC)",
        yaxis_title="Concurrency Limit",
        height=380,
        margin=dict(t=20, b=20, l=20, r=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Row 5 – Controller settings table
# ---------------------------------------------------------------------------

def render_controller_settings():
    ctrl = st.session_state.last_controller
    if not ctrl:
        return

    cfg = ctrl.get("controller", {})
    if not cfg:
        return

    st.subheader("⚙️ Controller Configuration")
    st.caption("Live data from gateway  •  GET /admin/controller/status")

    rows = [
        {"Setting": "Mode", "Value": cfg.get("mode", "N/A")},
        {"Setting": "Running", "Value": str(cfg.get("running", "N/A"))},
        {"Setting": "Interval (s)", "Value": cfg.get("interval_seconds", "N/A")},
        {"Setting": "Target Latency (ms)", "Value": cfg.get("target_latency_ms", "N/A")},
        {
            "Setting": "Error Rate Threshold",
            "Value": cfg.get("error_rate_threshold", "N/A"),
        },
        {
            "Setting": "Additive Increase Step",
            "Value": cfg.get("additive_increase_step", "N/A"),
        },
        {
            "Setting": "Multiplicative Decrease Factor",
            "Value": cfg.get("multiplicative_decrease_factor", "N/A"),
        },
    ]
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Render sidebar and get user settings
    gateway_url, api_key, auto_refresh, refresh_interval = render_sidebar()

    st.title("🛡️ Adaptive Traffic Protection – Live Dashboard")
    st.caption(
        "B.Tech Capstone Project  •  Adaptive Traffic Protection Without Static Rate Limits"
    )

    # Initial fetch (or on page reload)
    if st.session_state.last_refresh == 0.0:
        refresh_all_data(gateway_url, api_key)

    # Show error banner if gateway is unreachable
    if not st.session_state.gateway_ok:
        st.error(
            f"⚠️ {st.session_state.error_msg}\n\n"
            "Dashboard will keep retrying. Check that the gateway is running "
            f"and reachable at **{gateway_url}**."
        )
        # Still try to show stale data if any
        if not st.session_state.last_health:
            if auto_refresh:
                time.sleep(refresh_interval)
                refresh_all_data(gateway_url, api_key)
                st.rerun()
            return

    # Render all sections
    render_platform_status(gateway_url)
    st.divider()

    render_app_cards()
    st.divider()

    render_comparison_charts()
    st.divider()

    render_history_chart()
    st.divider()

    render_controller_settings()

    # Auto-refresh loop
    if auto_refresh:
        elapsed = time.time() - st.session_state.last_refresh
        remaining = max(0.0, refresh_interval - elapsed)
        progress_placeholder = st.empty()
        with progress_placeholder.container():
            st.caption(
                f"⏱ Next auto-refresh in {int(remaining)}s  "
                f"(interval: {refresh_interval}s)"
            )
        time.sleep(remaining)
        refresh_all_data(gateway_url, api_key)
        st.rerun()


if __name__ == "__main__":
    pass

main()
