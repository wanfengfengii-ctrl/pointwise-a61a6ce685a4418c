from dataclasses import dataclass, field
import asyncio


@dataclass
class Response:
    status: int
    headers: dict
    body: str = ''


@dataclass
class Exchange:
    language: str
    validator: object
    response: Response
    entered: object = field(default=None)
    release: object = field(default=None)


ANY = object()


class RecordedHTTP:
    """Each call consumes a recorded exchange at dispatch, not completion."""
    def __init__(self, exchanges):
        self.exchanges = list(exchanges)
        self.calls = []

    async def get(self, url, headers):
        if not self.exchanges:
            raise AssertionError('unexpected extra GET')
        step = self.exchanges.pop(0)
        self.calls.append([url, dict(headers)])
        assert headers['accept-language'] == step.language, self.calls
        if step.validator is not ANY:
            assert headers.get('if-none-match') == step.validator, self.calls
        if step.entered is not None:
            step.entered.set()
        if step.release is not None:
            await step.release.wait()
        await asyncio.sleep(0)
        return step.response
