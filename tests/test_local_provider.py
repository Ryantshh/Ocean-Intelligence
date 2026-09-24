"""Shared graph/UI integration contracts. Model responses here are test doubles."""

import asyncio

from ai_platform.backend import context, llm


def test_concurrent_local_profiles_do_not_leak(monkeypatch):
    calls = []

    async def generate(profile, messages, schema):
        await asyncio.sleep(0)
        calls.append((profile, messages, schema))
        return {
            "content": profile,
            "usage": {"prompt_tokens": 123, "completion_tokens": 7},
        }

    monkeypatch.setattr(llm.local_qwen, "generate_completion", generate)

    async def run(profile):
        token = llm.use_local(profile)
        try:
            message = [
                {"role": "system", "content": "x" * 15000},
                {"role": "user", "content": "船" * 2000},
            ]
            result = await llm.get_client().chat.completions.create(
                messages=message,
                response_format={"json_schema": {"schema": {"type": "object"}}},
            )
            assert result.choices[0].message.content == profile
            assert result.usage.prompt_tokens == 123
            assert result.usage.completion_tokens == 7
            assert len(message[0]["content"]) == 15000
        finally:
            llm.reset_provider(token)

    async def run_all():
        await asyncio.gather(run("llama-1b"), run("llama-3b-qlora"), run("qwen-0.5b"))

    asyncio.run(run_all())
    assert llm._local_profile.get() is None
    assert len(calls) == 3
    assert all(len(messages[0]["content"]) == 15000 for _, messages, _ in calls)


def test_local_usage_stream_and_context_are_separate_from_hosted(monkeypatch):
    async def generate(*_):
        return {
            "content": "answer",
            "usage": {"prompt_tokens": 101, "completion_tokens": 9},
        }

    monkeypatch.setattr(llm.local_qwen, "generate_completion", generate)
    monkeypatch.setattr(context, "_measured_overhead", {})
    hosted = context.usable_tokens()

    async def run():
        token = llm.use_local("llama-1b")
        try:
            context.record_prompt_tokens(100, [])
            assert context.usable_tokens() == 3996
            usage = {}
            content = "".join(
                [chunk async for chunk in llm.stream_chat([], usage=usage)]
            )
            assert content == "answer"
            assert usage == {
                "prompt_tokens": 101,
                "completion_tokens": 9,
                "reasoning_tokens": 0,
            }
        finally:
            llm.reset_provider(token)

    asyncio.run(run())
    assert context.usable_tokens() == hosted


def test_ui_profiles_and_message_routing_share_agent(monkeypatch):
    from ai_platform.app import cl_app

    profiles = asyncio.run(cl_app.list_chat_profiles(None, None))
    names = {p.name for p in profiles}
    assert names == {"agent", "plain-model", *llm.local_qwen.SELECTABLE_PROFILES}
    calls = []

    async def agent(question, local_profile=None):
        calls.append((question, local_profile))

    monkeypatch.setattr(cl_app, "run_agent", agent)
    for profile in llm.local_qwen.PROFILES:
        monkeypatch.setattr(
            cl_app.cl.user_session,
            "get",
            lambda key, profile=profile: profile if key == "chat_profile" else None,
        )
        asyncio.run(
            cl_app.handle_message(type("Message", (), {"content": "Question"})())
        )
    assert calls == [("Question", profile) for profile in llm.local_qwen.PROFILES]


def test_shared_graph_runs_extraction_retrieval_and_answer_for_llama(monkeypatch):
    from ai_platform.backend import nodes
    from ai_platform.backend.graph import graph
    from ai_platform.backend.tables import Extraction, TonnageRequest, tonnage

    extraction = Extraction(
        request=TonnageRequest(
            target="tonnage",
            filters=tonnage.Filters(),
            semantic=tonnage.SemanticTerms(),
        ),
        needs_clarification=False,
        clarifying_question=None,
    )
    calls = []
    queries = []

    async def generate(profile, messages, schema):
        calls.append((profile, schema))
        return {
            "content": extraction.model_dump_json() if schema else "No vessels found.",
            "usage": {"prompt_tokens": 500, "completion_tokens": 40},
        }

    async def fetch(sql, params):
        queries.append((sql, params))
        return []

    monkeypatch.setattr(llm.local_qwen, "generate_completion", generate)
    monkeypatch.setattr(nodes, "fetch_rows", fetch)

    async def run():
        token = llm.use_local("llama-3b-qlora")
        try:
            return await graph.with_config({"callbacks": []}).ainvoke(
                {"question": "List vessels", "history": []}
            )
        finally:
            llm.reset_provider(token)

    result = asyncio.run(run())
    assert result["target"] == "tonnage" and result["rows"] == []
    assert len(queries) == 1 and len(calls) == 2
    assert calls[0][0] == calls[1][0] == "llama-3b-qlora"
    assert calls[0][1]["json_schema"]["name"] == "Extraction"
    assert calls[1][1] is None
