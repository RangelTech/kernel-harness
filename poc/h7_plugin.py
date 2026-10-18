"""H7: plugin rt-broker. Confirma: handler roda in-process, recebe session_id, broker stub e chamado,
ferramenta fora de platform_toolsets/settings e negada. Saida: poc/results/h7_plugin.json"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rig, hc

R = {}
rig.bootstrap(extra_env={"RT_BROKER_DEBUG_META": "1"})
P = rig.PORT
# A) perfil com rt_echo + rt_sql_read liberados
a = rig.gen_profile("tenantA", "tplA", "alice", {"tools": ["rt_echo", "rt_sql_read"], "model_alias": "fake-model", "learning": "off"})
pa, ka = f"/p/{a['profile_id']}", a["api_key"]
rig.reset_logs()
rid, ev, fin = hc.run_events(P, ka, "CALL:rt_echo:{\"text\": \"oi-broker\"}\nCALL:rt_sql_read:{\"datasource\":\"db1\",\"query\":\"select 1\"}", session_id="sess-A1", prefix=pa)
R["A_run_status"] = fin["status"]
R["A_tools_oferecidas_ao_modelo"] = rig.req_log()[0]["tools_offered"]
R["A_eventos"] = [(e["data"].get("event"), e["data"].get("tool"), (e["data"].get("preview") or "")[:120] if isinstance(e["data"], dict) else None) for e in ev if isinstance(e.get("data"), dict)]
R["A_saida_final"] = fin.get("output")
R["A_broker_log"] = [{"auth_ok": b["auth_ok"], "body": b["body"]} for b in rig.broker_log()]
# B) a mesma tool pedida pelo modelo, mas FORA do manifesto (rt_pix_create nao esta em settings.tools)
rig.reset_logs()
rid, ev, fin = hc.run_events(P, ka, "CALL:rt_pix_create:{\"amount\":\"1.00\",\"reference\":\"x\"}", session_id="sess-A2", prefix=pa)
R["B_fora_do_manifesto"] = {"status": fin["status"], "saida": fin.get("output"), "broker_chamado": len(rig.broker_log())}
# C) perfil SEM tools no manifesto: nem plugin nem toolset
c = rig.gen_profile("tenantA", "tplC", "bob", {"tools": [], "model_alias": "fake-model", "learning": "off"})
pc, kc = f"/p/{c['profile_id']}", c["api_key"]
rig.reset_logs()
rid, ev, fin = hc.run_events(P, kc, "CALL:rt_echo:{\"text\": \"nao deveria\"}", session_id="sess-C1", prefix=pc)
R["C_sem_tools"] = {"status": fin["status"], "tools_oferecidas": rig.req_log()[0]["tools_offered"], "saida": fin.get("output"), "broker_chamado": len(rig.broker_log())}
# D) rt_platform fora de platform_toolsets mas tool em settings.tools (edicao manual do config)
rig.exec_in(f"sed -i 's/- rt_platform//' /opt/data/profiles/{a['profile_id']}/config.yaml; grep -n 'rt_platform\|rt_echo' /opt/data/profiles/{a['profile_id']}/config.yaml", user="root")
rig.reset_logs()
rid, ev, fin = hc.run_events(P, ka, "CALL:rt_echo:{\"text\": \"toolset removido\"}", session_id="sess-A3", prefix=pa)
R["D_toolset_removido"] = {"status": fin["status"], "tools_oferecidas": rig.req_log()[0]["tools_offered"], "saida": (fin.get("output") or "")[:300], "broker_chamado": len(rig.broker_log())}
# E) broker recusa (session_id inexistente): erro volta ao modelo
rig.gen_profile("tenantA", "tplA", "alice", {"tools": ["rt_echo"], "model_alias": "fake-model", "learning": "off"})
rig.reset_logs()
rid, ev, fin = hc.run_events(P, ka, "CALL:rt_echo:{\"text\": \"sessao negada\"}", session_id="deny-xyz", prefix=pa)
R["E_broker_403"] = {"status": fin["status"], "saida": (fin.get("output") or "")[:300]}
# F) broker fora do ar
rig.docker("stop", "rt-poc-broker")
rig.reset_logs()
rid, ev, fin = hc.run_events(P, ka, "CALL:rt_echo:{\"text\": \"broker off\"}", session_id="sess-A4", prefix=pa)
R["F_broker_fora"] = {"status": fin["status"], "saida": (fin.get("output") or "")[:300]}
rig.docker("start", "rt-poc-broker")
rig.save("h7_plugin", R)
print(json.dumps(R, indent=1, ensure_ascii=False)[:6000])
