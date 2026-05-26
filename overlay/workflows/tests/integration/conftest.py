"""Integration tests for paper workflows — gated on CENTAUR_TEST_DATABASE_URL.

Exercises save_papers against a real Postgres with the centaur schema
and pg_search migrations applied. The Semantic Scholar HTTP client is
still mocked because flaky external calls don't add coverage we don't
already have in the unit suite.

The DSN re-basing, ``CREATE DATABASE`` guard, migration apply pass, and
per-test ``TRUNCATE`` fixtures all live in ``centaur_lab.testing`` so
they stay in sync with the sibling
``overlay/tools/semantic_scholar/tests/integration/conftest.py``.

Recommended local setup:

    kubectl port-forward -n centaur-system svc/centaur-centaur-postgres 5432:5432 &
    PGPASSWORD=$(kubectl get secret -n centaur-system centaur-infra-env \\
        -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
    export CENTAUR_TEST_DATABASE_URL="postgres://tempo:$PGPASSWORD@localhost:5432/ai_v2"
    just overlay::test-workflows-integration
"""

pytest_plugins = ["centaur_lab.testing"]
