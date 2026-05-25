"""Local helpers for poking at the centaur Postgres.

    import centaur_db as db
    conn = db.connect()
    db.query(conn, "SELECT count(*) FROM api_keys")
"""

from __future__ import annotations

import atexit
import base64
import os
import socket
import subprocess
import time
from dataclasses import dataclass

import pandas as pd
import psycopg

NS_DEFAULT = "centaur-system"
RELEASE_DEFAULT = "centaur"
DB_USER = "tempo"
DB_NAME = "ai_v2"
DEFAULT_PORT = 5432
INFRA_SECRET = "centaur-infra-env"
PASSWORD_KEY = "POSTGRES_PASSWORD"

__all__ = [
    "ClusterRef",
    "cluster_from_env",
    "connect",
    "ensure_port_forward",
    "execute",
    "query",
]


@dataclass(frozen=True, slots=True)
class ClusterRef:
    """The Kubernetes deployment we're talking to."""

    namespace: str
    release: str

    @property
    def postgres_service(self) -> str:
        return f"{self.release}-centaur-postgres"


def cluster_from_env() -> ClusterRef:
    """Resolve the active cluster ref from CENTAUR_NAMESPACE / CENTAUR_RELEASE."""
    return ClusterRef(
        namespace=os.getenv("CENTAUR_NAMESPACE", NS_DEFAULT),
        release=os.getenv("CENTAUR_RELEASE", RELEASE_DEFAULT),
    )


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def ensure_port_forward(
    service: str,
    remote_port: int,
    local_port: int,
    cluster: ClusterRef | None = None,
    timeout_s: float = 5.0,
) -> subprocess.Popen[bytes] | None:
    """Start a kubectl port-forward to `svc/<service>` if `local_port` isn't bound.

    Returns the Popen handle when a new forward was started (registered with
    atexit for cleanup), or None if an existing forward is reused.
    """
    if _port_in_use(local_port):
        return None
    cluster = cluster or cluster_from_env()
    pf = subprocess.Popen(
        [
            "kubectl", "port-forward",
            "-n", cluster.namespace,
            f"svc/{service}",
            f"{local_port}:{remote_port}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _port_in_use(local_port):
            atexit.register(pf.terminate)
            return pf
        if pf.poll() is not None:
            raise RuntimeError(
                f"kubectl port-forward to {service} exited with code {pf.returncode}"
            )
        time.sleep(0.1)
    pf.terminate()
    raise RuntimeError(f"port-forward to {service} not ready after {timeout_s}s")


def _fetch_password(cluster: ClusterRef) -> str:
    """Read POSTGRES_PASSWORD out of the centaur-infra-env Secret."""
    try:
        raw = subprocess.check_output(
            [
                "kubectl", "get", "secret",
                "-n", cluster.namespace, INFRA_SECRET,
                "-o", f"jsonpath={{.data.{PASSWORD_KEY}}}",
            ],
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode().strip()
        raise RuntimeError(
            f"could not read {INFRA_SECRET}/{PASSWORD_KEY} in {cluster.namespace!r}: {detail}"
        ) from exc
    if not raw:
        raise RuntimeError(f"{INFRA_SECRET} has no {PASSWORD_KEY} entry")
    return base64.b64decode(raw).decode()


def connect(
    cluster: ClusterRef | None = None,
    port: int = DEFAULT_PORT,
) -> psycopg.Connection:
    """Open an autocommit psycopg connection to the centaur Postgres.

    Port-forwards Postgres into localhost if `port` isn't already bound;
    cleanup is registered with atexit.
    """
    cluster = cluster or cluster_from_env()
    ensure_port_forward(cluster.postgres_service, 5432, port, cluster)
    conn = psycopg.connect(
        host="localhost",
        port=port,
        dbname=DB_NAME,
        user=DB_USER,
        password=_fetch_password(cluster),
    )
    conn.autocommit = True
    return conn


def query(
    conn: psycopg.Connection,
    sql: str,
    params: tuple[object, ...] | None = None,
) -> pd.DataFrame:
    """Execute ``sql`` on ``conn`` and return the result as a pandas DataFrame."""
    with conn.cursor() as cur:
        cur.execute(sql, params)  # pyright: ignore[reportArgumentType]
        if cur.description is None:
            return pd.DataFrame()
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)


def execute(
    conn: psycopg.Connection,
    sql: str,
    params: tuple[object, ...] | None = None,
) -> int:
    """Execute ``sql`` (INSERT / UPDATE / DELETE) and return the affected row count."""
    with conn.cursor() as cur:
        cur.execute(sql, params)  # pyright: ignore[reportArgumentType]
        return cur.rowcount
