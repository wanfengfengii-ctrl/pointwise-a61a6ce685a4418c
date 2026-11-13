import asyncio
import json
import sys
from collector import Collector
from samples import URL, page, bilingual
from transport import ANY, Exchange, Response, RecordedHTTP


class BehaviorMismatch(AssertionError):
    pass


def expect(actual, expected, label):
    if actual != expected:
        raise BehaviorMismatch(label + ': expected=' + repr(expected) +
                               ', actual=' + repr(actual))


async def languages():
    source = RecordedHTTP(bilingual())
    collector = Collector(source)
    try:
        results = []
        for lang in ['en', 'zh', 'en', 'zh']:
            results.append(await collector.collect(URL, {'Accept-Language': lang}))
        actual = [[r['title'], r['etag'], r['content_language']] for r in results]
        expected = [
            ['River notice', '"e1"', 'en'], ['河道公告', '"z1"', 'zh'],
            ['River notice', '"e1"', 'en'], ['河道公告', '"z1"', 'zh']]
        expect(actual, expected, 'bilingual 304 extraction')
        expect([p['body'] for p in collector.cache.publications()],
               [r['body'] for r in results], 'published bodies')
        return actual
    finally:
        collector.close()


async def vary():
    source = RecordedHTTP([
        Exchange('en', None, page('Shared', 'en', '"u1"', vary='')),
        Exchange('en', '"u1"', Response(304, {
            'Vary': 'Accept-Language', 'ETag': '"u1"'})),
        Exchange('zh', ANY, page('中文', 'zh', '"z1"')),
    ])
    collector = Collector(source)
    try:
        await collector.collect(URL, {'Accept-Language': 'en'})
        result = await collector.collect(URL, {'Accept-Language': 'en'})
        expect([result['title'], result['etag']], ['Shared', '"u1"'], 'merge')
        await collector.collect(URL, {'Accept-Language': 'zh'})
        expect(source.calls[2][1].get('if-none-match'), None,
               'narrowed Vary must not validate zh with en')
        return source.calls
    finally:
        collector.close()


async def interleave():
    entered, release = asyncio.Event(), asyncio.Event()
    source = RecordedHTTP([
        Exchange('en', None, page('Old', 'en', '"e1"')),
        Exchange('en', '"e1"', Response(304, {'ETag': '"e1"'}),
                 entered, release),
        Exchange('en', '"e1"', page('New', 'en', '"e2"')),
        Exchange('en', '"e2"', Response(304, {'ETag': '"e2"'})),
    ])
    collector = Collector(source)
    slow = None
    try:
        await collector.collect(URL, {'Accept-Language': 'en'})
        slow = asyncio.create_task(collector.collect(URL, {'Accept-Language': 'en'}))
        await entered.wait()
        await collector.collect(URL, {'Accept-Language': 'en'})
        release.set()
        result = await slow
        expect([result['title'], result['etag']], ['New', '"e2"'],
               'late response must be revalidated')
        expect(len(source.calls), 4, 'retry count')
        expect([p['etag'] for p in collector.cache.publications()],
               ['"e1"', '"e2"', '"e2"'], 'discarded response cannot publish')
        return [[p['title'], p['etag']] for p in collector.cache.publications()]
    finally:
        release.set()
        if slow is not None and not slow.done():
            slow.cancel()
            await asyncio.gather(slow, return_exceptions=True)
        collector.close()


CASES = {'languages': languages, 'vary': vary, 'interleave': interleave}


async def bounded(name):
    return await asyncio.wait_for(CASES[name](), timeout=15)


def main():
    name = sys.argv[1]
    try:
        result = asyncio.run(bounded(name))
    except asyncio.TimeoutError:
        print('PUBLIC_' + name.upper() + '_TIMEOUT')
        return 2
    except BehaviorMismatch as error:
        print('PUBLIC_' + name.upper() + '_MISMATCH', str(error))
        return 1
    print('PUBLIC_' + name.upper() + '_OK',
          json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
