"""Durable SQLite state and outbox for the mesh-alerts service.

The store prevents duplicate enqueueing across restarts. A transport result that
was interrupted while sending is marked uncertain and is never retried silently.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


VALID_MESSAGE_TYPES = {"Alert", "Update", "Cancel"}
VALID_OUTBOX_STATES = {"pending", "sending", "sent", "uncertain", "failed"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_aware_datetime(value: str | None, field: str, *, optional: bool = False) -> datetime | None:
    if value is None or value == "":
        if optional:
            return None
        raise ValueError(f"{field} must be a timezone-aware ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"invalid {field} timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AlertStateStore:
    """SQLite-backed CAP lifecycle state and persistent outbound queue.

    Use one store per service instance. SQLite transactions serialize concurrent
    writers. The database should live on a local persistent filesystem, not in
    a temporary directory or a network filesystem.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        if str(database_path) != ":memory:":
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._database = str(database_path)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cap_alerts (
                    sender TEXT NOT NULL,
                    identifier TEXT NOT NULL,
                    sent TEXT NOT NULL,
                    msg_type TEXT NOT NULL CHECK (msg_type IN ('Alert','Update','Cancel')),
                    content_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    expires_at TEXT,
                    matches_region INTEGER NOT NULL CHECK (matches_region IN (0,1)),
                    active INTEGER NOT NULL CHECK (active IN (0,1)),
                    lifecycle_state TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    PRIMARY KEY (sender, identifier)
                );

                CREATE TABLE IF NOT EXISTS cap_references (
                    alert_sender TEXT NOT NULL,
                    alert_identifier TEXT NOT NULL,
                    referenced_sender TEXT NOT NULL,
                    referenced_identifier TEXT NOT NULL,
                    PRIMARY KEY (
                        alert_sender, alert_identifier,
                        referenced_sender, referenced_identifier
                    )
                );

                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_key TEXT NOT NULL UNIQUE,
                    sender TEXT NOT NULL,
                    identifier TEXT NOT NULL,
                    action TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('pending','sending','sent','uncertain','failed')
                    ),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sent_at TEXT,
                    last_error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_outbox_pending
                    ON outbox(status, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_alerts_active
                    ON cap_alerts(active, expires_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def process_cap_alert(
        self,
        *,
        sender: str,
        identifier: str,
        sent: str,
        msg_type: str,
        payload: dict[str, Any],
        references: Sequence[tuple[str, str]] = (),
        expires_at: str | None = None,
        matches_region: bool,
        message_text: str | None = None,
        destination: str = "^all",
        now: datetime | None = None,
    ) -> str:
        """Persist one CAP message and enqueue its notification at most once.

        Returns one of: 'queued', 'recorded', 'duplicate', 'conflict',
        'cancelled', or 'expired'. CAP references are (sender, identifier)
        pairs; the original reference timestamp can be stored in payload.
        """
        sender = sender.strip()
        identifier = identifier.strip()
        if not sender or not identifier:
            raise ValueError("sender and identifier are required")
        if msg_type not in VALID_MESSAGE_TYPES:
            raise ValueError(f"unsupported CAP msgType: {msg_type!r}")
        sent_dt = _parse_aware_datetime(sent, "sent")
        expires_dt = _parse_aware_datetime(expires_at, "expires", optional=True)
        now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if sent_dt > now_dt:
            raise ValueError("CAP sent timestamp is in the future")
        now_text = now_dt.isoformat()
        normalized_references = []
        for ref_sender, ref_identifier in references:
            ref_sender = ref_sender.strip()
            ref_identifier = ref_identifier.strip()
            if not ref_sender or not ref_identifier:
                raise ValueError("CAP references require sender and identifier")
            normalized_references.append((ref_sender, ref_identifier))

        payload_json = _json_dump(payload)
        canonical = _json_dump({
            "sender": sender,
            "identifier": identifier,
            "sent": sent_dt.isoformat(),
            "msg_type": msg_type,
            "payload": payload,
            "references": sorted(normalized_references),
            "expires_at": expires_dt.isoformat() if expires_dt else None,
            "matches_region": bool(matches_region),
        })
        content_hash = _digest(canonical)
        is_expired = expires_dt is not None and expires_dt <= now_dt

        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT content_hash FROM cap_alerts WHERE sender=? AND identifier=?",
                (sender, identifier),
            ).fetchone()
            if existing:
                return "duplicate" if existing["content_hash"] == content_hash else "conflict"

            affected_local_alert = False
            if msg_type in ("Update", "Cancel"):
                for ref_sender, ref_identifier in normalized_references:
                    previous = connection.execute(
                        """SELECT matches_region, lifecycle_state
                           FROM cap_alerts WHERE sender=? AND identifier=?""",
                        (ref_sender, ref_identifier),
                    ).fetchone()
                    if previous and previous["matches_region"]:
                        affected_local_alert = True
                    connection.execute(
                        """UPDATE cap_alerts
                           SET active=0, lifecycle_state=?
                           WHERE sender=? AND identifier=?""",
                        (
                            "cancelled" if msg_type == "Cancel" else "superseded",
                            ref_sender,
                            ref_identifier,
                        ),
                    )

            active = not is_expired and msg_type != "Cancel"
            lifecycle_state = (
                "expired" if is_expired else
                "cancelled" if msg_type == "Cancel" else
                "active" if active else "inactive"
            )
            connection.execute(
                """INSERT INTO cap_alerts
                   (sender, identifier, sent, msg_type, content_hash, payload_json,
                    expires_at, matches_region, active, lifecycle_state, received_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sender, identifier, sent_dt.isoformat(), msg_type, content_hash,
                    payload_json, expires_dt.isoformat() if expires_dt else None,
                    int(bool(matches_region)), int(active), lifecycle_state, now_text,
                ),
            )
            for ref_sender, ref_identifier in normalized_references:
                connection.execute(
                    """INSERT INTO cap_references
                       (alert_sender, alert_identifier, referenced_sender, referenced_identifier)
                       VALUES (?, ?, ?, ?)""",
                    (sender, identifier, ref_sender, ref_identifier),
                )

            if is_expired:
                return "expired"

            should_queue = (
                bool(message_text)
                and (
                    (msg_type in ("Alert", "Update") and bool(matches_region))
                    or (msg_type == "Cancel" and affected_local_alert)
                )
            )
            if should_queue:
                action = msg_type.lower()
                message_key = _digest(f"{sender}\n{identifier}\n{action}")
                connection.execute(
                    """INSERT OR IGNORE INTO outbox
                       (message_key, sender, identifier, action, destination,
                        message_text, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                    (
                        message_key, sender, identifier, action, destination,
                        message_text, now_text, now_text,
                    ),
                )
                return "queued" if connection.execute(
                    "SELECT changes()"
                ).fetchone()[0] else "recorded"
            return "cancelled" if msg_type == "Cancel" else "recorded"

    def claim_next(self) -> dict[str, Any] | None:
        """Atomically claim the oldest pending message for a transport attempt."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM outbox WHERE status='pending' ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                """UPDATE outbox SET status='sending', attempts=attempts+1, updated_at=?
                   WHERE id=? AND status='pending'""",
                (_utc_now(), row["id"]),
            ).rowcount
            if changed != 1:
                return None
            result = dict(row)
            result["status"] = "sending"
            result["attempts"] += 1
            return result

    def mark_sent(self, outbox_id: int) -> None:
        with self._transaction() as connection:
            changed = connection.execute(
                """UPDATE outbox SET status='sent', sent_at=?, updated_at=?, last_error=NULL
                   WHERE id=? AND status='sending'""",
                (_utc_now(), _utc_now(), outbox_id),
            ).rowcount
            if changed != 1:
                raise ValueError("outbox item is not in sending state")

    def mark_failed(self, outbox_id: int, error: str, *, retryable: bool) -> None:
        status = "pending" if retryable else "failed"
        with self._transaction() as connection:
            changed = connection.execute(
                """UPDATE outbox SET status=?, last_error=?, updated_at=?
                   WHERE id=? AND status='sending'""",
                (status, str(error)[:2000], _utc_now(), outbox_id),
            ).rowcount
            if changed != 1:
                raise ValueError("outbox item is not in sending state")

    def recover_interrupted_sends(self) -> int:
        """Mark interrupted sends uncertain; do not silently resend them."""
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE outbox SET status='uncertain', updated_at=?,
                       last_error='Process ended before transport result was recorded'
                   WHERE status='sending'""",
                (_utc_now(),),
            )
            return cursor.rowcount

    def resolve_uncertain(self, outbox_id: int, *, resolution: str) -> None:
        """Resolve an uncertain send explicitly: 'sent' or 'retry'."""
        if resolution not in {"sent", "retry"}:
            raise ValueError("resolution must be 'sent' or 'retry'")
        status = "sent" if resolution == "sent" else "pending"
        with self._transaction() as connection:
            changed = connection.execute(
                """UPDATE outbox SET status=?, sent_at=?, updated_at=?, last_error=NULL
                   WHERE id=? AND status='uncertain'""",
                (status, _utc_now() if status == "sent" else None, _utc_now(), outbox_id),
            ).rowcount
            if changed != 1:
                raise ValueError("outbox item is not in uncertain state")

    def list_outbox(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in VALID_OUTBOX_STATES:
            raise ValueError(f"invalid outbox status: {status!r}")
        with self._connection() as connection:
            if status:
                rows = connection.execute(
                    "SELECT * FROM outbox WHERE status=? ORDER BY id", (status,)
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM outbox ORDER BY id").fetchall()
            return [dict(row) for row in rows]

    def get_alert(self, sender: str, identifier: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM cap_alerts WHERE sender=? AND identifier=?",
                (sender, identifier),
            ).fetchone()
            return dict(row) if row else None

    def close(self) -> None:
        """Compatibility method; connections are opened per operation."""
        return None
