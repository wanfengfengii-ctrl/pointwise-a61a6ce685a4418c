import json
import sqlite3


def normalized(headers):
    return {str(k).lower(): str(v) for k, v in headers.items()}


LANGUAGES = ('en', 'zh')

# Returned by Cache.accept when another successful publication changed the
# involved scope after the request was sent; the late response is discarded.
CONFLICT = object()


def vary_of(headers):
    # Legal Vary is empty, Accept-Language or *; case and padding are ignored.
    return headers.get('vary', '').strip().lower()


def response_scope(headers, language):
    """Languages covered by a response with these (merged) headers."""
    if vary_of(headers) == 'accept-language':
        return {language}
    # Empty Vary selects both languages; Vary:* invalidates both languages.
    return set(LANGUAGES)


def storable(headers):
    return vary_of(headers) != '*' and (
        headers.get('cache-control', '').strip().lower() != 'no-store')


def entry_scope(entry):
    return set(LANGUAGES) if entry['variant'] == '' else {entry['variant']}


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
        # Internal optimistic-publish clock, not business data: counts
        # successful publications per (url, language).
        self._epochs = {}

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

    def stamp(self, url):
        """Publish-clock snapshot taken when the request is sent."""
        return {language: self._epochs.get((url, language), 0)
                for language in LANGUAGES}

    def accept(self, url, request, selected, stamp, body, headers, status):
        """Publish one accepted response.

        Invalidation, the reusable representation and the extraction record
        commit in one transaction; an extraction failure rolls back all of
        them. Returns the extraction record, or CONFLICT without touching
        anything when a publication after stamp changed the involved scope.
        """
        language = request['accept-language']
        involved = response_scope(headers, language)
        if selected is not None:
            involved |= entry_scope(selected)
        if any(self._epochs.get((url, lang), 0) != stamp[lang]
               for lang in involved):
            return CONFLICT
        self.db.execute('BEGIN IMMEDIATE')
        try:
            # Extraction uses response data even when the response is
            # non-storable; imported lazily so tests can patch extract.extract.
            from extract import extract
            record = extract(body, headers, status)
            new_scope = response_scope(headers, language)
            for row in self.db.execute(
                    'SELECT id, variant FROM entries WHERE url=?',
                    (url,)).fetchall():
                if (selected is not None and row['id'] == selected['id']
                        ) or entry_scope(row) & new_scope:
                    self.db.execute('DELETE FROM entries WHERE id=?', (row['id'],))
            if storable(headers):
                variant = (language if vary_of(headers) == 'accept-language'
                           else '')
                self.db.execute(
                    """INSERT INTO entries(url,variant,body,headers)
                       VALUES(?,?,?,?)""",
                    (url, variant, body, json.dumps(headers)))
            self.db.execute(
                """INSERT INTO publications(url,language,title,body,etag,
                   content_language,origin_status) VALUES(?,?,?,?,?,?,?)""",
                (url, language, record['title'], body,
                 record['etag'], record['content_language'], status))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        for lang in involved:
            self._epochs[(url, lang)] = self._epochs.get((url, lang), 0) + 1
        return record

    def entries(self):
        return [self._entry(row) for row in self.db.execute(
            'SELECT * FROM entries ORDER BY url,variant')]

    def publications(self):
        return [dict(row) for row in self.db.execute(
            'SELECT * FROM publications ORDER BY seq')]
