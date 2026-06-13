# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities **privately**. Do **not** open a public
GitHub issue for a security report.

You can report a vulnerability by either:

- emailing **security@getstaykey.com**, or
- using GitHub's
  [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
  ("Report a vulnerability" on the Security tab of this repository).

We aim to acknowledge your report within **about 5 business days** and will keep
you updated on remediation progress.

## Scope

This integration handles sensitive credentials. In particular it:

- stores a **Staykey API token**, and
- posts smart-lock and device events to a **configured webhook/gateway over TLS**.

Please flag any issue that could lead to mishandling of those secrets — for
example, a token or webhook URL being written to logs, exposed in diagnostics,
transmitted without TLS, or leaked through an error message.

## Supported versions

The **latest released version** receives security fixes. Please upgrade to the
latest release before reporting an issue where possible.
