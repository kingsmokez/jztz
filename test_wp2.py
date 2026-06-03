import requests

# Trigger a new wp2 pick run
r = requests.post('http://127.0.0.1:5559/api/wp2_pick_run', json={}, timeout=10)
print(f"Run trigger: {r.json()}")

import time
print("Waiting 120 seconds for wp2 pick to complete...")
time.sleep(120)

# Now check
r2 = requests.get('http://127.0.0.1:5559/api/wp2_pick', timeout=30)
data = r2.json()
print(f"\nAfter run: Stocks={len(data.get('stocks', []))}")
print(f"Pick time: {data.get('pick_time')}")
if data.get('stocks'):
    for s in data['stocks'][:3]:
        print(f"  {s}")
