// 95-read-secret.cedar — TUNABLE (reading a secret is allowed; exfiltration is the guardrail)
@id("base.read-secret")
permit (principal == Agent::"session", action == Action::"ReadSecret", resource);
