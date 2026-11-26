from functools import lru_cache
from contextlib import closing
from pathlib import Path
import sqlite3
import unicodedata

from rapidfuzz import fuzz, process


def normalize(value):
    text = unicodedata.normalize('NFKD', str(value)).casefold()
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in text
                            if not unicodedata.combining(c)).split())


class PlacesIndex:
    SCHEMA = 1

    def __init__(self, path):
        self.path = Path(path).resolve()

    def connect(self):
        if not self.path.is_file():
            raise FileNotFoundError('Falta la base local de lugares: data/offline/places.sqlite')
        connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def check(self):
        with closing(self.connect()) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != self.SCHEMA:
                raise ValueError('La base local de lugares no es compatible con esta versión de Navia.')
            metadata = dict(db.execute('SELECT key, value FROM metadata'))
            count = db.execute('SELECT count(*) FROM places').fetchone()[0]
            if count < 100:
                raise ValueError('La base local de lugares está incompleta.')
            indexed = db.execute('SELECT count(*) FROM places_fts').fetchone()[0]
            if indexed != count or int(metadata.get('count', 0)) != count:
                raise ValueError('El índice local de lugares está incompleto.')
            db.execute("SELECT rowid FROM places_fts WHERE places_fts MATCH 'salvador' LIMIT 1").fetchall()
            return {**metadata, 'count': count}

    @lru_cache(maxsize=1)
    def candidates(self):
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute('SELECT * FROM places')]

    def search(self, query):
        key = normalize(query[:120])
        if len(key) < 2:
            return []
        tokens = key.split()[:12]
        expression = ' AND '.join('"' + token + '"*' for token in tokens)
        with closing(self.connect()) as db:
            rows = [dict(row) for row in db.execute('''
                SELECT p.* FROM places_fts f JOIN places p ON p.id=f.rowid
                WHERE places_fts MATCH ?
                ORDER BY CASE WHEN p.normalized=? THEN 0 WHEN p.normalized LIKE ? THEN 1 ELSE 2 END,
                         bm25(places_fts) LIMIT 100
            ''', (expression, key, key + '%'))]
        if len(rows) < 8:
            candidates = self.candidates()
            matches = process.extract(key, [row['normalized'] for row in candidates],
                                      scorer=fuzz.WRatio, score_cutoff=65, limit=20)
            present = {row['id'] for row in rows}
            rows.extend(candidates[index] for _, _, index in matches
                        if candidates[index]['id'] not in present)

        def rank(row):
            name = row['normalized']
            score = 100 if name == key else 96 if name.startswith(key) else 92 if key in name else fuzz.WRatio(key, name)
            priority = {'national_capital': 7, 'city': 6, 'town': 5, 'village': 4, 'poi': 3, 'natural': 2,
                        'building': 1, 'street': 0}.get(row['kind'], 2)
            matches_all = all(token in normalize(name + ' ' + row['label']) for token in tokens)
            return matches_all, score, priority, -len(name)

        rows.sort(key=rank, reverse=True)
        results, seen = [], set()
        for row in rows:
            identity = row['normalized'], round(row['lat'], 4), round(row['lon'], 4)
            if identity in seen:
                continue
            seen.add(identity)
            results.append({key: row[key] for key in ('name', 'label', 'lat', 'lon', 'kind')})
            if len(results) == 8:
                break
        return results
