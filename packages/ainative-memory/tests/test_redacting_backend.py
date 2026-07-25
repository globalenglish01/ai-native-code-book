from __future__ import annotations

import pytest
from ainative_memory.redacting_backend import wrap_summarization_backend


class _FakeBackend:
    def __init__(self) -> None:
        self.written: dict[str, str] = {}

    def write(self, file_path, content):
        self.written[file_path] = content
        return "write-ok"

    def edit(self, file_path, old_string, new_string):
        self.written[file_path] = self.written.get(file_path, "").replace(old_string, new_string)
        return "edit-ok"

    async def awrite(self, file_path, content):
        return self.write(file_path, content)

    async def aedit(self, file_path, old_string, new_string):
        return self.edit(file_path, old_string, new_string)

    def read(self, file_path):
        return self.written.get(file_path, "")


def _redact(text: str) -> str:
    return text.replace("SECRET", "[REDACTED]")


def test_write_redacts_content_before_delegating():
    backend = _FakeBackend()
    proxy = wrap_summarization_backend(backend, _redact)

    proxy.write("history.md", "user said SECRET")
    assert backend.written["history.md"] == "user said [REDACTED]"


def test_edit_only_redacts_new_string_not_old_string():
    backend = _FakeBackend()
    backend.written["history.md"] = "already [REDACTED] content"
    proxy = wrap_summarization_backend(backend, _redact)

    # old_string must match the actual (already-redacted) disk content verbatim,
    # so it must NOT be redacted again before being passed through.
    proxy.edit("history.md", "already [REDACTED] content", "new turn with SECRET")
    assert backend.written["history.md"] == "new turn with [REDACTED]"


@pytest.mark.asyncio
async def test_async_write_and_edit_also_redact():
    backend = _FakeBackend()
    proxy = wrap_summarization_backend(backend, _redact)

    await proxy.awrite("history.md", "async SECRET here")
    assert backend.written["history.md"] == "async [REDACTED] here"

    await proxy.aedit("history.md", "async [REDACTED] here", "more SECRET")
    assert backend.written["history.md"] == "more [REDACTED]"


def test_unrelated_methods_pass_through_untouched():
    backend = _FakeBackend()
    backend.written["a.md"] = "raw content"
    proxy = wrap_summarization_backend(backend, _redact)

    assert proxy.read("a.md") == "raw content"
