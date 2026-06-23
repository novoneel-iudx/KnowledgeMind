"""
config/cloud_config.py
----------------------
Bridges Streamlit secrets into os.environ so the existing _ENV_OVERRIDES
mechanism in config/store.py picks them up without modification.

Call inject_streamlit_secrets() at the very top of streamlit_app.py,
before any get_config() call.
"""

from __future__ import annotations

import os


def inject_streamlit_secrets() -> None:
    """
    Copy st.secrets into os.environ so load_config() ENV overrides work.
    No-ops silently when not running under Streamlit or when secrets are absent.
    """
    try:
        import streamlit as st
        for key, value in st.secrets.items():
            if isinstance(value, str) and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass
