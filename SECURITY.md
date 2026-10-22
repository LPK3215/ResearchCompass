# Security Policy

## Supported versions

Security fixes are applied to the `0.11.x` release line. Older tags are kept for reproducibility but are not actively patched.

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

- Preferred: open a private report through GitHub Security Advisories: <https://github.com/LPK3215/ResearchCompass/security/advisories/new>
- Alternative: email `17538703215@163.com` with:
  - affected version or commit;
  - a description of the issue and its impact;
  - reproduction steps or a proof of concept;
  - any suggested fix or mitigation.

We will acknowledge the report, investigate it, and coordinate disclosure. Please allow reasonable time for a fix before publishing details.

## Secrets and private data

Never commit:

- `.env`, `.env.prod` or any file containing model/API keys;
- test accounts, admin passwords or session tokens;
- private papers, PDFs or research data that cannot be redistributed.

Use `.env.template` and `.env.prod.template` as the public configuration references. If a secret is exposed, rotate it immediately and remove it from the working tree and Git history.

## Scope notes

- The AI research assistant is designed not to execute system commands or write arbitrary files; deliverables go through export tools.
- Agents that require sandbox execution use the dedicated sandbox service, not the research assistant.
- Strict graph retrieval and evidence synthesis fail explicitly when prerequisites are missing; they do not silently downgrade.

## Dependencies

The project builds on Yuxi and other open-source dependencies. Report vulnerabilities in upstream projects to their respective maintainers as well; we will help coordinate when the issue affects ResearchCompass.
