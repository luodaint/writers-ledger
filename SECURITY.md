# Security

Please report a vulnerability privately: open the repository's **Security** tab and
choose **Report a vulnerability**. Don't open a public issue for it.

How the app protects itself (basic auth, webhook signatures, read-only Odoo key, no
customer data in the ledger or in front of the LLM) is described in the
[README](README.md#what-the-ledger-can-and-cant-see) and in [AGENTS.md](AGENTS.md#security-rules).

`main` only changes through pull requests that pass CI and CodeQL; secret scanning
with push protection is on.
