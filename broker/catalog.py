"""Grupos de ferramentas: o catalogo do produto lista capacidades; o broker executa ferramentas finas."""
from __future__ import annotations

GROUPS: dict[str, tuple[str, ...]] = {
    "rt_env_manage": ("rt_env_start", "rt_env_exec", "rt_env_upload", "rt_env_download", "rt_env_stop", "rt_env_backup", "rt_env_expose",
                      "rt_env_unexpose", "rt_env_domain_add", "rt_env_domain_verify"),
    "rt_job_schedule": ("rt_schedule_create", "rt_schedule_list", "rt_schedule_pause",
                        "rt_schedule_delete"),
}


def expand_tools(tools) -> list[str]:
    """Ids do manifesto (capacidades ou ferramentas) -> nomes de ferramenta do broker, sem repetir."""
    out: list[str] = []
    for t in ["rt_confirm", *(tools or [])]:  # confirmar e sempre possivel: a regra e do template
        for name in GROUPS.get(t, (t,)):
            if name not in out:
                out.append(name)
    return out
