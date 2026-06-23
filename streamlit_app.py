"""
streamlit_app.py
----------------
Streamlit Cloud entry point for KnowledgeMind.

Launch locally:  streamlit run streamlit_app.py
Deploy:          push to GitHub, connect to Streamlit Cloud,
                 add secrets via the dashboard (Settings → Secrets).

The original launcher.py + Gradio UI remain untouched for local / on-device use.
"""

import sys
from pathlib import Path

# Ensure repo root is on sys.path (handles both direct run and Streamlit Cloud).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Inject Streamlit secrets into os.environ BEFORE any get_config() call.
from config.cloud_config import inject_streamlit_secrets
inject_streamlit_secrets()

from config.store import reload_config
reload_config()

from ui.streamlit_ui import render_app
render_app()
