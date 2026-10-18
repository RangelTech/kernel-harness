"""Prepara o HERMES_HOME (perfil default) de um worker Hermes REAL para o modo multiplex.
Roda como root na imagem rt-hermes (entrypoint = python do venv). Env: HOMEDIR, LLM_URL, GWKEY, MAXCONC."""
import json
import os

for h in os.environ["HOMEDIRS"].split(","):
  os.makedirs(h, exist_ok=True)
  cfg = {
      "model": {"default": "fake-model", "provider": "custom", "base_url": os.environ["LLM_URL"],
                "api_key": "fake-key", "context_length": 200000},
      "platform_toolsets": {"api_server": ["skills"]},
      "agent": {"max_turns": 12, "disabled_toolsets": [
          "cronjob", "delegation", "browser", "tts", "kanban", "computer_use", "connections",
          "terminal", "code_execution", "file", "image_gen", "video_gen", "web", "vision"]},
      "skills": {"external_dirs": [], "project_discovery": False, "write_approval": True},
      "memory": {"write_approval": True, "nudge_interval": 0},
      "curator": {"enabled": False},
      "auxiliary": {"background_review": {"enabled": False}},
      "gateway": {"multiplex_profiles": True, "strict": True,
                  "api_server": {"max_concurrent_runs": int(os.environ.get("MAXCONC", "4"))}},
  }
  open(h + "/config.yaml", "w").write(json.dumps(cfg, indent=1))
  open(h + "/.no-bundled-skills", "w").write("")
  # com multiplex_profiles o env do container e IGNORADO para o perfil default (achado H1)
  open(h + "/.env", "w").write("API_SERVER_ENABLED=true\nAPI_SERVER_HOST=0.0.0.0\nAPI_SERVER_KEY="
                               + os.environ["GWKEY"] + "\n")
  os.chmod(h + "/.env", 0o600)
  for p in (h + "/config.yaml", h + "/.env", h + "/.no-bundled-skills", h, "/opt/data/profiles",
            "/skills/tenant", "/srv/hermes/users"):
      try:
          os.chown(p, 10000, 10000)
      except OSError:
          pass
