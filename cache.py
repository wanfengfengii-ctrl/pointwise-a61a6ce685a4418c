import json
import sqlite3


def normalized(headers):
    return {str(k).lower(): str(v) for k, v in headers.items()}


class Cache:
    def __init__(self, path=':memory:'):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS entries(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          url TEXT NOT NULL, variant TEXT NOT NULL,
          body TEXT NOT NULL, headers TEXT NOT NULL,
          UNIQUE(url,variant));
        CREATE TABLE IF NOT EXISTS publications(
          seq INTEGER PRIMARY KEY AUTOINCREMENT,
          url TEXT NOT NULL, language TEXT NOT NULL,
          title TEXT NOT NULL, body TEXT NOT NULL,
          etag TEXT, content_language TEXT, origin_status INTEGER NOT NULL);
        """)

    def close(self):
        self.db.close()

    def _entry(self, row):
        if row is None:
            return None
        result = dict(row)
        result['headers'] = json.loads(result['headers'])
        return result

    def choose(self, url, request):
        row = self.db.execute(
            """SELECT * FROM entries WHERE url=? AND variant IN (?, '')
               ORDER BY id DESC LIMIT 1""",
            (url, request['accept-language'])).fetchone()
        return self._entry(row)

    def newest(self, url):
        return self._entry(self.db.execute(
            'SELECT * FROM entries WHERE url=? ORDER BY id DESC LIMIT 1',
            (url,)).fetchone())

    def merge_304(self, url, headers):
        # This cache predates concurrent bilingual collection.
        current = self.newest(url)
        if current is None:
            raise ValueError('304_WITHOUT_BODY')
        merged = dict(current['headers'])
        merged.update(headers)
        return current['body'], merged

    def save(self, url, request, selected, body, headers, status):
        """Cache and materialized extraction share one local transaction."""
        self.db.execute('BEGIN IMMEDIATE')
        try:
            excluded = headers.get('vary', '').strip() == '*' or (
                headers.get('cache-control', '').lower() == 'no-store')
            if excluded:
                if selected is not None:
                    self.db.execute('DELETE FROM entries WHERE id=?', (selected['id'],))
            else:
                if selected is not None:
                    self.db.execute(
                        'UPDATE entries SET body=?,headers=? WHERE id=?',
                        (body, json.dumps(headers), selected['id']))
                else:
                    variant = (request['accept-language']
                               if headers.get('vary', '').lower() == 'accept-language'
                               else '')
                    self.db.execute(
                        """INSERT INTO entries(url,variant,body,headers) VALUES(?,?,?,?)
                           ON CONFLICT(url,variant) DO UPDATE SET
                           body=excluded.body,headers=excluded.headers""",
                        (url, variant, body, json.dumps(headers)))
            # Extraction uses response data even when the response is non-storable.
            from extract import extract
            record = extract(body, headers, status)
            self.db.execute(
                """INSERT INTO publications(url,language,title,body,etag,
                   content_language,origin_status) VALUES(?,?,?,?,?,?,?)""",
                (url, request['accept-language'], record['title'], body,
                 record['etag'], record['content_language'], status))
            self.db.execute('COMMIT')
            return record
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def entries(self):
        return [self._entry(row) for row in self.db.execute(
            'SELECT * FROM entries ORDER BY url,variant')]

    def publications(self):
        return [dict(row) for row in self.db.execute(
            'SELECT * FROM publications ORDER BY seq')]
