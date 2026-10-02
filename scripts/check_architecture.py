"""Require design documentation alongside changes to architecture-sensitive contracts."""

import argparse
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    # Resolve the revision before passing it to git diff; never treat it as an option.
    base = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{args.base}^{{commit}}"], text=True
    ).strip()
    paths = subprocess.check_output(
        ["git", "diff", "--name-only", f"{base}...HEAD", "--"], text=True
    ).splitlines()
    sensitive = any(
        p in {"api/openapi.yaml", "pyproject.toml", "compose.yaml"}
        or p.startswith("backend/migrations/versions/")
        for p in paths
    )
    documented = any(p.startswith(("docs/adr/", "docs/architecture/", "docs/api/")) for p in paths)
    if sensitive and not documented:
        raise SystemExit("Update an architecture document or ADR with this contract/schema change.")
    print("Architecture documentation gate passed.")


if __name__ == "__main__":
    main()
