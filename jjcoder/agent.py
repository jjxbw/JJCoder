"""Core agent loop.
user message -> LLM(with tools) -> tool calls? ->execute -> loop ->text reply? -> return to user
"""

import concurrent.futures
import inspect

from .context import ContextManager
from .llm import LLM
from .permissions import Permission
from .prompt import PLAN_MODE_PROMPT, system_prompt
from .tools import ALL_TOOLS
from .tools.agent import AgentTool
from .tools.base import Tool
from .tools.todo import TodoWriteTool

class Agent:
    def __init__(
            self,
            llm: LLM,
            tools: list[Tool] | None = None,
            max_context_tokens: int = 128_000,
            max_rounds: int = 50,
            permission=None,
            hooks=None,
    ):
        self.llm = llm
        self.tools = tools if tools is not None else ALL_TOOLS
        self.permission = permission 
        self.hooks = hooks 
        self._tool_by_name = {t.name: t for t in self.tools}
        self.messages: list[dict] = []
        self.context = ContextManager(max_tokens=max_context_tokens)
        self.max_rounds = max_rounds
        self._system = system_prompt(self.tools)
        self.plan_mode = False

        for t in self.tools:
            if isinstance(t, AgentTool):
                t._parent_agent = self

        self._todo = next((t for t in self.tools if isinstance(t, TodoWriteTool)), None)

    def _full_message(self) -> list[dict]:
        system = self._system
        if self.plan_mode:
            system += "\n\n" + PLAN_MODE_PROMPT

        if self._todo is not None:
            rendered = self._todo.render()
            if rendered:
                system += "\n\n# Current task list\n" + rendered
        return [{"role": "system", "content": system}] + self.messages

    def _tool_schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools]

    def chat(self, user_input: str, on_token = None, on_tool=None,on_reasoning = None) -> str:
        self.messages.append({"role": "user", "content": user_input})
        self.context.maybe_compress(self.messages, self.llm)

        for _ in range(self.max_rounds):
            resp = self.llm.chat(
                messages = self._full_message(),
                tools = self._tool_schemas(),
                on_token = on_token,
                on_reasoning = on_reasoning,
            )

            if not resp.tool_calls:
                self.messages.append(resp.message)
                return resp.content
            self.messages.append(resp.message)

            try:
                if len(resp.tool_calls) == 1:
                    tc = resp.tool_calls[0]
                    if on_tool:
                        on_tool(tc.name, tc.arguments)
                    result = self._pre_hooks(tc) or self._permit(tc)
                    if result is None:
                        result = self._exec_tool(tc)
                        self._post_hooks(tc, result)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                else:
                    results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                    for tc. result in zip(resp.tool_calls, results):
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
            except KeyboardInterrupt:
                self._answer_pending_tool_calls(resp.tool_calls)
                raise

            self.context.maybe_compress(self.messages, self.llm)

        return "(reached maximum tool-call rounds)"

    def _pre_hooks(self, tc) -> str | None:
        if self.hooks is None:
            return None
        return self.hooks.run_pre(tc.name, tc.arguments)
                                  
    def _post_hooks(self, tc, result: str):
        if self.hooks is not None:
            self.hooks.run_post(tc.name, tc.arguments, result)

    def _permit(self, tc) -> str | None:
        if self.plan_mode and tc.name not in Permission.READ_ONLY:
            return (
                "Plan mode is on, so this call was refused: plan mode is "
                "read-only. Do not retry it. Keep investigating with the "
                "read-only tools, then present the plan and stop. The user can "
                'approve it by typing "approve", or exit plan mode with /plan.'
            )
        if self.permission is None:
            return None
        return self.permission.check(tc.name, tc.arguments)

    def _exec_tool(self, tc) -> str:
        tool = self._tool_by_name.get(tc.name)
        if tool is None:
            return f"Error: unknown tool '{tc.name}'"
        try:
            inspect.signature(tool.execute).bind(**tc.arguments)
        except TypeError as e:
            return f"Error: bad arguments for tool {tc.name}: {e}"

        try:
            result = tool.execute(**tc.arguments)
        except Exception as e:
            return f"Error executing {tc.name}: {e}"

    def _exec_tools_parallel(self, tool_calls, on_tool=None) -> list[str]:

        from .tools.bash import get_tracked_cwd, set_tracked_cwd

        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)

                results = [self._pre_hooks(tc) or self._permit(tc) for tc in tool_calls]

        session_cwd = get_tracked_cwd()
        with concurrent.futures.ThreadPoolExecutor(max_workers = 9) as pool:
            def run(i, tc, base_cwd):
                is_bash = tc.name == "bash"
                if is_bash and base_cwd is not None:
                    set_tracked_cwd(base_cwd)
                out = self._exec_tool(tc)
                if not is_bash:
                    return i, out, None
                cwd_after = get_tracked_cwd()
                return i, out, cwd_after if cwd_after != base_cwd else None

            futures = {
                i: pool.submit(run, i, tc, session_cwd)
                for i, tc in enumerate(tool_calls)
                if results[i] is None
            }

            for i, future in futures.items():
                i, results[i], worker_cwd = future.result()
                if worker_cwd is not None:
                    session_cwd = worker_cwd
                    set_tracked_cwd(session_cwd)

            for i in futures:
                self._post_hooks(tool_calls[i], results[i])
                
            return results

    def _answer_pending_tool_calls(self, tool_calls):
        answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
        for tc in tool_calls:
            if tc.id not in answered:
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "[interrupted]",
                })

    def reset(self):
        self.messages.clear()
        todo = self._tool_by_name.get("todo_writer")
        if isinstance(todo, TodoWriterTool):
            todo._tasks = []
        