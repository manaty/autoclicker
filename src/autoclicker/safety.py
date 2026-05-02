from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel, Field

from .config import Config


SYSTEM_PROMPT = """You classify text scraped from a CLI coding-assistant confirmation dialog
(Claude Code's 'Allow this bash command?' prompt, or Codex CLI's 'Yes / No, and tell Codex what to do differently' prompt).

The text usually contains:
  - An optional question / title (in any language, e.g. French).
  - One or more shell commands that the assistant wants to execute (often in a code block).

Focus on the actual shell command(s). Ignore prose, explanations, or UI labels.
If you can't identify any command, return safe=false with category='unparsable'.

Mark UNSAFE if any command is plausibly destructive, irreversible, privilege-escalating, or exfiltrates data. Examples of UNSAFE:
- rm -rf, rm of many files, rmdir of non-empty dirs
- git reset --hard, git clean -fdx, git push --force (especially to main/master), git branch -D
- Any DROP TABLE, TRUNCATE, DELETE without WHERE
- dd, mkfs, format, diskpart
- kill -9 on system/PID 1
- chmod/chown on /etc, /usr, system roots
- curl|sh, wget|sh, piping remote scripts to a shell
- Downloading and running unverified binaries
- shutdown, reboot, halt
- Modifying PATH, shell rc, systemd units for persistence
- Commands that exfiltrate tokens, env vars, SSH keys, /etc/shadow
- Recursive chmod 777
- Package removals/downgrades of core system packages
- Anything that bypasses hooks (--no-verify) without clear justification
- Network-wide scans, mass port scans
- Commands that appear to disable security tooling

Mark SAFE if it is a read, query, build, test, lint, format, local file edit, normal git commit/push to a feature branch, install a known library, or run a dev server.

When ambiguous, prefer UNSAFE. The user can always override manually."""


class Verdict(BaseModel):
    safe: bool = Field(description="True only if the command is clearly non-destructive.")
    category: str = Field(description="Short category like 'read', 'build', 'destructive-fs', 'force-push'.")
    reason: str = Field(description="One short sentence explaining the verdict.")


@dataclass
class ClassifyResult:
    verdict: Verdict
    error: Optional[str] = None


def classify(command_text: str, cfg: Config) -> ClassifyResult:
    api_key = cfg.resolved_api_key()
    if not api_key:
        return ClassifyResult(
            verdict=Verdict(
                safe=True,
                category="no-api-key",
                reason="AI check skipped: OPENAI_API_KEY not set",
            ),
        )

    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=cfg.openai_timeout_s)

    primary, fallback = cfg.resolved_safety_models()
    for model in (primary, fallback):
        try:
            resp = client.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Command:\n```\n{command_text}\n```"},
                ],
                response_format=Verdict,
            )
            verdict = resp.choices[0].message.parsed
            if verdict is None:
                continue
            return ClassifyResult(verdict=verdict)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{model}: {exc}"
            continue

    if getattr(cfg, "fail_open_on_api_error", False):
        return ClassifyResult(
            verdict=Verdict(
                safe=True,
                category="api-error-fail-open",
                reason="classifier unreachable; fail-open per config",
            ),
            error=last_error,
        )
    return ClassifyResult(
        verdict=Verdict(safe=False, category="api-error", reason="classifier unreachable; failing closed"),
        error=last_error,
    )
