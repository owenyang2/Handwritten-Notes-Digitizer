"""Property coverage for ordering, retries, conflicts, tombstones, and compaction."""

from __future__ import annotations

import uuid

from hypothesis import given, settings, strategies as st
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from app.data import Base, DocumentOperation, RepositoryFactory, User, create_all, create_db_engine, create_session_factory
from app.services.auth import hash_password
from app.services.sync_api import SyncOperationRequest, SyncPushRequest, push_sync_operations


@settings(max_examples=25, deadline=None)
@given(actions=st.lists(
    st.sampled_from(["rename", "reorder", "trash", "restore", "duplicate", "conflict"]),
    min_size=1,
    max_size=12,
))
def test_sync_state_machine_preserves_log_invariants(actions: list[str]) -> None:
    engine = create_db_engine("sqlite://", poolclass=StaticPool)
    create_all(engine)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            repos = RepositoryFactory(session)
            user = repos.users.add(
                User(
                    username=f"property-{uuid.uuid4().hex}",
                    email=f"{uuid.uuid4().hex}@example.com",
                    password_hash=hash_password("test-password"),
                )
            )
            document = repos.documents.create(user.user_id, "property note")
            session.commit()
            document_id = document.document_id
            seen: list[SyncOperationRequest] = []
            accepted_sequences: list[int] = []
            conflict_count = 0

            for index, action in enumerate(actions):
                current = repos.documents.get(user.user_id, document_id)
                assert current is not None
                if action == "duplicate" and seen:
                    operation = seen[0]
                else:
                    base = 0 if action == "conflict" and current.sync_version > 0 else current.sync_version
                    operation_type = {
                        "rename": "document.rename",
                        "reorder": "document.reorder",
                        "trash": "document.trash",
                        "restore": "document.restore",
                        "conflict": "document.rename",
                    }.get(action, "document.rename")
                    payload = (
                        {"title": f"title-{index}"}
                        if operation_type == "document.rename"
                        else ({"position": index} if operation_type == "document.reorder" else {})
                    )
                    operation = SyncOperationRequest(
                        client_id="property-client",
                        client_operation_id=uuid.uuid4(),
                        document_id=document_id,
                        base_sync_version=base,
                        entity_type="document",
                        entity_id=str(document_id),
                        operation_type=operation_type,
                        payload=payload,
                    )
                    seen.append(operation)

                response = push_sync_operations(
                    SyncPushRequest(operations=[operation]),
                    user=user,
                    repos=repos,
                )
                if response.accepted:
                    accepted_sequences.append(response.accepted[0].server_sequence)
                if response.conflicts:
                    conflict_count += 1

            rows = list(
                repos.sync.list_operations(user.user_id, cursor=0, limit=100)
            )
            assert [row.server_sequence for row in rows] == sorted(set(accepted_sequences))
            assert len(rows) <= len({operation.client_operation_id for operation in seen})
            assert conflict_count == len(
                list(repos.sync.list_conflicts(user.user_id))
            )

            client = repos.sync_clients.register(
                user.user_id, "property-client", platform="test"
            )
            session.commit()
            current_cursor = repos.sync.current_cursor()
            repos.sync_clients.acknowledge(user.user_id, client.client_id, current_cursor)
            session.commit()
            _compacted, _snapshots, floor = repos.sync.compact_operations(
                user.user_id, through_cursor=current_cursor
            )
            session.commit()
            assert floor == current_cursor

            tombstones = list(
                session.scalars(
                    select(DocumentOperation).where(
                        DocumentOperation.user_id == user.user_id,
                        DocumentOperation.is_tombstone.is_(True),
                    )
                ).all()
            )
            assert all(row.compacted_at is None for row in tombstones)
    finally:
        Base.metadata.drop_all(engine)
