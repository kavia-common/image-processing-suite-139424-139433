#!/bin/bash
cd /home/kavia/workspace/code-generation/image-processing-suite-139424-139433/backend_api
source venv/bin/activate
flake8 .
LINT_EXIT_CODE=$?
if [ $LINT_EXIT_CODE -ne 0 ]; then
  exit 1
fi

