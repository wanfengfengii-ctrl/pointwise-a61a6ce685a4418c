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
        print('COLLECTOR_VERIFY_OK')
    else:
        raise ValueError('unknown mode')


if __name__ == '__main__':
    asyncio.run(run(sys.argv[1]))
