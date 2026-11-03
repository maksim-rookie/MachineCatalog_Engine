"""Local SQL prototype. Standard library only; production PostgreSQL is separate."""
import hashlib
import json
from pathlib import Path
import sqlite3


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA foreign_keys=ON')
    connection.executescript('''
    CREATE TABLE IF NOT EXISTS documents(
      id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS equipment(
      id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
      manufacturer TEXT NOT NULL, model TEXT NOT NULL, configuration TEXT NOT NULL,
      UNIQUE(document_id, manufacturer, model, configuration));
    CREATE TABLE IF NOT EXISTS measurements(
      id INTEGER PRIMARY KEY, equipment_id INTEGER NOT NULL REFERENCES equipment(id),
      parameter TEXT NOT NULL, value REAL, unit TEXT NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('confirmed','unverified','conflict','absent')),
      raw_json TEXT NOT NULL, UNIQUE(equipment_id, parameter));
    CREATE INDEX IF NOT EXISTS measurement_search ON measurements(parameter,unit,value);
    ''')
    return connection


def load(connection, source):
    """Validate first, then load a complete document in one transaction."""
    source = Path(source)
    content = source.read_bytes()
    payload = json.loads(content)
    if not isinstance(payload, dict) or not isinstance(payload.get('equipment'), list):
        raise ValueError('Expected an object with an equipment list')
    prepared = []
    identities = set()
    for machine in payload['equipment']:
        identity = tuple(str(machine.get(k, '')).strip() for k in ('manufacturer', 'model', 'configuration'))
        if not all(identity) or identity in identities:
            raise ValueError('Manufacturer/model/configuration must be nonempty and unique')
        identities.add(identity)
        measurements = machine.get('measurements', [])
        if not isinstance(measurements, list):
            raise ValueError('Measurements must be a list')
        parameters = set()
        for measurement in measurements:
            parameter = measurement.get('parameter')
            if not isinstance(parameter, str) or not parameter or parameter in parameters:
                raise ValueError('Measurement parameters must be nonempty and unique')
            parameters.add(parameter)
            value = measurement.get('value')
            import math
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError('Value must be a finite number or null')
            if measurement.get('status') not in {'confirmed', 'unverified', 'conflict', 'absent'}:
                raise ValueError('Invalid measurement status')
            if measurement['status'] == 'absent' and value is not None:
                raise ValueError('Absent measurement cannot have a numeric value')
            if not isinstance(measurement.get('unit'), str):
                raise ValueError('Unit must be a string')
        prepared.append((identity, measurements))
    digest = hashlib.sha256(content).hexdigest()
    with connection:
        existing = connection.execute('SELECT id FROM documents WHERE sha256=?', (digest,)).fetchone()
        if existing:
            return existing[0]
        document_id = connection.execute('INSERT INTO documents(sha256,name) VALUES(?,?)', (digest, source.name)).lastrowid
        for identity, measurements in prepared:
            equipment_id = connection.execute(
                'INSERT INTO equipment(document_id,manufacturer,model,configuration) VALUES(?,?,?,?)',
                (document_id, *identity)).lastrowid
            for measurement in measurements:
                connection.execute('INSERT INTO measurements(equipment_id,parameter,value,unit,status,raw_json) VALUES(?,?,?,?,?,?)',
                                   (equipment_id, measurement['parameter'], measurement.get('value'), measurement['unit'],
                                    measurement['status'], json.dumps(measurement, ensure_ascii=False)))
    return document_id


def select_equipment(connection, requirements):
    """All requirements are mandatory minima, expressed in exact canonical units.

    Unknown units are not converted. Contradictory documents remain separate.
    Synthetic demo confirmations do not certify any real catalogue.
    """
    for requirement in requirements:
        import math
        minimum = requirement.get('min')
        if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not math.isfinite(minimum):
            raise ValueError('Requirement min must be finite')
        if not requirement.get('parameter') or not requirement.get('unit'):
            raise ValueError('Requirement parameter and unit are required')
    result = []
    for equipment_id, manufacturer, model, configuration, document in connection.execute(
            'SELECT e.id,e.manufacturer,e.model,e.configuration,d.name FROM equipment e JOIN documents d ON d.id=e.document_id ORDER BY e.manufacturer,e.model'):
        checks = []
        for requirement in requirements:
            row = connection.execute('SELECT value,unit,status FROM measurements WHERE equipment_id=? AND parameter=?',
                                     (equipment_id, requirement['parameter'])).fetchone()
            if row is None or row[2] != 'confirmed' or row[0] is None or row[1] != requirement['unit']:
                verdict = 'insufficient_data'
            else:
                verdict = 'pass' if row[0] >= requirement['min'] else 'fail'
            checks.append({'parameter': requirement['parameter'], 'min': requirement['min'],
                           'unit': requirement['unit'], 'value': row[0] if row else None, 'result': verdict})
        verdict = 'fail' if any(c['result'] == 'fail' for c in checks) else 'insufficient_data' if any(c['result'] == 'insufficient_data' for c in checks) else 'pass'
        result.append({'manufacturer': manufacturer, 'model': model, 'configuration': configuration,
                       'document': document, 'result': verdict, 'checks': checks})
    return result
