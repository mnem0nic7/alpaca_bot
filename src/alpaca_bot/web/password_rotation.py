from __future__ import annotations

from pathlib import Path
import secrets
import shlex

from alpaca_bot.web.auth import hash_password


def generate_password() -> str:
    return secrets.token_urlsafe(24)


def update_dashboard_auth_env(
    env_text: str,
    *,
    username: str,
    password_hash: str,
) -> str:
    replacements = {
        "DASHBOARD_AUTH_ENABLED": "true",
        "DASHBOARD_AUTH_USERNAME": username,
        "DASHBOARD_AUTH_PASSWORD_HASH": password_hash,
    }

    seen: set[str] = set()
    rendered_lines: list[str] = []
    for raw_line in env_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            rendered_lines.append(raw_line)
            continue
        key, _value = raw_line.split("=", 1)
        if key in replacements:
            rendered_lines.append(_render_env_line(key, replacements[key]))
            seen.add(key)
        else:
            rendered_lines.append(raw_line)

    if rendered_lines and rendered_lines[-1] != "":
        rendered_lines.append("")
    for key in (
        "DASHBOARD_AUTH_ENABLED",
        "DASHBOARD_AUTH_USERNAME",
        "DASHBOARD_AUTH_PASSWORD_HASH",
    ):
        if key not in seen:
            rendered_lines.append(_render_env_line(key, replacements[key]))
    return "\n".join(rendered_lines) + "\n"


def rotate_dashboard_password(
    *,
    env_file: Path,
    password_file: Path,
    username: str | None = None,
    password: str | None = None,
) -> tuple[str, str]:
    env_text = env_file.read_text(encoding="utf-8")
    resolved_username = username or _extract_env_value(
        env_text,
        "DASHBOARD_AUTH_USERNAME",
    )
    if not resolved_username:
        raise ValueError("Dashboard username is required or must already exist in the env file")

    resolved_password = password or generate_password()
    password_hash = hash_password(resolved_password)
    updated_env = update_dashboard_auth_env(
        env_text,
        username=resolved_username,
        password_hash=password_hash,
    )

    env_file.write_text(updated_env, encoding="utf-8")
    password_file.parent.mkdir(parents=True, exist_ok=True)
    password_file.write_text(f"{resolved_password}\n", encoding="utf-8")
    password_file.chmod(0o600)
    return resolved_username, resolved_password


def _extract_env_value(env_text: str, key: str) -> str | None:
    for line in env_text.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        candidate_key, candidate_value = line.split("=", 1)
        if candidate_key != key:
            continue
        return candidate_value.strip().strip("'").strip('"')
    return None


def _render_env_line(key: str, value: str) -> str:
    return f"{key}={shlex.quote(value)}"
