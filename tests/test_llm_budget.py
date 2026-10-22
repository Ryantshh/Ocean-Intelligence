"""Keep provider completion defaults from exhausting a fresh request's context."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ai_platform.backend import llm


class CompletionBudgetTests(unittest.IsolatedAsyncioTestCase):
    def test_agent_sends_explicit_completion_budget(self):
        with patch.dict('os.environ', {'GROQ_API_KEY': 'test-key'}):
            model = llm.get_chat_model()
        self.assertEqual(model._default_params['max_completion_tokens'], llm.MAX_COMPLETION_TOKENS)
        self.assertLess(llm.MAX_COMPLETION_TOKENS, llm.MAX_INPUT_TOKENS * .2)

    async def test_plain_chat_uses_the_same_budget(self):
        async def chunks():
            if False:
                yield None
        create = AsyncMock(return_value=chunks())
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(llm, 'get_client', return_value=client):
            self.assertEqual([chunk async for chunk in llm.stream_chat([{'role': 'user', 'content': 'Hello'}])], [])
        self.assertEqual(create.await_args.kwargs['max_completion_tokens'], llm.MAX_COMPLETION_TOKENS)
