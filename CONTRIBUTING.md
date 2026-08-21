# Contributing

## Branching and Pulls

- Create feature branches from `main`.
- Keep local history linear:

```bash
git config pull.rebase true
```

- Sync before push:

```bash
git pull --rebase --tags origin main
```

## Secrets and Environment Variables

- Never commit real API keys, tokens, or passwords.
- Use `.env.example` as the template.
- Create your local `.env` from the template:

```bash
cp .env.example .env
```

- Continue agent config should reference environment variables (already configured in `.continue/agents/new-config.yaml`).

## If Push Protection Blocks a Push

- Do not use "unblock secret" links unless absolutely necessary.
- Remove secrets from commits, then rotate the exposed key in the provider dashboard.
- After rewriting history, push again.