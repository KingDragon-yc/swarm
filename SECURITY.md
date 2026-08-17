# Security Policy

IMF is intended only for authorized SRC, lab, and security-research scopes.

Do not submit API keys, Feishu App Secrets, cookies, session files, private target data, or exploit evidence in public issues. If you discover a credential exposure or a vulnerability in the framework itself, contact the repository owner privately through their GitHub profile before publishing details.

Before opening an issue, remove organization names, production hosts, user data, access tokens, and any artifact that is not safe to redistribute.

IMF is currently a local operator console. Keep the Web UI on loopback, treat
`workplaces/` as the authorized root, and pass only attachment files that are
safe to copy into that root. Every dispatch checks in all four operatives before
starting work. Cursor actions use the OS sandbox on macOS/Linux; Windows
Cursor CLI falls back to its allowlist mode because its OS sandbox is
unsupported there. Keep Windows runs restricted to disposable, authorized
workplaces and do not treat the fallback as a host-wide security boundary.

Long-running turns use a 30-minute idle window, ten-minute output polling, and
a two-hour hard cap. A live heartbeat extends the idle window; a silent or
over-cap process is terminated and recorded as failed.
