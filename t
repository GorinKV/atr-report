#!/bin/bash
cd /opt/app
source venv/bin/activate
pytest "$@"
