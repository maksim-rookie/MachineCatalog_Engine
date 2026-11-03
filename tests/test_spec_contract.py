import json
import unittest
from catalog_engine.spec_contract import CONFIG,normalize,parse_expression,project

class ContractTests(unittest.TestCase):
 def test_intervals(self):
  for value,low,high in [('≥ 5',5,None),('≤ 7',None,7),('10 ± 2',8,12),('от 2 до 8',2,8)]:
   p=parse_expression(value); self.assertEqual((p['min'],p['max']),(low,high))
 def test_conversions(self):
  c=json.loads(CONFIG.read_text())
  for field,unit,value,expected in [('spindle_power','HP',10,7.4569987158),('machine_weight','lbs',100,45.359237),('air_pressure','psi',100,6.894757293),('feed_cutting_max','ipm',100,2540)]:
   p,reasons=normalize(value,c['fields'][field],unit,c); self.assertFalse(reasons); self.assertAlmostEqual(p['value_num'],expected)
 def test_range_scalar_and_enum(self):
  c=json.loads(CONFIG.read_text())
  p,_=normalize('500',c['fields']['travel_x'],'mm',c); self.assertEqual((p['value_min'],p['value_max']),(500,500))
  p,r=normalize('BT 40',c['fields']['spindle_taper'],'',c); self.assertEqual(p['value_enum'],'BT40'); self.assertFalse(r)
 def test_applies_to_and_repeat(self):
  docs=[{'source_file':'a.pdf','facts':[{'model':'A','parameter':'gantry_width_mm','value':'500','unit_source':'mm'}]}]
  a=project(docs,{'a.pdf':{'A':'turning'}}); self.assertFalse(a['machine_spec']); self.assertEqual(a['NEEDS_REVIEW'][0]['reason'],'not_applicable'); self.assertEqual(a,project(docs,{'a.pdf':{'A':'turning'}}))
 def test_persist_upsert(self):
  import sqlite3
  from catalog_engine.spec_contract import persist
  c=sqlite3.connect(':memory:')
  c.execute('CREATE TABLE machine_spec(machine_id INTEGER,field_id TEXT,value_num REAL,value_min REAL,value_max REAL,value_text TEXT,value_enum TEXT,unit TEXT,raw TEXT,confidence REAL,UNIQUE(machine_id,field_id))')
  cfg=json.loads(CONFIG.read_text()); value,_=normalize('12000',cfg['fields']['spindle_speed_max'],'rpm',cfg)
  row={'machine_id':'source-model','field_id':'spindle_speed_max',**value}
  with c: persist(c,[row],{'source-model':1}); persist(c,[row],{'source-model':1})
  self.assertEqual(c.execute('SELECT count(*) FROM machine_spec').fetchone()[0],1)
  c.close()
