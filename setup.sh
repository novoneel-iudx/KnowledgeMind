#!/bin/bash
# Streamlit Cloud post-install hook — downloads the spaCy model.
# This runs automatically after pip install -r requirements.txt.
python -m spacy download en_core_web_sm
