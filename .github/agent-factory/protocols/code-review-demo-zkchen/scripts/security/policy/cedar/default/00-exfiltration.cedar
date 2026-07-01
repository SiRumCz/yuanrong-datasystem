// 00-exfiltration.cedar — LOCKED (OWASP ASI01 exfiltration)
@id("locked.no-exfiltration")
forbid (principal, action == Action::"Network", resource)
when { context.touched_secret && context.external_host };
