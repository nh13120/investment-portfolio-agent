import os
# Dummy vars so config.py loads; we're only testing pure math.
for k in ["TELEGRAM_TOKEN","ANTHROPIC_API_KEY","SUPABASE_URL","SUPABASE_SERVICE_KEY","EODHD_API_KEY"]:
    os.environ[k] = "test"

from app.portfolio import time_weighted_return

def snap(d, v, flow=0.0):
    return {"date": d, "total_value_base": v, "net_flow_base": flow}

print("TEST 1: flat portfolio, big deposit mid-period")
print("  100k -> deposit 10k -> 110k. True performance = 0%.")
r = time_weighted_return([
    snap("2026-01-01", 100000),
    snap("2026-01-02", 110000, flow=10000),
])
print(f"  TWR reported: {r['return_pct']}%   <-- must be 0.0")
assert abs(r["return_pct"]) < 0.01, "FAIL: deposit leaked into return"

print("\nTEST 2: naive (wrong) calculation for contrast")
naive = (110000/100000 - 1) * 100
print(f"  Naive end/start: {naive}%  <-- this is the bug we're avoiding")

print("\nTEST 3: real 10% gain, no flows")
r = time_weighted_return([snap("2026-01-01", 100000), snap("2026-01-02", 110000)])
print(f"  TWR: {r['return_pct']}%   <-- must be 10.0")
assert abs(r["return_pct"] - 10.0) < 0.01

print("\nTEST 4: gain AND deposit together")
print("  100k -> +5% growth AND 20k deposit -> 125k. True perf = 5%.")
r = time_weighted_return([snap("2026-01-01", 100000), snap("2026-01-02", 125000, flow=20000)])
print(f"  TWR: {r['return_pct']}%   <-- must be 5.0")
assert abs(r["return_pct"] - 5.0) < 0.01

print("\nTEST 5: drawdown tracking")
r = time_weighted_return([
    snap("2026-01-01", 100000), snap("2026-01-02", 120000),
    snap("2026-01-03", 90000),  snap("2026-01-04", 100000),
])
print(f"  Max drawdown: {r['max_drawdown_pct']}%  <-- peak 120k to trough 90k = -25%")
assert abs(r["max_drawdown_pct"] + 25.0) < 0.01

print("\nTEST 6: insufficient history (your situation today)")
r = time_weighted_return([snap("2026-01-01", 100000)])
print(f"  -> {r['error']}: refuses to invent a number. Correct.")
assert "error" in r

print("\n\nALL MATH TESTS PASSED")
