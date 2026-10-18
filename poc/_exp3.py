import sys, json
sys.path.insert(0, "poc")
import rig, hc
m = {"tools": ["rt_echo", "rt_sql_read"], "model_alias": "fake-model", "learning": "off"}
r = rig.gen_profile("tenantA", "tpl1", "alice", m, template_version="current")
print(r["changed"])
PID=r["profile_id"]; KEY=r["api_key"]; P=rig.PORT
rig.reset_logs()
rid, ev, fin = hc.run_events(P,KEY,"ola 2",session_id="s2",prefix=f"/p/{PID}")
print(fin["status"], fin.get("output"))
for x in rig.req_log(): print(x["n_msgs"], x["tools_offered"], x["sys_len"], x["sys_head"][:50].replace("\n"," "))
