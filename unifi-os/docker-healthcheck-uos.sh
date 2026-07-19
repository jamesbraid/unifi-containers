#!/bin/bash
# Healthy = the ucore public API answers real JSON on :443. During boot
# nginx may be up while the API behind it is not; requiring the /api/system
# payload (isSetup field) means "the controller API is answering".
curl -ks --max-time 5 https://localhost/api/system | grep -q '"isSetup"'
