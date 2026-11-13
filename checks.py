import asyncio
import json
import sys
from collector import Collector
from samples import URL, page, bilingual
from transport import Exchange, Response, RecordedHTTP
from check_public import BehaviorMismatch, expect, languages, vary, interleave


async def normal():
    source = RecordedHTTP([
        Exchange('en', None, page('First', 'en', '"v1"')),
        Exchange('en', '"v1"', Response(304, {'ETag': '"v1"'})),
        Exchange('en', '"v1"', page('Revised', 'en', '"v3"')),
    ])
    collector = Collector(source)
    try:
        output = [await collector.collect(URL, {'Accept-Language': 'en'})
                  for _ in range(3)]
        expect([[r['title'], r['etag'], r['origin_status']] for r in output],
               [['First', '"v1"', 200], ['First', '"v1"', 304],
                ['Revised', '"v3"', 200]], 'single language regression')
        expect(len(collector.cache.publications()), 3, 'one publication per call')
        # Publication and cache must roll back together if extraction fails.
        from unittest.mock import patch
        before = [collector.cache.entries(), collector.cache.publications()]
        source.exchanges.append(
            Exchange('en', '"v3"', page('Rejected', 'en', '"v4"')))
        with patch('extract.extract', side_effect=ValueError('EXTRACT_REJECTED')):
            try:
                await collector.collect(URL, {'Accept-Language': 'en'})
            except ValueError as error:
                expect(str(error), 'EXTRACT_REJECTED', 'extraction error')
            else:
                raise BehaviorMismatch('extraction rejection was lost')
        expect([collector.cache.entries(), collector.cache.publications()],
               before, 'transaction rollback')
        return [[r['title'], r['etag']] for r in output]
    finally:
        collector.close()


async def demo():
    collector = Collector(RecordedHTTP(bilingual()))
    try:
        for language in ['en', 'zh', 'en', 'zh']:
            result = await collector.collect(URL, {'Accept-Language': language})
            print(json.dumps({'request_language': language, 'result': result},
                             ensure_ascii=False))
        print(json.dumps({'publications': collector.cache.publications()},
                         ensure_ascii=False))
    finally:
        collector.close()


async def boundary():
    # Vary widening: an empty-Vary 200 replaces per-language entries and is
    # reused by both languages afterwards.
    source = RecordedHTTP([
        Exchange('en', None, page('EN1', 'en', '"e1"')),
        Exchange('zh', None, page('ZH1', 'zh', '"z1"')),
        Exchange('en', '"e1"', page('WIDE', 'en', '"w1"', vary='')),
        Exchange('zh', '"w1"', Response(304, {})),
    ])
    collector = Collector(source)
    try:
        await collector.collect(URL, {'Accept-Language': 'en'})
        await collector.collect(URL, {'Accept-Language': 'zh'})
        await collector.collect(URL, {'Accept-Language': 'en'})
        expect([entry['variant'] for entry in collector.cache.entries()], [''],
               'widened Vary keeps one shared entry')
        reused = await collector.collect(URL, {'Accept-Language': 'zh'})
        expect([reused['title'], reused['etag'], reused['origin_status']],
               ['WIDE', '"w1"', 304], 'zh reuses the widened representation')
    finally:
        collector.close()

    # no-store publishes and invalidates its scope but stores nothing.
    source = RecordedHTTP([
        Exchange('zh', None, page('Z1', 'zh', '"z1"')),
        Exchange('en', None, page('WNS', 'en', '"w1"', vary='',
                                  control='no-store')),
        Exchange('zh', None, page('Z2', 'zh', '"z2"')),
    ])
    collector = Collector(source)
    try:
        await collector.collect(URL, {'Accept-Language': 'zh'})
        published = await collector.collect(URL, {'Accept-Language': 'en'})
        expect(published['title'], 'WNS', 'no-store still publishes')
        expect(collector.cache.entries(), [],
               'no-store stores nothing and invalidates overlap')
        await collector.collect(URL, {'Accept-Language': 'zh'})
        expect(source.calls[2][1].get('if-none-match'), None,
               'invalidated zh entry cannot validate')
        expect(len(collector.cache.publications()), 3,
               'one publication per call')
    finally:
        collector.close()

    # Vary:* publishes, stores nothing and invalidates both languages.
    source = RecordedHTTP([
        Exchange('en', None, page('S1', 'en', '"s1"')),
        Exchange('zh', None, page('S2', 'zh', '"z1"', vary='*')),
        Exchange('en', None, page('S3', 'en', '"s3"')),
    ])
    collector = Collector(source)
    try:
        await collector.collect(URL, {'Accept-Language': 'en'})
        star = await collector.collect(URL, {'Accept-Language': 'zh'})
        expect(star['title'], 'S2', 'Vary:* still extracts')
        expect(collector.cache.entries(), [], 'Vary:* stores nothing')
        await collector.collect(URL, {'Accept-Language': 'en'})
        expect(source.calls[2][1].get('if-none-match'), None,
               'Vary:* invalidated the en entry')
    finally:
        collector.close()

    # A late 200 is discarded and revalidated, never backfilled.
    entered, release = asyncio.Event(), asyncio.Event()
    source = RecordedHTTP([
        Exchange('en', None, page('Old', 'en', '"e1"')),
        Exchange('en', '"e1"', page('Stale', 'en', '"e9"'), entered, release),
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
               'late 200 must be discarded and revalidated')
        expect([p['etag'] for p in collector.cache.publications()],
               ['"e1"', '"e2"', '"e2"'], 'discarded 200 cannot publish')
        expect([e['headers'].get('etag') for e in collector.cache.entries()],
               ['"e2"'], 'discarded 200 cannot be stored')
    finally:
        release.set()
        if slow is not None and not slow.done():
            slow.cancel()
            await asyncio.gather(slow, return_exceptions=True)
        collector.close()

    # A publication in one language never retries a disjoint language.
    entered, release = asyncio.Event(), asyncio.Event()
    source = RecordedHTTP([
        Exchange('en', None, page('E1', 'en', '"e1"')),
        Exchange('zh', None, page('Z1', 'zh', '"z1"')),
        Exchange('en', '"e1"', Response(304, {'ETag': '"e1"'}), entered, release),
        Exchange('zh', '"z1"', Response(304, {})),
    ])
    collector = Collector(source)
    slow = None
    try:
        await collector.collect(URL, {'Accept-Language': 'en'})
        await collector.collect(URL, {'Accept-Language': 'zh'})
        slow = asyncio.create_task(collector.collect(URL, {'Accept-Language': 'en'}))
        await entered.wait()
        zh = await collector.collect(URL, {'Accept-Language': 'zh'})
        release.set()
        en = await slow
        expect([en['title'], zh['title']], ['E1', 'Z1'],
               'disjoint languages publish independently')
        expect(len(source.calls), 4, 'disjoint language must not retry')
    finally:
        release.set()
        if slow is not None and not slow.done():
            slow.cancel()
            await asyncio.gather(slow, return_exceptions=True)
        collector.close()

    # A no-store publication still changes the scope for in-flight requests.
    entered, release = asyncio.Event(), asyncio.Event()
    source = RecordedHTTP([
        Exchange('en', None, page('Old', 'en', '"e1"')),
        Exchange('en', '"e1"', Response(304, {'ETag': '"e1"'}), entered, release),
        Exchange('en', '"e1"', page('NS', 'en', '"n1"', control='no-store')),
        Exchange('en', None, page('Fresh', 'en', '"f1"')),
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
        expect([result['title'], result['etag']], ['Fresh', '"f1"'],
               'no-store publication invalidates an in-flight 304')
        expect([p['etag'] for p in collector.cache.publications()],
               ['"e1"', '"n1"', '"f1"'], 'discarded 304 cannot publish')
        expect(len(source.calls), 4, 'conflict retries exactly once')
    finally:
        release.set()
        if slow is not None and not slow.done():
            slow.cancel()
            await asyncio.gather(slow, return_exceptions=True)
        collector.close()

    # An empty-cache request cannot backfill over a newer overlapping entry.
    entered, release = asyncio.Event(), asyncio.Event()
    source = RecordedHTTP([
        Exchange('en', None, page('Wide', 'en', '"w1"', vary=''), entered, release),
        Exchange('zh', None, page('Z1', 'zh', '"z1"')),
        Exchange('en', None, page('Wide2', 'en', '"w2"', vary='')),
    ])
    collector = Collector(source)
    slow = None
    try:
        slow = asyncio.create_task(collector.collect(URL, {'Accept-Language': 'en'}))
        await entered.wait()
        await collector.collect(URL, {'Accept-Language': 'zh'})
        release.set()
        result = await slow
        expect([result['title'], result['etag']], ['Wide2', '"w2"'],
               'empty-cache backfill must be discarded')
        expect([p['title'] for p in collector.cache.publications()],
               ['Z1', 'Wide2'], 'discarded 200 cannot publish')
        expect(len(source.calls), 3, 'backfill retries exactly once')
    finally:
        release.set()
        if slow is not None and not slow.done():
            slow.cancel()
            await asyncio.gather(slow, return_exceptions=True)
        collector.close()

    # 304 headers replace stored ones; no-store in a 304 drops the entry.
    source = RecordedHTTP([
        Exchange('en', None, page('C1', 'en', '"c1"')),
        Exchange('en', '"c1"', Response(304, {'ETag': '"c1"',
                                              'Cache-Control': 'no-store'})),
        Exchange('en', None, page('C2', 'en', '"c2"')),
    ])
    collector = Collector(source)
    try:
        await collector.collect(URL, {'Accept-Language': 'en'})
        merged = await collector.collect(URL, {'Accept-Language': 'en'})
        expect([merged['title'], merged['etag'], merged['content_language'],
                merged['origin_status']], ['C1', '"c1"', 'en', 304],
               '304 merges selected body with inherited headers')
        expect(collector.cache.entries(), [], 'no-store in 304 drops the entry')
        await collector.collect(URL, {'Accept-Language': 'en'})
        expect(source.calls[2][1].get('if-none-match'), None,
               'dropped entry cannot validate')
    finally:
        collector.close()

    # Rejected calls leave neither cache entries nor publications.
    async def rejected(call, marker):
        try:
            await call
        except ValueError as error:
            expect(str(error), marker, 'rejection marker')
        else:
            raise BehaviorMismatch(marker + ' rejection was lost')

    source = RecordedHTTP([
        Exchange('en', None, Response(500, {}, '')),
        Exchange('en', None, Response(304, {})),
        Exchange('en', None, page('Fine', 'en', '"f1"')),
    ])
    collector = Collector(source)
    try:
        await rejected(collector.collect('', {'Accept-Language': 'en'}), 'URL')
        await rejected(collector.collect(URL, {'Accept-Language': 'fr'}),
                       'LANGUAGE')
        await rejected(collector.collect(URL, {'Accept-Language': 'en'}),
                       'STATUS')
        await rejected(collector.collect(URL, {'Accept-Language': 'en'}),
                       'UNSOLICITED_304')
        expect([collector.cache.entries(), collector.cache.publications()],
               [[], []], 'rejections leave no cache or publications')
        ok = await collector.collect(URL, {'Accept-Language': 'en'})
        expect(ok['title'], 'Fine', 'collector still works after rejections')
        expect(len(collector.cache.publications()), 1,
               'only successful calls publish')
    finally:
        collector.close()
    return 'boundary ok'


async def run(mode):
    if mode == 'demo':
        await demo()
        return
    if mode == 'normal':
        print(json.dumps(await normal(), ensure_ascii=False))
        print('NORMAL_REGRESSION_OK')
    elif mode == 'incident':
        try:
            print(json.dumps(await languages(), ensure_ascii=False))
        except BehaviorMismatch as error:
            print('CACHE_REPRESENTATION_MISMATCH', str(error))
            raise SystemExit(1)
        print('CACHE_REPRESENTATION_OK')
    elif mode == 'verify':
        await normal()
        await languages()
        await vary()
        await interleave()
        await boundary()
        print('COLLECTOR_VERIFY_OK')
    else:
        raise ValueError('unknown mode')


if __name__ == '__main__':
    asyncio.run(run(sys.argv[1]))
