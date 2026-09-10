# ADR 0009: Separate local and production secret sources

## Decision

Keep one `just` entry point for Kubernetes secret operations with two explicit modes. Local mode may read `.env` and update the kind cluster. Production mode reads only the Kubernetes Secret and verifies that required pilot settings are populated.

Production secret values are supplied by the deployment's secret manager or External Secrets controller. The repository does not choose a cloud provider or store production credentials.

## Consequences

Local setup remains reproducible with the existing `.env` workflow. Production deployment fails before rollout when required settings are absent, and cannot accidentally upload a developer `.env` or apply the placeholder Secret manifest. The same image and application configuration can be used in both environments while secret ownership stays explicit.
