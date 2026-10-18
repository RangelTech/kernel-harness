import sys, json
sys.path.insert(0, "poc")
import rig, hc
PID="u-ed2beafd92cb08547550f59b"; KEY="d269af6ff598720da51c780ac3b18b7d4633b55cbe0e543803c30aedd244ed83"
P=rig.PORT
print(hc.call(P,KEY,"GET",f"/p/{PID}/v1/toolsets")[2])
print(hc.call(P,KEY,"GET",f"/p/{PID}/v1/skills")[2])
rid, ev, fin = hc.run_events(P,KEY,"ola mundo",session_id="s1",prefix=f"/p/{PID}")
print(rid, json.dumps(fin)[:600])
for e in ev: print(e.get("id"), e.get("event"), json.dumps(e.get("data"))[:300])
r=rig.req_log()[-1]; print(r["tools_offered"], r["sys_skill_names"], r["sys_len"])
