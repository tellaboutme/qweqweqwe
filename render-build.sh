#!/bin/bash

# Render build script for Python 3.14 pydantic-core fix

# Configure cargo to work in read-only environment
export CARGO_REGISTRY_READ_ONLY=1
export CARGO_NET_OFFLINE=false
export CARGO_HTTP_MULTIPLEXING=false
export CARGO_NET_GIT_FETCH_WITH_CLI=true

# Upgrade pip first
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
