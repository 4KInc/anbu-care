"""Persistence for profiles, cases, and receipt chains.

Single-table layout (PK/SK), so one Firestore collection holds every entity and
a case's whole chain is one range read:

    PK                 SK                 entity
    PARENT#<pid>       PROFILE            ParentProfile
    PARENT#<pid>       DOC#<doc_id>       ParsedDocument
    CASE#<cid>         META               Case
    CASE#<cid>         RECEIPT#000000     Receipt
    CASE#<cid>         RECEIPT#000001     Receipt

Two backends behind one interface: Firestore (real / emulator) and in-memory
(tests, CI, and offline demo runs).
"""

from __future__ import annotations

import threading
from typing import Any, Protocol

from anbu_care.config import settings
from anbu_care.provenance.chain import Receipt

COLLECTION = "anbu"


def _doc_id(pk: str, sk: str) -> str:
    return f"{pk}__{sk}"


def receipt_sk(seq: int) -> str:
    return f"RECEIPT#{seq:06d}"


class Store(Protocol):
    def put(self, pk: str, sk: str, data: dict[str, Any]) -> None: ...
    def get(self, pk: str, sk: str) -> dict[str, Any] | None: ...
    def query_prefix(self, pk: str, sk_prefix: str) -> list[dict[str, Any]]: ...
    def query_by_sk(self, sk: str) -> list[dict[str, Any]]: ...
    def query_sk_prefix_across(self, sk_prefix: str) -> list[dict[str, Any]]: ...
    def delete(self, pk: str, sk: str) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.Lock()

    def put(self, pk: str, sk: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._data[(pk, sk)] = {**data, "pk": pk, "sk": sk}

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._data.get((pk, sk))
            return dict(row) if row else None

    def query_prefix(self, pk: str, sk_prefix: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(v) for (p, s), v in self._data.items() if p == pk and s.startswith(sk_prefix)]
        return sorted(rows, key=lambda r: r["sk"])


    def query_sk_prefix_across(self, sk_prefix: str) -> list[dict[str, Any]]:
        """Every row with this sort-key prefix, in any partition.

        A payment webhook names a provider order and nothing else, so this is
        the one lookup that cannot start from a partition key.
        """
        with self._lock:
            return [dict(v) for (_p, sk), v in self._data.items()
                    if sk.startswith(sk_prefix)]

    def query_by_sk(self, sk: str) -> list[dict[str, Any]]:
        """Every row with this exact sort key, across partitions.

        Only used by maintenance that has to walk one entity type — a backfill,
        not a request path. Nothing in the serving code fans out like this.
        """
        with self._lock:
            return [dict(v) for (_, s_), v in self._data.items() if s_ == sk]

    def delete(self, pk: str, sk: str) -> None:
        with self._lock:
            self._data.pop((pk, sk), None)


class FirestoreStore:
    def __init__(self, project: str | None = None, database: str | None = None) -> None:
        from google.cloud import firestore  # imported lazily so tests need no GCP deps

        cfg = settings()
        self._client = firestore.Client(
            project=project or cfg.project_id,
            database=database or cfg.firestore_database,
        )

    def put(self, pk: str, sk: str, data: dict[str, Any]) -> None:
        self._client.collection(COLLECTION).document(_doc_id(pk, sk)).set(
            {**data, "pk": pk, "sk": sk}
        )

    def get(self, pk: str, sk: str) -> dict[str, Any] | None:
        snap = self._client.collection(COLLECTION).document(_doc_id(pk, sk)).get()
        return snap.to_dict() if snap.exists else None

    def query_prefix(self, pk: str, sk_prefix: str) -> list[dict[str, Any]]:
        # Range read on sk within one partition. U+F8FF is the conventional
        # Firestore high sentinel for a prefix scan, and this pk-equality +
        # sk-range + order-by combination is what infra/firestore.indexes.json
        # exists for.
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = (
            self._client.collection(COLLECTION)
            .where(filter=FieldFilter("pk", "==", pk))
            .where(filter=FieldFilter("sk", ">=", sk_prefix))
            .where(filter=FieldFilter("sk", "<", sk_prefix + ""))
            .order_by("sk")
        )
        return [doc.to_dict() for doc in query.stream()]

    def query_by_sk(self, sk: str) -> list[dict[str, Any]]:
        """Cross-partition read on one sort key. Maintenance only."""
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = self._client.collection(COLLECTION).where(
            filter=FieldFilter("sk", "==", sk))
        return [doc.to_dict() for doc in query.stream()]

    def query_sk_prefix_across(self, sk_prefix: str) -> list[dict[str, Any]]:
        """Every row with this sort-key prefix, across every partition.

        No pk equality, so this is a range read on sk alone. It exists for the
        payment webhook, which knows a provider order id and nothing about our
        cases. At demo scale that is free; at real scale it wants an index on
        the reference itself rather than a cleverer scan.
        """
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = (
            self._client.collection(COLLECTION)
            .where(filter=FieldFilter("sk", ">=", sk_prefix))
            .where(filter=FieldFilter("sk", "<", sk_prefix + "\uf8ff"))
            .order_by("sk")
        )
        return [doc.to_dict() for doc in query.stream()]

    def delete(self, pk: str, sk: str) -> None:
        """Remove one document.

        Deliberately not surfaced on the service layer: receipts are
        append-only, and a delete path reachable from case code would undermine
        the chain. This exists so health probes can clean up after themselves
        rather than littering the ledger.
        """
        self._client.collection(COLLECTION).document(_doc_id(pk, sk)).delete()


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = MemoryStore() if settings().use_memory_store else FirestoreStore()
        return _store


def set_store(store: Store) -> None:
    """Override the backend — used by tests and the offline demo script."""
    global _store
    with _store_lock:
        _store = store


# --------------------------------------------------------------------------
# Receipt-chain helpers
# --------------------------------------------------------------------------


# A chain is a sequence of receipts about one subject. Almost always that
# subject is a case, but not always: a wellbeing check-in belongs to a parent
# and usually arrives with no case open at all, which is the healthy state.
# The chain core never knew what a case was — seq, prev_hash and verification
# carry no case knowledge — so a second subject is a partition key, not a
# refactor.
CASE_SUBJECT = "CASE#"
PARENT_SUBJECT = "PARENT#"


def load_receipts(
    subject_id: str, store: Store | None = None, subject: str = CASE_SUBJECT
) -> list[Receipt]:
    store = store or get_store()
    rows = store.query_prefix(f"{subject}{subject_id}", "RECEIPT#")
    return [Receipt.model_validate(_strip_keys(r)) for r in rows]


def save_receipt(
    receipt: Receipt, store: Store | None = None, subject: str = CASE_SUBJECT
) -> None:
    store = store or get_store()
    store.put(
        f"{subject}{receipt.case_id}",
        receipt_sk(receipt.seq),
        receipt.model_dump(mode="json"),
    )


def _strip_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in {"pk", "sk"}}
