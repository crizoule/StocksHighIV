"""Optional machine-readable progress for the local launcher; CLI output stays readable."""
import json
import os


def emit(**fields):
    if os.environ.get('HIGHIV_PROGRESS') == '1':
        print('@highiv ' + json.dumps(fields), flush=True)
