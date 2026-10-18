import sys, json, time
sys.path.insert(0, "poc")
import rig, hc
rig.put_files(rig.VOL_SYS, "/skills/system", {"sys-ola/SKILL.md": "---\nname: sys-ola\ndescription: Skill de sistema de teste\n---\n# sys\nCorpo sistema"})
rig.put_files(rig.VOL_TENANT, "/skills/tenant", {"tpl1/v1/emp-ola/SKILL.md": "---\nname: emp-ola\ndescription: Skill da empresa\n---\n# emp\nCorpo empresa"})
rig.root_sh("cd /t/tpl1 && ln -sfn v1 current && chown -h 10000:10000 current", f"{rig.VOL_TENANT}:/t")
m = {"tools": ["rt_echo", "rt_sql_read"], "model_alias": "fake-model", "learning": "review"}
r = rig.gen_profile("tenantA", "tpl1", "alice", m, template_version="current")
print(r)
rig.start_worker()
print(rig.docker("logs","--tail","40",rig.WORKER,check=False).stdout[-3000:])
print(rig.exec_in("ls -la /opt/data /opt/data/profiles/*; cat /opt/data/profiles/*/config.yaml | head -80; ls /opt/data/skills")).stdout
