from __future__ import annotations

from yuxi.repositories.conversation_repository import ConversationRepository


async def test_lock_source_scope_uses_transaction_advisory_lock():
    calls = []

    class FakeSession:
        async def execute(self, statement, parameters):
            calls.append((str(statement), parameters))

    repository = ConversationRepository(FakeSession())

    await repository.lock_source_scope(
        uid="user-1",
        agent_id="research-copilot",
        source="research_copilot",
        scope_key="kb-1:project-1",
    )

    assert calls == [
        (
            "SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))",
            {
                "lock_key": '{"agent_id":"research-copilot","scope_key":"kb-1:project-1",'
                '"source":"research_copilot","uid":"user-1"}'
            },
        )
    ]
