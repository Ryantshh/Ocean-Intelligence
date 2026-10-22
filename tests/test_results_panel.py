"""Panel lifecycle checks without starting the chat server or model."""
import ast
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock


class ResultsPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_outputs_preserve_message_ownership_and_are_bounded(self):
        source = Path('ai_platform/app/cl_app.py').read_text()
        function = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_show_panel')
        session = {}
        sidebar = SimpleNamespace(set_title=AsyncMock(), set_elements=AsyncMock())
        counter = iter(range(100))
        cl = SimpleNamespace(
            CustomElement=lambda **kwargs: SimpleNamespace(id=str(next(counter)), **kwargs),
            user_session=SimpleNamespace(get=session.get, set=session.__setitem__),
            ElementSidebar=sidebar,
        )
        namespace = {'cl': cl, 'Any': Any, 'cast': cast, 'time': time, 'RESULTS_ELEMENT': 'Results'}
        function.returns = None
        for arg in function.args.args:
            arg.annotation = None
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<panel>', 'exec'), namespace)  # noqa: S102 - trusted local function under test
        old_reply = SimpleNamespace(elements=[])
        await namespace['_show_panel']([{'rows': [[1]]}], old_reply, 'a')
        await namespace['_show_panel']([{'rows': [[2]]}], old_reply, 'b')
        self.assertEqual(len(old_reply.elements), 2)
        new_reply = SimpleNamespace(elements=[])
        for i in range(13):
            await namespace['_show_panel']([{'rows': [[i]]}], new_reply, str(i))
        self.assertEqual(len(session['result_panels']), 12)
        self.assertEqual(len(old_reply.elements), 2)
        self.assertEqual(sidebar.set_elements.await_args.args[0], session['result_panels'])
        self.assertEqual(old_reply.elements[0].props['sets'][0]['rows'], [[1]])
