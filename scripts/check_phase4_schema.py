import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ['COMMERCE_AGENT_DB'] = '/tmp/hht-phase4-schema.sqlite3'
try:
    os.remove(os.environ['COMMERCE_AGENT_DB'])
except FileNotFoundError:
    pass
from hht_app import commerce_agent
commerce_agent.init_db()
with commerce_agent.connect() as db:
    tables = {row[0] for row in db.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    columns = {row[1] for row in db.connection.execute('PRAGMA table_info(listings)').fetchall()}
assert {'listing_performance_daily', 'listing_versions', 'fulfillment_orders', 'rotation_actions'} <= tables
assert {'lifecycle_status', 'listing_start_time', 'quantity_sold', 'watch_count'} <= columns
print('phase4 schema ok')
