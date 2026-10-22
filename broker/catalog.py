"""Grupos de ferramentas: o catalogo do produto lista capacidades; o broker executa ferramentas finas."""
from __future__ import annotations

GROUPS: dict[str, tuple[str, ...]] = {
    "rt_env_manage": ("rt_env_start", "rt_env_exec", "rt_env_upload", "rt_env_download", "rt_env_stop", "rt_env_backup", "rt_env_expose",
                      "rt_env_unexpose", "rt_env_domain_add", "rt_env_domain_verify"),
    "rt_job_schedule": ("rt_schedule_create", "rt_schedule_list", "rt_schedule_pause",
                        "rt_schedule_delete"),
}


def environment_enabled(manifest) -> bool:
    """`manifest.environment` vem como {"enabled": bool, ...} (ou bool solto, legado)."""
    env = (manifest or {}).get("environment")
    return bool(env.get("enabled")) if isinstance(env, dict) else bool(env)


def expand_tools(tools, env_enabled: bool = False) -> list[str]:
    """Ids do manifesto (capacidades ou ferramentas) -> nomes de ferramenta do broker, sem repetir.

    `env_enabled` (ambiente ligado no manifesto) acrescenta as ferramentas rt_env_* do runtime-manager."""
    out: list[str] = []
    extra = ["rt_env_status", "rt_env_manage"] if env_enabled else []
    for t in ["rt_confirm", *(tools or []), *extra]:  # confirmar e sempre possivel: a regra e do template
        for name in GROUPS.get(t, (t,)):
            if name not in out:
                out.append(name)
    return out
